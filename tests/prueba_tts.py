"""Diagnóstico de edge-tts: qué frases fallan y con qué voces.

Uso:  .venv\\Scripts\\python.exe tests\\prueba_tts.py
"""
import asyncio
import sys
import time
from pathlib import Path

import edge_tts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FRASES = [
    "Hola.",
    "Volví. Seguimos con tu sesión de Control desde cero.",
    "No logro conectarme con mi cerebro ahora mismo. Revisa que FreeLLMAPI esté corriendo.",
    "Es la capacidad de un sistema para llevar su estado desde cualquier punto inicial hasta el deseado.",
]


async def probar(texto: str, voz: str, velocidad: str) -> str:
    t0 = time.time()
    audio = 0
    try:
        async for trozo in edge_tts.Communicate(texto, voz, rate=velocidad).stream():
            if trozo["type"] == "audio":
                audio += len(trozo["data"])
        return f"ok {audio // 1024} KB en {time.time() - t0:.1f} s"
    except Exception as e:  # noqa: BLE001
        return f"FALLA ({type(e).__name__}: {e}) en {time.time() - t0:.1f} s"


async def main() -> None:
    print("edge-tts", edge_tts.__version__ if hasattr(edge_tts, "__version__") else "")
    for voz, velocidad in (("es-MX-JorgeNeural", "+8%"), ("es-MX-JorgeNeural", "+0%"), ("es-CO-GonzaloNeural", "+0%")):
        for frase in FRASES:
            print(f"{voz} {velocidad:>4} | {await probar(frase, voz, velocidad)} | {frase[:45]}")


if __name__ == "__main__":
    asyncio.run(main())
