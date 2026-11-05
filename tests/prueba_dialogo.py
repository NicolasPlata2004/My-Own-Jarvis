"""Dibuja el diálogo «nueva sesión» (con el botón «2 horas» pulsado) en una imagen.

Uso:  .venv\\Scripts\\python.exe tests\\prueba_dialogo.py [ruta.png]
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
os.environ["QT_SCALE_FACTOR"] = "2"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from jarvis import config  # noqa: E402
from jarvis.ui import DialogoSesion  # noqa: E402


def main() -> None:
    app = QApplication([])
    app.setFont(QFont("Segoe UI", 9))
    dialogo = DialogoSesion(config.cargar())
    next(b for b in dialogo.findChildren(QPushButton) if b.text() == "2 horas").click()
    dialogo.show()
    app.processEvents()
    print("datos:", dialogo.datos())
    salida = sys.argv[1] if len(sys.argv) > 1 else str(config.DATOS / "dialogo.png")
    dialogo.grab().save(salida)
    print("Imagen:", salida)


if __name__ == "__main__":
    main()
