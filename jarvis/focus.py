"""Estados de foco: junta pantalla, cámara y teclado/ratón y decide cómo vas.

Un estado nuevo solo cuenta si dura su "gracia" (p. ej. 12 s en Instagram),
para que mirar algo de reojo no dispare un regaño.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .camera import ObsCamara
from .classifier import Veredicto, normalizar_titulo
from .db import ESTADOS_FOCO
from .screen import REPRODUCTORES, Ventana

ESTADOS_MALOS = ("distraido", "celular", "somnoliento")

NOMBRES = {
    "sin_sesion": "Sin sesión", "enfocado": "Enfocado", "mesa": "Escribiendo / leyendo",
    "pensando": "Pensando", "mensajeria": "Mensajes", "distraido": "Distraído",
    "celular": "Con el celular", "ausente": "Ausente", "somnoliento": "Con sueño",
    "descanso": "Descanso", "pausa": "En pausa",
}
# Cómo se ve el estado de un vistazo (etiqueta del orbe y de la vista de la cámara).
ETIQUETAS = {
    "sin_sesion": "Sin sesión", "enfocado": "Estudiando", "mesa": "Estudiando (mesa)",
    "pensando": "Pensando", "mensajeria": "En el chat", "distraido": "Procrastinando",
    "celular": "Con el celular", "ausente": "Ausente", "somnoliento": "Con sueño",
    "descanso": "Descanso", "pausa": "En pausa",
}


def etiqueta(estado: str, segundos_en_estado: float = 0.0) -> str:
    """«Pensando» un rato está bien; mirando a otro lado más de 2 minutos ya es desconcentrarse."""
    if estado == "pensando" and segundos_en_estado > 120:
        return "Desconcentrado"
    return ETIQUETAS.get(estado, estado)


@dataclass
class Sesion:
    id: int
    tema: str
    objetivo: str
    plan_min: int                    # largo de cada bloque de estudio
    descanso_min: int
    papel: bool = True
    inicio: float = field(default_factory=time.time)
    total_min: int = 0               # tiempo total de estudio pedido; 0 = sin límite
    modo: str = "estudio"            # estudio | descanso | pausa
    inicio_bloque: float = field(default_factory=time.time)
    fin_descanso: float = 0.0
    extra_bloque_s: float = 0.0      # «dame 10 más» sin tiempo total: alarga solo este bloque
    aviso_bloque: bool = False
    aviso_total: bool = False
    aviso_cinco: bool = False
    foco_s: float = 0.0
    total_s: float = 0.0             # tiempo en modo estudio (sin descansos ni pausas)
    distracciones: int = 0
    bloques: int = 0

    def bloque_s(self, ahora: float | None = None) -> float:
        return (ahora or time.time()) - self.inicio_bloque

    def largo_bloque_s(self) -> float:
        return self.plan_min * 60 + self.extra_bloque_s

    def restante_total_s(self) -> float | None:
        return None if not self.total_min else self.total_min * 60 - self.total_s


@dataclass
class Observacion:
    ts: float
    ventana: Ventana
    veredicto: Veredicto
    inactivo_s: float
    camara: ObsCamara | None = None


class MotorFoco:
    def __init__(self, cfg: dict, db):
        f = cfg["foco"]
        self.db = db
        self.gracia = {
            "enfocado": 3, "mesa": 4, "pensando": 20, "mensajeria": 3, "ausente": 20,
            "distraido": f["gracia_distraccion_s"], "celular": f["gracia_celular_s"],
            "somnoliento": f["gracia_somnolencia_s"],
        }
        self.gracia_ausencia = f["gracia_ausencia_s"]
        self.gracia_mensajeria = f["gracia_mensajeria_s"]
        self.estado = "sin_sesion"
        self.desde = time.time()
        self.racha_desde: float | None = None
        self.sesion_id: int | None = None
        self.ultima_obs: Observacion | None = None
        self._candidato: str | None = None
        self._candidato_desde = 0.0
        self._mensajeria_desde: float | None = None
        self._ultimo_ts: float | None = None
        self._seg_inicio = time.time()
        self._seg_info = ("", "", "")

    # ------------------------------------------------------------------------
    def crudo(self, o: Observacion, s: Sesion | None) -> str:
        """Estado instantáneo, sin gracia."""
        if s is None:
            return "sin_sesion"
        if s.modo in ("pausa", "descanso"):
            return s.modo
        categoria = o.veredicto.categoria
        cam = o.camara if o.camara and o.camara.ok else None
        usando_pc = o.inactivo_s < 3
        if cam and cam.celular and not usando_pc:
            return "celular"
        if categoria == "distraccion":
            return "distraido"
        if categoria == "mensajeria":
            self._mensajeria_desde = self._mensajeria_desde or o.ts
            return "distraido" if o.ts - self._mensajeria_desde > self.gracia_mensajeria else "mensajeria"
        self._mensajeria_desde = None
        if cam:
            if not cam.presente and not usando_pc:
                return "ausente"
            if not usando_pc:
                if cam.atencion == "abajo":
                    return "mesa" if s.papel else "pensando"
                if cam.ojos_cerrados:
                    return "somnoliento"
                if cam.atencion == "lado":
                    return "pensando"
        elif o.inactivo_s > self.gracia_ausencia:
            # Sin cámara solo se sabe por el teclado; ver un vídeo del curso no es ausencia.
            if not (categoria == "estudio" and o.ventana.proceso in REPRODUCTORES):
                return "ausente"
        return "enfocado"

    def actualizar(self, o: Observacion, s: Sesion | None) -> list[dict]:
        eventos: list[dict] = []
        ahora = o.ts
        dt = 0.0 if self._ultimo_ts is None else ahora - self._ultimo_ts
        if dt > 30:  # el PC estuvo suspendido: ese hueco no cuenta
            self._cerrar_segmento(self._ultimo_ts or ahora)
            self._seg_inicio = ahora
            dt = 0.0
        self._ultimo_ts = ahora

        crudo = self.crudo(o, s)
        if crudo != self.estado:
            if crudo != self._candidato:
                self._candidato, self._candidato_desde = crudo, ahora
            # Al entrar o salir de una sesión, pausa o descanso no hay gracia que valga.
            inmediato = crudo in ("sin_sesion", "pausa", "descanso") or self.estado in ("sin_sesion", "pausa", "descanso")
            if inmediato or ahora - self._candidato_desde >= self.gracia.get(crudo, 5):
                eventos.append(self._cambiar(crudo, ahora, o, s))
        else:
            self._candidato = None

        if s and s.modo == "estudio":
            s.total_s += dt
            if self.estado in ESTADOS_FOCO:
                s.foco_s += dt
        self._seg_info = (o.ventana.proceso, normalizar_titulo(o.ventana.titulo), o.veredicto.categoria)
        if ahora - self._seg_inicio >= 60:  # se guarda cada minuto por si se cierra de golpe
            self._cerrar_segmento(ahora)
            self._seg_inicio = ahora
        self.ultima_obs = o
        return eventos

    def racha_min(self, ahora: float | None = None) -> float:
        return 0.0 if self.racha_desde is None else ((ahora or time.time()) - self.racha_desde) / 60

    def segundos_sin_guardar(self, ahora: float | None = None) -> float:
        """Foco del tramo actual que aún no está en la base de datos."""
        if self.estado not in ESTADOS_FOCO:
            return 0.0
        return max(0.0, (ahora or time.time()) - self._seg_inicio)

    def cerrar(self, ahora: float | None = None) -> None:
        self._cerrar_segmento(ahora or time.time())
        self._seg_inicio = ahora or time.time()

    # ------------------------------------------------------------------------
    def _cambiar(self, nuevo: str, ahora: float, o: Observacion, s: Sesion | None) -> dict:
        anterior, duracion = self.estado, ahora - self.desde
        self._cerrar_segmento(ahora)
        self._seg_inicio = ahora
        self.estado, self.desde, self._candidato = nuevo, ahora, None
        if nuevo in ESTADOS_FOCO:
            if self.racha_desde is None:
                self.racha_desde = ahora
        elif nuevo != "mensajeria":
            self.racha_desde = None
        if s and nuevo in ESTADOS_MALOS:
            s.distracciones += 1
        return {"tipo": "cambio", "de": anterior, "a": nuevo, "duracion_previa": duracion,
                "titulo": normalizar_titulo(o.ventana.titulo), "proceso": o.ventana.proceso,
                "categoria": o.veredicto.categoria, "razon": o.veredicto.razon}

    def _cerrar_segmento(self, fin: float) -> None:
        if self.estado == "sin_sesion":
            return
        app, titulo, categoria = self._seg_info
        self.db.guardar_segmento(self.sesion_id, self._seg_inicio, fin, self.estado, app, titulo, categoria)
