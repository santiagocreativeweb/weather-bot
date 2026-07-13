#!/usr/bin/env python3
# scripts/stats_page.py — ESTADISTICAS del bot: generales + rendimiento DIA POR DIA, marcando en
# cada mercado si GANO o PERDIO (pedido Santiago 2026-07-11). Genera data/stats.html (tab aparte,
# linkeado desde el dashboard). Track record VIVO: bot vs bucket ganador oficial de Polymarket.
#
# Regla (coherente con leaderboard/check_predictions/dashboard): pick = floor(mu_cal); top-2/3 por
# bucket_prob(mu-0.5, sigma, lo, hi); ganador = Gamma (o fisica IEM floreada si el dia paso sin
# ganador de mercado). WU FLOOREA la obs SIEMPRE. Verdicto por mercado: EXACTO / TOP-2 / TOP-3 / PERDIDA.
import concurrent.futures as cf
import json, math, os, sys
import datetime as dt
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import STATION_META, CSS, ddmmyyyy, fecha_es, STATIONS               # noqa: E402
from check_predictions import resolved_buckets, fetch_obs_iem, winner_by_temp        # noqa: E402
from wxbt.market import bucket_prob                                                  # noqa: E402
from wxbt.forward_scoring import frozen_forecast                                     # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")


def lbl_of(bucket, unit):
    """(lo,hi) -> etiqueta legible del bucket, coherente con el mercado."""
    lo, hi = bucket
    deg = "°F" if unit == "F" else "°C"
    if lo is None:
        return f"≤{hi}{deg}"
    if hi is None:
        return f"≥{lo}{deg}"
    return (f"{lo}-{hi}{deg}" if lo != hi else f"{lo}{deg}")


def records(today):
    """Una fila por (station, target) resuelto con pick/ganador/nivel/error (para stats + dia a dia)."""
    p = pd.read_csv(os.path.join(D, "predictions_forward.csv"), parse_dates=["target"])
    p["target"] = p["target"].dt.date
    due = p[p["target"] <= today].sort_values("lead_h").drop_duplicates(
        ["station", "target"], keep="first")
    # IEM por red, fila por fila, hacia que la tarea diaria tardara minutos. La historia local es
    # la fuente validada; solo completar en paralelo las fechas forward que aun no llegaron a obs.csv.
    obs_cache = {}
    try:
        oh = pd.read_csv(os.path.join(D, "obs.csv"), parse_dates=["date"])
        oh["date"] = oh.date.dt.date
        obs_cache = {(r.station, r.date): float(r.tmax) for r in oh.itertuples()}
    except (OSError, ValueError, AttributeError):
        pass
    missing = [k for k in due[["station", "target"]].itertuples(index=False, name=None)
               if k not in obs_cache]
    if missing:
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            vals = pool.map(lambda k: fetch_obs_iem(*k), missing)
            obs_cache.update({k: v for k, v in zip(missing, vals) if v is not None})
    try:
        with open(os.path.join(D, "forecast_audit.json"), encoding="utf-8") as fh:
            audit = json.load(fh)
    except (OSError, ValueError):
        audit = {}
    resb = resolved_buckets(list(due[["station", "target"]].itertuples(index=False, name=None)))
    recs = []
    for r in due.itertuples():
        info = resb.get((r.station, r.target))
        if not info or not info["buckets"]:
            continue
        buckets, winner, res = info["buckets"], info["winner"], "mercado"
        obs = obs_cache.get((r.station, r.target))
        if winner is None and r.target < today and obs is not None:
            winner, res = winner_by_temp(buckets, int(math.floor(obs))), "fisica"
        if winner is None:
            continue
        unit = STATIONS[r.station][3]
        mu, sigma, forecast_source = frozen_forecast(
            audit, r.station, r.target, r.mu_cal, r.sigma_cal)
        if forecast_source == "forward-fallback":
            continue  # no acreditar un resultado sin snapshot congelado/reconstruible
        pick = winner_by_temp(buckets, int(math.floor(mu)))
        probs = [bucket_prob(mu - 0.5, sigma, lo, hi) for lo, hi in buckets]
        order = sorted(range(len(buckets)), key=lambda i: -probs[i])
        rank_w = order.index(buckets.index(winner)) + 1
        exact = int(pick == winner)
        top2 = int(exact or rank_w <= 2)
        top3 = int(top2 or rank_w <= 3)
        nivel = "EXACTO" if exact else ("TOP-2" if top2 else ("TOP-3" if top3 else "PERDIDA"))
        recs.append(dict(station=r.station, target=r.target, unit=unit, res=res,
                         pick=lbl_of(pick, unit) if pick else "—",
                         win=lbl_of(winner, unit), nivel=nivel, exact=exact, top2=top2, top3=top3,
                         err=(abs(mu - obs) if obs is not None else None),
                         mu=mu, real=obs, forecast_source=forecast_source))
    return recs


