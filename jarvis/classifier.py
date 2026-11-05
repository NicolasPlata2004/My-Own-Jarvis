"""¿Lo que hay en pantalla es del tema? Reglas rápidas primero; si no alcanzan, la IA.

A la IA solo le llega el título de la ventana y el nombre del programa (y una
captura únicamente si activas pantalla.captura_para_dudas). Cada respuesta se
guarda, así que cada título se consulta una sola vez.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .screen import Ventana

log = logging.getLogger("jarvis.clasificador")

CATEGORIAS = ("estudio", "herramienta", "neutral", "mensajeria", "distraccion")
NAVEGADORES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe"}
SISTEMA = {"searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "lockapp.exe",
           "taskmgr.exe", "systemsettings.exe", "applicationframehost.exe", "searchapp.exe"}
# Títulos que no dicen nada del contenido (solo con capturas activadas se mira dentro).
GENERICOS = {"gemini", "google gemini", "chatgpt", "claude", "reproductor multimedia", "media player",
             "películas y tv", "fotos"}
EXT_INDICE = {".mp4", ".mkv", ".webm", ".pdf", ".xopp", ".mp3", ".m4a"}
CARPETAS_IGNORADAS = {"codigo", "media", "media_hd", "partial_movie_files", "__pycache__", ".git",
                      "node_modules", ".venv"}
_SUFIJO_NAVEGADOR = re.compile(
    r"\s+[-–—]\s+(?:[^-–—]*?:\s*)?(?:google chrome|microsoft\W*edge|mozilla firefox|brave|opera|vivaldi)\s*$",
    re.I)
_CONTADOR = re.compile(r"^\(\d+\+?\)\s*")
_ALIAS_CATEGORIA = {"distracción": "distraccion", "distraction": "distraccion", "study": "estudio",
                    "tool": "herramienta", "herramientas": "herramienta", "mensajería": "mensajeria"}
_PALABRA_CATEGORIA = re.compile(r"\b(estudio|herramienta|neutral|distracci[oó]n)\b", re.I)


def leer_respuesta(texto: str) -> tuple[str, str]:
    """«distraccion | vídeo de fútbol» → ('distraccion', 'vídeo de fútbol'). Tolera adornos del modelo."""
    texto = re.sub(r"[*`#]", "", texto).strip()
    m = _PALABRA_CATEGORIA.search(texto)
    if not m:
        return "neutral", texto[:80]
    categoria = _ALIAS_CATEGORIA.get(m.group(1).lower(), m.group(1).lower())
    razon = texto[m.end():].strip(" |:-–—.\"'").split("\n")[0]
    return categoria, razon[:80]


def normalizar_titulo(titulo: str) -> str:
    titulo = _CONTADOR.sub("", titulo.strip())
    titulo = _SUFIJO_NAVEGADOR.sub("", titulo)
    return re.sub(r"\s+", " ", titulo).strip()


def _contiene(texto: str, patrones: list[str]) -> str | None:
    for p in patrones:
        if p and p in texto:
            return p
    return None


@dataclass
class Veredicto:
    categoria: str            # estudio | herramienta | neutral | mensajeria | distraccion | desconocido
    razon: str = ""
    fuente: str = "regla"     # regla | archivo | usuario | llm | vision | pendiente


class Clasificador:
    def __init__(self, cfg: dict, db, llm):
        self.db = db
        self.llm = llm
        apps = cfg["apps"]
        self.listas = {k: [s.lower() for s in apps.get(k, [])]
                       for k in ("estudio", "herramientas", "mensajeria", "distracciones", "neutras")}
        self.captura = bool(cfg["pantalla"]["captura_para_dudas"])
        self.temas_cfg = cfg["temas"]
        self.descripcion = cfg["usuario"].get("descripcion", "estudiante")
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clasificador")
        self._pendientes: set[str] = set()
        self._fallos: dict[str, float] = {}
        self._revisiones: dict[str, tuple[float, str, str]] = {}
        self._indice: dict[str, str] = {}
        self._palabras: list[str] = []
        self.tema = ""
        self.objetivo = ""
        self._mi_pid = os.getpid()
        # Las carpetas de todos los temas cuentan como material (repasar otra materia no es perder
        # el tiempo), y se reconocen aunque no haya sesión abierta.
        carpetas = {c for t in self.temas_cfg for c in t.get("carpetas", [])}
        self._carpetas = [str(Path(c)).lower() for c in carpetas if Path(c).exists()]
        threading.Thread(target=self._indexar, args=(list(carpetas),), daemon=True).start()

    # --- al empezar una sesión ----------------------------------------------------
    def preparar(self, tema: str, objetivo: str) -> None:
        self.tema, self.objetivo = tema, objetivo
        actual = next((t for t in self.temas_cfg if t["nombre"] == tema), None)
        if actual:
            palabras = actual.get("palabras", [])
        else:  # tema libre: sus propias palabras cuentan
            palabras = re.findall(r"\w{5,}", f"{tema} {objetivo}".lower())
        self._palabras = [p.lower() for p in palabras]

    def _indexar(self, carpetas: list[str]) -> None:
        indice: dict[str, str] = {}
        for carpeta in carpetas:
            for raiz, dirs, archivos in os.walk(carpeta):
                dirs[:] = [d for d in dirs if d.lower() not in CARPETAS_IGNORADAS and not d.startswith("_")]
                for a in archivos:
                    ruta = Path(raiz, a)
                    if ruta.suffix.lower() in EXT_INDICE and len(ruta.stem) >= 6:
                        indice.setdefault(ruta.stem.lower(), str(ruta))
        self._indice = indice
        log.info("Índice de estudio: %d archivos", len(indice))

    # --- utilidades ---------------------------------------------------------------
    @staticmethod
    def clave(v: Ventana) -> str:
        return f"{v.proceso}|{normalizar_titulo(v.titulo).lower()[:150]}"

    def archivos_de_estudio(self, v: Ventana) -> list[str]:
        """Archivos del material (vídeos, PDF) que está mostrando esa ventana."""
        rutas = [f for f in v.archivos if any(f.lower().startswith(c) for c in self._carpetas)]
        titulo = normalizar_titulo(v.titulo).lower()
        rutas += [r for stem, r in self._indice.items() if stem in titulo and r not in rutas]
        return rutas

    def marcar(self, v: Ventana, categoria: str) -> None:
        """Corrección del usuario: 'esto sí/no es del tema'. Manda sobre todo lo demás."""
        self.db.guardar_clasificacion(self.tema or "*", self.clave(v), categoria, "lo dijiste tú", "usuario")
        self._revisiones.pop(self.clave(v), None)

    # --- decisión -------------------------------------------------------------------
    def clasificar(self, v: Ventana, sesion_activa: bool = True) -> Veredicto:
        if v.pid == self._mi_pid:
            return Veredicto("neutral", "Jarvis")
        titulo = normalizar_titulo(v.titulo)
        tl, proc = titulo.lower(), v.proceso
        if not titulo and proc in ("", "explorer.exe"):
            return Veredicto("neutral", "escritorio")
        if proc in SISTEMA:
            return Veredicto("neutral", "Windows")

        archivos = self.archivos_de_estudio(v)
        if archivos:
            return Veredicto("estudio", Path(archivos[0]).name, "archivo")

        clave = self.clave(v)
        guardado = self.db.clasificacion(self.tema or "*", clave)
        if guardado and guardado[2] == "usuario":
            return Veredicto(guardado[0], guardado[1], "usuario")

        texto = f"{tl} {proc}"
        if p := _contiene(texto, self.listas["distracciones"]):
            return Veredicto("distraccion", p)
        if p := _contiene(texto, self.listas["mensajeria"]):
            return Veredicto("mensajeria", p)
        if p := _contiene(texto, self.listas["neutras"]):
            return Veredicto("neutral", p)
        if p := _contiene(texto, self.listas["herramientas"]):
            return self._revisar_herramienta(v, clave, Veredicto("herramienta", p))
        if p := _contiene(texto, self.listas["estudio"]):
            return Veredicto("estudio", p)
        if p := _contiene(tl, self._palabras):
            return Veredicto("estudio", f"habla de «{p}»")

        if guardado:
            return Veredicto(*guardado)
        if sesion_activa:
            self._consultar(v, titulo, clave)
        return Veredicto("desconocido", "consultando a la IA", "pendiente")

    def _revisar_herramienta(self, v: Ventana, clave: str, veredicto: Veredicto) -> Veredicto:
        """Con capturas activadas, cada 5 min mira si lo de Gemini/ChatGPT es del tema."""
        if not self.captura:
            return veredicto
        revision = self._revisiones.get(clave)
        if revision and time.time() - revision[0] < 300:
            if revision[1] == "distraccion":
                return Veredicto("distraccion", revision[2], "vision")
            return veredicto
        if clave not in self._pendientes:
            self._pendientes.add(clave)
            self._revisiones[clave] = (time.time(), "herramienta", "")
            self._pool.submit(self._revisar_con_captura, normalizar_titulo(v.titulo), clave)
        return veredicto

    # --- IA ---------------------------------------------------------------------------
    def _consultar(self, v: Ventana, titulo: str, clave: str) -> None:
        if clave in self._pendientes or time.time() - self._fallos.get(clave, 0) < 60:
            return
        self._pendientes.add(clave)
        self._pool.submit(self._preguntar, v.proceso, titulo, clave)

    def _instrucciones(self) -> str:
        return (
            f"Clasificas lo que un {self.descripcion} tiene en "
            "pantalla durante una sesión de estudio.\n"
            f"Tema de la sesión: {self.tema}\nObjetivo: {self.objetivo or '(no lo dijo)'}\n\n"
            "Categorías:\n"
            "- estudio: material del tema o de la carrera (clases, vídeos o búsquedas técnicas, apuntes, "
            "ejercicios, Matlab, documentos de la universidad).\n"
            "- herramienta: ayuda a estudiar aunque no sea del tema (traductor, calculadora, IA, correo de la universidad).\n"
            "- neutral: sistema o gestión sin contenido (carpetas, ajustes, descargas, música para estudiar).\n"
            "- distraccion: redes sociales, entretenimiento, noticias, compras, juegos, o vídeos y temas ajenos al estudio.\n"
            "Responde en UNA sola línea con este formato exacto: categoria | razón de máximo 8 palabras\n"
            "Ejemplo: distraccion | vídeo de fútbol ajeno al estudio")

    def _preguntar(self, proceso: str, titulo: str, clave: str) -> None:
        try:
            texto = self.llm.chat(
                [{"role": "system", "content": self._instrucciones()},
                 {"role": "user", "content": f"Programa: {proceso}\nTítulo de la ventana: {titulo}"}],
                rapido=True, temperatura=0, max_tokens=300, timeout=20)
            (categoria, razon), fuente = leer_respuesta(texto), "llm"
            if self.captura and (categoria == "neutral" or titulo.lower() in GENERICOS):
                vision = self._mirar_pantalla()
                if vision:
                    categoria, razon, fuente = vision[0], vision[1], "vision"
            self.db.guardar_clasificacion(self.tema or "*", clave, categoria, razon, fuente)
            log.info("IA: %r → %s (%s)", titulo, categoria, razon)
        except Exception as e:  # noqa: BLE001
            self._fallos[clave] = time.time()
            log.warning("No pude clasificar %r: %s", titulo, e)
        finally:
            self._pendientes.discard(clave)

    def _revisar_con_captura(self, titulo: str, clave: str) -> None:
        try:
            vision = self._mirar_pantalla()
            if vision:
                self._revisiones[clave] = (time.time(), vision[0], vision[1])
                log.info("Captura de %r → %s (%s)", titulo, *vision)
        except Exception as e:  # noqa: BLE001
            log.warning("Revisión con captura falló: %s", e)
        finally:
            self._pendientes.discard(clave)

    def _mirar_pantalla(self) -> tuple[str, str] | None:
        from PIL import ImageGrab

        imagen = ImageGrab.grab()
        imagen.thumbnail((1280, 1280))
        buf = io.BytesIO()
        imagen.convert("RGB").save(buf, "JPEG", quality=70)
        url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        texto = self.llm.chat(
            [{"role": "system", "content": self._instrucciones()},
             {"role": "user", "content": [
                 {"type": "text", "text": "Esta es su pantalla ahora. ¿Lo que se ve es del tema?"},
                 {"type": "image_url", "image_url": {"url": url}}]}],
            modelo=self.llm.modelo_vision, temperatura=0, max_tokens=300, timeout=30)
        return leer_respuesta(texto)
