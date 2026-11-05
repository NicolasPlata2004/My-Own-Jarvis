"""Prueba de la voz sin micrófono: sintetiza frases, las transcribe y prueba la palabra clave.

Uso:  .venv\\Scripts\\python.exe tests\\prueba_voz.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import config  # noqa: E402
from jarvis.voice import FS, BLOQUE, Oido, Voz  # noqa: E402


class _LLMFalso:
    def transcribir(self, wav):
        raise RuntimeError("no debería usarse")


def a_16k(ruta: Path) -> np.ndarray:
    datos, fs = sf.read(str(ruta), dtype="float32")
    if datos.ndim > 1:
        datos = datos.mean(axis=1)
    t = np.arange(0, len(datos) / fs, 1 / FS)
    return np.interp(t, np.arange(len(datos)) / fs, datos).astype(np.float32)


def main() -> None:
    cfg = config.cargar()
    voz = Voz(cfg, al_hablar=lambda b: None)
    t0 = time.time()
    frase = "Instagram no te va a pasar el examen de matemáticas. Vuelve a las derivadas."
    ruta = voz.sintetizar_a_archivo(frase)
    print(f"TTS ({cfg['voz']['voz_tts']}): {ruta.stat().st_size // 1024} KB en {time.time() - t0:.1f} s")

    oido = Oido(cfg, None, _LLMFalso(), al_texto=print, al_estado=lambda e: None)
    oido._cargar_modelos("cpu")
    t0 = time.time()
    texto = oido.transcribir(a_16k(ruta))
    print(f"Whisper {cfg['voz']['whisper_modelo']} ({time.time() - t0:.1f} s): {texto}")

    from openwakeword.model import Model
    detector = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    voz_en = Voz({"voz": {**cfg["voz"], "voz_tts": "en-US-GuyNeural", "velocidad": "+0%"}}, lambda b: None)
    for prueba in ("Hey Jarvis!", "Hola, ¿cómo vas con el ejercicio?"):
        audio = a_16k(voz_en.sintetizar_a_archivo(prueba))
        audio = np.concatenate([np.zeros(FS, np.float32), audio, np.zeros(FS, np.float32)])
        pcm = (audio * 32767).astype(np.int16)
        detector.reset()
        maximo = max(max(detector.predict(pcm[i:i + BLOQUE]).values())
                     for i in range(0, len(pcm) - BLOQUE, BLOQUE))
        print(f"Palabra clave con «{prueba}»: puntaje {maximo:.2f}")


if __name__ == "__main__":
    main()
