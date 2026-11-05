"""Corre el núcleo de Jarvis de verdad (pantalla, cámara, IA) ~25 s, sin voz ni ventanas.

Uso:  .venv\\Scripts\\python.exe tests\\prueba_integracion.py
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import config  # noqa: E402
from jarvis.app import Jarvis  # noqa: E402


class UIConsola:
    def __init__(self):
        self.ultimo = {}

    def estado(self, d):
        self.ultimo = d

    def mensaje(self, texto, quien):
        print(f"  [{quien}] {texto}")

    def oido(self, e):
        pass

    def hablando(self, b):
        pass


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    cfg = config.cargar()
    cfg["voz"]["activar"] = False
    ui = UIConsola()
    j = Jarvis(cfg, ui)
    j.arrancar()
    time.sleep(2)
    tema = next(t["nombre"] for t in cfg["temas"] if "Matemáticas" in t["nombre"])
    j.iniciar_sesion(tema, "prueba de integración", minutos=50)
    for _ in range(5):
        time.sleep(5)
        d = ui.ultimo
        print(f"estado={d.get('estado'):<10} cámara={d.get('camara'):<10} ventana={d.get('ventana', '')[:40]!r} "
              f"→ {d.get('categoria')} | foco {d.get('foco_pct')} % | hoy {d.get('hoy_min', 0):.1f} min")
    print("Texto de estado:", j.texto_estado())
    j.terminar_sesion()
    time.sleep(12)  # deja que el coach hable del balance
    j.salir()
    filas = j.db.consultar("SELECT estado, ROUND(SUM(fin-inicio)) FROM segmentos WHERE sesion_id = "
                           "(SELECT MAX(id) FROM sesiones) GROUP BY estado")
    print("Segmentos guardados:", filas)


if __name__ == "__main__":
    main()
