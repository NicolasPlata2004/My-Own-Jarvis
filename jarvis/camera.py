"""Cámara: si estás, hacia dónde miras, si se te cierran los ojos y si tienes el celular.

Cada imagen se analiza en memoria con MediaPipe y se descarta: nunca se guarda
ni sale del PC. Solo queda un resumen (p. ej. "mirando abajo").
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, replace

log = logging.getLogger("jarvis.camara")

# Puntos de la malla facial de MediaPipe.
NARIZ, OJO_IZQ, OJO_DER, MENTON = 1, 33, 263, 152


@dataclass
class ObsCamara:
    ok: bool = False             # la cámara está entregando imágenes
    presente: bool = False       # hay alguien frente al PC (cara o cuerpo)
    rostro: bool = False
    atencion: str = "desconocida"  # pantalla | abajo | lado | ausente
    ojos_cerrados: bool = False
    celular: bool = False
    vertical: float = 0.0        # desviación respecto a tu postura normal (+ = cabeza abajo)
    horizontal: float = 0.0      # desviación lateral
    ts: float = 0.0


class MonitorCamara(threading.Thread):
    def __init__(self, cfg: dict, inactivo_fn):
        super().__init__(name="camara", daemon=True)
        c = cfg["camara"]
        self.indice = int(c["indice"])
        self.fps = max(1, int(c["fps"]))
        self.detectar_celular = bool(c["detectar_celular"])
        self.umbral_celular = float(c["umbral_celular"])
        self.inactivo_fn = inactivo_fn
        # Con la ventana «Lo que ve la cámara» abierta: 10 imágenes por segundo, anotadas.
        self.vista_activa = False
        self.error = ""
        self._cuadro = None
        self._cajas: list[tuple[int, int, int, int, str, float]] = []
        self._dv_dh = (0.0, 0.0)
        self._obs = ObsCamara()
        self._lock = threading.Lock()
        self._parar = threading.Event()
        # Postura "mirando la pantalla", aprendida mientras usas el teclado o el ratón.
        self._base_v: float | None = None
        self._base_h = 0.0
        self._hist_atencion: deque[str] = deque(maxlen=self.fps * 2)
        self._hist_ojos: deque[bool] = deque(maxlen=self.fps * 3)
        self._hist_celular: deque[bool] = deque(maxlen=3)
        self._persona = False
        self._ultima_atencion = "pantalla"
        self._ts_ultima_cara = 0.0
        # Resumen por minuto en el registro, para poder ajustar los umbrales con datos reales.
        self._minuto_atencion: Counter[str] = Counter()
        self._minuto_dv: list[float] = []
        self._minuto_desde = time.time()

    def obs(self) -> ObsCamara:
        with self._lock:
            return replace(self._obs)

    def cuadro(self):
        """Última imagen anotada (RGB, como un espejo) si la vista está abierta."""
        with self._lock:
            return self._cuadro

    def detener(self) -> None:
        self._parar.set()

    def run(self) -> None:
        try:
            self._bucle()
        except Exception as e:  # noqa: BLE001 - la cámara no debe tumbar a Jarvis
            log.exception("Cámara detenida")
            self.error = str(e)
        finally:
            with self._lock:
                self._obs = ObsCamara()

    # ------------------------------------------------------------------------
    def _bucle(self) -> None:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        from .config import modelo

        rostros = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(modelo("face_landmarker.task"))),
            running_mode=vision.RunningMode.VIDEO, num_faces=1, output_face_blendshapes=True))
        objetos = vision.ObjectDetector.create_from_options(vision.ObjectDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(modelo("efficientdet_lite0.tflite"))),
            running_mode=vision.RunningMode.VIDEO, max_results=6, score_threshold=0.3,
            category_allowlist=["cell phone", "person"]))
        cap = cv2.VideoCapture(self.indice, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not cap.isOpened():
            raise RuntimeError("no pude abrir la cámara (¿la está usando otra aplicación?)")
        log.info("Cámara encendida")
        fallos = 0
        ultimo_objetos = 0.0
        try:
            while not self._parar.is_set():
                t0 = time.monotonic()
                ok, frame = cap.read()
                if not ok:
                    fallos += 1
                    if fallos > 25:
                        raise RuntimeError("la cámara dejó de entregar imágenes")
                    time.sleep(0.2)
                    continue
                fallos = 0
                imagen = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                ts_ms = int(t0 * 1000)
                if t0 - ultimo_objetos >= 1.0:  # celular y persona, ~1 vez por segundo
                    ultimo_objetos = t0
                    self._procesar_objetos(objetos.detect_for_video(imagen, ts_ms), frame.shape)
                resultado = rostros.detect_for_video(imagen, ts_ms)
                self._procesar_rostro(resultado)
                if self.vista_activa:
                    self._anotar(cv2, frame, resultado)
                elif self._cuadro is not None:
                    with self._lock:
                        self._cuadro = None
                periodo = 1.0 / (10 if self.vista_activa else self.fps)
                time.sleep(max(0.0, periodo - (time.monotonic() - t0)))
        finally:
            cap.release()
            rostros.close()
            objetos.close()
            with self._lock:
                self._cuadro = None
            log.info("Cámara apagada")

    def _procesar_objetos(self, resultado, forma) -> None:
        alto, ancho = forma[:2]
        persona = celular = False
        cajas = []
        for d in resultado.detections:
            cat = d.categories[0]
            caja = d.bounding_box
            if cat.category_name == "person" and cat.score > 0.4:
                persona = True
                cajas.append((caja.origin_x, caja.origin_y, caja.width, caja.height, "persona", cat.score))
            elif cat.category_name == "cell phone" and cat.score >= self.umbral_celular:
                # Un celular en la mano ocupa poco; algo grande suele ser la tableta.
                pequeno = caja.width * caja.height / (ancho * alto) < 0.12
                celular = celular or pequeno
                cajas.append((caja.origin_x, caja.origin_y, caja.width, caja.height,
                              "celular" if pequeno else "tableta?", cat.score))
        self._cajas = cajas
        self._persona = persona
        self._hist_celular.append(celular and self.detectar_celular)

    def _procesar_rostro(self, res) -> None:
        ahora = time.time()
        dv = dh = 0.0
        rostro = bool(res.face_landmarks)
        if rostro:
            lm = res.face_landmarks[0]
            ojos_y = (lm[OJO_IZQ].y + lm[OJO_DER].y) / 2
            alto = lm[MENTON].y - ojos_y
            ancho = lm[OJO_DER].x - lm[OJO_IZQ].x
            if alto < 1e-3 or abs(ancho) < 1e-3:
                return
            # Al bajar la cabeza la punta de la nariz se acerca al mentón en la imagen.
            v = (lm[NARIZ].y - ojos_y) / alto
            h = (lm[NARIZ].x - lm[OJO_IZQ].x) / ancho - 0.5
            if self._base_v is None:
                self._base_v, self._base_h = v, h
            elif self.inactivo_fn() < 2:
                self._base_v += 0.03 * (v - self._base_v)
                self._base_h += 0.03 * (h - self._base_h)
            dv, dh = v - self._base_v, h - self._base_h
            self._dv_dh = (dv, dh)
            if dv > 0.07:
                atencion = "abajo"
            elif abs(dh) > 0.15:
                atencion = "lado"
            else:
                atencion = "pantalla"
            parpadeo = 0.0
            if res.face_blendshapes:
                bs = {c.category_name: c.score for c in res.face_blendshapes[0]}
                parpadeo = (bs.get("eyeBlinkLeft", 0.0) + bs.get("eyeBlinkRight", 0.0)) / 2
            self._hist_ojos.append(parpadeo > 0.55 and atencion == "pantalla")
            self._ultima_atencion, self._ts_ultima_cara = atencion, ahora
        else:
            # Sin cara visible: si el cuerpo sigue ahí o acababas de bajar la
            # cabeza, sigues en la mesa (escribiendo en la tableta, p. ej.).
            if self._ultima_atencion == "abajo" and (self._persona or ahora - self._ts_ultima_cara < 4):
                atencion = "abajo"
            elif self._persona:
                atencion = "lado"
            else:
                atencion = "ausente"
            self._hist_ojos.append(False)
        self._hist_atencion.append(atencion)
        self._minuto_atencion[atencion] += 1
        if rostro:
            self._minuto_dv.append(dv)
        if ahora - self._minuto_desde >= 60:
            self._registrar_minuto(ahora)
        votada = Counter(self._hist_atencion).most_common(1)[0][0]
        ojos = len(self._hist_ojos) >= self.fps and sum(self._hist_ojos) > 0.7 * len(self._hist_ojos)
        celular = sum(self._hist_celular) >= 2 and votada != "pantalla"
        with self._lock:
            self._obs = ObsCamara(ok=True, presente=votada != "ausente", rostro=rostro, atencion=votada,
                                  ojos_cerrados=ojos, celular=celular, vertical=round(dv, 3),
                                  horizontal=round(dh, 3), ts=ahora)

    def _registrar_minuto(self, ahora: float) -> None:
        total = sum(self._minuto_atencion.values()) or 1
        reparto = ", ".join(f"{a} {100 * n // total}%" for a, n in self._minuto_atencion.most_common())
        dv = sorted(self._minuto_dv)
        if dv:
            log.info("Cámara (1 min): %s | dv p10 %+.3f, mediana %+.3f, p90 %+.3f (abajo si > 0.07)", reparto,
                     dv[len(dv) // 10], dv[len(dv) // 2], dv[9 * len(dv) // 10])
        else:
            log.info("Cámara (1 min): %s | sin cara visible", reparto)
        self._minuto_atencion.clear()
        self._minuto_dv.clear()
        self._minuto_desde = ahora

    def _anotar(self, cv2, frame, resultado) -> None:
        """Dibuja lo que entiende Jarvis: puntos de la cara, una flecha hacia donde apunta la cabeza
        (respecto a tu postura normal) y los recuadros de persona/celular. Queda como un espejo."""
        img = frame.copy()
        alto, ancho = img.shape[:2]
        o = self.obs()
        color = {"pantalla": (110, 220, 60), "abajo": (240, 200, 40), "lado": (40, 170, 255),
                 "ausente": (70, 70, 235)}.get(o.atencion, (200, 200, 200))  # BGR
        for x, y, w, h, nombre, _ in self._cajas:
            cv2.rectangle(img, (x, y), (x + w, y + h), (60, 60, 240) if nombre != "persona" else (150, 150, 150), 2)
        if resultado.face_landmarks:
            lm = resultado.face_landmarks[0]
            for p in lm[::5]:
                cv2.circle(img, (int(p.x * ancho), int(p.y * alto)), 1, (215, 215, 215), -1)
            for i in (OJO_IZQ, OJO_DER, MENTON):
                cv2.circle(img, (int(lm[i].x * ancho), int(lm[i].y * alto)), 4, color, -1)
            nx, ny = int(lm[NARIZ].x * ancho), int(lm[NARIZ].y * alto)
            dv, dh = self._dv_dh
            cv2.arrowedLine(img, (nx, ny), (int(nx + dh * 500), int(ny + dv * 500)), color, 3, tipLength=0.3)
            cv2.circle(img, (nx, ny), 5, color, -1)
        img = cv2.flip(img, 1)
        for x, y, w, h, nombre, puntaje in self._cajas:  # textos después de voltear, para que se lean
            cv2.putText(img, f"{nombre} {puntaje:.0%}", (ancho - x - w + 4, max(18, y + 18)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        with self._lock:
            self._cuadro = rgb
