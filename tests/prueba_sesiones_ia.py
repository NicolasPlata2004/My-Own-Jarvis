"""Prueba en vivo: pedidos de sesión con frases raras que solo entiende la IA (base temporal, sin voz).

Uso:  .venv\\Scripts\\python.exe tests\\prueba_sesiones_ia.py
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

FRASES = [
    "¿Me armas algo cortico para estudiar, como tres cuartos, con pausitas de cinco?",
    "Hoy quiero darle duro a física un par de horas, pero que me dejes respirar cada 40.",
    "Uy, mejor que dure veinte más.",
]


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
    db = BaseDatos(Path(tempfile.mkdtemp()) / "s.db")
    app.BaseDatos = lambda: db
    app.generar_informe = lambda *a, **k: None
    ui = UI()
    j = app.Jarvis(cfg, ui)
    for frase in FRASES:
        antes = len(ui.dicho)
        t0 = time.time()
        j.texto_usuario(frase)
        limite = time.time() + 40
        while len(ui.dicho) == antes + 0 and time.time() < limite:
            time.sleep(0.2)
        time.sleep(1.5)   # deja que lleguen la confirmación de la acción y el saludo del coach
        s = j.sesion
        plan = f"{s.tema[:25]} | {j.describir_plan()}" if s else "sin sesión"
        print(f"\nTú: {frase}\n  ({time.time() - t0:.1f} s) Jarvis: " + " / ".join(ui.dicho[antes:])
              + f"\n  → sesión: {plan}")


if __name__ == "__main__":
    main()
