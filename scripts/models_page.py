#!/usr/bin/env python3
# scripts/models_page.py — QUE MODELO ACIERTA EN CADA CIUDAD (pedido Santiago 2026-07-15:
# "filtremos por los resultados de los modelos que mejor predicen la ciudad; ej: icon predijo
# Milan 5 veces y las 5 acerto -> referencia; ecmwf no acerto ninguna -> no ganamos con ese").
# Genera:
#   * data/models.html          — pagina por ciudad: ranking de modelos vs ganador oficial Gamma.
#   * data/model_city_rank.csv  — lo consumen dashboard (badge "mejor modelo") y telegram_bot.
# DOS fuentes SIEMPRE etiquetadas (honestidad):
#   vivo  = models_forward.csv, ultima captura ANTERIOR al freeze (point-in-time real, crece a diario)
#   retro = lab_m8.csv lead-2 (Previous-Runs retrospectivo, hereda bug #5 — referencia, no veredicto)
import argparse
import csv
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wxbt_insights as I                                    # noqa: E402
from dashboard import CSS, STATION_META, to_art              # noqa: E402
from show_live import STATIONS                               # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
RANK_CSV = os.path.join(D, "model_city_rank.csv")
OUT_HTML = os.path.join(D, "models.html")

EXTRA_CSS = """
.viz-root table.mp{border-collapse:collapse;width:100%;max-width:680px;font-size:12.5px;margin:4px 0 18px;}
.viz-root table.mp th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:right;
  padding:5px 10px;border-bottom:1px solid var(--bd);letter-spacing:.04em;}
.viz-root table.mp th:first-child{text-align:left;}
.viz-root table.mp td{padding:6px 10px;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;
  font-family:var(--mono);}
.viz-root table.mp td.num{text-align:right;}
.viz-root table.mp tr.best td{color:var(--fc);font-weight:700;}
.viz-root table.mp tr.worst td{color:var(--red);}
.viz-root .src-tag{font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid var(--bd);
  background:var(--s2);color:var(--ink2);margin-left:8px;font-family:var(--mono);}
.viz-root h2.city{font-size:13px;color:var(--fc);margin:26px 0 4px;font-family:var(--mono);
  letter-spacing:.08em;text-transform:uppercase;}
.viz-root .barwrap{display:inline-block;width:90px;height:8px;background:var(--s2);
  border:1px solid var(--bd);border-radius:2px;vertical-align:middle;margin-left:8px;}
.viz-root .bar{display:block;height:100%;background:var(--fc);border-radius:2px;}
.viz-root a{color:var(--mkt);}
"""


def pct(x):
    return f"{x:.0%}" if x == x else "&mdash;"


def num(x, nd=2):
    return f"{x:.{nd}f}" if x == x else "&mdash;"


