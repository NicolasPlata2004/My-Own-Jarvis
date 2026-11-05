"""Mide cuánto tarda en dibujarse el orbe (para que en reposo no gaste procesador).

Uso:  .venv\\Scripts\\python.exe tests\\prueba_rendimiento_orbe.py
"""
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jarvis.ui import dibujar_jarvis  # noqa: E402


def main() -> None:
    app = QApplication([])  # noqa: F841
    for escala in (1.0, 1.5):
        lado = int(128 * escala)
        imagen = QImage(lado, lado, QImage.Format.Format_ARGB32_Premultiplied)
        n = 300
        t0 = time.perf_counter()
        for i in range(n):
            imagen.fill(0)
            p = QPainter(imagen)
            dibujar_jarvis(p, QPointF(lado / 2, lado / 2), 46 * escala, QColor("#22d3ee"), i * 0.05, 0.15)
            p.end()
        ms = (time.perf_counter() - t0) / n * 1000
        print(f"escala {escala}: {ms:.2f} ms por cuadro → {ms * 16 / 10:.1f} % de un núcleo a 16 fps")


if __name__ == "__main__":
    main()
