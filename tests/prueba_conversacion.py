"""Prueba en vivo: preguntas a la IA, «qué estoy viendo» y clasificación (base de datos temporal, sin voz).

Uso:  .venv\\Scripts\\python.exe tests\\prueba_conversacion.py
"""
import copy
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jarvis.app as app  # noqa: E402
from jarvis import config  # noqa: E402
from jarvis.db import BaseDatos  # noqa: E402
from jarvis.intents import detectar  # noqa: E402
from jarvis.screen import Ventana  # noqa: E402


class UI:
    def __init__(self):
        self.dicho = []

    def mensaje(self, texto, quien):
        if quien == "jarvis":
            self.dicho.append(texto)

    def estado(self, d):
        pass

    def oido(self, e):
        pass

    def hablando(self, b):
        pass


def main() -> None:
    cfg = copy.deepcopy(config.cargar())
    cfg["voz"]["activar"] = False
    cfg["camara"]["activar"] = False
    db = BaseDatos(Path(tempfile.mkdtemp()) / "conv.db")
    app.BaseDatos = lambda: db
    ui = UI()
    j = app.Jarvis(cfg, ui)
    j.tick()  # una lectura real de la pantalla

    for frase in ("¿Qué ventanas tengo abiertos, Jarvis?", "Jarvis, ahorita en ese momento, ¿qué estás viendo?"):
        t0 = time.time()
        j.ejecutar(detectar(frase, cfg["temas"]), frase)
        print(f"[{time.time() - t0:.2f} s] {frase}\n   → {ui.dicho[-1]}")

    for frase in ("¿Qué es una derivada, en dos frases?", "Dame un consejo rápido para no distraerme."):
        t0 = time.time()
        j._conversar(frase)
        print(f"[{time.time() - t0:.1f} s, {j.llm.ultimo_modelo}] {frase}\n   → {ui.dicho[-1]}")

    tema = next(t["nombre"] for t in cfg["temas"] if "Matemáticas" in t["nombre"])
    j.clasif.preparar(tema, "")
    titulos = ["Los 10 mejores goles de la Champions - YouTube - Google Chrome",
               "integral por partes ejemplos - Buscar con Google - Google Chrome",
               "Ofertas de zapatillas Nike - Google Chrome"]
    for t in titulos:
        j.clasif.clasificar(Ventana(t, "chrome.exe", 1))
    limite = time.time() + 40
    while j.clasif._pendientes and time.time() < limite:
        time.sleep(0.3)
    for t in titulos:
        v = j.clasif.clasificar(Ventana(t, "chrome.exe", 1))
        print(f"   {v.categoria:<12} [{v.fuente}] {t[:55]} ({v.razon})")

    from jarvis.musica import _spotify
    print("Spotify (abierto, título):", _spotify())


if __name__ == "__main__":
    main()
