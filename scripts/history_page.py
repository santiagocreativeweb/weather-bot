#!/usr/bin/env python3
# scripts/history_page.py — HISTORIAL DE PRONOSTICOS desde el 08/07 (pedido Santiago 2026-07-15:
# "poder ver los pronosticos de los dias anteriores arrancando desde 08/07").
# Genera data/history.html: un bloque por DIA (mas reciente primero); por ciudad muestra el pick
# CONGELADO del bot (audit inmutable), el bucket que PAGO Polymarket, el resultado
# (EXACTO/TOP-2/TOP-3/PERDIDA), la obs real y — si hay capturas — que dijo CADA MODELO ese dia
# (ultima captura pre-freeze, con ✓ en los que acertaron el bucket ganador).
# Honestidad: filas sin evidencia congelada point-in-time NO se scorean (mismo criterio que
# leaderboard/stats). El ganador oficial es Gamma (lo que pago), no la obs IEM.
import argparse
import math
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wxbt_insights as I                                          # noqa: E402
from dashboard import CSS, STATION_META, fecha_es, to_art          # noqa: E402
from show_live import STATIONS                                     # noqa: E402
from wxbt.market import resolve_bucket                             # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT_HTML = os.path.join(D, "history.html")

NIV_ICON = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌"}
NIV_CLS = {"EXACTO": "g-ex", "TOP-2": "g-t2", "TOP-3": "g-t3", "PERDIDA": "g-bad"}

EXTRA_CSS = """
.viz-root table.hy{border-collapse:collapse;width:100%;font-size:12.5px;margin:4px 0 6px;}
.viz-root table.hy th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:left;
  padding:5px 10px;border-bottom:1px solid var(--bd);letter-spacing:.04em;}
.viz-root table.hy td{padding:6px 10px;border-bottom:1px solid var(--grid);font-family:var(--mono);
  font-variant-numeric:tabular-nums;}
.viz-root .gv{font-weight:700;white-space:nowrap;}
.viz-root .gv.g-ex{color:var(--fin);} .viz-root .gv.g-t2{color:#ffd23e;}
.viz-root .gv.g-t3{color:#ff8c42;} .viz-root .gv.g-bad{color:#d03b3b;}
.viz-root .models{font-size:11px;color:var(--ink2);}
.viz-root .models b{color:var(--fin);font-weight:700;}
.viz-root .kpis{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0 4px;font-family:var(--mono);}
.viz-root .kpi{background:var(--s1);border:1px solid var(--bd);border-radius:6px;padding:10px 16px;}
.viz-root .kpi b{display:block;font-size:20px;color:var(--fc);}
.viz-root .kpi span{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;}
.viz-root h3.dia{font-size:12px;color:var(--fc);margin:22px 0 8px;text-transform:uppercase;
  letter-spacing:.14em;font-family:var(--mono);}
.viz-root a{color:var(--mkt);}
.viz-root td.stn a{color:var(--ink);text-decoration:none;font-weight:700;}
.viz-root td.stn a:hover{color:var(--mkt);}
.viz-root .pend{color:var(--mut);}
"""


def fmt(v, unit, nd=1):
    return f"{v:.{nd}f}°{unit}" if v is not None and v == v else "—"


