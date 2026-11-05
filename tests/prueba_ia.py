"""Prueba con IA real (necesita FreeLLMAPI; Jarvis lo arranca si está apagado).

Uso:  .venv\\Scripts\\python.exe tests\\prueba_ia.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import config  # noqa: E402
from jarvis.classifier import Clasificador  # noqa: E402
from jarvis.coach import Coach  # noqa: E402
from jarvis.db import BaseDatos  # noqa: E402
from jarvis.llm import ClienteLLM  # noqa: E402
from jarvis.screen import Ventana  # noqa: E402


def main() -> None:
    cfg = config.cargar()
    llm = ClienteLLM(cfg)
    t0 = time.time()
    print("FreeLLMAPI listo:", llm.asegurar_servidor(), f"({time.time() - t0:.1f} s)")

    db = BaseDatos(Path(tempfile.mkdtemp()) / "ia.db")
    tema = next(t["nombre"] for t in cfg["temas"] if "Matemáticas" in t["nombre"])
    c = Clasificador(cfg, db, llm)
    c.preparar(tema, "repasar derivadas")
    titulos = [
        ("Derivadas parciales - clase - YouTube - Google Chrome", "chrome.exe"),
        ("Los 10 mejores goles de la Champions - YouTube - Google Chrome", "chrome.exe"),
        ("integral por partes ejemplos - Buscar con Google - Google Chrome", "chrome.exe"),
        ("Correo de la universidad - Google Chrome", "chrome.exe"),
        ("Mercado de pulgas este fin de semana - Google Chrome", "chrome.exe"),
    ]
    for titulo, proceso in titulos:
        c.clasificar(Ventana(titulo, proceso, 1))
    limite = time.time() + 60
    while c._pendientes and time.time() < limite:
        time.sleep(0.5)
    for titulo, proceso in titulos:
        v = c.clasificar(Ventana(titulo, proceso, 1))
        print(f"  {v.categoria:<12} [{v.fuente}] {titulo[:60]}  ({v.razon})")

    dichos = []
    coach = Coach(cfg, llm, lambda texto, prioridad, valido=None: dichos.append(texto),
                  lambda: {"tema": tema, "objetivo": "repasar derivadas", "plan": 50, "descanso": 10,
                           "hoy_min": 35, "meta_min": 180, "foco_pct": 72})
    t0 = time.time()
    coach._generar_y_decir("distraido", 2, {"titulo": "Instagram", "razon": "red social", "min": 3}, None, 5)
    print(f"Regaño nivel 2 ({time.time() - t0:.1f} s, {llm.ultimo_modelo}): {dichos[-1]}")
    t0 = time.time()
    coach._generar_y_decir("inicio", 1, {}, None, 2)
    print(f"Inicio de sesión ({time.time() - t0:.1f} s, {llm.ultimo_modelo}): {dichos[-1]}")

    t0 = time.time()
    r = llm.chat_json([
        {"role": "system", "content": coach.persona() + ' Responde SOLO JSON: {"respuesta": "...", "accion": null}'},
        {"role": "user", "content": "Jarvis, ¿qué es una integral, en una frase?"}],
        max_tokens=300, timeout=40)
    print(f"Pregunta ({time.time() - t0:.1f} s, {llm.ultimo_modelo}): {r.get('respuesta')}")


if __name__ == "__main__":
    main()
