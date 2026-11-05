"""El coach: cuándo hablarte y qué decirte (regañar, motivar, recordar).

Los mensajes los escribe la IA con el contexto del momento (tema, minutos,
examen más cercano). Si la IA no responde a tiempo, usa frases fijas.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .db import ESTADOS_FOCO
from .focus import ESTADOS_MALOS

log = logging.getLogger("jarvis.coach")

TONOS = {
    "suave": "amable y comprensivo; motivas sin presionar",
    "firme": "directo y exigente pero buena gente, con el humor seco del Jarvis de Iron Man",
    "sargento": "duro y sarcástico, como un sargento que cree en él; regañas sin insultar",
}
NIVELES = {1: "primer aviso, ligero", 2: "segundo aviso, más firme", 3: "tercer aviso, contundente"}

PLANTILLAS = {
    ("distraido", 1): ["{nombre}, eso no es {tema}. De vuelta.", "Ojo, {nombre}: eso no estaba en el plan."],
    ("distraido", 2): ["Sigues ahí, {nombre}. {examen_corto}", "Ya van {min} minutos fuera. Cierra eso y volvemos."],
    ("distraido", 3): ["{nombre}, esto no te va a pasar el examen. Cierra eso ya.",
                       "Llevas {min} minutos perdidos. {examen_corto} A estudiar."],
    ("celular", 1): ["Suelta el celular, {nombre}.", "El celular puede esperar. Volvamos a {tema}."],
    ("celular", 2): ["Sigues con el celular. Déjalo boca abajo y lejos."],
    ("celular", 3): ["{nombre}, el celular otra vez. Guárdalo en otro cuarto."],
    ("somnoliento", 1): ["Se te cierran los ojos. Párate, toma agua y vuelve en dos minutos."],
    ("somnoliento", 2): ["Sigues con sueño. Mejor un descanso de diez minutos de verdad."],
    ("somnoliento", 3): ["Así no rinde. Descanso ya, y si es tarde, a dormir."],
    ("regreso", 1): ["Eso, de vuelta.", "Bien. Sigamos con {tema}."],
    ("bienvenida", 1): ["Bienvenido de vuelta. Seguimos con {tema}."],
    ("pensando", 1): ["¿Te atascaste? Pregúntame o pregúntale a Gemini y seguimos."],
    ("racha", 1): ["Llevas {racha} minutos seguidos. Así es."],
    ("inicio", 1): ["Listo: {tema}, {plan_texto}. {examen_corto}"],
    ("fin_tiempo", 1): ["Listo, se cumplieron tus {pedido} minutos: {foco_min} de foco. "
                        "Si quieres seguir, dime «Jarvis, otros 20 minutos»."],
    ("fin_bloque", 1): ["Llevas {plan} minutos. Toma {descanso} de descanso: párate y toma agua. Yo te aviso."],
    ("fin_descanso", 1): ["Se acabó el descanso. Volvemos con {tema}."],
    ("recordatorio", 1): ["{nombre}, ya son las {hora}. ¿Arrancamos? {examen_corto}"],
    ("fin_sesion", 1): ["Sesión terminada: {foco_min} minutos de foco, {pct} por ciento. Hoy llevas {hoy} de {meta}."],
}


def corto(nombre: str) -> str:
    """'Matemáticas – cálculo y álgebra' → 'Matemáticas' (para decirlo en voz alta)."""
    return re.split(r"\s[–—-]\s|\s\(", nombre, maxsplit=1)[0].strip()


class _Datos(dict):
    def __missing__(self, clave):  # una plantilla nunca falla por un dato que falte
        return ""


class Coach:
    def __init__(self, cfg: dict, llm, decir, contexto):
        self.llm = llm
        self.decir = decir            # decir(texto, prioridad, valido=None)
        self.contexto = contexto      # contexto() -> dict
        self.configurar(cfg)
        self.silencio_hasta = 0.0
        self._ultimo_aviso = 0.0
        self._episodio: dict | None = None
        self._racha_avisada = 0
        self._pensando_avisado = False
        self._recordados: set = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coach")

    def configurar(self, cfg: dict) -> None:
        self.cfg = cfg
        self.nombre = cfg["usuario"]["nombre"]
        self.descripcion = cfg["usuario"].get("descripcion", "estudiante")
        self.habla = cfg["usuario"].get("habla", "español neutro")
        self.tono = cfg["usuario"]["tono"] if cfg["usuario"]["tono"] in TONOS else "firme"
        self.min_entre = cfg["foco"]["min_entre_avisos_s"]
        self.escalar_cada = cfg["foco"]["escalar_cada_s"]

    # --- textos -----------------------------------------------------------------
    def persona(self) -> str:
        return (
            f"Eres Jarvis, el asistente de estudio de {self.nombre}, {self.descripcion}. "
            f"Le hablas en {self.habla}, natural y cercano. Tu tono: {TONOS[self.tono]}. "
            "Todo lo que escribes se lee en voz alta: frases cortas, sin emojis, sin markdown, sin listas.")

    def proximo_examen(self, tema: str = "", hoy: dt.date | None = None) -> tuple[str, int] | None:
        """El examen más cercano; si hay uno del mismo profesor que el tema, ese."""
        hoy = hoy or dt.date.today()
        futuros = []
        for nombre, fecha in self.cfg.get("examenes", {}).items():
            if isinstance(fecha, str):
                fecha = dt.date.fromisoformat(fecha)
            if isinstance(fecha, dt.datetime):
                fecha = fecha.date()
            if fecha >= hoy:
                futuros.append(((fecha - hoy).days, nombre))
        if not futuros:
            return None
        del_tema = [f for f in futuros if corto(f[1]).lower() in tema.lower()] if tema else []
        dias, nombre = min(del_tema or futuros)
        return nombre, dias

    def examen_texto(self, tema: str = "") -> str:
        prox = self.proximo_examen(tema)
        if not prox:
            return ""
        nombre, dias = prox
        cuando = "hoy" if dias == 0 else "mañana" if dias == 1 else f"en {dias} días"
        return f"{nombre}: {cuando}"

    # --- eventos del motor de foco ------------------------------------------------
    def procesar(self, eventos: list[dict], motor, sesion) -> None:
        for e in eventos:
            if e["tipo"] != "cambio":
                continue
            a, de, ahora = e["a"], e["de"], motor.desde
            if a in ESTADOS_MALOS:
                self._episodio = {"estado": a, "inicio": ahora - motor.gracia.get(a, 0), "nivel": 1,
                                  "ultimo": ahora, "titulo": e["titulo"], "razon": e["razon"]}
                self._avisar(a, 1, motor, e)
                continue
            if de in ESTADOS_MALOS and a in ESTADOS_FOCO:
                if e["duracion_previa"] >= 25 and random.random() < 0.6:
                    self._mensaje("regreso", 1, {"min": round(e["duracion_previa"] / 60, 1)},
                                  valido=lambda: motor.estado in ESTADOS_FOCO, prioridad=6)
            elif de == "ausente" and a in ESTADOS_FOCO and e["duracion_previa"] > 120:
                self._mensaje("bienvenida", 1, {"min": round(e["duracion_previa"] / 60)},
                              valido=lambda: motor.estado in ESTADOS_FOCO, prioridad=6)
            self._episodio = None

    def tick(self, motor, sesion, ahora: float) -> None:
        ep = self._episodio
        if ep and motor.estado == ep["estado"]:
            espera = self.escalar_cada if ep["nivel"] < 3 else 2 * self.escalar_cada
            if ahora - ep["ultimo"] >= espera:
                ep["nivel"] = min(3, ep["nivel"] + 1)
                ep["ultimo"] = ahora
                self._avisar(ep["estado"], ep["nivel"], motor, ep)
        if sesion is None or sesion.modo != "estudio":
            return
        racha = motor.racha_min(ahora)
        if motor.racha_desde is None:
            self._racha_avisada = 0
        for hito in (25, 75, 120):
            if racha >= hito > self._racha_avisada:
                self._racha_avisada = hito
                if sesion.bloque_s(ahora) < sesion.plan_min * 60 - 120:  # no pisar el aviso de descanso
                    self._mensaje("racha", 1, {"racha": hito}, prioridad=7)
        if motor.estado == "pensando" and ahora - motor.desde > 240 and not self._pensando_avisado:
            self._pensando_avisado = True
            self._mensaje("pensando", 1, {"min": round((ahora - motor.desde) / 60)},
                          valido=lambda: motor.estado == "pensando")
        elif motor.estado != "pensando":
            self._pensando_avisado = False

    def _avisar(self, estado: str, nivel: int, motor, info: dict) -> None:
        minutos = round((time.time() - (self._episodio or {}).get("inicio", time.time())) / 60, 1)
        self._mensaje(estado, nivel, {"titulo": info.get("titulo", ""), "razon": info.get("razon", ""),
                                      "min": minutos},
                      valido=lambda: motor.estado == estado)

    # --- momentos de la sesión ------------------------------------------------------
    def inicio_sesion(self) -> None:
        self._racha_avisada = 0
        self._mensaje("inicio", 1, {}, prioridad=2, forzar=True)

    def fin_bloque(self) -> None:
        self._mensaje("fin_bloque", 1, {}, prioridad=2, forzar=True)

    def fin_descanso(self) -> None:
        self._mensaje("fin_descanso", 1, {}, prioridad=2, forzar=True)

    def fin_sesion(self, resumen: dict) -> None:
        self._episodio = None
        por_tiempo = resumen.get("motivo") == "tiempo"
        if resumen.get("total_min", 0) < 3 and not por_tiempo:
            self.decir("Sesión cerrada. Fue muy corta para hacer balance.", 2)
            return
        self._mensaje("fin_tiempo" if por_tiempo else "fin_sesion", 1, resumen, prioridad=2, forzar=True)

    def recordatorio(self, ahora: float, inactivo_s: float, hoy_min: float, hubo_sesion: bool,
                     ventana: str) -> None:
        r = self.cfg["recordatorios"]
        if not r["activar"] or inactivo_s > 120 or hubo_sesion:
            return
        if hoy_min >= self.cfg["sesion"]["meta_diaria_min"]:
            return
        hoy = dt.date.today()
        for hora in r["horas"]:
            hh, mm = (int(x) for x in hora.split(":"))
            t = dt.datetime.combine(hoy, dt.time(hh, mm)).timestamp()
            for intento, desfase in enumerate((0, 20 * 60)):
                clave = (hoy, hora, intento)
                if t + desfase <= ahora < t + desfase + 15 * 60 and clave not in self._recordados:
                    self._recordados.add(clave)
                    self._mensaje("recordatorio", 1, {"hora": hora, "ventana": ventana}, prioridad=3, forzar=True)
                    return

    # --- generar y decir ---------------------------------------------------------------
    def _mensaje(self, tipo: str, nivel: int, datos: dict, valido=None, prioridad: int = 5,
                 forzar: bool = False) -> None:
        ahora = time.time()
        if not forzar and ahora - self._ultimo_aviso < self.min_entre:
            return
        self._ultimo_aviso = ahora
        self._pool.submit(self._generar_y_decir, tipo, nivel, datos, valido, prioridad)

    def _generar_y_decir(self, tipo, nivel, datos, valido, prioridad) -> None:
        ctx = _Datos(self.contexto())
        ctx.update(datos)
        ctx["nombre"] = self.nombre
        ctx.setdefault("tema", "lo tuyo")
        prox = self.proximo_examen(ctx["tema"])
        ctx["examen_corto"] = ""
        if prox and prox[1] <= 21:
            cuando = "hoy es" if prox[1] == 0 else "mañana es" if prox[1] == 1 else f"faltan {prox[1]} días para"
            ctx["examen_corto"] = f"Ojo: {cuando} {prox[0]}."
        texto = ""
        try:
            texto = self._con_ia(tipo, nivel, ctx)
        except Exception as e:  # noqa: BLE001 - sin IA también hay que hablar
            log.info("Mensaje sin IA (%s): %s", tipo, e)
        if not texto:
            opciones = PLANTILLAS.get((tipo, nivel)) or PLANTILLAS.get((tipo, 1)) or [""]
            plantilla = _Datos(ctx, tema=corto(ctx["tema"]))
            if prox and prox[1] <= 21:
                plantilla["examen_corto"] = f"Faltan {prox[1]} días para lo de {corto(prox[0])}."
            texto = random.choice(opciones).format_map(plantilla).replace("  ", " ").strip()
        if texto and (valido is None or valido()):
            self.decir(texto, prioridad, valido)

    def _con_ia(self, tipo: str, nivel: int, c: dict) -> str:
        situaciones = {
            "distraido": "Durante su sesión de {tema} (objetivo: {objetivo}) tiene abierto «{titulo}» ({razon}). "
                         "Lleva {min} minutos distraído. Es el " + NIVELES.get(nivel, "") + ".",
            "celular": "Durante su sesión de {tema} lleva {min} minutos con el celular en la mano. Es el "
                       + NIVELES.get(nivel, "") + ".",
            "somnoliento": "Se le están cerrando los ojos frente a la pantalla. Es el " + NIVELES.get(nivel, "") + ".",
            "regreso": "Acaba de volver a estudiar {tema} después de distraerse. Reconócelo en una frase corta.",
            "bienvenida": "Volvió a su puesto después de {min} minutos fuera. Recíbelo y que siga con {tema}.",
            "pensando": "Lleva {min} minutos quieto mirando a otro lado sin tocar el PC. Pregúntale si se "
                        "atascó con algo de {tema} y ofrécele ayuda.",
            "racha": "Lleva {racha} minutos seguidos concentrado en {tema}. Reconócelo en una frase.",
            "inicio": "Empieza una sesión de {tema}: {plan_texto}; objetivo: {objetivo}. Arráncalo con energía y "
                      "di el tiempo que va a estudiar.",
            "fin_tiempo": "Se cumplió la sesión de {pedido} minutos que pidió para {tema}: {foco_min} minutos de foco "
                          "({pct} por ciento) y {distracciones} distracciones. Haz un balance corto y dile que, si "
                          "quiere seguir, diga «Jarvis, otros 20 minutos».",
            "fin_bloque": "Completó un bloque de {plan} minutos con {foco_pct} por ciento de foco. Dile que tome "
                          "{descanso} minutos de descanso de verdad: pararse, agua, lejos de la pantalla.",
            "fin_descanso": "Terminó su descanso. Que vuelva a {tema} ya.",
            "recordatorio": "Son las {hora} y hoy lleva {hoy_min} de {meta_min} minutos de estudio. Ahora está en: "
                            "{ventana}. Anímalo a empezar una sesión.",
            "fin_sesion": "Terminó la sesión de {tema}: {foco_min} minutos de foco en {total_min} minutos "
                          "({pct} por ciento) y {distracciones} distracciones. Hoy lleva {hoy} de {meta} minutos. "
                          "Haz un balance corto y honesto.",
        }
        if tipo not in situaciones:
            return ""
        situacion = situaciones[tipo].format_map(c)
        if c.get("examen_corto"):
            situacion += f" Dato útil si viene al caso: {c['examen_corto']}"
        tranquilo = tipo in ("inicio", "fin_bloque", "fin_sesion", "recordatorio")
        texto = self.llm.chat(
            [{"role": "system", "content": self.persona()},
             {"role": "user", "content": f"{situacion}\nEscribe lo que le dices ahora: máximo {2 if tranquilo else 1} "
                                         "frase(s) corta(s), solo el texto. Al nombrar el tema usa solo lo esencial "
                                         "(p. ej. el apellido del profesor), no el título completo."}],
            rapido=True, max_tokens=300, temperatura=0.9, timeout=15 if tranquilo else 8)
        return texto.strip().strip('"').strip()