def main(a):
    today = dt.date.today()
    perf = I.model_perf(days=a.days, refresh=a.refresh, today=today)
    bm = I.best_models(perf)

    # ---- CSV para dashboard/telegram (badge "mejor modelo por ciudad") ----
    with open(RANK_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["station", "src", "rank", "model", "n", "hits", "rate", "mae"])
        for st, info in sorted(bm.items()):
            for i, (model, rate, n, mae) in enumerate(info["rank"], 1):
                w.writerow([st, info["src"], i, model, n, round(rate * n),
                            f"{rate:.4f}", (f"{mae:.3f}" if mae == mae else "")])

    # ---- HTML ----
    by_st = {}
    for r in perf:
        by_st.setdefault(r["station"], {}).setdefault(r["src"], []).append(r)
    updated = to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")
    secs = []
    order = sorted(by_st, key=lambda st: STATION_META.get(st, ("?", "?", st))[2])
    for st in order:
        cont, pais, ciudad = STATION_META.get(st, ("?", "?", st))[:3]
        chunks = [f'<h2 class="city">{st} · {ciudad} <span class="src-tag">{pais}</span></h2>']
        for src in ("vivo", "retro"):
            rows = by_st[st].get(src)
            if not rows:
                continue
            rows = sorted(rows, key=lambda r: (-(r["rate"] if r["rate"] == r["rate"] else -1),
                                               r["mae"] if r["mae"] == r["mae"] else 99))
            tag = ("point-in-time REAL (ultima captura pre-freeze) — crece a diario" if src == "vivo"
                   else "retrospectivo Previous-Runs 90d (bug #5: referencia, no veredicto)")
            trs = []
            best_rate = rows[0]["rate"] if rows and rows[0]["n"] >= a.min_n else None
            for i, r in enumerate(rows):
                cls = "best" if (i == 0 and r["n"] >= a.min_n) else (
                    "worst" if (i == len(rows) - 1 and r["n"] >= a.min_n and len(rows) > 3) else "")
                bar = (f'<span class="barwrap"><span class="bar" '
                       f'style="width:{max(2, r["rate"] * 100):.0f}%"></span></span>'
                       if r["rate"] == r["rate"] else "")
                trs.append(f'<tr class="{cls}"><td>{r["model"]}</td>'
                           f'<td class="num">{r["hits"]}/{r["n"]}</td>'
                           f'<td class="num">{pct(r["rate"])}{bar}</td>'
                           f'<td class="num">{num(r["mae"])}</td></tr>')
            chunks.append(
                f'<span class="src-tag">{src.upper()}</span> <span class="subt">{tag}</span>'
                f'<table class="mp"><thead><tr><th>modelo</th><th>exactos</th>'
                f'<th>% exacto (bucket ganador)</th><th>MAE vs obs</th></tr></thead>'
                f'<tbody>{"".join(trs)}</tbody></table>')
        secs.append("".join(chunks))

    body = f"""<div class="viz-root">
<div class="topbar"><div class="row1"><h1>🧪 Modelos por ciudad — WXBT</h1>
<span class="subt">que modelo ACIERTA el bucket ganador (Gamma/WU) en cada ciudad
· <a href="live_dashboard.html">← dashboard</a> · <a href="history.html">🗓 historial</a>
· <a href="leaderboard.html">🏆 leaderboard</a></span></div>
<div class="updbar" style="font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px">
🕒 Actualizado: <b style="color:var(--live)">{updated}</b> (hora Argentina) ·
regenerar: <code>python scripts/models_page.py --refresh</code></div></div>
<p class="subt" style="margin:10px 0 4px;max-width:900px">
<b>Como leerlo:</b> "exactos 5/5" = las 5 veces que ese modelo se uso, su floor cayo en el bucket
que PAGO Polymarket. El mejor de cada ciudad va en <span style="color:var(--fc)">verde</span>
(minimo n={a.min_n}); el peor con historial, en <span style="color:var(--red)">rojo</span> —
sirve de referencia pero NO estamos ganando con ese modelo en esa ciudad.
<b>VIVO</b> = capturas reales pre-freeze (la fuente que manda cuando junte n).
<b>RETRO</b> = Previous-Runs 90d, frescura ambigua (bug #5) — orienta, no decide.
Seleccion por estacion = winner's curse (V8/lab_city_models): usar esto como CONTEXTO, no para
cambiar el mix del bot sin gate pre-registrado.</p>
{"".join(secs)}</div>"""
    html = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Modelos por ciudad</title><style>{CSS}{EXTRA_CSS}</style></head>"
            f"<body>{body}</body></html>")
    open(OUT_HTML, "w", encoding="utf-8").write(html)
    print(f"Modelos por ciudad -> {os.path.abspath(OUT_HTML)}")
    print(f"Ranking CSV        -> {os.path.abspath(RANK_CSV)}  ({sum(1 for _ in open(RANK_CSV)) - 1} filas)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ranking de modelos por ciudad vs ganador oficial.")
    ap.add_argument("--days", type=int, default=90, help="ventana de evaluacion (default 90)")
    ap.add_argument("--min-n", type=int, default=5, help="n minimo para marcar mejor/peor (default 5)")
    ap.add_argument("--refresh", action="store_true", help="completar ganadores/obs desde Gamma/IEM")
    main(ap.parse_args())
