#!/usr/bin/env python3
# scripts/leaderboard.py — TRACK RECORD VIVO: ranking de estaciones por los RESULTADOS REALES del
# bot contra el bucket ganador oficial de Polymarket (data/predictions_forward.csv, targets
# 2026-07-08 -> hoy, crece cada dia). "Si Tokio acerto 2 exactos, va top 1."
# Genera data/leaderboard.html (tab aparte, linkeado desde el dashboard).
#
# [2026-07-13, pedido Santiago] Cada fila es CLICKEABLE -> despliega un GAMELOG por ciudad estilo
# app de apuestas (Fecha | Ganó WU | Pick bot | Resultado ✅/❌) para ver de un vistazo los EXACTOS,
# TOP-2, TOP-3 y las PERDIDAS de esa estacion. + timestamp de ultima actualizacion de la tabla.
#
# Reglas (coherentes con check_predictions.py / dashboard):
#   * Por (station, target) se scorea el forecast CONGELADO a la hora de entrada.
#   * Pick oficial = floor(mu_cal) -> su bucket (WU FLOOREA la obs SIEMPRE).
#   * top-2/3 = ranking de buckets por bucket_prob(mu-0.5, sigma, lo, hi).
#   * Resolucion MERCADO (Gamma outcomePrices) primaria; FISICA (obs IEM floreada) secundaria.
import collections
import json
import math
import os
import sys
import datetime as dt
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import STATION_META, CSS, ddmmyyyy, to_art, STATIONS               # noqa: E402
from check_predictions import resolved_buckets, fetch_obs_iem, winner_by_temp     # noqa: E402
from wxbt.market import bucket_prob                                               # noqa: E402
from wxbt.forward_scoring import frozen_forecast, audit_only_targets              # noqa: E402
from wxbt_nav import nav_html, NAV_CSS                                            # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")


def lbl_of(bucket, unit):
    """(lo,hi) -> etiqueta legible del bucket, coherente con el mercado."""
    if bucket is None:
        return "—"
    lo, hi = bucket
    deg = "°F" if unit == "F" else "°C"
    if lo is None:
        return f"≤{hi}{deg}"
    if hi is None:
        return f"≥{lo}{deg}"
    return (f"{lo}-{hi}{deg}" if lo != hi else f"{lo}{deg}")


def live_records(today):
    """DataFrame por (station, target) YA RESUELTO con exact/top2/top3/pwin/res + labels de pick y
    ganador y nivel (para el gamelog por ciudad)."""
    p = pd.read_csv(os.path.join(D, "predictions_forward.csv"), parse_dates=["target"])
    p["target"] = p["target"].dt.date
    due = p[p["target"] <= today].sort_values("lead_h").drop_duplicates(
        ["station", "target"], keep="first")  # fallback si el audit historico no existe
    try:
        with open(os.path.join(D, "forecast_audit.json"), encoding="utf-8") as fh:
            audit = json.load(fh)
    except (OSError, ValueError):
        audit = {}
    # UNIVERSO = acumulador UNION audit congelado: el acumulador puede perderse un dia entero
    # (Asia/NZ post-pico, primer dia de una ciudad nueva) aunque el freeze exista en el audit.
    cand = [(r.station, r.target, r.mu_cal, r.sigma_cal) for r in due.itertuples()]
    known = {(st, tg) for st, tg, _, _ in cand}
    sig_fb = p.groupby("station").sigma_cal.median().to_dict()   # sigma fallback filas solo-audit
    for st, tg in audit_only_targets(audit, known, today, STATIONS):
        cand.append((st, tg, float("nan"), sig_fb.get(st, 1.5)))
    resb = resolved_buckets([(st, tg) for st, tg, _, _ in cand])
    Cand = collections.namedtuple("Cand", "station target mu_cal sigma_cal")
    recs = []
    for r in (Cand(*c) for c in cand):
        info = resb.get((r.station, r.target))
        if not info or not info["buckets"]:
            continue
        buckets, winner, res = info["buckets"], info["winner"], "mercado"
        if winner is None and r.target < today:                 # fisica solo con el dia completo
            obs = fetch_obs_iem(r.station, r.target)
            if obs is not None:
                winner, res = winner_by_temp(buckets, int(math.floor(obs))), "fisica"
        if winner is None:
            continue                                            # todavia no resuelto -> no cuenta
        unit = STATIONS[r.station][3]
        mu, sigma, forecast_source = frozen_forecast(
            audit, r.station, r.target, r.mu_cal, r.sigma_cal)
        if forecast_source == "forward-fallback":
            continue  # sin evidencia point-in-time del pick operable: no entra al KPI oficial
        pick = winner_by_temp(buckets, int(math.floor(mu)))
        probs = [bucket_prob(mu - 0.5, sigma, lo, hi) for lo, hi in buckets]
        order = sorted(range(len(buckets)), key=lambda i: -probs[i])
        rank_w = order.index(buckets.index(winner)) + 1         # 1 = nuestro bucket mas probable
        exact = int(pick == winner)
        top2 = int(exact or rank_w <= 2)                        # EXACTO siempre cuenta como top-2
        top3 = int(top2 or rank_w <= 3)
        nivel = ("EXACTO" if exact else ("TOP-2" if top2 else ("TOP-3" if top3 else "PERDIDA")))
        recs.append(dict(station=r.station, target=r.target, res=res, exact=exact,
                         top2=top2, top3=top3, pwin=probs[buckets.index(winner)], nivel=nivel,
                         pick_lbl=lbl_of(pick, unit), win_lbl=lbl_of(winner, unit),
                         forecast_source=forecast_source))
    return pd.DataFrame(recs)


