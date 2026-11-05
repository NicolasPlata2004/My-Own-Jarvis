"""Controlar la música. A Spotify se le habla directo a su ventana (WM_APPCOMMAND), para no
pausar por error el vídeo que tengas abierto: las teclas multimedia de Windows van a la app que
Windows decida. Spotify se titula «Spotify Premium/Free» en pausa y «Artista - Canción» sonando.
"""
from __future__ import annotations

import ctypes
import os
import time

from .screen import titulo_de, ventanas_del_programa

_user32 = ctypes.WinDLL("user32")
_WM_APPCOMMAND = 0x0319
_APPCOMMAND = {"alternar": 14, "siguiente": 11, "anterior": 12}
_TECLAS = {"alternar": 0xB3, "siguiente": 0xB0, "anterior": 0xB1, "subir": 0xAF, "bajar": 0xAE}
_SOLTAR = 0x0002


def _pulsar(vk: int, veces: int = 1) -> None:
    for _ in range(veces):
        _user32.keybd_event(vk, 0, 0, 0)
        _user32.keybd_event(vk, 0, _SOLTAR, 0)
        time.sleep(0.03)


def _spotify() -> int | None:
    ventanas = ventanas_del_programa("spotify.exe", "Chrome_WidgetWin")
    return ventanas[0][0] if ventanas else None


def _en_pausa(titulo: str) -> bool:
    return not titulo or titulo.lower().startswith("spotify")


def _ordenar(hwnd: int, accion: str) -> None:
    _user32.PostMessageW(hwnd, _WM_APPCOMMAND, hwnd, _APPCOMMAND[accion] << 16)


def _esperar_cambio(hwnd: int, antes: str, segundos: float = 2.0) -> str:
    limite = time.time() + segundos
    while time.time() < limite:
        time.sleep(0.2)
        ahora = titulo_de(hwnd)
        if ahora != antes:
            return ahora
    return titulo_de(hwnd)


def controlar(accion: str) -> str:
    """Ejecuta la acción y devuelve qué decirle al usuario."""
    if accion in ("subir", "bajar"):
        _pulsar(_TECLAS[accion], veces=5)
        return "Más volumen." if accion == "subir" else "Menos volumen."
    hwnd = _spotify()
    if hwnd is None and accion == "play":
        os.startfile("spotify:")
        limite = time.time() + 12
        while hwnd is None and time.time() < limite:
            time.sleep(0.5)
            hwnd = _spotify()
        if hwnd is None:
            return "No pude abrir Spotify."
        time.sleep(1.5)
    if hwnd is None:  # sin Spotify: lo que esté sonando, con las teclas multimedia
        _pulsar(_TECLAS["alternar" if accion in ("play", "pausa") else accion])
        return {"pausa": "Pausado.", "siguiente": "Siguiente.", "anterior": "La anterior."}.get(accion, "Listo.")
    titulo = titulo_de(hwnd)
    if accion == "play":
        if not _en_pausa(titulo):
            return f"Ya está sonando {titulo}."
        _ordenar(hwnd, "alternar")
        titulo = _esperar_cambio(hwnd, titulo)
        return f"Listo: {titulo}." if not _en_pausa(titulo) else "Le di play a Spotify pero no arrancó; elige una lista y vuelve a pedírmelo."
    if accion == "pausa":
        if _en_pausa(titulo):
            return "La música ya está en pausa."
        _ordenar(hwnd, "alternar")
        _esperar_cambio(hwnd, titulo)
        return "Pausada."
    if accion in ("siguiente", "anterior"):
        _ordenar(hwnd, accion)
        nuevo = _esperar_cambio(hwnd, titulo)
        return f"Ahora suena {nuevo}." if not _en_pausa(nuevo) else ("Siguiente." if accion == "siguiente" else "La anterior.")
    return ""
