"""Base de datos SQLite: sesiones, tramos de foco, eventos y lo aprendido."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import DATOS

# Estados que cuentan como tiempo de estudio.
ESTADOS_FOCO = ("enfocado", "mesa", "pensando")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sesiones(
    id INTEGER PRIMARY KEY, inicio REAL, fin REAL, tema TEXT, objetivo TEXT,
    plan_min INTEGER, descanso_min INTEGER);
CREATE TABLE IF NOT EXISTS segmentos(
    id INTEGER PRIMARY KEY, sesion_id INTEGER, inicio REAL, fin REAL,
    estado TEXT, app TEXT, titulo TEXT, categoria TEXT);
CREATE INDEX IF NOT EXISTS ix_segmentos_inicio ON segmentos(inicio);
CREATE TABLE IF NOT EXISTS eventos(
    id INTEGER PRIMARY KEY, ts REAL, sesion_id INTEGER, tipo TEXT, detalle TEXT);
CREATE TABLE IF NOT EXISTS clasificaciones(
    tema TEXT, clave TEXT, categoria TEXT, razon TEXT, fuente TEXT, ts REAL,
    PRIMARY KEY(tema, clave));
CREATE TABLE IF NOT EXISTS conversacion(id INTEGER PRIMARY KEY, ts REAL, rol TEXT, texto TEXT);
CREATE TABLE IF NOT EXISTS archivos(ruta TEXT PRIMARY KEY, primera_vez REAL, segundos REAL);
"""


class BaseDatos:
    def __init__(self, ruta: Path = DATOS / "jarvis.db"):
        self._con = sqlite3.connect(ruta, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.executescript(ESQUEMA)
            columnas = {fila[1] for fila in self._con.execute("PRAGMA table_info(sesiones)")}
            if "total_min" not in columnas:  # bases de antes de las sesiones con tiempo total
                self._con.execute("ALTER TABLE sesiones ADD COLUMN total_min INTEGER DEFAULT 0")

    def ejecutar(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._con.execute(sql, params).lastrowid

    def consultar(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._con.execute(sql, params).fetchall()

    # --- sesiones -----------------------------------------------------------
    def nueva_sesion(self, tema: str, objetivo: str, plan_min: int, descanso_min: int, total_min: int = 0) -> int:
        return self.ejecutar(
            "INSERT INTO sesiones(inicio, tema, objetivo, plan_min, descanso_min, total_min) VALUES (?,?,?,?,?,?)",
            (time.time(), tema, objetivo, plan_min, descanso_min, total_min))

    def actualizar_sesion(self, sesion_id: int, **campos) -> None:
        """Cambios en marcha: tema, objetivo, plan_min, descanso_min o total_min."""
        permitidos = {"tema", "objetivo", "plan_min", "descanso_min", "total_min"}
        pares = [(k, v) for k, v in campos.items() if k in permitidos]
        if pares:
            self.ejecutar(f"UPDATE sesiones SET {', '.join(f'{k}=?' for k, _ in pares)} WHERE id=?",
                          (*[v for _, v in pares], sesion_id))

    def cerrar_sesion(self, sesion_id: int) -> None:
        self.ejecutar("UPDATE sesiones SET fin=? WHERE id=?", (time.time(), sesion_id))

    # --- tramos de estado ---------------------------------------------------
    def guardar_segmento(self, sesion_id: int | None, inicio: float, fin: float, estado: str,
                         app: str, titulo: str, categoria: str) -> None:
        if fin - inicio < 0.5:
            return
        self.ejecutar(
            "INSERT INTO segmentos(sesion_id, inicio, fin, estado, app, titulo, categoria) VALUES (?,?,?,?,?,?,?)",
            (sesion_id, inicio, fin, estado, app, titulo[:200], categoria))

    def segundos_por_estado(self, desde: float, hasta: float, sesion_id: int | None = None) -> dict[str, float]:
        sql = ("SELECT estado, SUM(MIN(fin, ?) - MAX(inicio, ?)) FROM segmentos "
               "WHERE fin > ? AND inicio < ?")
        params: tuple = (hasta, desde, desde, hasta)
        if sesion_id is not None:
            sql += " AND sesion_id = ?"
            params += (sesion_id,)
        return {estado: s or 0.0 for estado, s in self.consultar(sql + " GROUP BY estado", params)}

    def evento(self, sesion_id: int | None, tipo: str, detalle: str = "") -> None:
        self.ejecutar("INSERT INTO eventos(ts, sesion_id, tipo, detalle) VALUES (?,?,?,?)",
                      (time.time(), sesion_id, tipo, detalle))

    # --- lo que Jarvis aprende de cada ventana ---------------------------------
    def clasificacion(self, tema: str, clave: str) -> tuple[str, str, str] | None:
        filas = self.consultar(
            "SELECT categoria, razon, fuente FROM clasificaciones WHERE tema=? AND clave=?", (tema, clave))
        return filas[0] if filas else None

    def guardar_clasificacion(self, tema: str, clave: str, categoria: str, razon: str, fuente: str) -> None:
        self.ejecutar(
            "INSERT OR REPLACE INTO clasificaciones(tema, clave, categoria, razon, fuente, ts) VALUES (?,?,?,?,?,?)",
            (tema, clave, categoria, razon, fuente, time.time()))

    # --- otros ----------------------------------------------------------------
    def sumar_archivo(self, ruta: str, segundos: float) -> None:
        self.ejecutar(
            "INSERT INTO archivos(ruta, primera_vez, segundos) VALUES (?,?,?) "
            "ON CONFLICT(ruta) DO UPDATE SET segundos = segundos + excluded.segundos",
            (ruta, time.time(), segundos))

    def mensaje(self, rol: str, texto: str) -> None:
        self.ejecutar("INSERT INTO conversacion(ts, rol, texto) VALUES (?,?,?)", (time.time(), rol, texto))