NIV_CLS = {"EXACTO": "n-ex", "TOP-2": "n-t2", "TOP-3": "n-t3", "PERDIDA": "n-bad"}
NIV_ICON = {"EXACTO": "✓", "TOP-2": "✓", "TOP-3": "~", "PERDIDA": "✗"}


def scard(lbl, big, sub, cls=""):
    return f'<div class="scard {cls}"><div class="lbl">{lbl}</div><div class="big">{big}</div><div class="sub">{sub}</div></div>'


def main():
    today = dt.date.today()
    print(f"Estadisticas del bot al {today} ...")
    recs = records(today)
    n = len(recs)
    ex = sum(r["exact"] for r in recs)
    t2 = sum(r["top2"] for r in recs)
    t3 = sum(r["top3"] for r in recs)
    perd = sum(1 for r in recs if r["nivel"] == "PERDIDA")
    errs = [r["err"] for r in recs if r["err"] is not None]
    mae = sum(errs) / len(errs) if errs else float("nan")
    rmse = (sum(e * e for e in errs) / len(errs)) ** 0.5 if errs else float("nan")

    def pct(a, b):
        return f"{a/b:.0%}" if b else "—"

    cards = (
        scard("mercados resueltos", str(n), f"desde {ddmmyyyy(min(r['target'] for r in recs))}" if n else "—") +
        scard("acierto EXACTO", pct(ex, n), f"{ex}/{n} · bucket clavado") +
        scard("acierto TOP-2", pct(t2, n), f"{t2}/{n} · ganador en top-2", "y") +
        scard("acierto TOP-3", pct(t3, n), f"{t3}/{n}", "o") +
        scard("PÉRDIDAS", str(perd), f"de {n} · fuera del top-3", "bad") +
        scard("MAE / RMSE", f"{mae:.2f}°" if mae == mae else "—", f"RMSE {rmse:.2f}°" if rmse == rmse else "")
    )

    # rendimiento DIA POR DIA (mas reciente primero)
    days = sorted({r["target"] for r in recs}, reverse=True)
    day_html = []
    CONT = {"Asia": 0, "Europa": 1, "America": 2}
    for d in days:
        drecs = [r for r in recs if r["target"] == d]
        drecs.sort(key=lambda r: (CONT.get(STATION_META.get(r["station"], ("?",))[0], 9), r["station"]))
        dex = sum(r["exact"] for r in drecs); dt2 = sum(r["top2"] for r in drecs)
        dpe = sum(1 for r in drecs if r["nivel"] == "PERDIDA")
        trs = []
        for r in drecs:
            cont, pais, ciudad = STATION_META.get(r["station"], ("?", "?", r["station"]))[:3]
            fis = ' <span class="fis" title="resuelto por obs fisica IEM (mercado sin ganador aun)">·fís</span>' if r["res"] == "fisica" else ""
            trs.append(
                f'<tr><td class="stn">{r["station"]}<span>{ciudad}</span></td>'
                f'<td>{r["pick"]}</td><td class="win">{r["win"]}{fis}</td>'
                f'<td class="verd {NIV_CLS[r["nivel"]]}">{NIV_ICON[r["nivel"]]} {r["nivel"]}</td>'
                f'<td class="num">{("%.1f°"%r["err"]) if r["err"] is not None else "—"}</td></tr>')
        day_html.append(
            f'<div class="daysec"><h3>{fecha_es(d)}'
            f'<span class="droll">{dex} exacto{"s" if dex!=1 else ""} · {dt2} top-2 · '
            f'<b class="dbad">{dpe} pérdida{"s" if dpe!=1 else ""}</b></span></h3>'
            f'<table class="dtab"><thead><tr><th>estación</th><th>pick bot</th><th>ganó</th>'
            f'<th>resultado</th><th class="num">error</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>')

    body = f'''<div class="viz-root">
<div class="topbar"><div class="row1"><h1>📊 ESTADÍSTICAS — rendimiento del bot</h1>
<span class="subt">track record vivo vs ganador oficial de Polymarket · crece cada día
· <a href="live_dashboard.html">← volver a la terminal</a> · <a href="leaderboard.html">🏆 leaderboard</a></span></div></div>
<div class="sgrid">{cards}</div>
<p class="subt" style="margin:6px 0 12px">Rendimiento <b>día por día</b>: en cada mercado, el
<b>pick</b> del bot (floor μ) contra el <b>bucket que ganó</b>, y el veredicto —
<span class="verd n-ex">✓ EXACTO</span> (clavó) ·
<span class="verd n-t2">✓ TOP-2</span> (ganador entre sus 2 más probables) ·
<span class="verd n-t3">~ TOP-3</span> ·
<span class="verd n-bad">✗ PÉRDIDA</span> (afuera). Ordenado ASIA→EUROPA→AMÉRICA.</p>
{"".join(day_html) if day_html else '<p class="subt">Sin mercados resueltos todavía — vuelve cuando el día haya cerrado.</p>'}
<p class="subt" style="margin-top:16px">Regenerar: <code>python scripts/stats_page.py</code> o el botón
📊 del dashboard.</p></div>'''

    extra = '''
.viz-root .daysec{margin:16px 0 8px;}
.viz-root .daysec h3{font-size:13px;color:var(--fc);margin:0 0 6px;font-family:var(--mono);
  letter-spacing:.06em;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;}
.viz-root .daysec h3::before{content:"┌─ ";color:var(--base);}
.viz-root .droll{font-size:11px;color:var(--ink2);font-family:var(--mono);}
.viz-root .droll .dbad{color:var(--red);}
.viz-root table.dtab{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:6px;}
.viz-root table.dtab th{font-size:8.5px;color:var(--mut);text-transform:uppercase;text-align:left;
  letter-spacing:.06em;padding:3px 10px;border-bottom:1px solid var(--bd);}
.viz-root table.dtab td{padding:5px 10px;border-bottom:1px solid var(--grid);font-family:var(--mono);}
.viz-root table.dtab td.num,.viz-root table.dtab th.num{text-align:right;}
.viz-root table.dtab tr:hover td{background:var(--s2);}
.viz-root .dtab .stn{font-weight:700;}
.viz-root .dtab .stn span{display:block;font-size:9.5px;color:var(--mut);font-weight:400;font-family:"Segoe UI",sans-serif;}
.viz-root .dtab .win{color:var(--live);}
.viz-root .dtab .fis{font-size:9px;color:var(--mut);}
.viz-root .verd{font-weight:700;font-size:11px;white-space:nowrap;}
.viz-root .verd.n-ex{color:var(--fin);} .viz-root .verd.n-t2{color:#ffd23e;}
.viz-root .verd.n-t3{color:#ff8c42;} .viz-root .verd.n-bad{color:var(--red);}
.viz-root a{color:var(--mkt);}
'''
    html = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Estadísticas</title><style>{CSS}{extra}</style></head><body>{body}</body></html>")
    out = os.path.abspath(os.path.join(D, "stats.html"))
    open(out, "w", encoding="utf-8").write(html)
    print(f"Stats -> {out}")
    print(f"General: n={n} exacto {pct(ex,n)} top2 {pct(t2,n)} top3 {pct(t3,n)} perdidas {perd} "
          f"MAE {mae:.2f}" if n else "General: sin resueltos aun")
    for d in days:
        dr = [r for r in recs if r["target"] == d]
        print(f"  {d}: {sum(x['exact'] for x in dr)} exactos, "
              f"{sum(x['top2'] for x in dr)} top2, {sum(1 for x in dr if x['nivel']=='PERDIDA')} perdidas "
              f"(n={len(dr)})")


if __name__ == "__main__":
    main()
