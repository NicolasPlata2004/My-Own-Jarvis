"""Rutas del proyecto y carga de config.toml (con valores por defecto)."""
from __future__ import annotations

import copy
import logging
import tomllib
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"
MODELOS = DATOS / "modelos"
INFORMES = DATOS / "informes"
CACHE_VOZ = DATOS / "voz_cache"
ARCHIVO_CONFIG = RAIZ / "config.toml"

log = logging.getLogger("jarvis.config")

DEFECTOS: dict = {
    "usuario": {"nombre": "Estudiante", "descripcion": "estudiante universitario", "habla": "español neutro",
                "tono": "firme"},
    "llm": {
        "url": "http://127.0.0.1:3001/v1",
        "modelo": "auto",
        "modelo_rapido": "auto:fast",
        "modelo_vision": "auto",
        "freellmapi_dir": "",
        "arrancar_freellmapi": True,
        "timeout_s": 20,
    },
    "voz": {
        "activar": True,
        "voz_tts": "es-MX-JorgeNeural",
        "velocidad": "+8%",
        "palabra_clave": True,
        "umbral_palabra_clave": 0.5,
        "tecla_hablar": "ctrl+alt+j",
        "whisper_modelo": "small",
        "whisper_modelo_clave": "base",
        "whisper_dispositivo": "auto",
        "max_grabacion_s": 15,
    },
    "camara": {"activar": True, "indice": 0, "fps": 5, "detectar_celular": True, "umbral_celular": 0.45},
    "pantalla": {"captura_para_dudas": False},
    "foco": {
        "gracia_distraccion_s": 12,
        "gracia_mensajeria_s": 90,
        "gracia_celular_s": 8,
        "gracia_ausencia_s": 45,
        "gracia_somnolencia_s": 10,
        "cabeza_abajo_es_estudio": True,
        "min_entre_avisos_s": 40,
        "escalar_cada_s": 75,
    },
    "sesion": {"duracion_min": 50, "descanso_min": 10, "meta_diaria_min": 180},
    "recordatorios": {"activar": True, "horas": ["10:00", "16:00", "20:00"]},
    "informes": {"carpeta_videos": ""},
    "temas": [],
    "apps": {"estudio": [], "herramientas": [], "mensajeria": [], "distracciones": [], "neutras": []},
    "examenes": {},
}

# Modelos de visión de MediaPipe (se bajan una vez, ~8 MB en total).
URLS_MODELOS = {
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "efficientdet_lite0.tflite":
        "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/1/efficientdet_lite0.tflite",
}


def _fusionar(base: dict, extra: dict) -> dict:
    for clave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(base.get(clave), dict):
            _fusionar(base[clave], valor)
        else:
            base[clave] = valor
    return base


def cargar(ruta: Path = ARCHIVO_CONFIG) -> dict:
    ejemplo = RAIZ / "config.ejemplo.toml"
    if ruta == ARCHIVO_CONFIG and not ruta.exists() and ejemplo.exists():
        ruta.write_text(ejemplo.read_text(encoding="utf-8"), encoding="utf-8")  # primera vez
    cfg = copy.deepcopy(DEFECTOS)
    if ruta.exists():
        with open(ruta, "rb") as f:
            _fusionar(cfg, tomllib.load(f))
    for carpeta in (DATOS, MODELOS, INFORMES, CACHE_VOZ):
        carpeta.mkdir(parents=True, exist_ok=True)
    return cfg


def modelo(nombre: str) -> Path:
    """Ruta local de un modelo de MediaPipe; lo descarga la primera vez."""
    ruta = MODELOS / nombre
    if not ruta.exists():
        log.info("Descargando %s", nombre)
        MODELOS.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(ruta.suffix + ".part")
        urllib.request.urlretrieve(URLS_MODELOS[nombre], temporal)
        temporal.replace(ruta)
    return ruta