NIV_ICON = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌"}
NIV_CLS = {"EXACTO": "g-ex", "TOP-2": "g-t2", "TOP-3": "g-t3", "PERDIDA": "g-bad"}


def main():
    today = dt.date.today()
    print(f"Track record vivo al {today} (consultando ganadores en Gamma)...")
    df = live_records(today)
    try:
        bf = pd.read_csv(os.path.join(D, "backfill_check.csv"))
        bf = bf[(bf.lead == 2) & bf.max_real.notna()]
        lab = bf.groupby("station").hit_cal.mean().to_dict()
    except Exception:
        lab = {}
    # TODAS las estaciones activas aparecen aunque n=0 (ciudades nuevas esperando su primer
    # mercado resuelto) — antes eran invisibles y parecia que "no contaban".
    stations = sorted(set(df.station.unique() if len(df) else []) | set(lab) | set(STATIONS))

    rows, gamelog = [], {}
    for st in stations:
        g = df[df.station == st] if len(df) else pd.DataFrame()
        n = len(g)
        ex = int(g.exact.sum()) if n else 0
        t2 = int(g.top2.sum()) if n else 0
        t3 = int(g.top3.sum()) if n else 0
        pw = float(g.pwin.mean()) if n else float("nan")
        nfis = int((g.res == "fisica").sum()) if n else 0
        cont, pais, ciudad = STATION_META.get(st, ("?", "?", st))[:3]
        rows.append(dict(st=st, ciudad=ciudad, cont=cont, n=n, ex=ex, t2=t2, t3=t3,
                         pw=pw, nfis=nfis, lab=lab.get(st, float("nan"))))
        # gamelog por estacion: mas reciente primero
        gl = []
        for r in (g.sort_values("target", ascending=False).itertuples() if n else []):
            gl.append({"d": ddmmyyyy(r.target), "win": r.win_lbl, "pick": r.pick_lbl,
                       "niv": r.nivel, "fis": (r.res == "fisica")})
        gamelog[st] = gl
    rows.sort(key=lambda r: (-r["ex"], -r["t2"], -(r["pw"] if r["pw"] == r["pw"] else -1.0),
                             -r["t3"], r["st"]))

    def cls_of(r):
        if not r["n"]:
            return "wait"            # sin mercados resueltos aun: neutro, no "malo"
        if r["ex"] / r["n"] >= 0.5:
            return "top"
        return "ok" if r["ex"] >= 1 else "bad"

    def pct(x):
        return f"{x:.0%}" if x == x else "&mdash;"

    trs = []
    for i, r in enumerate(rows, 1):
        cls = cls_of(r)
        has_gl = len(gamelog.get(r["st"], [])) > 0
        caret = '<span class="caret">▸</span>' if has_gl else '<span class="caret" style="opacity:.2">·</span>'
        trs.append(
            f'<tr class="lbrow {cls}" data-st="{r["st"]}" data-has="{1 if has_gl else 0}">'
            f'<td class="rk">{i}</td>'
            f'<td class="stn">{caret}{r["st"]}<span>{r["ciudad"]} · {r["cont"]}</span></td>'
            f'<td class="num big">{r["ex"]}/{r["n"]}</td>'
            f'<td class="num">{r["t2"]}/{r["n"]}</td>'
            f'<td class="num">{r["t3"]}/{r["n"]}</td>'
            f'<td class="num">{pct(r["pw"])}</td>'
            f'<td class="num">{r["n"]}</td>'
            f'<td class="num lab">{pct(r["lab"])}</td></tr>')
        # fila-detalle (oculta) con el gamelog embebido
        if has_gl:
            gl_rows = "".join(
                f'<tr><td class="gd">{e["d"]}</td>'
                f'<td class="gw">{e["win"]}{" ·fís" if e["fis"] else ""}</td>'
                f'<td class="gp">{e["pick"]}</td>'
                f'<td class="gv {NIV_CLS[e["niv"]]}">{NIV_ICON[e["niv"]]} {e["niv"]}</td></tr>'
                for e in gamelog[r["st"]])
            trs.append(
                f'<tr class="glrow hidden" data-for="{r["st"]}"><td></td><td colspan="7">'
                f'<div class="glbox"><div class="glhead">📊 GAMELOG {r["st"]} · {r["ciudad"]} '
                f'— {r["ex"]} exacto{"s" if r["ex"]!=1 else ""} · {r["t2"]} top-2 · {r["t3"]} top-3 '
                f'de {r["n"]}</div>'
                f'<table class="gl"><thead><tr><th>fecha</th><th>ganó (WU)</th><th>pick bot</th>'
                f'<th>resultado</th></tr></thead><tbody>{gl_rows}</tbody></table></div></td></tr>')

    if len(df):
        d0, d1 = min(df.target), max(df.target)
        rango = f"{ddmmyyyy(d0)}&ndash;{ddmmyyyy(d1)}"
        rango_txt = f"{ddmmyyyy(d0)}..{ddmmyyyy(d1)}"
    else:
        rango = rango_txt = "sin targets resueltos aun"
    nfis_tot = sum(r["nfis"] for r in rows)
    nota_fis = (f" ({nfis_tot} resuelto{'s' if nfis_tot != 1 else ''} por obs IEM, "
                f"mercado sin ganador publicado)") if nfis_tot else ""
    # ULTIMA ACTUALIZACION (pedido Santiago): hora ART de esta regeneracion.
    updated = to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")

    body = f'''<div class="viz-root">
<div class="topbar">{nav_html("leaderboard")}<div class="row1"><h1>🏆 Track record VIVO del bot — WXBT</h1>
<span class="subt">ranking por RESULTADOS REALES contra el bucket ganador oficial de Polymarket
· targets resueltos {rango}</span></div>
<div class="updbar">🕒 Tabla actualizada: <b>{updated}</b> (hora Argentina) · se regenera cada corrida</div></div>
<p class="subt" style="margin:8px 0 14px"><b>Clic en cualquier fila</b> para ver el GAMELOG de esa
ciudad: cada mercado con lo que ganó (WU), el pick del bot y el resultado —
<span class="gv g-ex">✅ EXACTO</span> · <span class="gv g-t2">✅ TOP-2</span> ·
<span class="gv g-t3">🔶 TOP-3</span> · <span class="gv g-bad">❌ PÉRDIDA</span>. Ordenado por
<b>EXACTOS</b>, desempate TOP-2 y p ganador. <b>lab 60d</b> = hit del backfill (referencia).</p>
<table class="lb"><thead><tr><th>#</th><th>estación</th><th>EXACTOS</th><th>TOP-2</th>
<th>TOP-3</th><th>p ganador</th><th>n</th><th>lab 60d</th></tr></thead>
<tbody>{"".join(trs)}</tbody></table>
<p class="subt" style="margin-top:14px">Pick oficial = <code>floor(μ_cal)</code> del snapshot más
fresco (mín lead_h) — WU FLOOREA la obs siempre. Con pocos días esto es indicativo, no veredicto.
Regenerar: <code>python scripts/leaderboard.py</code></p></div>'''

    extra_css = '''
.viz-root table.lb{border-collapse:collapse;width:100%;font-size:13px;}
.viz-root table.lb th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:right;
  padding:6px 10px;border-bottom:1px solid var(--bd);letter-spacing:.04em;}
.viz-root table.lb th:nth-child(-n+2){text-align:left;}
.viz-root table.lb td{padding:8px 10px;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;}
.viz-root table.lb td.num{text-align:right;}
.viz-root table.lb td.big{font-size:17px;font-weight:700;}
.viz-root table.lb td.lab{font-size:11px;color:var(--mut);}
.viz-root tr.lbrow[data-has="1"]{cursor:pointer;}
.viz-root tr.lbrow[data-has="1"]:hover td{background:var(--s2);}
.viz-root tr.lbrow.open td{background:var(--s2);}
.viz-root .caret{display:inline-block;width:12px;color:var(--mut);font-size:10px;transition:transform .12s;}
.viz-root tr.lbrow.open .caret{transform:rotate(90deg);color:var(--fc);}
.viz-root .rk{color:var(--mut);font-weight:700;}
.viz-root .stn{font-weight:700;}
.viz-root .stn span{display:block;font-size:10.5px;color:var(--mut);font-weight:400;margin-left:12px;}
.viz-root tr.top td.big{color:var(--fin);} .viz-root tr.bad td.big{color:#d03b3b;}
.viz-root tr.wait td{color:var(--mut);} .viz-root tr.wait td.big{color:var(--mut);font-weight:400;}
.viz-root .updbar{font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px;}
.viz-root .updbar b{color:var(--live);}
.viz-root tr.glrow.hidden{display:none;}
.viz-root .glbox{padding:6px 4px 12px 22px;}
.viz-root .glhead{font-size:11px;color:var(--fc);font-family:var(--mono);letter-spacing:.05em;margin-bottom:6px;}
.viz-root table.gl{border-collapse:collapse;width:100%;max-width:560px;font-size:12px;}
.viz-root table.gl th{font-size:9px;color:var(--mut);text-transform:uppercase;text-align:left;
  padding:3px 10px;border-bottom:1px solid var(--bd);letter-spacing:.05em;}
.viz-root table.gl td{padding:5px 10px;border-bottom:1px solid var(--grid);font-family:var(--mono);}
.viz-root .gl .gd{color:var(--ink2);} .viz-root .gl .gw{color:var(--live);font-weight:700;}
.viz-root .gl .gp{color:var(--ink);}
.viz-root .gv{font-weight:700;white-space:nowrap;}
.viz-root .gv.g-ex{color:var(--fin);} .viz-root .gv.g-t2{color:#ffd23e;}
.viz-root .gv.g-t3{color:#ff8c42;} .viz-root .gv.g-bad{color:#d03b3b;}
.viz-root a{color:var(--mkt);}
'''
    lb_js = '''<script>
(function(){
  document.querySelectorAll('tr.lbrow[data-has="1"]').forEach(function(row){
    row.addEventListener('click',function(){
      var st=row.dataset.st;
      var det=document.querySelector('tr.glrow[data-for="'+st+'"]');
      if(!det) return;
      var open=det.classList.toggle('hidden')===false;
      row.classList.toggle('open',open);
    });
  });
})();
</script>'''
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Track record vivo</title>"
            f"<style>{CSS}{NAV_CSS}{extra_css}</style></head><body>{body}{lb_js}</body></html>")
    out = os.path.abspath(os.path.join(D, "leaderboard.html"))
    open(out, "w", encoding="utf-8").write(html)
    print(f"Leaderboard -> {out}   (actualizado {updated} AR)")
    print(f"Ranking vivo ({rango_txt}, n = targets resueltos):")
    for i, r in enumerate(rows, 1):
        pws = f"{r['pw']:.3f}" if r["pw"] == r["pw"] else "-"
        labs = f"{r['lab']:.0%}" if r["lab"] == r["lab"] else "-"
        print(f"  {i:2d}. {r['st']} ({r['ciudad']}): exactos {r['ex']}/{r['n']}  "
              f"top2 {r['t2']}/{r['n']}  top3 {r['t3']}/{r['n']}  pwin {pws}  lab60d {labs}")
    if len(df):
        src = df["forecast_source"].value_counts().to_dict()
        print("Fuente temporal del score: " + ", ".join(f"{k}={v}" for k, v in src.items()))


if __name__ == "__main__":
    main()
