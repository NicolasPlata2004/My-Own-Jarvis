"""Interfaz: el orbe flotante, el globo de texto, el panel y el icono de la bandeja."""
from __future__ import annotations

import html
import math
import os
import subprocess
import threading
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QGuiApplication, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QProgressBar, QPushButton, QSpinBox, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

from .app import Jarvis
from .config import ARCHIVO_CONFIG

COLORES = {
    "sin_sesion": "#64748b", "enfocado": "#06b6d4", "mesa": "#10b981", "pensando": "#3b82f6",
    "mensajeria": "#f59e0b", "distraido": "#ef4444", "celular": "#f43f5e", "ausente": "#94a3b8",
    "somnoliento": "#a855f7", "descanso": "#84cc16", "pausa": "#94a3b8",
}
COLOR_JARVIS = "#22d3ee"   # el cian de Jarvis en reposo
NOMBRE_SERVIDOR = "jarvis-estudio-unico"
ESTILO_PANEL = """
QFrame#marco { background: rgba(15, 23, 42, 238); border: 1px solid rgba(148, 163, 184, 70); border-radius: 16px; }
QLabel { color: #e2e8f0; font-size: 13px; }
QLabel#titulo { font-size: 15px; font-weight: 700; letter-spacing: 2px; color: #67e8f9; }
QLabel#tenue { color: #94a3b8; font-size: 12px; }
QLabel#tema { font-size: 14px; font-weight: 600; }
QPushButton { background: rgba(51, 65, 85, 200); color: #e2e8f0; border: none; border-radius: 8px;
              padding: 6px 8px; font-size: 12px; }
QPushButton:hover { background: rgba(71, 85, 105, 230); }
QPushButton:disabled { color: #64748b; background: rgba(30, 41, 59, 200); }
QPushButton#cerrar { background: transparent; font-size: 16px; padding: 0 4px; color: #94a3b8; }
QLineEdit { background: rgba(30, 41, 59, 230); color: #f1f5f9; border: 1px solid #334155; border-radius: 8px;
            padding: 6px 8px; font-size: 13px; }
QTextBrowser { background: rgba(2, 6, 23, 120); color: #cbd5e1; border: none; border-radius: 8px; font-size: 12px; }
QProgressBar { background: rgba(51, 65, 85, 200); border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #06b6d4; border-radius: 4px; }
"""


CATEGORIAS = {"estudio": "estudio", "herramienta": "herramienta", "neutral": "neutral",
              "mensajeria": "mensajería", "distraccion": "distracción", "desconocido": "consultando…"}


def _hm(minutos: float) -> str:
    h, m = divmod(int(minutos), 60)
    if not h:
        return f"{m} min"
    return f"{h} h {m:02d} min" if m else f"{h} h"


def abrir_ajustes() -> None:
    subprocess.Popen(["notepad.exe", str(ARCHIVO_CONFIG)])


_CACHE_FONDOS: dict[tuple, QPixmap] = {}


def _fondo(r: float, base: QColor, marcas: bool, dpr: float) -> QPixmap:
    """Disco oscuro y dial de marcas: no cambian entre cuadros, así que se dibujan una sola vez."""
    clave = (round(r, 1), base.rgba(), marcas, round(dpr, 2))
    pix = _CACHE_FONDOS.get(clave)
    if pix is not None:
        return pix
    lado = 2 * r + 2
    pix = QPixmap(int(lado * dpr) + 1, int(lado * dpr) + 1)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    q = QPainter(pix)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(r + 1, r + 1)
    fondo = QRadialGradient(c, r)
    fondo.setColorAt(0.0, QColor(10, 25, 45, 240))
    fondo.setColorAt(0.82, QColor(4, 12, 26, 235))
    fondo.setColorAt(1.0, QColor(4, 12, 26, 0))
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(fondo)
    q.drawEllipse(c, r, r)
    if marcas:  # dial de marcas finas
        q.translate(c)
        for i in range(60):
            larga = i % 5 == 0
            color = QColor(base)
            color.setAlpha(170 if larga else 70)
            q.setPen(QPen(color, 1.3 if larga else 1.0))
            q.drawLine(QPointF(0, -(r - 2)), QPointF(0, -(r - (6 if larga else 4))))
            q.rotate(6)
    q.end()
    if len(_CACHE_FONDOS) > 40:
        _CACHE_FONDOS.clear()
    _CACHE_FONDOS[clave] = pix
    return pix


