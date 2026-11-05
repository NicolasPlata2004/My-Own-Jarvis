"""Genera jarvis.ico (el Jarvis de onda, en varios tamaños) para los accesos directos.

    .venv\\Scripts\\python.exe generar_icono.py
"""
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jarvis.ui import emblema  # noqa: E402


def main() -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - QPixmap necesita la aplicación
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    emblema(256).save(buffer, "PNG")
    imagen = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")
    imagen.save(RAIZ / "jarvis.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    imagen.save(RAIZ / "docs" / "icono.png")
    print("jarvis.ico listo")


if __name__ == "__main__":
    main()
