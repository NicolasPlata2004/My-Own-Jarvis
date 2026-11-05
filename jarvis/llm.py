"""Cliente de FreeLLMAPI (API compatible con OpenAI en 127.0.0.1:3001).

La clave unificada se busca, en este orden, en la variable FREELLMAPI_KEY, en
datos/jarvis.env y en la propia base de FreeLLMAPI (solo lectura). Así no hay
que copiarla a mano ni guardarla en otro sitio.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import httpx

from .config import DATOS

log = logging.getLogger("jarvis.llm")

_PENSAMIENTO = re.compile(r"<think>.*?</think>", re.S | re.I)


class ErrorLLM(Exception):
    pass


def extraer_json(texto: str) -> dict:
    """Saca el primer objeto JSON de una respuesta (los modelos a veces lo envuelven)."""
    texto = _PENSAMIENTO.sub("", texto).strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.M).strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise ErrorLLM(f"sin JSON en la respuesta: {texto[:120]!r}")
    return json.loads(texto[inicio:fin + 1])


class ClienteLLM:
    def __init__(self, cfg: dict):
        c = cfg["llm"]
        self.url = c["url"].rstrip("/")
        self.base = self.url.rsplit("/v1", 1)[0]
        self.modelo = c["modelo"]
        self.modelo_rapido = c["modelo_rapido"]
        self.modelo_vision = c["modelo_vision"]
        self.timeout = float(c["timeout_s"])
        self.dir_freellmapi = Path(c["freellmapi_dir"])
        self.autoarranque = bool(c["arrancar_freellmapi"])
        self._clave: str | None = None
        self._proceso: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.ultimo_modelo = ""

    # --- clave ----------------------------------------------------------------
    def clave(self) -> str:
        if self._clave:
            return self._clave
        clave = os.environ.get("FREELLMAPI_KEY", "").strip()
        env = DATOS / "jarvis.env"
        if not clave and env.exists():
            for linea in env.read_text(encoding="utf-8").splitlines():
                if linea.strip().startswith("FREELLMAPI_KEY="):
                    clave = linea.split("=", 1)[1].strip().strip('"')
        if not clave:
            bd = self.dir_freellmapi / "server" / "data" / "freeapi.db"
            if bd.exists():
                con = sqlite3.connect(f"file:{bd.as_posix()}?mode=ro", uri=True)
                try:
                    fila = con.execute("SELECT value FROM settings WHERE key='unified_api_key'").fetchone()
                    clave = fila[0] if fila else ""
                finally:
                    con.close()
        if not clave:
            raise ErrorLLM("No encontré la clave de FreeLLMAPI")
        self._clave = clave
        return clave

    # --- servidor -------------------------------------------------------------
    def servidor_activo(self) -> bool:
        try:
            return httpx.get(f"{self.base}/api/ping", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def asegurar_servidor(self, espera_s: float = 60) -> bool:
        """Arranca FreeLLMAPI en segundo plano si no está corriendo."""
        with self._lock:
            if self.servidor_activo():
                return True
            if not self.autoarranque:
                return False
            node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
            script = self.dir_freellmapi / "server" / "dist" / "index.js"
            if not script.exists():
                log.error("No encuentro FreeLLMAPI en %s", script)
                return False
            salida = open(DATOS / "freellmapi.log", "a", encoding="utf-8")
            self._proceso = subprocess.Popen(
                [node, str(script)], cwd=self.dir_freellmapi, stdout=salida, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            log.info("FreeLLMAPI arrancado (pid %s)", self._proceso.pid)
            limite = time.time() + espera_s
            while time.time() < limite:
                time.sleep(0.5)
                if self.servidor_activo():
                    return True
            return False

    def apagar_servidor(self) -> None:
        """Solo apaga FreeLLMAPI si lo arrancó Jarvis."""
        if self._proceso and self._proceso.poll() is None:
            self._proceso.terminate()

    # --- llamadas ---------------------------------------------------------------
    def _post(self, ruta: str, **kwargs) -> httpx.Response:
        cabeceras = {"Authorization": f"Bearer {self.clave()}"}
        try:
            return httpx.post(f"{self.url}{ruta}", headers=cabeceras, **kwargs)
        except httpx.ConnectError:
            if not self.asegurar_servidor():
                raise ErrorLLM("FreeLLMAPI no está corriendo")
            return httpx.post(f"{self.url}{ruta}", headers=cabeceras, **kwargs)

    def chat(self, mensajes: list[dict], *, rapido: bool = False, json_mode: bool = False,
             max_tokens: int = 300, temperatura: float = 0.7, timeout: float | None = None,
             modelo: str | None = None) -> str:
        modelo = modelo or (self.modelo_rapido if rapido else self.modelo)
        cuerpo = {"model": modelo, "messages": mensajes, "max_tokens": max_tokens, "temperature": temperatura}
        if json_mode:
            cuerpo["response_format"] = {"type": "json_object"}
        try:
            r = self._post("/chat/completions", json=cuerpo, timeout=timeout or self.timeout)
        except httpx.HTTPError as e:
            raise ErrorLLM(f"red: {e}") from e
        if r.status_code in (400, 404) and modelo != "auto":
            return self.chat(mensajes, json_mode=json_mode, max_tokens=max_tokens,
                             temperatura=temperatura, timeout=timeout, modelo="auto")
        if r.status_code >= 400:
            raise ErrorLLM(f"HTTP {r.status_code}: {r.text[:200]}")
        self.ultimo_modelo = r.headers.get("x-routed-via", "")
        texto = r.json()["choices"][0]["message"].get("content") or ""
        return _PENSAMIENTO.sub("", texto).strip()

    def chat_json(self, mensajes: list[dict], **kwargs) -> dict:
        return extraer_json(self.chat(mensajes, json_mode=True, **kwargs))

    def transcribir(self, wav: bytes, idioma: str = "es") -> str:
        """Plan B si Whisper local falla: transcripción por FreeLLMAPI (Groq/Cloudflare)."""
        r = self._post("/audio/transcriptions", timeout=60,
                       files={"file": ("voz.wav", wav, "audio/wav")},
                       data={"model": "whisper-large-v3-turbo", "language": idioma})
        if r.status_code >= 400:
            raise ErrorLLM(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json().get("text", "").strip()
