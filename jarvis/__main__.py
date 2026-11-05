"""Arranque: python -m jarvis (o el acceso directo / Jarvis.cmd)."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import DATOS, cargar


def main() -> int:
    DATOS.mkdir(parents=True, exist_ok=True)
    # Con pythonw no hay consola: lo que las librerías escriban va al registro.
    if sys.stdout is None or sys.stderr is None:
        salida = open(DATOS / "salida.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or salida
        sys.stderr = sys.stderr or salida
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(DATOS / "jarvis.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")])
    # Whisper anota cada frase que revisa (también las de fondo); lo útil ya lo anota Jarvis («Oí: …»).
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    sys.excepthook = lambda tipo, valor, tb: logging.getLogger("jarvis").critical(
        "Error no controlado", exc_info=(tipo, valor, tb))
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("GLOG_minloglevel", "2")
    from .ui import ejecutar
    return ejecutar(cargar(), sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
