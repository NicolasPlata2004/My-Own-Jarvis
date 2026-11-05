"""Dibuja el orbe (en reposo, hablando y en varios estados), el globo, el panel y la ventana de la
cámara en una imagen, sin abrir ventanas ni encender la cámara (usa una imagen de mentira).

Uso:  .venv\\Scripts\\python.exe tests\\prueba_interfaz.py   → docs\\interfaz.png
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
os.environ["QT_SCALE_FACTOR"] = "2"   # captura nítida para el LEEME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jarvis import config  # noqa: E402
from jarvis.app import Jarvis  # noqa: E402

Jarvis.arrancar = lambda self, *a, **k: None  # sin hilos: solo la interfaz
from jarvis.ui import Interfaz, emblema  # noqa: E402


def cuadro_de_mentira() -> np.ndarray:
    """Una «cámara» dibujada: fondo, silueta y puntos, para no usar la imagen real de nadie."""
    alto, ancho = 480, 640
    y, x = np.mgrid[0:alto, 0:ancho]
    img = np.zeros((alto, ancho, 3), np.uint8)
    img[..., 0] = 40 + 30 * x / ancho
    img[..., 1] = 50 + 40 * y / alto
    img[..., 2] = 70
    cara = ((x - 320) / 95) ** 2 + ((y - 230) / 125) ** 2 < 1
    img[cara] = (150, 130, 120)
    hombros = ((x - 320) / 230) ** 2 + ((y - 520) / 150) ** 2 < 1
    img[hombros] = (60, 80, 120)
    for px, py in [(285, 205), (355, 205), (320, 250), (320, 330)]:
        img[py - 4:py + 4, px - 4:px + 4] = (90, 230, 120)
    return img


def main() -> None:
    app = QApplication([])
    app.setFont(QFont("Segoe UI", 9))
    cfg = config.cargar()
    cfg["voz"]["activar"] = False
    cfg["camara"]["activar"] = False
    ui = Interfaz(app, cfg)
    base = {"nombre_estado": "", "hoy_min": 72, "meta_min": 180, "racha_min": 18, "ventana": "", "categoria": "estudio",
            "razon": "", "camara": "pantalla", "camara_error": "", "sesion": True, "etiqueta": "",
            "tema": "Matemáticas – cálculo y álgebra", "objetivo": "Repasar derivadas y hacer el ejercicio 11",
            "modo": "estudio", "bloque_s": 23 * 60, "bloque_total_s": 50 * 60, "foco_pct": 86, "distracciones": 2,
            "total_min": 120, "estudiado_s": 73 * 60, "plan_texto": ""}
    orbes = [  # (estado, etiqueta, texto debajo, oído, hablando, nivel)
        ("sin_sesion", "", "En reposo", "inactivo", False, 0.15),
        ("sin_sesion", "", "Hablando", "inactivo", True, 0.9),
        ("enfocado", "Estudiando", "", "inactivo", False, 0.15),
        ("distraido", "Procrastinando", "", "inactivo", False, 0.4),
        ("ausente", "Ausente", "", "inactivo", False, 0.15),
        ("enfocado", "Estudiando", "Escuchándote", "escuchando", False, 0.7),
    ]
    lienzo = QPixmap(2400, 1700)
    lienzo.setDevicePixelRatio(2)
    lienzo.fill(QColor("#334155"))
    p = QPainter(lienzo)
    p.setPen(QColor("white"))
    for i, (estado, etiqueta, nota, oido, hablando, nivel) in enumerate(orbes):
        datos = {**base, "estado": estado, "etiqueta": etiqueta, "sesion": estado != "sin_sesion"}
        if estado == "sin_sesion":
            datos.update(bloque_total_s=0, bloque_s=0)
        ui._estado(datos)
        ui.orbe.oido, ui.orbe.hablando = oido, hablando
        ui.orbe._fase, ui.orbe._nivel = 1.3 + i, nivel
        p.drawPixmap(QPoint(20 + i * 135, 20), ui.orbe.grab())
        if nota:
            p.drawText(10 + i * 135, 150, 148, 20, Qt.AlignmentFlag.AlignCenter, nota)
    ui.orbe.oido, ui.orbe.hablando = "inactivo", False

    d = {**base, "estado": "distraido", "nombre_estado": "Distraído", "etiqueta": "Procrastinando",
         "ventana": "Instagram", "categoria": "distraccion"}
    ui._estado(d)
    ui.panel.agregar_mensaje("Jarvis, voy a estudiar mates 50 minutos", "tu")
    ui.panel.agregar_mensaje("Listo: Matemáticas, 50 minutos. Faltan 5 días para el parcial.", "jarvis")
    ui.panel.agregar_mensaje("Instagram no estaba en el plan. Vuelve a las derivadas.", "jarvis")
    ui.panel.actualizar(d)
    ui.panel.show()
    ui.panel.adjustSize()
    app.processEvents()
    p.drawPixmap(QPoint(20, 190), ui.panel.grab())

    ui.jarvis.abrir_vista_camara = lambda: None   # no encender la cámara de verdad
    ui.jarvis.cuadro_camara = cuadro_de_mentira
    vista_info = {"estado": "distraido", "etiqueta": "Procrastinando", "sesion": True, "tema": "Matemáticas",
                  "mirada": "Mirando la pantalla", "camara_ok": True, "celular": False, "ojos_cerrados": False,
                  "vertical": 0.02, "horizontal": -0.04, "ventana": "Instagram", "categoria": "distraccion",
                  "inactivo_s": 1.0}
    ui.abrir_vista()
    ui.vista.resize(600, 640)
    app.processEvents()
    ui.vista.actualizar(vista_info)
    app.processEvents()
    ui.vista.actualizar(vista_info)
    p.drawPixmap(QPoint(420, 190), ui.vista.grab())
    p.drawPixmap(QPoint(1040, 190), emblema(128))
    p.drawText(1030, 330, 150, 20, Qt.AlignmentFlag.AlignCenter, "Icono")
    p.end()
    salida = config.RAIZ / "docs" / "interfaz.png"
    salida.parent.mkdir(exist_ok=True)
    lienzo.save(str(salida))
    print("Imagen:", salida)
    ui.vista.hide()
    ui.jarvis.salir()


if __name__ == "__main__":
    main()
