"""El núcleo de Jarvis: une pantalla, cámara, voz, IA, sesiones y estadísticas.

No sabe nada de la interfaz: le avisa por un objeto con los métodos
estado(dict), mensaje(texto, quien), oido(estado) y hablando(bool).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path

from .camera import MonitorCamara
from .classifier import Clasificador, normalizar_titulo
from .coach import Coach, corto
from .config import cargar
from .db import ESTADOS_FOCO, BaseDatos
from .focus import ESTADOS_MALOS, NOMBRES, MotorFoco, Observacion, Sesion, etiqueta
from .intents import buscar_tema, detectar
from .llm import ClienteLLM
from .report import generar as generar_informe
from .report import minutos_foco, rango_dia
from .screen import MonitorPantalla, Ventana, segundos_inactivo, ventanas_visibles

log = logging.getLogger("jarvis")


def _duracion(minutos: int) -> str:
    """90 → «1 hora y 30 minutos»."""
    h, m = divmod(int(minutos), 60)
    if not h:
        return f"{m} minutos"
    horas = "1 hora" if h == 1 else f"{h} horas"
    return f"{horas} y {m} minutos" if m else horas


# Lo que la IA puede pedir al final de su respuesta, p. ej. [[sesion total=30 tema="Matemáticas"]].
_ETIQUETA_ACCION = re.compile(r"\[\[\s*(\w+)([^\]]*)\]\]")
_ACCIONES_IA = {"sesion": "iniciar", "iniciar": "iniciar", "ajustar": "ajustar", "extender": "extender",
                "acortar": "acortar", "terminar": "terminar", "pausa": "pausa", "seguir": "reanudar",
                "reanudar": "reanudar", "descanso": "descanso"}


def acciones_de_la_ia(texto: str) -> tuple[str, list[dict]]:
    """Separa las etiquetas [[…]] de la respuesta de la IA y las convierte en órdenes."""
    acciones = []
    for m in _ETIQUETA_ACCION.finditer(texto):
        tipo = _ACCIONES_IA.get(m.group(1).lower())
        if not tipo:
            continue
        datos: dict = {"tipo": tipo}
        for clave, valor in re.findall(r'(\w+)\s*=\s*("[^"]*"|[^\s\]]+)', m.group(2)):
            valor = valor.strip('"')
            datos[clave.lower()] = int(valor) if valor.isdigit() else valor
        numeros = re.findall(r"(?<![=\w])(\d+)\b", m.group(2))
        if numeros and "minutos" not in datos:
            datos["minutos"] = int(numeros[0])
        acciones.append(datos)
    return _ETIQUETA_ACCION.sub("", texto).strip(), acciones


PROGRAMAS = {
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "spotify.exe": "Spotify",
    "microsoft.media.player.exe": "el Reproductor multimedia", "vlc.exe": "VLC", "explorer.exe": "el Explorador",
    "whatsapp.root.exe": "WhatsApp", "whatsapp.exe": "WhatsApp", "claude.exe": "Claude", "gemini.exe": "Gemini",
    "matlab.exe": "Matlab", "code.exe": "VS Code", "sumatrapdf.exe": "SumatraPDF", "acrobat.exe": "Acrobat",
    "winword.exe": "Word", "powerpnt.exe": "PowerPoint", "excel.exe": "Excel", "onenote.exe": "OneNote",
}


class Jarvis:
    def __init__(self, cfg: dict, ui):
        self.cfg = cfg
        self.ui = ui
        self.db = BaseDatos()
        self.llm = ClienteLLM(cfg)
        self.pantalla = MonitorPantalla()
        self.clasif = Clasificador(cfg, self.db, self.llm)
        self.motor = MotorFoco(cfg, self.db)
        self.sesion: Sesion | None = None
        self.camara: MonitorCamara | None = None
        self.camara_activa = bool(cfg["camara"]["activar"])
        self._camara_solo_vista = False
        # Para la ventana de la cámara sin sesión: qué estado le pondría si estuvieras estudiando.
        self._motor_vista = MotorFoco(cfg, None)
        self._sesion_vista = Sesion(0, "", "", 50, 10, bool(cfg["foco"]["cabeza_abajo_es_estudio"]))
        self._objetivo_pendiente = ""
        self._callar_confirmaciones = False
        self.ultima_ventana_util = Ventana()
        self.historial: deque[dict] = deque(maxlen=12)
        self._lock = threading.RLock()
        self._parar = threading.Event()
        self._segundos_archivo: dict[str, float] = defaultdict(float)
        self._ultimo_volcado = time.time()
        self._foco_hoy = (0.0, 0.0)  # (cuándo se calculó, minutos guardados)
        self.coach = Coach(cfg, self.llm, self.decir, self.contexto)
        self.voz = self.oido = self.atajo = None
        if cfg["voz"]["activar"]:
            from .voice import Atajo, Oido, Voz
            self.voz = Voz(cfg, al_hablar=ui.hablando)
            self.oido = Oido(cfg, self.voz, self.llm, al_texto=lambda t: self.texto_usuario(t, por_voz=True),
                             al_estado=ui.oido)
            self.atajo = Atajo(cfg["voz"]["tecla_hablar"], self.escuchar)

    # =========================================================================
    #  Ciclo de vida
    # =========================================================================
    def arrancar(self, silencioso: bool = False) -> None:
        """silencioso: al encender Windows no saluda en voz alta (puedes estar en clase)."""
        retomada = self._retomar_sesion()
        for hilo in (self.voz, self.oido, self.atajo):
            if hilo:
                hilo.start()
        if self.voz:
            threading.Thread(target=self.voz.precalentar, args=(self._frases_fijas(),), daemon=True).start()
        threading.Thread(target=self._bucle, name="monitor", daemon=True).start()
        threading.Thread(target=self._saludar, args=(silencioso, retomada), name="saludo", daemon=True).start()

    def _saludar(self, silencioso: bool, retomada: bool) -> None:
        if not self.llm.asegurar_servidor():
            self.ui.mensaje("No pude arrancar FreeLLMAPI: usaré frases fijas hasta que vuelva.", "jarvis")
        tecla = self.cfg["voz"]["tecla_hablar"].upper()
        if retomada and self.sesion:
            texto = f"Volví. Seguimos con tu sesión de {corto(self.sesion.tema)}."
        else:
            texto = f"Listo, {self.cfg['usuario']['nombre']}. Cuando quieras empezamos."
        if silencioso:
            self.ui.mensaje(texto, "jarvis")
        else:
            self.decir(texto, prioridad=2)
        self.ui.mensaje(f"Di «hey Jarvis» o pulsa {tecla} para hablarme. Clic en el orbe para el panel.", "jarvis")

    def _frases_fijas(self) -> list[str]:
        nombre = self.cfg["usuario"]["nombre"]
        temas = [corto(t["nombre"]) for t in self.cfg["temas"]]
        return ([f"Listo, {nombre}. Cuando quieras empezamos.", "Seguimos.", "De vuelta.",
                 "En pausa. Dime «sigamos» cuando vuelvas.", "Pausada.", "Listo, música.", "Siguiente canción.",
                 "La anterior.", "Más volumen.", "Menos volumen.", "La música ya está en pausa.",
                 "Se me cayó la conexión con la IA. Repíteme en un momento.", "Me callo media hora. Sigo anotando.",
                 "Ya puedo hablar otra vez.", "Sesión cerrada. Fue muy corta para hacer balance.",
                 "Entendido: en esta sesión no vigilo el celular.", "No pude manejar la música."]
                + [f"Volví. Seguimos con tu sesión de {t}." for t in temas])

    def _retomar_sesion(self) -> bool:
        """Si Jarvis se cerró con una sesión abierta (reinicio, apagón), la retoma si fue hace menos de 3 h;
        las más viejas se cierran donde quedaron."""
        ahora = time.time()
        abiertas = self.db.consultar("SELECT id, inicio, tema, objetivo, plan_min, descanso_min, total_min "
                                     "FROM sesiones WHERE fin IS NULL ORDER BY id DESC")
        for i, (sid, inicio, tema, objetivo, plan, descanso, total) in enumerate(abiertas):
            ultimo = self.db.consultar("SELECT MAX(fin) FROM segmentos WHERE sesion_id = ?", (sid,))[0][0] or inicio
            if i > 0 or ahora - ultimo > 3 * 3600:
                self.db.ejecutar("UPDATE sesiones SET fin = ? WHERE id = ?", (ultimo, sid))
                continue
            s = Sesion(sid, tema, objetivo or "", plan, descanso, self.cfg["foco"]["cabeza_abajo_es_estudio"],
                       inicio=inicio, total_min=total or 0)
            segundos = self.db.segundos_por_estado(inicio, ahora, sid)
            s.foco_s = sum(v for e, v in segundos.items() if e in ESTADOS_FOCO)
            s.total_s = sum(v for e, v in segundos.items() if e not in ("descanso", "pausa"))
            s.distracciones = self.db.consultar(
                "SELECT COUNT(*) FROM eventos WHERE sesion_id = ? AND tipo = 'distraccion'", (sid,))[0][0]
            with self._lock:
                self.sesion = s
                self.motor.sesion_id = sid
                self.clasif.preparar(tema, objetivo or "")
                self._encender_camara()
            log.info("Sesión %s retomada (%s)", sid, tema)
        return self.sesion is not None

    def silenciar(self, minutos: int) -> None:
        """Sin regaños ni recordatorios en voz alta (sigue anotando y escribiendo en el globo)."""
        self.coach.silencio_hasta = time.time() + minutos * 60 if minutos else 0.0
        if minutos and self.voz:
            self.voz.callar()
        if minutos:
            self.ui.mensaje(f"En silencio {minutos // 60} h {minutos % 60:02d} min: sigo anotando, solo escribo.",
                            "jarvis")
        else:
            self.decir("Ya puedo hablar otra vez.", prioridad=1)

    def salir(self, cerrar_sesion: bool = True) -> None:
        """cerrar_sesion=False (reinicio): la sesión queda abierta y la retoma el próximo arranque."""
        if self.sesion and cerrar_sesion:
            self.terminar_sesion(hablar=False)
        elif self.sesion:
            with self._lock:
                self.motor.cerrar()
        self._parar.set()
        self._apagar_camara(forzar=True)
        for hilo in (self.oido, self.atajo, self.voz):
            if hilo:
                hilo.detener()
        self._volcar_archivos()
        self.motor.cerrar()
        self.llm.apagar_servidor()

    def recargar(self) -> None:
        cfg = cargar()
        with self._lock:
            self.cfg = cfg
            self.clasif = Clasificador(cfg, self.db, self.llm)
            if self.sesion:
                self.clasif.preparar(self.sesion.tema, self.sesion.objetivo)
            gracias = MotorFoco(cfg, self.db).gracia
            self.motor.gracia.update(gracias)
            self.coach.configurar(cfg)
        self.decir("Ajustes recargados. Los de voz se aplican al reiniciarme.", prioridad=2)

    # =========================================================================
    #  Bucle principal (1 vez por segundo)
    # =========================================================================
    def _bucle(self) -> None:
        while not self._parar.is_set():
            t0 = time.monotonic()
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - el bucle no puede morir
                log.exception("Error en el bucle")
            self._parar.wait(max(0.0, 1.0 - (time.monotonic() - t0)))

    def tick(self) -> None:
        ahora = time.time()
        v = self.pantalla.ventana_activa()
        inactivo = segundos_inactivo()
        with self._lock:
            s = self.sesion
            if s:
                self._controlar_bloques(s, ahora)
                s = self.sesion   # pudo cerrarse al cumplirse el tiempo
            ver = self.clasif.clasificar(v, sesion_activa=bool(s and s.modo == "estudio"))
            cam = self.camara.obs() if self.camara else None
            o = Observacion(ahora, v, ver, inactivo, cam)
            eventos = self.motor.actualizar(o, s)
        if v.pid != os.getpid() and v.titulo:
            self.ultima_ventana_util = v
        for e in eventos:
            log.info("%s → %s (%s)", e["de"], e["a"], e["titulo"][:60])
            if e["de"] in ESTADOS_MALOS:
                self.db.evento(self.motor.sesion_id, "distraccion",
                               f"{e['de']}|{e['duracion_previa']:.0f}|{e['titulo'][:80]}")
        self.coach.procesar(eventos, self.motor, s)
        self.coach.tick(self.motor, s, ahora)
        if s is None:
            self.coach.recordatorio(ahora, inactivo, self.minutos_hoy(ahora), self._estudio_reciente(ahora),
                                    normalizar_titulo(v.titulo)[:60] or "el escritorio")
        elif self.motor.estado in ESTADOS_FOCO:
            for ruta in self.clasif.archivos_de_estudio(v):
                self._segundos_archivo[ruta] += 1.0
        if ahora - self._ultimo_volcado > 30:
            self._volcar_archivos()
        self.ui.estado(self.resumen(o))

    def _controlar_bloques(self, s: Sesion, ahora: float) -> None:
        """Bloques de estudio con descanso (al estilo pomodoro) y, si se pidió, un tiempo total."""
        if s.modo == "estudio":
            restante = s.restante_total_s()
            if restante is not None and restante <= 0 and not s.aviso_total:
                s.aviso_total = True   # se cumplió el tiempo que pediste: se cierra con balance
                self.terminar_sesion(hablar=True, motivo="tiempo")
                return
            if restante is not None and restante <= 300 and s.total_min >= 20 and not s.aviso_cinco:
                s.aviso_cinco = True
                self.decir("Te quedan cinco minutos.", prioridad=6)
            if not s.aviso_bloque and s.bloque_s(ahora) >= s.largo_bloque_s():
                s.aviso_bloque = True
                s.bloques += 1
                if s.descanso_min and (restante is None or restante > 300):  # si queda poco, sin descanso
                    self._iniciar_descanso(s, s.descanso_min, ahora)
                    self.coach.fin_bloque()
        elif s.modo == "descanso" and s.fin_descanso and ahora >= s.fin_descanso:
            self._volver_a_estudiar(s, ahora)
            self.coach.fin_descanso()

    def _iniciar_descanso(self, s: Sesion, minutos: int, ahora: float) -> None:
        s.modo = "descanso"
        s.fin_descanso = ahora + minutos * 60
        self._apagar_camara()

    def _volver_a_estudiar(self, s: Sesion, ahora: float) -> None:
        s.modo = "estudio"
        s.fin_descanso = 0.0
        s.inicio_bloque = ahora
        s.extra_bloque_s = 0.0
        s.aviso_bloque = False
        self._encender_camara()

    def _volcar_archivos(self) -> None:
        pendientes, self._segundos_archivo = self._segundos_archivo, defaultdict(float)
        for ruta, segundos in pendientes.items():
            self.db.sumar_archivo(ruta, segundos)
        self._ultimo_volcado = time.time()

    # =========================================================================
    #  Sesiones
    # =========================================================================
    def iniciar_sesion(self, tema: str | None = None, objetivo: str = "", bloque: int | None = None,
                       descanso: int | None = None, papel: bool | None = None, total: int | None = None) -> None:
        """total: minutos de estudio y se cierra sola (0 o None = sin límite); bloque y descanso: el ritmo."""
        with self._lock:
            if self.sesion:
                self.terminar_sesion(hablar=False)
            tema = tema or self._ultimo_tema()
            objetivo = objetivo or self._objetivo_pendiente
            self._objetivo_pendiente = ""
            total = max(0, int(total or 0))
            plan = int(bloque or self.cfg["sesion"]["duracion_min"])
            if total and plan > total:
                plan = total   # un solo bloque: el tiempo que pediste
            desc = int(descanso if descanso is not None else self.cfg["sesion"]["descanso_min"])
            papel = self.cfg["foco"]["cabeza_abajo_es_estudio"] if papel is None else papel
            sid = self.db.nueva_sesion(tema, objetivo, plan, desc, total)
            self.sesion = Sesion(sid, tema, objetivo, plan, desc, papel, total_min=total)
            self.motor.sesion_id = sid
            self.clasif.preparar(tema, objetivo)
            self._encender_camara()
        log.info("Sesión %s: %s (%s)", sid, tema, self.describir_plan())
        if not self._callar_confirmaciones:   # si la pidió la IA, ella ya lo dijo
            self.coach.inicio_sesion()

    def _ultimo_tema(self) -> str:
        """Sin tema dicho: el de la última sesión de hoy, o el primero de la configuración."""
        filas = self.db.consultar("SELECT tema FROM sesiones WHERE inicio > ? ORDER BY id DESC LIMIT 1",
                                  (rango_dia(dt.date.today())[0],))
        if filas and filas[0][0]:
            return filas[0][0]
        temas = self.cfg["temas"]
        return temas[0]["nombre"] if temas else "Estudio"

    # --- cambiar la sesión en marcha -----------------------------------------------
    def describir_plan(self, s: Sesion | None = None) -> str:
        s = s or self.sesion
        if not s:
            return ""
        partes = [f"{_duracion(s.total_min)} en total" if s.total_min else "sin límite de tiempo"]
        if not s.total_min or s.total_min > s.plan_min:
            ritmo = f"bloques de {s.plan_min} minutos"
            ritmo += f" con descansos de {s.descanso_min}" if s.descanso_min else " sin descansos"
            partes.append(ritmo)
        return ", ".join(partes)

    def ajustar_sesion(self, total: int | None = None, bloque: int | None = None,
                       descanso: int | None = None) -> None:
        with self._lock:
            s = self.sesion
            if not s:
                return
            if bloque:
                s.plan_min = int(bloque)
            if descanso is not None:
                s.descanso_min = int(descanso)
            if total is not None:
                s.total_min = max(0, int(total))
                s.aviso_total = s.aviso_cinco = False
            if s.total_min and s.plan_min > s.total_min:
                s.plan_min = s.total_min
            self.db.actualizar_sesion(s.id, plan_min=s.plan_min, descanso_min=s.descanso_min, total_min=s.total_min)
        self._confirmar(f"Listo: {self.describir_plan()}.")

    def extender(self, minutos: int) -> None:
        """«Dame 10 minutos más»: alarga el total (o este bloque, si la sesión no tiene total).
        Sin sesión, abre una nueva de esos minutos con el último tema."""
        minutos = max(1, int(minutos))
        with self._lock:
            s = self.sesion
            if s and s.total_min:
                s.total_min += minutos
                s.aviso_total = s.aviso_cinco = False
                self.db.actualizar_sesion(s.id, total_min=s.total_min)
                restante = max(0, round((s.restante_total_s() or 0) / 60))
                texto = f"Listo, {minutos} minutos más: te quedan {restante}."
            elif s:
                s.extra_bloque_s += minutos * 60
                texto = f"Listo, {minutos} minutos más en este bloque."
            else:
                texto = ""
        if s is None:
            self.iniciar_sesion(total=minutos)
            return
        self._confirmar(texto)

    def acortar(self, minutos: int) -> None:
        with self._lock:
            s = self.sesion
            if not s:
                return
            if s.total_min:
                s.total_min = max(int(s.total_s // 60) + 1, s.total_min - int(minutos))
                self.db.actualizar_sesion(s.id, total_min=s.total_min)
                texto = f"Hecho: te quedan {max(0, round((s.restante_total_s() or 0) / 60))} minutos."
            else:
                s.extra_bloque_s = max(s.bloque_s() - s.plan_min * 60 + 60, s.extra_bloque_s - int(minutos) * 60)
                texto = f"Hecho: este bloque termina en {max(1, round((s.largo_bloque_s() - s.bloque_s()) / 60))} minutos."
        self._confirmar(texto)

    def terminar_en(self, minutos: int) -> None:
        with self._lock:
            s = self.sesion
            if not s:
                return
            s.total_min = int(s.total_s // 60) + max(1, int(minutos))
            s.aviso_total = s.aviso_cinco = False
            self.db.actualizar_sesion(s.id, total_min=s.total_min)
        self._confirmar(f"Listo, terminamos en {minutos} minutos.")

    def cambiar_tema(self, tema: str) -> None:
        with self._lock:
            s = self.sesion
            if not s:
                return
            s.tema = tema
            self.clasif.preparar(tema, s.objetivo)
            self.db.actualizar_sesion(s.id, tema=tema)
        self._confirmar(f"Cambiamos a {corto(tema)}.")

    def poner_objetivo(self, objetivo: str) -> None:
        with self._lock:
            s = self.sesion
            if s:
                s.objetivo = objetivo
                self.clasif.preparar(s.tema, objetivo)
                self.db.actualizar_sesion(s.id, objetivo=objetivo)
            else:
                self._objetivo_pendiente = objetivo
        self._confirmar(f"Anotado el objetivo: {objetivo}." if s else "Anotado: lo uso en cuanto empecemos.")

    def texto_restante(self) -> str:
        s = self.sesion
        if not s:
            return "No hay una sesión abierta. Dime, por ejemplo, «sesión de 30 minutos»."
        if s.modo == "descanso":
            texto = f"Te quedan {max(0, round((s.fin_descanso - time.time()) / 60))} minutos de descanso."
        elif s.modo == "pausa":
            texto = "La sesión está en pausa."
        else:
            texto = f"Te quedan {max(0, round((s.largo_bloque_s() - s.bloque_s()) / 60))} minutos de este bloque."
        restante = s.restante_total_s()
        if restante is not None:
            texto += f" De la sesión, {max(0, round(restante / 60))} de {s.total_min}."
        return texto

    def pausar(self) -> None:
        with self._lock:
            if self.sesion and self.sesion.modo == "estudio":
                self.sesion.modo = "pausa"
                self._apagar_camara()
        self._confirmar("En pausa. Dime «sigamos» cuando vuelvas.")

    def reanudar(self) -> None:
        with self._lock:
            if self.sesion and self.sesion.modo != "estudio":
                self._volver_a_estudiar(self.sesion, time.time())
        self._confirmar("Seguimos.")

    def descanso(self, minutos: int | None = None) -> None:
        with self._lock:
            s = self.sesion
            if not s:
                return
            minutos = int(minutos or s.descanso_min or 10)
            self._iniciar_descanso(s, minutos, time.time())
        self._confirmar(f"Descanso de {minutos} minutos. Párate y toma agua; yo te aviso.")

    def terminar_sesion(self, hablar: bool = True, motivo: str = "") -> None:
        """motivo="tiempo": se cumplió el tiempo total que pediste."""
        with self._lock:
            s = self.sesion
            if not s:
                return
            self.motor.cerrar()
            self.sesion = None
            self.db.cerrar_sesion(s.id)
            self._apagar_camara()
        ahora = time.time()
        resumen = {
            "tema": s.tema, "foco_min": round(s.foco_s / 60), "total_min": round(s.total_s / 60),
            "pct": round(100 * s.foco_s / s.total_s) if s.total_s else 0, "distracciones": s.distracciones,
            "hoy": round(self.minutos_hoy(ahora, forzar=True)), "meta": self.cfg["sesion"]["meta_diaria_min"],
            "motivo": motivo, "pedido": s.total_min,
        }
        log.info("Fin de sesión: %s", resumen)
        if hablar:
            self.coach.fin_sesion(resumen)
        threading.Thread(target=lambda: generar_informe(self.db, self.cfg), daemon=True).start()

    # =========================================================================
    #  Cámara
    # =========================================================================
    def _encender_camara(self) -> None:
        if self.sesion and self.sesion.modo == "estudio":
            self._camara_solo_vista = False   # la cámara ahora es de la sesión, no solo de la ventana
        if not self.camara_activa or (self.camara and self.camara.is_alive()):
            return
        self.camara = MonitorCamara(self.cfg, segundos_inactivo)
        self.camara.start()

    def _apagar_camara(self, forzar: bool = False) -> None:
        if self.camara and self.camara.vista_activa and not forzar:
            self._camara_solo_vista = True   # la ventana de la cámara sigue abierta: que siga viendo
            return
        camara, self.camara = self.camara, None
        if camara:
            camara.detener()
            camara.join(timeout=3)

    def alternar_camara(self) -> bool:
        with self._lock:
            self.camara_activa = not self.camara_activa
            if self.camara_activa and self.sesion and self.sesion.modo == "estudio":
                self._encender_camara()
            elif not self.camara_activa:
                self._apagar_camara()
        return self.camara_activa

    # --- ventana «Lo que ve la cámara» ----------------------------------------------
    def abrir_vista_camara(self) -> None:
        """Enciende la cámara aunque no haya sesión, para ver cómo te está leyendo."""
        with self._lock:
            if not (self.camara and self.camara.is_alive()):
                self.camara = MonitorCamara(self.cfg, segundos_inactivo)
                self.camara.start()
                self._camara_solo_vista = True
            self.camara.vista_activa = True

    def cerrar_vista_camara(self) -> None:
        with self._lock:
            if self.camara:
                self.camara.vista_activa = False
            estudiando = self.sesion is not None and self.sesion.modo == "estudio" and self.camara_activa
            if self._camara_solo_vista and not estudiando:
                self._apagar_camara(forzar=True)
            self._camara_solo_vista = False

    def cuadro_camara(self):
        return self.camara.cuadro() if self.camara else None

    def estado_vista(self) -> dict:
        """Lo que Jarvis entiende ahora mismo, para la ventana de la cámara."""
        cam = self.camara.obs() if self.camara else None
        o = self.motor.ultima_obs
        s = self.sesion
        if s:
            estado, segundos = self.motor.estado, time.time() - self.motor.desde
        elif o is not None:  # sin sesión: lo que pensaría si estuvieras estudiando
            estado = self._motor_vista.crudo(replace(o, camara=cam), self._sesion_vista)
            segundos = 0.0
        else:
            estado, segundos = "sin_sesion", 0.0
        mirada = "La cámara está encendiendo…"
        if self.camara and self.camara.error:
            mirada = f"La cámara falló: {self.camara.error}"
        elif cam and cam.ok:
            mirada = {"pantalla": "Mirando la pantalla", "abajo": "Cabeza abajo: escribiendo o leyendo en la mesa",
                      "lado": "Mirando hacia un lado", "ausente": "No te veo frente al PC"}.get(cam.atencion, "—")
        return {
            "estado": estado, "etiqueta": etiqueta(estado, segundos), "sesion": s is not None,
            "tema": corto(s.tema) if s else "", "mirada": mirada, "camara_ok": bool(cam and cam.ok),
            "celular": bool(cam and cam.celular), "ojos_cerrados": bool(cam and cam.ojos_cerrados),
            "vertical": cam.vertical if cam else 0.0, "horizontal": cam.horizontal if cam else 0.0,
            "ventana": normalizar_titulo(o.ventana.titulo)[:60] if o else "",
            "categoria": o.veredicto.categoria if o else "", "inactivo_s": o.inactivo_s if o else 0.0,
        }

    # =========================================================================
    #  Hablar y escuchar
    # =========================================================================
    def decir(self, texto: str, prioridad: int = 5, valido=None) -> None:
        self.ui.mensaje(texto, "jarvis")
        self.db.mensaje("jarvis", texto)
        en_silencio = time.time() < self.coach.silencio_hasta and prioridad > 2
        if self.voz and not en_silencio:
            self.voz.decir(texto, prioridad, valido)

    def _confirmar(self, texto: str) -> None:
        """«Listo, 10 minutos más»: se dice en voz alta, salvo cuando la orden vino de la IA,
        que ya lo dijo con sus palabras (entonces solo queda escrito en el globo)."""
        if self._callar_confirmaciones:
            self.ui.mensaje(texto, "jarvis")
        else:
            self.decir(texto, prioridad=1)

    def escuchar(self) -> None:
        if self.oido:
            if self.voz:
                self.voz.callar()
            self.oido.escuchar_ahora()

    def texto_usuario(self, texto: str, por_voz: bool = False) -> None:
        texto = texto.strip()
        if not texto:
            return
        self.ui.mensaje(texto, "tu")
        self.db.mensaje("usuario", texto)
        intento = detectar(texto, self.cfg["temas"])
        if intento and self.ejecutar(intento, texto):
            return
        threading.Thread(target=self._conversar, args=(texto,), name="conversacion", daemon=True).start()

    def ejecutar(self, intento: dict, texto: str = "") -> bool:
        """Lleva a cabo una orden. Devuelve False si mejor la responde la IA."""
        tipo = intento.get("tipo")
        s = self.sesion
        plan = {k: intento.get(k) for k in ("total", "bloque", "descanso") if intento.get(k) is not None}
        if tipo in ("iniciar", "ajustar"):
            tema = intento.get("tema") or (buscar_tema(texto, self.cfg["temas"]) if texto else None)
            objetivo = intento.get("objetivo") or (texto.split(":", 1)[1].strip() if ":" in texto else "")
            if s and (not tema or tema == s.tema):
                # Ya hay sesión: «sesión de 30 minutos» o «descansos de 5» la ajustan en vez de empezar otra.
                if plan:
                    self.ajustar_sesion(**plan)
                else:
                    self._confirmar(f"Ya estamos en sesión: {self.describir_plan()}.")
                if objetivo:
                    self.poner_objetivo(objetivo)
            else:
                self.iniciar_sesion(tema, objetivo, plan.get("bloque"), plan.get("descanso"), total=plan.get("total"))
        elif tipo == "extender":
            self.extender(intento.get("minutos") or 10)
        elif tipo == "acortar" and s:
            self.acortar(intento.get("minutos") or 10)
        elif tipo == "terminar_en" and s:
            self.terminar_en(intento.get("minutos") or 5)
        elif tipo == "restante":
            self.decir(self.texto_restante(), prioridad=1)
        elif tipo == "cambiar_tema":
            if s:
                self.cambiar_tema(intento["tema"])
            else:
                self.iniciar_sesion(intento["tema"])
        elif tipo == "objetivo":
            self.poner_objetivo(intento["texto"])
        elif tipo == "pausa" and s:
            self.pausar()
        elif tipo == "reanudar":
            if s and s.modo != "estudio":
                self.reanudar()
            elif not s and (tema := buscar_tema(texto, self.cfg["temas"])):
                self.iniciar_sesion(tema)
            else:
                return False
        elif tipo == "descanso" and s:
            self.descanso(intento.get("minutos"))
        elif tipo == "terminar" and s:
            self.terminar_sesion()
        elif tipo == "marcar":
            v = self.ultima_ventana_util
            if not v.titulo:
                return False
            self.clasif.marcar(v, intento["categoria"])
            que = "cuenta como estudio" if intento["categoria"] == "estudio" else "es distracción"
            self.decir(f"Anotado: «{normalizar_titulo(v.titulo)[:50]}» {que}.", prioridad=1)
        elif tipo == "no_celular":
            if self.camara:
                self.camara.detectar_celular = False
            self.decir("Entendido: en esta sesión no vigilo el celular.", prioridad=1)
        elif tipo == "silencio":
            self.coach.silencio_hasta = time.time() + 30 * 60
            if self.voz:
                self.voz.callar()
            self.decir("Me callo media hora. Sigo anotando.", prioridad=1)
        elif tipo == "hablar":
            self.coach.silencio_hasta = 0.0
            self.decir("De vuelta.", prioridad=1)
        elif tipo == "estado":
            self.decir(self.texto_estado(), prioridad=1)
        elif tipo == "que_ves":
            self.decir(self.describir_pantalla(intento.get("listar", False)), prioridad=1)
        elif tipo == "musica":
            from .musica import controlar
            try:
                self.decir(controlar(intento["accion"]), prioridad=1)
            except Exception:  # noqa: BLE001
                log.exception("Música")
                self.decir("No pude manejar la música.", prioridad=1)
        else:
            return False
        return True

    def _conversar(self, texto: str) -> None:
        """Pregunta libre. En texto normal (pedir JSON hacía fallar a muchos modelos gratis) y
        primero con el modo rápido del router: en voz importa más la rapidez."""
        temas = ", ".join(t["nombre"] for t in self.cfg["temas"])
        sistema = (
            self.coach.persona()
            + "\nTambién resuelves dudas de la carrera con explicaciones claras y cortas (máximo 4 frases) y "
              "manejas sus sesiones de estudio: puedes crearlas, cambiarlas, alargarlas, acortarlas, pausarlas o "
              "terminarlas con la duración, los bloques y los descansos que pida. Nunca digas que no puedes.\n"
              "Para hacerlo, responde normal y agrega AL FINAL una sola etiqueta (no la leas en voz alta):\n"
              '- empezar: [[sesion tema="..." total=MINUTOS bloque=MINUTOS descanso=MINUTOS objetivo="..."]] '
              "(pon solo lo que pidió; total=0 es sin límite; si no nombra un tema, NO pongas tema: nunca lo "
              "inventes; si pide pausas o descansos, pon descanso y también bloque, p. ej. bloque=20 si no dice "
              "cada cuánto)\n"
              "- cambiar la sesión en marcha: [[ajustar total=MINUTOS bloque=MINUTOS descanso=MINUTOS]]\n"
              "- [[extender MINUTOS]], [[acortar MINUTOS]], [[descanso MINUTOS]], [[pausa]], [[seguir]], [[terminar]]\n"
            + f"Temas configurados: {temas}.\n"
            + "Lo que sabes ahora mismo: " + self.texto_situacion())
        mensajes = [{"role": "system", "content": sistema}, *self.historial, {"role": "user", "content": texto}]
        respuesta = ""
        self.ui.oido("procesando")
        try:
            for modelo, espera in dict.fromkeys([(self.llm.modelo_rapido, 15), (self.llm.modelo, 20)]):
                try:
                    respuesta = self.llm.chat(mensajes, modelo=modelo, max_tokens=600, temperatura=0.7,
                                              timeout=espera)
                    if respuesta:
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("Conversación con %s falló: %s", modelo, e)
        finally:
            self.ui.oido("inactivo")
        if not respuesta:
            self.decir("Se me cayó la conexión con la IA. Repíteme en un momento.", prioridad=1)
            return
        self.historial.append({"role": "user", "content": texto})
        self.historial.append({"role": "assistant", "content": respuesta})
        respuesta, acciones = acciones_de_la_ia(respuesta)
        if respuesta:
            self.decir(respuesta, prioridad=1)
        self._callar_confirmaciones = True
        try:
            for accion in acciones:
                log.info("La IA pidió: %s", accion)
                if "minutos" in accion and accion["tipo"] in ("iniciar", "ajustar") and "total" not in accion:
                    accion["total"] = accion.pop("minutos")
                self.ejecutar(accion)
        finally:
            self._callar_confirmaciones = False

    # --- lo que Jarvis ve, contado sin IA ------------------------------------------
    def _ventana_actual(self) -> Ventana:
        o = self.motor.ultima_obs
        if o is None or o.ventana.pid == os.getpid() or not o.ventana.titulo:
            return self.ultima_ventana_util
        return o.ventana

    def describir_pantalla(self, listar: bool = False) -> str:
        v = self._ventana_actual()
        programa = PROGRAMAS.get(v.proceso, v.proceso.removesuffix(".exe") or "algo")
        archivos = self.clasif.archivos_de_estudio(v)
        if archivos:
            frases = [f"Tienes al frente {programa} con «{Path(archivos[0]).stem}», que es material de estudio."]
        elif v.titulo:
            veredicto = self.clasif.clasificar(v, sesion_activa=False)
            como = {"estudio": "lo cuento como estudio", "herramienta": "lo cuento como herramienta",
                    "distraccion": "eso es distracción", "mensajeria": "es mensajería", "neutral": "no lo juzgo",
                    }.get(veredicto.categoria, "aún no sé si es del tema")
            titulo = normalizar_titulo(v.titulo)
            generico = titulo.lower() in (programa.lower(), programa.lower().removeprefix("el "))
            que = programa if generico else f"{programa}: «{titulo[:70]}»"
            frases = [f"Tienes al frente {que}; {como}."]
        else:
            frases = ["No veo ninguna ventana al frente."]
        if self.camara and self.camara.obs().ok:
            frases.append({"pantalla": "Con la cámara te veo mirando la pantalla.",
                           "abajo": "Con la cámara te veo con la cabeza abajo, como escribiendo o leyendo en la mesa.",
                           "lado": "Con la cámara te veo mirando hacia un lado.",
                           "ausente": "La cámara no te ve frente al PC."}.get(self.camara.obs().atencion, ""))
        else:
            frases.append("La cámara está apagada: solo se enciende durante una sesión.")
        if listar:
            otras = [normalizar_titulo(t)[:60] for t, p in ventanas_visibles() if t != v.titulo][:6]
            if otras:
                frases.append("También tienes abiertas: " + "; ".join(otras) + ".")
        return " ".join(f for f in frases if f)

    def texto_situacion(self) -> str:
        """Resumen en prosa para la IA: qué hora es, cómo va la sesión, qué hay en pantalla."""
        c = self.contexto()
        partes = [f"Son las {c['hora']}.", f"Hoy lleva {c['hoy_min']} de {c['meta_min']} minutos de foco."]
        if self.sesion:
            partes.append(f"Está en una sesión de {c['tema']} (objetivo: {c['objetivo']}), bloque de "
                          f"{c['bloque_min']} de {c['plan']} min, {c['foco_pct']} % de foco, "
                          f"{c['distracciones']} distracciones. Estado: {c['estado']}.")
        else:
            partes.append("No tiene una sesión abierta.")
        partes.append(self.describir_pantalla())
        if c["examen"]:
            partes.append(f"Próximo examen: {c['examen']}.")
        return " ".join(partes)

    # =========================================================================
    #  Números para la interfaz, el coach y la IA
    # =========================================================================
    def minutos_hoy(self, ahora: float | None = None, forzar: bool = False) -> float:
        ahora = ahora or time.time()
        calculado, guardado = self._foco_hoy
        if forzar or ahora - calculado > 30:
            guardado = minutos_foco(self.db, rango_dia(dt.date.today())[0], ahora)
            self._foco_hoy = (ahora, guardado)
        return guardado + self.motor.segundos_sin_guardar(ahora) / 60

    def _estudio_reciente(self, ahora: float) -> bool:
        filas = self.db.consultar("SELECT MAX(COALESCE(fin, ?)) FROM sesiones", (ahora,))
        return bool(filas and filas[0][0] and ahora - filas[0][0] < 90 * 60)

    def contexto(self) -> dict:
        ahora = time.time()
        meta = self.cfg["sesion"]["meta_diaria_min"]
        c = {"hora": time.strftime("%H:%M"), "hoy_min": round(self.minutos_hoy(ahora)), "meta_min": meta,
             "estado": NOMBRES.get(self.motor.estado, self.motor.estado),
             "racha_min": round(self.motor.racha_min(ahora))}
        s = self.sesion
        c["examen"] = self.coach.examen_texto(s.tema if s else "")
        if s:
            c.update(tema=s.tema, objetivo=s.objetivo or "(sin objetivo)", plan=s.plan_min,
                     descanso=s.descanso_min, modo=s.modo, bloque_min=round(s.bloque_s(ahora) / 60),
                     foco_pct=round(100 * s.foco_s / s.total_s) if s.total_s else 100,
                     distracciones=s.distracciones, plan_texto=self.describir_plan(s),
                     total=s.total_min, estudiado_min=round(s.total_s / 60))
        o = self.motor.ultima_obs
        if o:
            c["ventana"] = normalizar_titulo(o.ventana.titulo)[:80]
        return c

    def texto_estado(self) -> str:
        c = self.contexto()
        texto = f"Hoy llevas {c['hoy_min']} minutos de foco de {c['meta_min']}."
        if self.sesion:
            texto += (f" En esta sesión vas con {c['foco_pct']} por ciento de foco y {c['distracciones']} "
                      f"distracciones; racha actual de {c['racha_min']} minutos. {self.texto_restante()}")
        if c["examen"]:
            texto += f" Próximo: {c['examen']}."
        return texto

    def resumen(self, o: Observacion) -> dict:
        s = self.sesion
        ahora = o.ts
        d = {"estado": self.motor.estado, "nombre_estado": NOMBRES.get(self.motor.estado, ""),
             "etiqueta": etiqueta(self.motor.estado, ahora - self.motor.desde) if s else "",
             "hoy_min": self.minutos_hoy(ahora), "meta_min": self.cfg["sesion"]["meta_diaria_min"],
             "racha_min": self.motor.racha_min(ahora), "ventana": normalizar_titulo(o.ventana.titulo)[:70],
             "categoria": o.veredicto.categoria, "razon": o.veredicto.razon,
             "camara": "apagada" if not self.camara_activa else (
                 o.camara.atencion if o.camara and o.camara.ok else "encendiendo" if self.camara else "en espera"),
             "camara_error": self.camara.error if self.camara else "",
             "sesion": bool(s), "tema": "", "objetivo": "", "modo": "", "bloque_s": 0.0,
             "bloque_total_s": 0.0, "foco_pct": 0, "distracciones": 0, "total_min": 0, "estudiado_s": 0.0,
             "plan_texto": ""}
        if s:
            d.update(tema=s.tema, objetivo=s.objetivo, modo=s.modo,
                     foco_pct=round(100 * s.foco_s / s.total_s) if s.total_s else 100,
                     distracciones=s.distracciones, total_min=s.total_min, estudiado_s=s.total_s,
                     plan_texto=self.describir_plan(s))
            if s.modo == "descanso":
                total = s.descanso_min * 60 or 1
                d.update(bloque_total_s=total, bloque_s=total - max(0.0, s.fin_descanso - ahora))
            else:
                d.update(bloque_total_s=s.largo_bloque_s(), bloque_s=s.bloque_s(ahora))
        return d