def dibujar_jarvis(p: QPainter, c: QPointF, r: float, base: QColor, fase: float, nivel: float,
                   marcas: bool = True) -> None:
    """El Jarvis «de verdad»: disco oscuro con su dial, arcos que giran en sentidos opuestos y una
    onda circular que se mueve con la voz (nivel 0–1)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    transparente = QColor(base)
    transparente.setAlpha(0)
    dpr = p.device().devicePixelRatioF() if p.device() else 1.0
    p.drawPixmap(QPointF(c.x() - r - 1, c.y() - r - 1), _fondo(r, base, marcas, dpr))
    brillo = QRadialGradient(c, r)
    color = QColor(base)
    color.setAlpha(int(45 + 90 * nivel))
    brillo.setColorAt(0.3, color)
    brillo.setColorAt(1.0, transparente)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(brillo)
    p.drawEllipse(c, r, r)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for radio, grosor, velocidad, tramo, alfa, desfase in ((0.84, 2.2, 50, 75, 200, 0), (0.84, 2.2, 50, 40, 140, 180),
                                                          (0.74, 1.4, -70, 120, 120, 90)):
        rr = r * radio
        color = QColor(base)
        color.setAlpha(alfa)
        pluma = QPen(color, grosor)
        pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pluma)
        p.drawArc(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), int(((fase * velocidad + desfase) % 360) * 16),
                  int(tramo * 16))
    capas = ((1.0, 235, 1.8, 0.0), (0.75, 140, 1.2, 1.9), (0.55, 90, 1.0, 3.7))
    for capa, (amplitud, alfa, grosor, desfase) in enumerate(capas if nivel >= 0.3 else capas[:2]):
        camino = QPainterPath()
        n = 64
        for k in range(n + 1):
            a = 2 * math.pi * k / n
            onda = (math.sin(3 * a + fase * 2.2 + desfase) * 0.5 + math.sin(5 * a - fase * 1.4 + desfase) * 0.3
                    + math.sin(9 * a + fase * 3.1 + 2 * desfase) * 0.2)
            rr = r * 0.50 + amplitud * (r * 0.05 + r * 0.22 * nivel) * onda
            punto = QPointF(c.x() + math.cos(a) * rr, c.y() + math.sin(a) * rr)
            if k == 0:
                camino.moveTo(punto)
            else:
                camino.lineTo(punto)
        color = QColor(base).lighter(140 if capa == 0 else 100)
        color.setAlpha(alfa)
        p.setPen(QPen(color, grosor))
        p.drawPath(camino)
    nucleo = QRadialGradient(c, r * 0.32)
    nucleo.setColorAt(0.0, QColor(255, 255, 255, int(150 + 90 * nivel)))
    color = QColor(base)
    color.setAlpha(170)
    nucleo.setColorAt(0.45, color)
    nucleo.setColorAt(1.0, transparente)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(nucleo)
    p.drawEllipse(c, r * 0.32, r * 0.32)
    p.restore()


def emblema(tam: int = 256, color: str = "#22d3ee") -> QPixmap:
    """El Jarvis quieto, para el icono de la bandeja y de los accesos directos."""
    pix = QPixmap(tam, tam)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    dibujar_jarvis(p, QPointF(tam / 2, tam / 2), tam / 2 - 1, QColor(color), fase=0.6, nivel=0.45, marcas=tam >= 48)
    p.end()
    return pix


def icono(tam: int = 64) -> QIcon:
    return QIcon(emblema(tam))


class Puente(QObject):
    """Lleva los avisos de los hilos de Jarvis al hilo de la interfaz."""
    estado = Signal(dict)
    mensaje = Signal(str, str)
    oido = Signal(str)
    hablando = Signal(bool)


class _Avisos:
    def __init__(self, puente: Puente):
        self.estado = puente.estado.emit
        self.mensaje = puente.mensaje.emit
        self.oido = puente.oido.emit
        self.hablando = puente.hablando.emit


# =============================================================================
class Orbe(QWidget):
    """El orbe: Jarvis animado (la onda se mueve con su voz o con la tuya), el avance del bloque y,
    durante una sesión, una etiqueta debajo con tu estado (Estudiando, Procrastinando…)."""
    ANCHO, ALTO, R = 128, 124, 46

    def __init__(self, ctl: "Interfaz"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.ANCHO, self.ALTO)
        self.setWindowTitle("Jarvis")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Jarvis · clic: panel · doble clic: hablar · clic derecho: menú · arrastra para moverlo")
        self.ctl = ctl
        self.d: dict = {"estado": "sin_sesion"}
        self.oido = "inactivo"
        self.hablando = False
        self._fase = 0.0
        self._nivel = 0.15
        self._t = time.monotonic()
        self._agarre: QPoint | None = None
        self._movido = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animar)
        self._timer.start(100)
        self._colocar()

    def _centro(self) -> QPointF:
        return QPointF(self.ANCHO / 2, self.R + 4)

    def _colocar(self) -> None:
        pos = QSettings("Jarvis", "Jarvis").value("orbe_pos")
        pantallas = [s.availableGeometry() for s in QGuiApplication.screens()]
        if isinstance(pos, QPoint) and any(g.contains(pos + QPoint(40, 40)) for g in pantallas):
            self.move(pos)
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.right() - self.ANCHO - 20, g.bottom() - self.ALTO - 20)

    def _nivel_objetivo(self) -> float:
        j = self.ctl.jarvis
        if self.hablando:
            n = j.voz.nivel() if j.voz else 0.0
            return n if n > 0 else 0.45 + 0.3 * abs(math.sin(self._fase * 5))
        if self.oido == "escuchando":
            return min(1.0, 0.25 + 1.6 * (j.oido.nivel_mic if j.oido else 0.0))
        if self.oido == "procesando":
            return 0.45 + 0.15 * math.sin(self._fase * 4)
        if self.d.get("estado") in ("distraido", "celular"):
            return 0.35 + 0.1 * math.sin(self._fase * 3)
        return 0.14 + 0.05 * math.sin(self._fase * 0.9)   # respiración en reposo

    def _animar(self) -> None:
        ahora = time.monotonic()
        dt = min(0.2, ahora - self._t)
        self._t = ahora
        activo = self.hablando or self.oido != "inactivo"
        self._fase += dt * (2.2 if activo else 0.9)
        self._nivel += (self._nivel_objetivo() - self._nivel) * min(1.0, dt * 12)
        # Hablando o escuchando, fluido; en reposo basta con 10 cuadros por segundo (gasta mucho menos).
        self._timer.setInterval(33 if activo else 100)
        self.update()

    def paintEvent(self, _evento) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        estado = self.d.get("estado", "sin_sesion")
        if self.oido == "escuchando":
            base = QColor("#e0f2fe")
        elif self.oido == "procesando":
            base = QColor("#c4b5fd")
        elif estado == "sin_sesion":
            base = QColor(COLOR_JARVIS)
        else:
            base = QColor(COLORES.get(estado, COLOR_JARVIS))
        c, r = self._centro(), self.R
        dibujar_jarvis(p, c, r, base, self._fase, self._nivel)

        total, hecho = self.d.get("bloque_total_s") or 0, self.d.get("bloque_s") or 0
        if total:  # avance del bloque de estudio (o del descanso)
            rr = r * 0.93
            pluma = QPen(QColor(255, 255, 255, 230), 3)
            pluma.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pluma)
            p.drawArc(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), 90 * 16, -int(min(1.0, hecho / total) * 360 * 16))
        if self.d.get("sesion") and self.oido == "inactivo" and not self.hablando:
            texto = f"{max(0, int((total - hecho) // 60))}′" if estado == "descanso" else f"{int(self.d.get('hoy_min', 0))}′"
            p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            caja = QRectF(c.x() - 30, c.y() - 12, 60, 24)
            p.setPen(QColor(0, 0, 0, 170))
            p.drawText(caja.translated(1, 1), Qt.AlignmentFlag.AlignCenter, texto)
            p.setPen(QColor("white"))
            p.drawText(caja, Qt.AlignmentFlag.AlignCenter, texto)

        etiqueta = self.d.get("etiqueta") or ""
        if etiqueta:  # «Estudiando», «Procrastinando»… debajo del orbe
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            metricas = p.fontMetrics()
            texto = metricas.elidedText(etiqueta, Qt.TextElideMode.ElideRight, self.ANCHO - 20)
            ancho = metricas.horizontalAdvance(texto) + 18
            caja = QRectF((self.ANCHO - ancho) / 2, c.y() + r + 3, ancho, 18)
            p.setBrush(QColor(15, 23, 42, 230))
            p.setPen(QPen(base, 1.2))
            p.drawRoundedRect(caja, 9, 9)
            p.setPen(QColor("#f1f5f9"))
            p.drawText(caja, Qt.AlignmentFlag.AlignCenter, texto)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._agarre = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._movido = False
        elif e.button() == Qt.MouseButton.RightButton:
            self.ctl.menu.popup(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e) -> None:
        if self._agarre is not None and e.buttons() & Qt.MouseButton.LeftButton:
            nueva = e.globalPosition().toPoint() - self._agarre
            if (nueva - self.pos()).manhattanLength() > 3 or self._movido:
                self._movido = True
                self.move(nueva)
                self.ctl.reubicar()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._movido:
            QSettings("Jarvis", "Jarvis").setValue("orbe_pos", self.pos())
        else:
            QTimer.singleShot(220, self._clic_simple)
        self._agarre = None

    def mouseDoubleClickEvent(self, _e) -> None:
        self._doble = time.time()
        self.ctl.jarvis.escuchar()

    def _clic_simple(self) -> None:
        if time.time() - getattr(self, "_doble", 0) > 0.4:
            self.ctl.alternar_panel()


# =============================================================================
class Globo(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.texto = QLabel(wordWrap=True)
        self.texto.setStyleSheet("color: #f1f5f9; font-size: 13px;")
        self.texto.setMaximumWidth(320)
        caja = QVBoxLayout(self)
        caja.setContentsMargins(16, 12, 16, 12)
        caja.addWidget(self.texto)
        self.color = QColor("#06b6d4")
        self._ocultar = QTimer(self, singleShot=True)
        self._ocultar.timeout.connect(self.hide)

    def mostrar(self, texto: str, quien: str, orbe: QWidget, color: str) -> None:
        self.texto.setText(("<b>Tú:</b> " if quien == "tu" else "") + html.escape(texto))
        self.color = QColor("#94a3b8" if quien == "tu" else color)
        self.adjustSize()
        g = orbe.screen().availableGeometry()
        o = orbe.frameGeometry()
        x = o.left() - self.width() - 8 if o.center().x() > g.center().x() else o.right() + 8
        y = min(max(g.top() + 8, o.center().y() - self.height() // 2), g.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()
        self.raise_()
        self._ocultar.start(min(20000, 5000 + 70 * len(texto)))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(15, 23, 42, 235))
        p.setPen(QPen(self.color, 1.5))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)

    def mousePressEvent(self, _e) -> None:
        self.hide()


# =============================================================================
class DialogoSesion(QDialog):
    def __init__(self, cfg: dict):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Jarvis · nueva sesión de estudio")
        self.setWindowIcon(icono())
        self.tema = QComboBox(editable=True)
        self.tema.addItems([t["nombre"] for t in cfg["temas"]])
        self.objetivo = QLineEdit(placeholderText="Ej.: ver los vídeos 20 a 25 y hacer el ej. 34 en papel")
        self.total = QSpinBox(minimum=0, maximum=600, singleStep=5, value=0, suffix=" min")
        self.total.setSpecialValueText("Sin límite")
        self.total.setToolTip("Cuando lleves este tiempo estudiando, Jarvis cierra la sesión con su balance")
        self.bloque = QSpinBox(minimum=5, maximum=240, singleStep=5, value=cfg["sesion"]["duracion_min"],
                               suffix=" min")
        self.descanso = QSpinBox(minimum=0, maximum=60, value=cfg["sesion"]["descanso_min"], suffix=" min")
        self.descanso.setSpecialValueText("Sin descanso")
        rapidos = QHBoxLayout()
        for nombre, (total, bloque, descanso) in {"30 min": (30, 30, 0), "1 hora": (60, 50, 10),
                                                  "2 horas": (120, 50, 10), "Pomodoro": (0, 25, 5)}.items():
            boton = QPushButton(nombre)
            boton.clicked.connect(lambda _=False, t=total, b=bloque, d=descanso: (
                self.total.setValue(t), self.bloque.setValue(b), self.descanso.setValue(d)))
            rapidos.addWidget(boton)
        self.papel = QCheckBox("Escribir en la tableta o en papel cuenta como estudio")
        self.papel.setChecked(cfg["foco"]["cabeza_abajo_es_estudio"])
        form = QFormLayout(self)
        form.addRow("Tema", self.tema)
        form.addRow("Objetivo", self.objetivo)
        form.addRow("Rápido", rapidos)
        form.addRow("Tiempo total", self.total)
        form.addRow("Bloques de", self.bloque)
        form.addRow("Descansos de", self.descanso)
        form.addRow(self.papel)
        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botones.button(QDialogButtonBox.StandardButton.Ok).setText("Empezar")
        botones.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        form.addRow(botones)
        self.resize(460, 0)

    def datos(self) -> dict:
        return {"tema": self.tema.currentText().strip() or None, "objetivo": self.objetivo.text().strip(),
                "total": self.total.value(), "bloque": self.bloque.value(), "descanso": self.descanso.value(),
                "papel": self.papel.isChecked()}


# =============================================================================
class Panel(QWidget):
    ANCHO = 360

    def __init__(self, ctl: "Interfaz"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Jarvis")
        self.setFixedWidth(self.ANCHO)
        self.setStyleSheet(ESTILO_PANEL)
        self.ctl = ctl
        marco = QFrame(objectName="marco")
        exterior = QVBoxLayout(self)
        exterior.setContentsMargins(0, 0, 0, 0)
        exterior.addWidget(marco)
        caja = QVBoxLayout(marco)
        caja.setContentsMargins(16, 12, 16, 14)
        caja.setSpacing(8)

        cabecera = QHBoxLayout()
        cabecera.addWidget(QLabel("JARVIS", objectName="titulo"))
        self.estado = QLabel()
        cabecera.addWidget(self.estado)
        cabecera.addStretch()
        cerrar = QPushButton("×", objectName="cerrar")
        cerrar.clicked.connect(self.hide)
        cabecera.addWidget(cerrar)
        caja.addLayout(cabecera)

        self.tema = QLabel(objectName="tema", wordWrap=True)
        self.objetivo = QLabel(objectName="tenue", wordWrap=True)
        self.bloque = QLabel(wordWrap=True)
        self.hoy = QLabel()
        self.barra_hoy = QProgressBar(textVisible=False, maximum=1000)
        self.barra_hoy.setFixedHeight(8)
        self.ventana = QLabel(objectName="tenue", wordWrap=True)
        for w in (self.tema, self.objetivo, self.bloque, self.hoy, self.barra_hoy, self.ventana):
            caja.addWidget(w)

        fila = QHBoxLayout()
        self.b_empezar = QPushButton("▶ Empezar")
        self.b_pausa = QPushButton("⏸ Pausa")
        self.b_descanso = QPushButton("☕ Descanso")
        self.b_terminar = QPushButton("■ Terminar")
        self.b_empezar.clicked.connect(ctl.nueva_sesion)
        self.b_pausa.clicked.connect(ctl.pausa_o_seguir)
        self.b_descanso.clicked.connect(lambda: ctl.en_hilo(ctl.jarvis.descanso))
        self.b_terminar.clicked.connect(lambda: ctl.en_hilo(ctl.jarvis.terminar_sesion))
        for b in (self.b_empezar, self.b_pausa, self.b_descanso, self.b_terminar):
            fila.addWidget(b)
        caja.addLayout(fila)

        self.chat = QTextBrowser(openExternalLinks=False)
        self.chat.setFixedHeight(150)
        caja.addWidget(self.chat)
        entrada = QHBoxLayout()
        self.texto = QLineEdit(placeholderText="Escríbele a Jarvis…")
        self.texto.returnPressed.connect(self._enviar)
        enviar = QPushButton("Enviar")
        enviar.clicked.connect(self._enviar)
        microfono = QPushButton("🎤")
        microfono.setToolTip("Hablarle (también: «hey Jarvis» o el atajo de teclado)")
        microfono.clicked.connect(ctl.jarvis.escuchar)
        entrada.addWidget(self.texto, 1)
        entrada.addWidget(enviar)
        entrada.addWidget(microfono)
        caja.addLayout(entrada)

        pie = QHBoxLayout()
        informe = QPushButton("📊 Estadísticas")
        informe.clicked.connect(ctl.abrir_informe)
        ajustes = QPushButton("⚙ Ajustes")
        ajustes.clicked.connect(abrir_ajustes)
        self.b_camara = QPushButton()
        self.b_camara.clicked.connect(ctl.alternar_camara)
        for b in (informe, ajustes, self.b_camara):
            pie.addWidget(b)
        caja.addLayout(pie)
        self._mensajes: list[str] = []

    def _enviar(self) -> None:
        texto = self.texto.text().strip()
        if texto:
            self.texto.clear()
            self.ctl.en_hilo(self.ctl.jarvis.texto_usuario, texto)

    def agregar_mensaje(self, texto: str, quien: str) -> None:
        color = "#67e8f9" if quien == "jarvis" else "#cbd5e1"
        nombre = "Jarvis" if quien == "jarvis" else "Tú"
        self._mensajes = (self._mensajes + [f"<p style='margin:4px 0'><b style='color:{color}'>{nombre}:</b> "
                                            f"{html.escape(texto)}</p>"])[-40:]
        self.chat.setHtml("".join(self._mensajes))
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def actualizar(self, d: dict) -> None:
        color = COLORES.get(d["estado"], "#64748b")
        self.estado.setText(f"<span style='color:{color}'>● </span>{d['nombre_estado']}")
        sesion = d["sesion"]
        if sesion:
            self.tema.setText(d["tema"])
            self.objetivo.setText(d["objetivo"] or "Sin objetivo: díselo a Jarvis o escríbelo al empezar.")
            if d["modo"] == "descanso":
                resta = max(0, d["bloque_total_s"] - d["bloque_s"])
                self.bloque.setText(f"Descanso: quedan {int(resta // 60)}:{int(resta % 60):02d}")
            else:
                total = (f"sesión {int(d['estudiado_s'] // 60)} / {d['total_min']} min" if d["total_min"]
                         else "sin límite")
                self.bloque.setText(f"Bloque {int(d['bloque_s'] // 60)} / {int(d['bloque_total_s'] // 60)} min · "
                                    f"{total} · foco {d['foco_pct']} % · {d['distracciones']} distracciones")
        else:
            self.tema.setText("Sin sesión")
            self.objetivo.setText("Pulsa «Empezar» o di «Jarvis, sesión de 30 minutos de matemáticas».")
            self.bloque.setText("")
        self.hoy.setText(f"Hoy: {_hm(d['hoy_min'])} de {_hm(d['meta_min'])} · racha {int(d['racha_min'])} min")
        self.barra_hoy.setValue(int(1000 * min(1.0, d["hoy_min"] / max(1, d["meta_min"]))))
        categoria = CATEGORIAS.get(d["categoria"], d["categoria"])
        self.ventana.setText(f"Ahora: {d['ventana'] or '—'} → {categoria}" + (
            f" · cámara: {d['camara']}" if sesion else ""))
        self.b_empezar.setEnabled(not sesion)
        self.b_pausa.setEnabled(sesion)
        self.b_pausa.setText("▶ Seguir" if d["modo"] in ("pausa", "descanso") else "⏸ Pausa")
        self.b_descanso.setEnabled(sesion and d["modo"] == "estudio")
        self.b_terminar.setEnabled(sesion)
        self.b_camara.setText("📷 Cámara: " + ("no" if d["camara"] == "apagada" else "sí"))


# =============================================================================
class VistaCamara(QWidget):
    """«Lo que ve la cámara»: la imagen en vivo (como un espejo), lo que entiende Jarvis arriba
    (ESTUDIANDO, PROCRASTINANDO…) y el detalle abajo. Enciende la cámara aunque no haya sesión."""

    def __init__(self, ctl: "Interfaz"):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.setWindowTitle("Jarvis · lo que ve la cámara")
        self.setWindowIcon(icono())
        self.setStyleSheet(
            "QWidget { background: #0b1220; color: #e2e8f0; }"
            "QLabel#estado { font-size: 26px; font-weight: 800; letter-spacing: 3px; }"
            "QLabel#sub { color: #94a3b8; font-size: 13px; }"
            "QLabel#video { background: #020617; border-radius: 10px; color: #64748b; font-size: 14px; }"
            "QLabel#detalles { font-size: 13px; }")
        caja = QVBoxLayout(self)
        caja.setContentsMargins(16, 12, 16, 14)
        caja.setSpacing(8)
        self.estado = QLabel(objectName="estado", alignment=Qt.AlignmentFlag.AlignCenter)
        self.sub = QLabel(objectName="sub", alignment=Qt.AlignmentFlag.AlignCenter)
        self.video = QLabel("Encendiendo la cámara…", objectName="video", alignment=Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(480, 360)
        self.detalles = QLabel(objectName="detalles", textFormat=Qt.TextFormat.RichText, wordWrap=True)
        for w in (self.estado, self.sub, self.video, self.detalles):
            caja.addWidget(w)
        caja.setStretch(2, 1)
        self.resize(680, 680)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.actualizar)

    def showEvent(self, evento) -> None:
        super().showEvent(evento)
        self.ctl.jarvis.abrir_vista_camara()
        self._timer.start(100)

    def closeEvent(self, evento) -> None:
        self._timer.stop()
        self.video.clear()
        self.video.setText("Encendiendo la cámara…")
        self.ctl.en_hilo(self.ctl.jarvis.cerrar_vista_camara)
        super().closeEvent(evento)

    def mostrar_cuadro(self, cuadro) -> None:
        alto, ancho = cuadro.shape[:2]
        imagen = QImage(cuadro.data, ancho, alto, 3 * ancho, QImage.Format.Format_RGB888).copy()
        self.video.setPixmap(QPixmap.fromImage(imagen).scaled(
            self.video.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def actualizar(self, info: dict | None = None) -> None:
        cuadro = self.ctl.jarvis.cuadro_camara()
        if cuadro is not None:
            self.mostrar_cuadro(cuadro)
        info = info or self.ctl.jarvis.estado_vista()
        if cuadro is None and "falló" in info["mirada"]:
            self.video.setText(info["mirada"])
        self.estado.setText(info["etiqueta"].upper())
        self.estado.setStyleSheet(f"color: {COLORES.get(info['estado'], COLOR_JARVIS)};")
        self.sub.setText(f"En sesión de {info['tema']}" if info["sesion"]
                         else "Sin sesión: así te leería si estuvieras estudiando")
        categoria = CATEGORIAS.get(info["categoria"], info["categoria"]) or "—"
        quietos = "en uso" if info["inactivo_s"] < 3 else f"quietos hace {int(info['inactivo_s'])} s"
        vertical = f"{info['vertical']:+.2f}".replace(".", ",")
        lateral = f"{info['horizontal']:+.2f}".replace(".", ",")
        filas = [
            ("Cámara", info["mirada"]),
            ("Pantalla", f"{html.escape(info['ventana'] or '—')} → {categoria}"),
            ("Teclado y ratón", quietos),
            ("Celular", "en la mano" if info["celular"] else "no"),
            ("Ojos", "cerrados" if info["ojos_cerrados"] else "abiertos"),
            ("Cabeza", f"{vertical} vertical (abajo si pasa de +0,07) · "
                       f"{lateral} lateral (de lado si pasa de ±0,15)"),
        ]
        self.detalles.setText("<table cellspacing=3>" + "".join(
            f"<tr><td style='color:#94a3b8; padding-right:10px'>{k}</td><td>{v}</td></tr>" for k, v in filas)
            + "</table>")


# =============================================================================
class Interfaz(QObject):
    def __init__(self, app: QApplication, cfg: dict, silencioso: bool = False):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.puente = Puente()
        self.jarvis = Jarvis(cfg, _Avisos(self.puente))
        self.ultimo: dict = {"estado": "sin_sesion", "modo": ""}
        self.vista: VistaCamara | None = None
        self.orbe = Orbe(self)
        self.globo = Globo()
        self.panel = Panel(self)
        self.menu = self._crear_menu()
        self.bandeja = QSystemTrayIcon(icono(), self)
        self.bandeja.setToolTip("Jarvis")
        self.bandeja.setContextMenu(self.menu)
        self.bandeja.activated.connect(
            lambda razon: self.alternar_panel() if razon == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.puente.estado.connect(self._estado)
        self.puente.mensaje.connect(self._mensaje)
        self.puente.oido.connect(self._oido)
        self.puente.hablando.connect(self._hablando)
        self.bandeja.show()
        self.orbe.show()
        self.jarvis.arrancar(silencioso)

    def _crear_menu(self) -> QMenu:
        menu = QMenu()
        acciones = [
            ("▶ Empezar sesión…", self.nueva_sesion),
            ("⏸ Pausa / seguir", self.pausa_o_seguir),
            ("☕ Descanso", lambda: self.en_hilo(self.jarvis.descanso)),
            ("■ Terminar sesión", lambda: self.en_hilo(self.jarvis.terminar_sesion)),
            None,
            ("🎤 Hablarle", self.jarvis.escuchar),
            ("🔇 Silencio 2 horas", lambda: self.en_hilo(self.jarvis.silenciar, 120)),
            ("🔊 Quitar silencio", lambda: self.en_hilo(self.jarvis.silenciar, 0)),
            ("📊 Estadísticas", self.abrir_informe),
            ("📷 Cámara sí/no", self.alternar_camara),
            ("👁 Ver lo que ve la cámara", self.abrir_vista),
            None,
            ("⚙ Abrir ajustes", abrir_ajustes),
            ("↻ Recargar ajustes", lambda: self.en_hilo(self.jarvis.recargar)),
            ("📄 LEEME", lambda: os.startfile(ARCHIVO_CONFIG.parent / "LEEME.pdf")),
            None,
            ("Salir", self.salir),
        ]
        for a in acciones:
            if a is None:
                menu.addSeparator()
            else:
                accion = QAction(a[0], menu)
                accion.triggered.connect(a[1])
                menu.addAction(accion)
        return menu

    @staticmethod
    def en_hilo(funcion, *args) -> None:
        """Las acciones que pueden tardar (cámara, IA) no deben congelar la interfaz."""
        threading.Thread(target=funcion, args=args, daemon=True).start()

    # --- señales desde Jarvis ------------------------------------------------------
    def _estado(self, d: dict) -> None:
        self.ultimo = d
        self.orbe.d = d
        self.orbe.update()
        if self.panel.isVisible():
            self.panel.actualizar(d)
        self.bandeja.setToolTip(f"Jarvis · {d['nombre_estado']}")

    def _mensaje(self, texto: str, quien: str) -> None:
        self.panel.agregar_mensaje(texto, quien)
        self.globo.mostrar(texto, quien, self.orbe, COLORES.get(self.ultimo.get("estado"), "#06b6d4"))

    def _oido(self, estado: str) -> None:
        self.orbe.oido = estado
        self.orbe.update()

    def _hablando(self, si: bool) -> None:
        self.orbe.hablando = si
        self.orbe.update()

    # --- acciones --------------------------------------------------------------------
    def alternar_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
            return
        if "nombre_estado" in self.ultimo:
            self.panel.actualizar(self.ultimo)
        self.panel.adjustSize()
        self.reubicar()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def reubicar(self) -> None:
        """Pone el panel junto al orbe, del lado de la pantalla donde cabe."""
        self.globo.hide()
        g = self.orbe.screen().availableGeometry()
        o = self.orbe.frameGeometry()
        x = o.left() - self.panel.width() - 10 if o.center().x() > g.center().x() else o.right() + 10
        y = min(max(g.top() + 8, o.bottom() - self.panel.height()), g.bottom() - self.panel.height() - 8)
        self.panel.move(x, y)

    def abrir_vista(self) -> None:
        if self.vista is None:
            self.vista = VistaCamara(self)
        self.vista.show()
        self.vista.raise_()
        self.vista.activateWindow()

    def nueva_sesion(self) -> None:
        dialogo = DialogoSesion(self.jarvis.cfg)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self.en_hilo(lambda: self.jarvis.iniciar_sesion(**dialogo.datos()))

    def pausa_o_seguir(self) -> None:
        if self.ultimo.get("modo") in ("pausa", "descanso"):
            self.en_hilo(self.jarvis.reanudar)
        else:
            self.en_hilo(self.jarvis.pausar)

    def alternar_camara(self) -> None:
        self.en_hilo(self.jarvis.alternar_camara)

    def abrir_informe(self) -> None:
        def generar():
            from .report import generar as generar_informe
            os.startfile(generar_informe(self.jarvis.db, self.jarvis.cfg))
        self.en_hilo(generar)

    def salir(self, cerrar_sesion: bool = True) -> None:
        self.bandeja.hide()
        self.orbe.hide()
        self.panel.hide()
        self.globo.hide()
        if self.vista:
            self.vista.hide()
        self.jarvis.salir(cerrar_sesion)
        self.app.quit()


def _ordenar(orden: bytes) -> bool:
    """Le pasa una orden al Jarvis que ya está abierto. False si no hay ninguno."""
    socket = QLocalSocket()
    socket.connectToServer(NOMBRE_SERVIDOR)
    if not socket.waitForConnected(300):
        return False
    socket.write(orden)
    socket.flush()
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def ejecutar(cfg: dict, argumentos: list[str] | tuple = ()) -> int:
    """Argumentos: --inicio (arranque con Windows, sin saludo hablado), --salir (cierra el Jarvis abierto)
    y --reiniciar (cierra el abierto sin terminar la sesión y arranca de nuevo, p. ej. tras una actualización)."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Jarvis")
    app.setWindowIcon(icono())
    # Una sola instancia: si ya está abierto, le pasa la orden y se va (salvo al reiniciar).
    if "--reiniciar" in argumentos:
        if _ordenar(b"reiniciar"):
            limite = time.time() + 25
            while time.time() < limite:  # espera a que el viejo suelte cámara, micrófono y nombre
                time.sleep(0.5)
                prueba = QLocalSocket()
                prueba.connectToServer(NOMBRE_SERVIDOR)
                if not prueba.waitForConnected(200):
                    break
                prueba.abort()
    elif _ordenar(b"salir" if "--salir" in argumentos else b"mostrar"):
        return 0
    if "--salir" in argumentos:
        return 0
    QLocalServer.removeServer(NOMBRE_SERVIDOR)
    servidor = QLocalServer()
    servidor.listen(NOMBRE_SERVIDOR)
    interfaz = Interfaz(app, cfg, silencioso="--inicio" in argumentos)

    def nueva_conexion() -> None:
        conexion = servidor.nextPendingConnection()

        def leer() -> None:
            orden = bytes(conexion.readAll()).decode("utf-8", "ignore").strip()
            if orden == "salir":
                interfaz.salir()
            elif orden == "reiniciar":
                interfaz.salir(cerrar_sesion=False)
            elif orden:
                interfaz.alternar_panel()

        conexion.readyRead.connect(leer)
        if conexion.bytesAvailable():
            leer()

    servidor.newConnection.connect(nueva_conexion)
    app.aboutToQuit.connect(lambda: servidor.close())
    return app.exec()
