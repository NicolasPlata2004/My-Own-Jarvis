"""¿Qué tan bien detecta «Jarvis» con acento hispano? Sintetiza frases con voces en español,
las pasa por openWakeWord y por Whisper (modelos tiny/base) y mide puntajes y tiempos.

Uso:  .venv\\Scripts\\python.exe tests\\prueba_palabra_clave.py
"""
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import config  # noqa: E402
from jarvis.voice import BLOQUE, FS, Voz  # noqa: E402

VOCES = ["es-CO-GonzaloNeural", "es-CO-SalomeNeural", "es-MX-JorgeNeural", "es-ES-AlvaroNeural"]
POSITIVAS = ["Jarvis.", "Jarvis, ¿qué estoy viendo?", "Hey Jarvis.", "Oye Jarvis, pon música."]
NEGATIVAS = ["Hola, ¿cómo estás?", "La transformada de Laplace convierte ecuaciones diferenciales.",
             "Vamos a ver el diagrama de Bode y el margen de fase."]


def a_16k(ruta: Path) -> np.ndarray:
    datos, fs = sf.read(str(ruta), dtype="float32")
    if datos.ndim > 1:
        datos = datos.mean(axis=1)
    t = np.arange(0, len(datos) / fs, 1 / FS)
    return np.interp(t, np.arange(len(datos)) / fs, datos).astype(np.float32)


def main() -> None:
    from faster_whisper import WhisperModel
    from openwakeword.model import Model

    cfg = config.cargar()
    detector = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    whispers = {n: WhisperModel(n, device="cpu", compute_type="int8") for n in ("tiny", "base")}
    print(f"{'voz':<22}{'frase':<44}{'oww':>6}  " + "  ".join(f"{n:<28}" for n in whispers))
    for voz in VOCES:
        v = Voz({"voz": {**cfg["voz"], "voz_tts": voz, "velocidad": "+0%"}}, lambda b: None)
        for frase in POSITIVAS + NEGATIVAS:
            audio = a_16k(v.sintetizar_a_archivo(frase))
            relleno = np.zeros(FS // 2, np.float32)
            pcm = (np.concatenate([relleno, audio, relleno]) * 32767).astype(np.int16)
            detector.reset()
            oww = max(max(detector.predict(pcm[i:i + BLOQUE]).values()) for i in range(0, len(pcm) - BLOQUE, BLOQUE))
            columnas = []
            for nombre, m in whispers.items():
                t0 = time.time()
                segs, _ = m.transcribe(audio[: int(2.0 * FS)], language="es", beam_size=1,
                                       condition_on_previous_text=False, initial_prompt="Jarvis.")
                texto = " ".join(s.text.strip() for s in segs)
                columnas.append(f"{(time.time() - t0) * 1000:4.0f} ms {texto[:18]!r:<20}")
            marca = "+" if frase in POSITIVAS else "-"
            print(f"{voz:<22}{marca} {frase[:41]:<42}{oww:6.2f}  " + "  ".join(columnas))


if __name__ == "__main__":
    main()
