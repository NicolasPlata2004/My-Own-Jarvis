"""Voz de Jarvis: hablar (voces de Microsoft), escuchar siempre (sin botón) y entender «Jarvis».

El micrófono se procesa en el PC: el detector de voz, «hey Jarvis» (openWakeWord) y la
transcripción (Whisper) corren aquí. Solo si Whisper falla se usa la de FreeLLMAPI.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import hashlib
import io
import logging
import os
import queue
import re
import subprocess
import threading
import time
import wave
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import psutil

from .config import CACHE_VOZ
from .intents import normalizar

log = logging.getLogger("jarvis.voz")

FS = 16000
BLOQUE = 1280  # 80 ms, el tamaño que espera openWakeWord
# Contexto para Whisper: las órdenes típicas y el vocabulario del curso (mejora las frases cortas).
VOCABULARIO = ("Jarvis, pon música. Pausa la música. Siguiente canción. Pausa. Sigamos. Descanso. "
               "¿Qué estoy viendo? ¿Cómo voy? Sesión de 30 minutos. Dame 10 minutos más. ¿Cuánto me falta?")
ALUCINACIONES = ("subtítulos realizados por", "subtitulos realizados por", "amara.org", "gracias por ver",
                 "suscríbete", "suscribete")


def preparar_cuda() -> bool:
    """faster-whisper necesita los DLL de cuDNN/cuBLAS de los paquetes pip nvidia-*."""
    try:
        import nvidia
    except ImportError:
        return False
    encontrado = False
    for base in (Path(p) for p in getattr(nvidia, "__path__", [])):
        for sub in ("cudnn/bin", "cublas/bin", "cuda_nvrtc/bin", "cuda_runtime/bin"):
            d = base / sub
            if d.is_dir():
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                encontrado = True
    return encontrado


def limpiar_transcripcion(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto).strip()
    bajo = texto.lower()
    if any(a in bajo for a in ALUCINACIONES) or bajo.strip(" .¡!") in ("", "gracias", "ok", "eh", "mm"):
        return ""
    return texto


def wav_bytes(audio: np.ndarray, fs: int = FS) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# =============================================================================
#  Hablar
# =============================================================================
class Voz(threading.Thread):
    def __init__(self, cfg: dict, al_hablar):
        super().__init__(name="voz", daemon=True)
        v = cfg["voz"]
        self.voz = v["voz_tts"]
        self.velocidad = v["velocidad"]
        self.al_hablar = al_hablar
        self.hablando = threading.Event()
        self._cola: queue.PriorityQueue = queue.PriorityQueue()
        self._n = 0
        self._lock = threading.Lock()
        self._envolvente: np.ndarray | None = None   # volumen de lo que está diciendo, cada 30 ms
        self._t_inicio = 0.0
        self._limpiar_cache()

    def nivel(self) -> float:
        """Volumen (0–1) de lo que suena ahora mismo: hace bailar la onda del orbe."""
        env = self._envolvente
        if env is None or not self.hablando.is_set():
            return 0.0
        i = int((time.monotonic() - self._t_inicio) / 0.03)
        return float(env[i]) if 0 <= i < len(env) else 0.0

    def decir(self, texto: str, prioridad: int = 5, valido=None) -> None:
        with self._lock:
            self._n += 1
            self._cola.put((prioridad, self._n, time.time(), texto, valido))

    def callar(self) -> None:
        import sounddevice as sd
        while not self._cola.empty():
            try:
                self._cola.get_nowait()
            except queue.Empty:
                break
        sd.stop()

    def detener(self) -> None:
        self.decir(None, prioridad=99)  # type: ignore[arg-type]

    def run(self) -> None:
        import sounddevice as sd
        while True:
            prioridad, _, ts, texto, valido = self._cola.get()
            if texto is None:
                break
            if prioridad >= 5 and time.time() - ts > 45:   # un regaño viejo ya no sirve
                continue
            if valido and not valido():
                continue
            datos = fs = None
            for intento in (1, 2, 3):  # el servicio de voces falla al azar ~1 de cada 6 veces
                try:
                    datos, fs = self._sintetizar(texto)
                    break
                except Exception as e:  # noqa: BLE001
                    log.info("edge-tts falló (intento %d): %s", intento, e)
                    time.sleep(0.4 * intento)
            if datos is None:
                log.warning("edge-tts falló 3 veces; uso la voz de Windows")
                self._decir_windows(texto)
                continue
            if valido and not valido():
                continue
            mono = datos if datos.ndim == 1 else datos.mean(axis=1)
            paso = max(1, int(fs * 0.03))
            n = len(mono) // paso
            if n:
                rms = np.sqrt(np.mean(mono[: n * paso].reshape(n, paso) ** 2, axis=1))
                self._envolvente = np.clip(rms / (rms.max() + 1e-6), 0.0, 1.0)
            self._t_inicio = time.monotonic()
            self.hablando.set()
            self.al_hablar(True)
            try:
                sd.play(datos, fs)
                sd.wait()
            except Exception:  # noqa: BLE001
                log.exception("No pude reproducir audio")
            finally:
                self.hablando.clear()
                self.al_hablar(False)

    def sintetizar_a_archivo(self, texto: str) -> Path:
        import edge_tts

        clave = hashlib.sha1(f"{self.voz}|{self.velocidad}|{texto}".encode()).hexdigest()
        ruta = CACHE_VOZ / f"{clave}.mp3"
        if ruta.exists():
            return ruta

        async def generar():
            try:
                import socket

                import aiohttp
                conector = aiohttp.TCPConnector(family=socket.AF_INET)
                com = edge_tts.Communicate(texto, self.voz, rate=self.velocidad, connector=conector)
            except TypeError:
                com = edge_tts.Communicate(texto, self.voz, rate=self.velocidad)
            await com.save(str(ruta) + ".part")

        asyncio.run(generar())
        Path(str(ruta) + ".part").replace(ruta)
        return ruta

    def precalentar(self, frases: list[str]) -> None:
        """Deja en caché las frases fijas: luego suenan al instante y sin depender de la red."""
        for frase in frases:
            for _ in range(3):
                try:
                    self.sintetizar_a_archivo(frase)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(1)

    def _sintetizar(self, texto: str):
        import soundfile as sf
        return sf.read(str(self.sintetizar_a_archivo(texto)), dtype="float32")

    def _decir_windows(self, texto: str) -> None:
        orden = ("[Console]::InputEncoding=[Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; "
                 "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                 "try { $s.SelectVoice('Microsoft Helena Desktop') } catch {}; $s.Speak([Console]::In.ReadToEnd())")
        self.hablando.set()
        self.al_hablar(True)
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", orden], input=texto.encode("utf-8"),
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
        except Exception:  # noqa: BLE001
            log.exception("Tampoco funcionó la voz de Windows")
        finally:
            self.hablando.clear()
            self.al_hablar(False)

    @staticmethod
    def _limpiar_cache(dias: int = 14) -> None:
        limite = time.time() - dias * 86400
        for f in CACHE_VOZ.glob("*.mp3"):
            if f.stat().st_mtime < limite:
                f.unlink(missing_ok=True)


# =============================================================================
#  Escuchar
# =============================================================================
DUR = BLOQUE / FS  # 0,08 s por bloque
_CLAVES = {"jarvis", "yarvis", "llarvis", "harvis", "jarbis", "yarbis", "charvis", "jervis", "jarvi", "jarvys",
           "garvis", "jarviz", "yarviz", "jarbiz", "harbis", "chervis", "yervis", "jarvies"}
_SALUDO_FINAL = re.compile(r"(?:\b(?:hey|ey|hei|oye|oie|ok|okey|okay|hola)\b[\s,.!¡]*)+$", re.I)


def _parece_jarvis(palabra: str) -> bool:
    t = re.sub(r"[^a-z]", "", normalizar(palabra))
    return len(t) >= 4 and (t in _CLAVES or SequenceMatcher(None, t, "jarvis").ratio() >= 0.75)


def separar_palabra_clave(texto: str) -> tuple[bool, str]:
    """¿Le hablan a Jarvis? y lo que le dicen, sin el «Jarvis»:
    «Jarvis, ¿qué hora es?» y «¿Qué hora es, Jarvis?» → (True, «¿qué hora es?»)."""
    palabras = list(re.finditer(r"\S+", texto))
    for i, m in enumerate(palabras):
        candidatos = [(m.start(), m.end(), m.group())]
        letras = re.sub(r"\W", "", m.group())
        if i + 1 < len(palabras) and len(letras) <= 4 and len(re.sub(r"\W", "", palabras[i + 1].group())) <= 4:
            candidatos.append((m.start(), palabras[i + 1].end(), m.group() + palabras[i + 1].group()))  # «jar vis»
        for inicio, fin, candidato in candidatos:
            if _parece_jarvis(candidato):
                antes = _SALUDO_FINAL.sub("", texto[:inicio]).strip(" ,.;:!¡")
                despues = texto[fin:].strip(" ,.;:!¡")
                return True, f"{antes} {despues}".strip()
    return False, texto


def _crear_tono() -> tuple[np.ndarray, int]:
    fs = 24000
    t = np.arange(int(0.07 * fs)) / fs
    notas = [np.sin(2 * np.pi * f * t) * np.hanning(len(t)) for f in (880, 1320)]
    return (np.concatenate(notas) * 0.18).astype(np.float32), fs


TONO, FS_TONO = _crear_tono()


class Oido(threading.Thread):
    """Siempre escuchando, sin botón. Un detector de voz (Silero) corta lo que oye en frases y:
    - «hey Jarvis» lo reconoce al instante openWakeWord (funciona con tu voz);
    - «Jarvis» al inicio o al final de una frase lo reconoce Whisper (modelo pequeño) frase a frase.
    La orden puede ir en la misma frase («Jarvis, pon música»); si solo dices «Jarvis», suena un
    tono y espera tu orden. Whisper va en la GPU con el portátil enchufado y en el procesador con
    batería (whisper_dispositivo = "auto")."""

    PAUSA_REVISION_CPU = 2.5   # en el procesador, no revisar frases más seguido que esto

    def __init__(self, cfg: dict, voz: Voz | None, llm, al_texto, al_estado):
        super().__init__(name="oido", daemon=True)
        v = cfg["voz"]
        self.palabra_clave = bool(v["palabra_clave"])
        self.umbral = float(v["umbral_palabra_clave"])
        self.modelo_comando = v["whisper_modelo"]
        self.modelo_clave = v.get("whisper_modelo_clave", "base")
        self.dispositivo_cfg = v["whisper_dispositivo"]
        self.max_s = float(v["max_grabacion_s"])
        self.voz, self.llm = voz, llm
        self.al_texto, self.al_estado = al_texto, al_estado
        self.error = ""
        self.dispositivo = ""
        self.nivel_mic = 0.0
        self._cola: queue.Queue = queue.Queue(maxsize=800)
        self._escuchar = threading.Event()
        self._parar = threading.Event()
        self._modelos: dict = {}
        self._listo = threading.Event()
        self._cargando = False
        self._vad = None
        self._oww = None
        self._previo: deque = deque(maxlen=8)   # 0,64 s antes de que empiece la voz
        self._frase: list[np.ndarray] = []
        self._voz_s = 0.0
        self._silencio = 0.0
        self._para_mi = False
        self._ultima_revision = 0.0
        self._ultimo_chequeo_energia = 0.0

    # --- control --------------------------------------------------------------
    def escuchar_ahora(self) -> None:
        self._escuchar.set()

    def detener(self) -> None:
        self._parar.set()

    def _entrada(self, datos, frames, info, estado) -> None:
        try:
            self._cola.put_nowait(datos[:, 0].copy())
        except queue.Full:
            pass

    def preparar_detectores(self) -> None:
        try:
            from openwakeword.vad import VAD
            self._vad = VAD()
        except Exception as e:  # noqa: BLE001
            log.warning("Sin detector de voz (%s); uso el volumen", e)
        if self.palabra_clave:
            try:
                from openwakeword.model import Model
                self._oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            except Exception as e:  # noqa: BLE001
                log.warning("Sin «hey Jarvis» de openWakeWord (%s)", e)

    def run(self) -> None:
        threading.Thread(target=self._cargar_modelos, args=(self._dispositivo_deseado(),), name="whisper",
                         daemon=True).start()
        self.preparar_detectores()
        import sounddevice as sd
        try:
            with sd.InputStream(samplerate=FS, channels=1, dtype="int16", blocksize=BLOQUE, callback=self._entrada):
                log.info("Micrófono abierto: escuchando «Jarvis»")
                self.bucle()
        except Exception as e:  # noqa: BLE001
            log.exception("Micrófono")
            self.error = str(e)

    def bucle(self) -> None:
        callado_hasta = 0.0
        while not self._parar.is_set():
            try:
                bloque = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            if bloque is None:  # fin de una prueba
                break
            ahora = time.time()
            if ahora - self._ultimo_chequeo_energia > 120:
                self._ultimo_chequeo_energia = ahora
                self._revisar_dispositivo()
            if self.voz and self.voz.hablando.is_set():
                callado_hasta = ahora + 0.7   # no escucharse a sí mismo
                self._reiniciar_frase()
                continue
            if self._escuchar.is_set():   # botón o atajo de teclado
                self._escuchar.clear()
                self._reiniciar_frase()
                self._tono()
                self._escuchar_orden()
                continue
            if ahora < callado_hasta:
                continue
            self.procesar_bloque(bloque)

    # --- frases -------------------------------------------------------------------
    def _prob_voz(self, bloque: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(bloque.astype(np.float32) ** 2)))
        self.nivel_mic = min(1.0, rms / 2500)   # para que la onda del orbe siga tu voz
        if self._vad is not None:
            return float(self._vad.predict(bloque, frame_size=640))
        return 1.0 if rms > 500 else 0.0

    def _reiniciar_frase(self) -> None:
        self._frase, self._voz_s, self._silencio, self._para_mi = [], 0.0, 0.0, False
        self._previo.clear()

    def procesar_bloque(self, bloque: np.ndarray) -> None:
        voz = self._prob_voz(bloque)
        if self._oww is not None and not self._para_mi:
            puntaje = max(self._oww.predict(bloque).values())
            if puntaje >= self.umbral:
                log.info("«Hey Jarvis» (%.2f)", puntaje)
                self._oww.reset()
                self._para_mi = True
                if not self._frase:
                    self._frase = list(self._previo)
                self.al_estado("escuchando")
                self._tono()
        if not self._frase:
            self._previo.append(bloque)
            if voz >= 0.5:
                self._frase, self._voz_s, self._silencio = list(self._previo), DUR, 0.0
            return
        self._frase.append(bloque)
        if voz >= 0.35:
            self._voz_s += DUR
            self._silencio = 0.0
        else:
            self._silencio += DUR
        duracion = len(self._frase) * DUR
        if self._silencio >= (1.0 if self._para_mi else 0.5) or duracion >= (self.max_s if self._para_mi else 12):
            frase, para_mi, voz_s = self._frase, self._para_mi, self._voz_s
            self._reiniciar_frase()
            self._cerrar_frase(frase, para_mi, voz_s)

    def _cerrar_frase(self, frase: list[np.ndarray], para_mi: bool, voz_s: float) -> None:
        if voz_s < 0.25 and not para_mi:
            return
        audio = np.concatenate(frase).astype(np.float32) / 32768.0
        duracion = len(audio) / FS
        if not para_mi:
            if not self._puedo_revisar(duracion):
                return
            # La frase entera si es corta (el «Jarvis» puede ir al final); si es larga, el comienzo.
            pista = self._transcribir(audio if duracion <= 6 else audio[: 3 * FS], "clave")
            if not separar_palabra_clave(pista)[0]:
                log.debug("Frase de %.1f s sin «Jarvis»: %r", duracion, pista)
                return
            log.info("«Jarvis» en: %r", pista)
            self.al_estado("escuchando")
        self.al_estado("procesando")
        texto = self.transcribir(audio)
        encontrado, resto = separar_palabra_clave(texto)
        if not encontrado and para_mi and len(texto.split()) <= 3:
            resto = ""   # «hey Jarvis» mal transcrito y nada más
        if len(re.sub(r"\W", "", resto)) < 2:   # solo dijo «Jarvis»: espera la orden
            if not para_mi:
                self._tono()
            self._escuchar_orden()
            return
        self.al_estado("inactivo")
        self.al_texto(resto)

    def _escuchar_orden(self) -> None:
        """Graba la orden después de «Jarvis» (o del botón): hasta 7 s para empezar a hablar."""
        self.al_estado("escuchando")
        partes: list[np.ndarray] = []
        hablo, voz_s, silencio = False, 0.0, 0.0
        while not self._parar.is_set():
            try:
                b = self._cola.get(timeout=1)
            except queue.Empty:
                break
            if b is None:
                self._cola.put(None)
                break
            partes.append(b)
            p = self._prob_voz(b)
            hablo = hablo or p >= 0.5
            if hablo:
                if p >= 0.35:
                    voz_s += DUR
                    silencio = 0.0
                else:
                    silencio += DUR
            transcurrido = len(partes) * DUR
            if (hablo and silencio >= 0.9) or (not hablo and transcurrido >= 7) or transcurrido >= self.max_s:
                break
        if not hablo or voz_s < 0.2:
            self.al_estado("inactivo")
            return
        self.al_estado("procesando")
        texto = self.transcribir(np.concatenate(partes).astype(np.float32) / 32768.0)
        self.al_estado("inactivo")
        if re.search(r"\w{2,}", texto):
            self.al_texto(separar_palabra_clave(texto)[1] or texto)

    def _puedo_revisar(self, duracion: float) -> bool:
        if self.dispositivo == "cuda":
            return True
        if duracion > 8:   # en el procesador, una narración larga no se revisa
            return False
        ahora = time.monotonic()
        if ahora - self._ultima_revision < self.PAUSA_REVISION_CPU:
            return False
        self._ultima_revision = ahora
        return True

    def _tono(self) -> None:
        def tocar():
            try:
                import sounddevice as sd
                with sd.OutputStream(samplerate=FS_TONO, channels=1, dtype="float32") as salida:
                    salida.write(TONO.reshape(-1, 1))
            except Exception:  # noqa: BLE001
                log.debug("No pude sonar el tono", exc_info=True)
        threading.Thread(target=tocar, daemon=True).start()

    # --- Whisper --------------------------------------------------------------------
    def _dispositivo_deseado(self) -> str:
        if self.dispositivo_cfg in ("cpu", "cuda"):
            return self.dispositivo_cfg
        try:
            bateria = psutil.sensors_battery()
        except Exception:  # noqa: BLE001
            bateria = None
        return "cuda" if bateria is None or bateria.power_plugged else "cpu"

    def _revisar_dispositivo(self) -> None:
        deseado = self._dispositivo_deseado()
        if self.dispositivo and deseado != self.dispositivo and not self._cargando:
            log.info("Cambió la energía: Whisper pasa a %s", deseado)
            threading.Thread(target=self._cargar_modelos, args=(deseado,), daemon=True).start()

    def _cargar_modelos(self, dispositivo: str) -> None:
        if self._cargando:
            return
        self._cargando = True
        try:
            from faster_whisper import WhisperModel

            def cargar(nombre: str, disp: str):
                tipo = "float16" if disp == "cuda" else "int8"
                try:  # la copia ya descargada: arranca aunque el PC aún no tenga red
                    return WhisperModel(nombre, device=disp, compute_type=tipo, local_files_only=True)
                except Exception:  # noqa: BLE001 - no está en caché: se descarga
                    return WhisperModel(nombre, device=disp, compute_type=tipo)

            if dispositivo == "cuda" and not preparar_cuda():
                dispositivo = "cpu"
            try:
                nuevos = {"clave": cargar(self.modelo_clave, dispositivo),
                          "comando": cargar(self.modelo_comando, dispositivo)}
                segmentos, _ = nuevos["clave"].transcribe(np.zeros(FS, np.float32), language="es")
                list(segmentos)  # los fallos de CUDA aparecen al usarlo, no al cargarlo
            except Exception as e:  # noqa: BLE001
                if dispositivo == "cpu":
                    raise
                log.warning("Whisper en la GPU falló (%s); uso el procesador", e)
                dispositivo = "cpu"
                nuevos = {"clave": cargar(self.modelo_clave, "cpu"), "comando": cargar(self.modelo_comando, "cpu")}
            self._modelos, self.dispositivo = nuevos, dispositivo
            log.info("Whisper listo en %s (%s para «Jarvis», %s para órdenes)", dispositivo, self.modelo_clave,
                     self.modelo_comando)
        except Exception:  # noqa: BLE001
            log.exception("Whisper no cargó; usaré la transcripción de FreeLLMAPI")
        finally:
            self._cargando = False
            self._listo.set()

    def _transcribir(self, audio: np.ndarray, cual: str = "comando") -> str:
        modelos = self._modelos if self._listo.wait(timeout=60) else {}
        if cual not in modelos:
            return "" if cual == "clave" else self.llm.transcribir(wav_bytes(audio))
        opciones = {"language": "es", "condition_on_previous_text": False, "without_timestamps": True}
        if cual == "clave":   # rápido: sin reintentos, empujando a que escriba «Jarvis»
            opciones.update(beam_size=1, temperature=0.0, initial_prompt="Jarvis.")
        else:                 # la orden: con más cuidado (en frases cortas casi no cuesta)
            opciones.update(beam_size=5, temperature=(0.0, 0.4), initial_prompt=VOCABULARIO, vad_filter=True)
        try:
            segmentos, _ = modelos[cual].transcribe(audio, **opciones)
            return " ".join(s.text.strip() for s in segmentos)
        except Exception as e:  # noqa: BLE001
            log.warning("Whisper (%s) falló: %s", cual, e)
            return "" if cual == "clave" else self.llm.transcribir(wav_bytes(audio))

    def transcribir(self, audio: np.ndarray) -> str:
        texto = limpiar_transcripcion(self._transcribir(audio, "comando"))
        log.info("Oí: %r", texto)
        return texto


# =============================================================================
#  Atajo de teclado global (Ctrl+Alt+J por defecto)
# =============================================================================
_MODIFICADORES = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_MOD_NOREPEAT, _WM_HOTKEY, _WM_QUIT = 0x4000, 0x0312, 0x0012


class Atajo(threading.Thread):
    def __init__(self, combinacion: str, al_pulsar):
        super().__init__(name="atajo", daemon=True)
        partes = combinacion.lower().replace(" ", "").split("+")
        self.mods = sum(_MODIFICADORES[p] for p in partes[:-1])
        tecla = partes[-1]
        if len(tecla) == 1:
            self.vk = ord(tecla.upper())
        elif tecla.startswith("f") and tecla[1:].isdigit():
            self.vk = 0x6F + int(tecla[1:])
        else:
            self.vk = {"space": 0x20, "espacio": 0x20}.get(tecla, 0x4A)
        self.al_pulsar = al_pulsar
        self._hilo_id = 0

    def run(self) -> None:
        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        self._hilo_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.mods | _MOD_NOREPEAT, self.vk):
            log.warning("No pude registrar el atajo de teclado (¿lo usa otro programa?)")
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _WM_HOTKEY:
                try:
                    self.al_pulsar()
                except Exception:  # noqa: BLE001
                    log.exception("Atajo")
        user32.UnregisterHotKey(None, 1)

    def detener(self) -> None:
        if self._hilo_id:
            ctypes.WinDLL("user32").PostThreadMessageW(self._hilo_id, _WM_QUIT, 0, 0)