def main(a):
    today = dt.date.today()
    hist = I.bot_history(refresh=a.refresh, today=today)
    caps = I.model_captures_pre_freeze()          # modelos VIVOS pre-freeze (desde 12/07)
    retro = I._retro_models()                      # retro para 08/07..11/07 (bug #5: referencia)
    winners = I.load_winners(today=today)

    by_date = {}
    for r in hist:
        by_date.setdefault(r["target"], []).append(r)

    # KPIs globales (solo scoreados)
    sc = [r for r in hist if r["nivel"]]
    n = len(sc)
    ex = sum(r["nivel"] == "EXACTO" for r in sc)
    t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in sc)
    t3 = sum(r["nivel"] in ("EXACTO", "TOP-2", "TOP-3") for r in sc)
    aes = [abs(r["mu"] - r["max_real"]) for r in sc if r.get("max_real") is not None]
    mae = sum(aes) / len(aes) if aes else float("nan")

    secs = []
    for d in sorted(by_date, reverse=True):
        rows = sorted(by_date[d], key=lambda r: STATION_META.get(r["station"], ("?", "?", r["station"]))[2])
        trs = []
        for r in rows:
            st, unit = r["station"], r["unit"]
            ciudad = STATION_META.get(st, ("?", "?", st))[2]
            w = winners.get((st, d)) or {}
            # que dijo cada modelo ese dia (vivo si hay; sino retro etiquetado)
            models = caps.get((st, d)) or {}
            src_tag = "vivo"
            if not models:
                models, src_tag = retro.get((st, d)) or {}, "retro"
            mparts = []
            for m, v in sorted(models.items(), key=lambda kv: kv[1]):
                hit = (w.get("lbl") and resolve_bucket(int(math.floor(v)), w.get("lo"), w.get("hi")))
                mparts.append(f"<b>{m} {v:.1f}✓</b>" if hit else f"{m} {v:.1f}")
            mtxt = (f'<span class="models">[{src_tag}] ' + " · ".join(mparts) + "</span>") if mparts else \
                '<span class="models pend">sin capturas de modelos ese dia</span>'
            niv = r["nivel"]
            res = (f'<span class="gv {NIV_CLS[niv]}">{NIV_ICON[niv]} {niv}</span>' if niv else
                   '<span class="pend">sin resolucion aun</span>')
            delta = (f"{r['mu'] - r['max_real']:+.1f}" if r.get("max_real") is not None else "—")
            pwin_txt = f"{r['pwin']:.2f}" if r.get("pwin") is not None else "—"
            trs.append(
                f'<tr><td class="stn"><a href="{I.pm_url(st, d)}" target="_blank" '
                f'title="abrir mercado en Polymarket">{st}</a><br>'
                f'<span style="font-size:10px;color:var(--mut)">{ciudad}</span></td>'
                f'<td>{r["pick_lbl"] or "—"}<br><span style="font-size:10px;color:var(--mut)">'
                f'μ {fmt(r["mu"], unit)} σ {r["sg"]:.1f}</span></td>'
                f'<td>{r.get("win_lbl") or "—"}</td>'
                f'<td>{res}<br><span style="font-size:10px;color:var(--mut)">p_win {pwin_txt}</span></td>'
                f'<td>{fmt(w.get("max_real"), unit)}<br>'
                f'<span style="font-size:10px;color:var(--mut)">Δ bot {delta}</span></td>'
                f'<td>{mtxt}</td></tr>')
        nsc = [r for r in rows if r["nivel"]]
        dex = sum(r["nivel"] == "EXACTO" for r in nsc)
        dt2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in nsc)
        head = (f' — {dex} exactos · {dt2} top-2 de {len(nsc)} resueltos' if nsc else
                ' — sin mercados resueltos')
        secs.append(
            f'<h3 class="dia">{fecha_es(d)}{head}</h3>'
            f'<table class="hy"><thead><tr><th>mercado</th><th>pick congelado 🔒</th>'
            f'<th>gano (WU/Gamma)</th><th>resultado</th><th>obs real</th>'
            f'<th>modelos ese dia (✓ = acerto el bucket)</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')

    updated = to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")
    body = f"""<div class="viz-root">
<div class="topbar"><div class="row1"><h1>🗓 Historial de pronosticos — WXBT</h1>
<span class="subt">pick CONGELADO vs lo que pago Polymarket, dia por dia desde el
{I.HISTORY_START.strftime('%d/%m/%Y')} · <a href="live_dashboard.html">← dashboard</a>
· <a href="models.html">🧪 modelos</a> · <a href="leaderboard.html">🏆 leaderboard</a></span></div>
<div style="font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px">
🕒 Actualizado: <b style="color:var(--live)">{updated}</b> (hora Argentina) ·
regenerar: <code>python scripts/history_page.py --refresh</code></div></div>
<div class="kpis">
<div class="kpi"><b>{ex}/{n}</b><span>exactos ({(ex / n if n else 0):.0%})</span></div>
<div class="kpi"><b>{t2}/{n}</b><span>top-2 ({(t2 / n if n else 0):.0%})</span></div>
<div class="kpi"><b>{t3}/{n}</b><span>top-3 ({(t3 / n if n else 0):.0%})</span></div>
<div class="kpi"><b>{mae:.2f}°</b><span>MAE pick vs obs</span></div>
</div>
<p class="subt" style="max-width:900px">Solo cuentan picks con evidencia CONGELADA (audit
inmutable, mismo criterio que el leaderboard). El ganador es el bucket que PAGO Polymarket
(Gamma) — no la obs IEM. Los modelos del dia son la ultima captura ANTERIOR al freeze (04:30
local); para dias previos al 12/07 se muestra el retro Previous-Runs (referencia, bug #5).</p>
{"".join(secs)}</div>"""
    html = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Historial de pronosticos</title><style>{CSS}{EXTRA_CSS}</style></head>"
            f"<body>{body}</body></html>")
    open(OUT_HTML, "w", encoding="utf-8").write(html)
    print(f"Historial -> {os.path.abspath(OUT_HTML)}  ({n} picks scoreados, {ex} exactos, "
          f"{t2} top-2 desde {I.HISTORY_START})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Historial dia-por-dia del pick congelado vs Polymarket.")
    ap.add_argument("--refresh", action="store_true", help="completar ganadores/obs desde Gamma/IEM")
    main(ap.parse_args())
