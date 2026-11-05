"""Qué hay en la pantalla: ventana activa, programa, archivos abiertos e inactividad.

Todo se lee con la API de Windows; no se toman capturas aquí.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32")

user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
kernel32.GetTickCount.restype = wt.DWORD
_ENUM_HIJAS = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumChildWindows.argtypes = [wt.HWND, _ENUM_HIJAS, wt.LPARAM]
user32.EnumWindows.argtypes = [_ENUM_HIJAS, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
dwmapi = ctypes.WinDLL("dwmapi")
_GW_OWNER, _GWL_EXSTYLE, _WS_EX_TOOLWINDOW, _DWMWA_CLOAKED = 4, -20, 0x80, 14

# Programas en los que vale la pena mirar qué archivo tienen abierto.
REPRODUCTORES = {
    "vlc.exe", "microsoft.media.player.exe", "mpc-hc64.exe", "mpc-hc.exe", "mpc-be64.exe",
    "potplayermini64.exe", "potplayer64.exe", "wmplayer.exe", "video.ui.exe", "sumatrapdf.exe",
    "acrobat.exe", "acrord32.exe", "foxitpdfreader.exe", "xournalpp.exe", "okular.exe",
}
EXTENSIONES_ESTUDIO = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".pdf", ".xopp", ".mp3", ".m4a"}


class _ULTIMA_ENTRADA(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def segundos_inactivo() -> float:
    """Segundos desde la última tecla o movimiento del ratón."""
    info = _ULTIMA_ENTRADA()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


@dataclass
class Ventana:
    titulo: str = ""
    proceso: str = ""          # nombre del .exe en minúsculas
    pid: int = 0
    archivos: tuple[str, ...] = ()


def _titulo(hwnd) -> str:
    largo = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(largo + 1)
    user32.GetWindowTextW(hwnd, buf, largo + 1)
    return buf.value


def _pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _pid_real_uwp(hwnd, pid_marco: int) -> int:
    """Las apps de la Tienda (p. ej. Reproductor multimedia) viven dentro de
    ApplicationFrameHost; el proceso real es el de alguna ventana hija."""
    encontrado = []

    def visitar(hija, _):
        pid = _pid(hija)
        if pid and pid != pid_marco:
            encontrado.append(pid)
            return False
        return True

    user32.EnumChildWindows(hwnd, _ENUM_HIJAS(visitar), 0)
    return encontrado[0] if encontrado else 0


def _nombre_proceso(pid: int) -> str:
    try:
        return psutil.Process(pid).name().lower()
    except (psutil.Error, ValueError):
        return ""


user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]


def ventanas_del_programa(exe: str, clase_prefijo: str = "") -> list[tuple[int, str]]:
    """(hwnd, título) de las ventanas principales de un programa, incluso ocultas (p. ej. Spotify en la bandeja)."""
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == exe.lower()}
    encontradas: list[tuple[int, str]] = []
    if not pids:
        return encontradas

    def visitar(hwnd, _):
        if _pid(hwnd) in pids:
            titulo = _titulo(hwnd).strip()
            clase = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, clase, 128)
            if titulo and clase.value.startswith(clase_prefijo):
                encontradas.append((hwnd, titulo))
        return True

    user32.EnumWindows(_ENUM_HIJAS(visitar), 0)
    return encontradas


def titulo_de(hwnd: int) -> str:
    return _titulo(hwnd).strip()


def ventanas_visibles(incluir_minimizadas: bool = True) -> list[tuple[str, str]]:
    """(título, programa) de las ventanas de la barra de tareas, de la de encima hacia abajo."""
    resultado: list[tuple[str, str]] = []
    mi_pid = os.getpid()

    def visitar(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, _GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        oculta = ctypes.c_int(0)  # ventanas «encubiertas» de apps de la Tienda en segundo plano
        dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), _DWMWA_CLOAKED, ctypes.byref(oculta), ctypes.sizeof(oculta))
        if oculta.value or (not incluir_minimizadas and user32.IsIconic(hwnd)):
            return True
        titulo = _titulo(hwnd).strip()
        pid = _pid(hwnd)
        if not titulo or titulo == "Program Manager" or pid == mi_pid:
            return True
        nombre = _nombre_proceso(pid)
        if nombre == "applicationframehost.exe":
            real = _pid_real_uwp(hwnd, pid)
            nombre = _nombre_proceso(real) if real else nombre
        if (titulo, nombre) not in resultado:
            resultado.append((titulo, nombre))
        return True

    user32.EnumWindows(_ENUM_HIJAS(visitar), 0)
    return resultado


class MonitorPantalla:
    def __init__(self):
        self._cache_archivos: dict[int, tuple[float, tuple[str, ...]]] = {}

    def ventana_activa(self) -> Ventana:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return Ventana()
        titulo = _titulo(hwnd)
        pid = _pid(hwnd)
        nombre = _nombre_proceso(pid)
        if nombre == "applicationframehost.exe":
            real = _pid_real_uwp(hwnd, pid)
            if real:
                pid, nombre = real, _nombre_proceso(real)
        return Ventana(titulo, nombre, pid, self._archivos(pid, nombre))

    def _archivos(self, pid: int, nombre: str) -> tuple[str, ...]:
        if nombre not in REPRODUCTORES:
            return ()
        ahora = time.monotonic()
        guardado = self._cache_archivos.get(pid)
        if guardado and ahora - guardado[0] < 10:
            return guardado[1]
        try:
            archivos = tuple(
                f.path for f in psutil.Process(pid).open_files()
                if Path(f.path).suffix.lower() in EXTENSIONES_ESTUDIO)
        except psutil.Error:
            archivos = ()
        self._cache_archivos[pid] = (ahora, archivos)
        return archivos
