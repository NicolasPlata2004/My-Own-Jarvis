"""Prueba de la escucha sin botón con audio sintético (no usa el micrófono ni suena nada).

Mete en la cola del oído varias frases con voces en español —unas le hablan a Jarvis y otras
son «narración» de fondo— y comprueba qué órdenes le llegarían a Jarvis.

Uso:  .venv\\Scripts\\python.exe tests\\prueba_escucha.py
"""
import logging
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis import config  # noqa: E402
from jarvis.voice import BLOQUE, FS, Oido, Voz  # noqa: E402

GUION = [  # (voz, frase, silencio después en s, ¿debe llegar como orden?)
    ("es-CO-GonzaloNeural", "Jarvis, ¿qué estoy viendo?", 1.2, "qué estoy viendo"),
    ("es-CO-SalomeNeural", "La transformada de Laplace convierte ecuaciones diferenciales en algebraicas.", 1.2, None),
    ("es-MX-JorgeNeural", "¿Qué hora es, Jarvis?", 1.2, "qué hora es"),
    ("es-ES-AlvaroNeural", "Jarvis.", 0.7, None),
    ("es-ES-AlvaroNeural", "Pon música.", 1.4, "pon música"),
    ("es-MX-JorgeNeural", "Hey Jarvis, pausa.", 1.4, "pausa"),
    ("es-CO-GonzaloNeural", "Vamos a ver el diagrama de Bode y el margen de fase.", 1.4, None),
]


def a_16k(ruta: Path) -> np.ndarray:
    datos, fs = sf.read(str(ruta), dtype="float32")
    if datos.ndim > 1:
        datos = datos.mean(axis=1)
    t = np.arange(0, len(datos) / fs, 1 / FS)
    return np.interp(t, np.arange(len(datos)) / fs, datos).astype(np.float32)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="   %(name)s: %(message)s")
    for ruido in ("faster_whisper", "httpx", "httpx2"):
        logging.getLogger(ruido).setLevel(logging.WARNING)
    cfg = config.cargar()
    if "--cpu" in sys.argv:   # como con el portátil desenchufado
        cfg["voz"]["whisper_dispositivo"] = "cpu"
    ordenes, estados = [], []
    oido = Oido(cfg, None, llm=None, al_texto=ordenes.append, al_estado=estados.append)
    oido._tono = lambda: None          # que no suene nada en la prueba
    oido.PAUSA_REVISION_CPU = 0
    t0 = time.time()
    oido._cargar_modelos(oido._dispositivo_deseado())
    oido.preparar_detectores()
    print(f"Modelos en {oido.dispositivo} ({time.time() - t0:.1f} s)")

    trozos = [np.zeros(FS, np.float32)]
    for voz, frase, pausa, _ in GUION:
        v = Voz({"voz": {**cfg["voz"], "voz_tts": voz, "velocidad": "+0%"}}, lambda b: None)
        trozos += [a_16k(v.sintetizar_a_archivo(frase)), np.zeros(int(pausa * FS), np.float32)]
    audio = np.concatenate(trozos)
    pcm = (audio * 32767 * 0.8).astype(np.int16)
    for i in range(0, len(pcm) - BLOQUE + 1, BLOQUE):
        oido._cola.put(pcm[i:i + BLOQUE])
    oido._cola.put(None)
    t0 = time.time()
    oido.bucle()
    print(f"\n{len(audio) / FS:.1f} s de audio procesados en {time.time() - t0:.1f} s")
    print("Órdenes que le llegaron a Jarvis:", ordenes)
    from jarvis.intents import normalizar
    esperadas = [e for *_, e in GUION if e]
    oidas = [normalizar(o) for o in ordenes]
    aciertos = sum(any(normalizar(e) in o for o in oidas) for e in esperadas)
    print(f"Órdenes bien entendidas: {aciertos}/{len(esperadas)}; "
          f"activaciones de más: {max(0, len(ordenes) - len(esperadas))}")


if __name__ == "__main__":
    main()
