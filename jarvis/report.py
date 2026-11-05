"""Informe de foco en HTML con gráficas vectoriales (se abre en el navegador)."""
from __future__ import annotations

import datetime as dt
import html
import io
import os
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .config import INFORMES  # noqa: E402
from .db import ESTADOS_FOCO  # noqa: E402
from .focus import ESTADOS_MALOS, NOMBRES  # noqa: E402

COLORES = {
    "enfocado": "#0891b2", "mesa": "#059669", "pensando": "#60a5fa", "mensajeria": "#d97706",
    "distraido": "#dc2626", "celular": "#e11d48", "ausente": "#94a3b8", "somnoliento": "#9333ea",
    "descanso": "#84cc16", "pausa": "#cbd5e1",
}
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
plt.rcParams.update({"font.family": "Segoe UI", "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False, "svg.fonttype": "none"})


def rango_dia(dia: dt.date) -> tuple[float, float]:
    inicio = dt.datetime.combine(dia, dt.time()).timestamp()
    return inicio, inicio + 86400


def minutos_foco(db, desde: float, hasta: float) -> float:
    return sum(s for e, s in db.segundos_por_estado(desde, hasta).items() if e in ESTADOS_FOCO) / 60


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue()
    return svg[svg.find("<svg"):]


def _hm(minutos: float) -> str:
    h, m = divmod(round(minutos), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _racha_mas_larga(segmentos: list[tuple]) -> float:
    mejor = actual = 0.0
    fin_anterior = None
    for inicio, fin, estado, *_ in segmentos:
        if estado in ESTADOS_FOCO:
            actual = actual + (fin - inicio) if fin_anterior and inicio - fin_anterior < 5 else fin - inicio
            mejor = max(mejor, actual)
            fin_anterior = fin
        elif estado != "mensajeria":
            actual, fin_anterior = 0.0, None
    return mejor / 60


def generar(db, cfg: dict, dia: dt.date | None = None) -> Path:
    dia = dia or dt.date.today()
    d0, d1 = rango_dia(dia)
    meta = cfg["sesion"]["meta_diaria_min"]
    segmentos = db.consultar(
        "SELECT inicio, fin, estado, app, titulo, categoria FROM segmentos "
        "WHERE fin > ? AND inicio < ? ORDER BY inicio", (d0, d1))
    por_estado = db.segundos_por_estado(d0, d1)
    foco = sum(por_estado.get(e, 0) for e in ESTADOS_FOCO) / 60
    estudio = sum(s for e, s in por_estado.items() if e not in ("descanso", "pausa")) / 60
    perdido = sum(por_estado.get(e, 0) for e in ESTADOS_MALOS) / 60
    episodios = db.consultar("SELECT COUNT(*) FROM eventos WHERE tipo='distraccion' AND ts >= ? AND ts < ?",
                             (d0, d1))[0][0]
    racha = _racha_mas_larga(segmentos)

    # Línea de tiempo del día.
    graficas = []
    if segmentos:
        fig, ax = plt.subplots(figsize=(9, 1.5))
        for inicio, fin, estado, *_ in segmentos:
            h0 = (inicio - d0) / 3600
            ax.broken_barh([(h0, (fin - inicio) / 3600)], (0, 1), color=COLORES.get(estado, "#999"))
        horas = [(s[0] - d0) / 3600 for s in segmentos] + [(s[1] - d0) / 3600 for s in segmentos]
        ax.set_xlim(max(0, int(min(horas))), min(24, int(max(horas)) + 1))
        ax.set_yticks([])
        ax.set_xlabel("hora del día")
        ax.spines["left"].set_visible(False)
        presentes = [e for e in COLORES if e in por_estado]
        ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=COLORES[e]) for e in presentes],
                  labels=[NOMBRES[e] for e in presentes], ncol=5, frameon=False,
                  loc="upper center", bbox_to_anchor=(0.5, -0.45), fontsize=8)
        graficas.append(("Tu día", _svg(fig)))

    # Últimos 14 días.
    dias = [dia - dt.timedelta(days=i) for i in range(13, -1, -1)]
    valores = [minutos_foco(db, *rango_dia(d)) for d in dias]
    fig, ax = plt.subplots(figsize=(9, 2.6))
    ax.bar([d.strftime("%d/%m") for d in dias], valores,
           color=["#0891b2" if v >= meta else "#7dd3fc" for v in valores])
    ax.axhline(meta, color="#334155", lw=1, ls="--")
    ax.text(len(dias) - 0.5, meta, f" meta {meta} min", va="bottom", ha="right", fontsize=8, color="#334155")
    ax.set_ylabel("minutos de foco")
    ax.tick_params(axis="x", labelsize=8)
    graficas.append(("Últimos 14 días", _svg(fig)))

    # En qué se fue el tiempo perdido.
    perdidas: dict[str, float] = defaultdict(float)
    for inicio, fin, estado, app, titulo, _ in segmentos:
        if estado in ESTADOS_MALOS:
            nombre = "Celular" if estado == "celular" else "Sueño" if estado == "somnoliento" else (titulo or app or "?")
            perdidas[nombre[:60]] += (fin - inicio) / 60
    filas_perdidas = sorted(perdidas.items(), key=lambda x: -x[1])[:8]

    # Minutos por tema (14 días).
    temas = db.consultar(
        "SELECT s.tema, SUM(g.fin - g.inicio) FROM segmentos g JOIN sesiones s ON s.id = g.sesion_id "
        "WHERE g.inicio >= ? AND g.estado IN ('enfocado','mesa','pensando') GROUP BY s.tema ORDER BY 2 DESC",
        (rango_dia(dias[0])[0],))

    # Vídeos de la serie.
    videos_html = ""
    carpeta = cfg.get("informes", {}).get("carpeta_videos", "")
    if carpeta and Path(carpeta).exists():
        total = len([f for f in os.listdir(carpeta) if f.lower().endswith(".mp4")])
        vistos = db.consultar("SELECT ruta, segundos FROM archivos WHERE lower(ruta) LIKE ? AND segundos >= 60 "
                              "ORDER BY ruta", (str(Path(carpeta)).lower() + "%.mp4",))
        lista = "".join(f"<li>{html.escape(Path(r).stem)} <span class=g>· {_hm(s / 60)}</span></li>"
                        for r, s in vistos)
        videos_html = (f"<h2>Vídeos de la serie</h2><p><b>{len(vistos)}</b> de {total} vistos "
                       f"(con más de un minuto de foco).</p><ul class=cols>{lista}</ul>")

    sesiones = db.consultar("SELECT inicio, fin, tema, objetivo, plan_min FROM sesiones "
                            "WHERE inicio >= ? AND inicio < ? ORDER BY inicio", (d0, d1))
    filas_sesiones = "".join(
        f"<tr><td>{dt.datetime.fromtimestamp(i):%H:%M}–{dt.datetime.fromtimestamp(f):%H:%M}</td>"
        f"<td>{html.escape(t or '')}</td><td>{html.escape(o or '')}</td></tr>"
        if f else
        f"<tr><td>{dt.datetime.fromtimestamp(i):%H:%M}– (en curso)</td><td>{html.escape(t or '')}</td>"
        f"<td>{html.escape(o or '')}</td></tr>"
        for i, f, t, o, _ in sesiones)

    pct = round(100 * foco / estudio) if estudio else 0
    tarjetas = [
        ("Foco hoy", _hm(foco), f"meta {_hm(meta)}"),
        ("Concentración", f"{pct} %", "del tiempo de sesión"),
        ("Racha más larga", _hm(racha), "sin distraerte"),
        ("Distracciones", str(episodios), f"{_hm(perdido)} perdidos"),
    ]
    cuerpo = "".join(f"<div class=card><div class=k>{k}</div><div class=v>{v}</div><div class=g>{g}</div></div>"
                     for k, v, g in tarjetas)
    graficas_html = "".join(f"<h2>{t}</h2><div class=fig>{s}</div>" for t, s in graficas)
    tabla_perdidas = "".join(f"<tr><td>{html.escape(n)}</td><td>{_hm(m)}</td></tr>" for n, m in filas_perdidas)
    tabla_temas = "".join(f"<tr><td>{html.escape(t or '')}</td><td>{_hm(s / 60)}</td></tr>" for t, s in temas)

    pagina = f"""<!doctype html><html lang=es><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Jarvis · {dia:%d/%m/%Y}</title>
<style>
body{{font-family:"Segoe UI",system-ui,sans-serif;margin:0;background:#f8fafc;color:#0f172a}}
main{{max-width:960px;margin:0 auto;padding:28px 18px 60px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:30px 0 10px}}
.sub,.g{{color:#64748b;font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:18px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px}}
.k{{font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.04em}} .v{{font-size:24px;font-weight:600;margin:4px 0}}
.fig{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:10px;overflow-x:auto}} .fig svg{{max-width:100%;height:auto}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden}}
td,th{{padding:8px 12px;border-bottom:1px solid #f1f5f9;text-align:left;font-size:14px}}
.cols{{columns:2;font-size:13px}}
</style></head><body><main>
<h1>Informe de foco</h1><div class=sub>{DIAS[dia.weekday()]} {dia:%d/%m/%Y} · generado por Jarvis</div>
<div class=cards>{cuerpo}</div>
{graficas_html}
<h2>En qué se fue el tiempo perdido</h2>
<table>{tabla_perdidas or '<tr><td>Nada que reportar. Bien.</td></tr>'}</table>
<h2>Foco por tema (14 días)</h2><table>{tabla_temas or '<tr><td>Aún sin sesiones.</td></tr>'}</table>
<h2>Sesiones de hoy</h2><table>{filas_sesiones or '<tr><td>Ninguna.</td></tr>'}</table>
{videos_html}
</main></body></html>"""
    ruta = INFORMES / f"informe_{dia.isoformat()}.html"
    ruta.write_text(pagina, encoding="utf-8")
    return ruta
