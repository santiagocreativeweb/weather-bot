#!/usr/bin/env python3
# scripts/city_pages.py — DASHBOARD POR CIUDAD (pedido Santiago 2026-07-15: "arma dashboards por
# ciudades con el mercado y los analisis, o con el mapa de WU mostrando las PWS").
# Genera data/city_<ICAO>.html (una por estacion) + data/cities.html (indice). Cada pagina:
#   * mercado de HOY y MAÑANA: rango completo de Polymarket con mid, pbot y Δ¢ (edge BRUTO)
#   * prediccion del bot (congelada + snapshot) y los 12 modelos capturados pre-freeze
#   * performance de modelos EN ESA CIUDAD (vivo + retro, de wxbt_insights)
#   * historial gamelog (pick congelado vs lo que pago Polymarket)
#   * PWS de referencia: tabla de bias 180d + lectura EN VIVO bias-corregida + mini-mapa SVG
#   * grafico SVG 30 dias: obs real vs pick congelado
# Solo lectura. Regenerar: python scripts/city_pages.py [--no-live] [--station LIMC]
import argparse
import json
import math
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wxbt_insights as I                                            # noqa: E402
import dashboard as D                                                # noqa: E402
from show_live import STATIONS                                       # noqa: E402
import pws_setup as P                                                # noqa: E402
from wxbt_nav import nav_html, NAV_CSS                               # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

NIV_ICON = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌"}
NIV_CLS = {"EXACTO": "g-ex", "TOP-2": "g-t2", "TOP-3": "g-t3", "PERDIDA": "g-bad"}

EXTRA_CSS = """
/* indice de ciudades: grid con buscador/filtros */
.viz-root .cigrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(268px,1fr));gap:12px;margin-top:16px;}
.viz-root .ci-card{display:block;position:relative;background:linear-gradient(180deg,var(--s1),#0b1119);
  border:1px solid var(--bd);border-radius:var(--r);padding:13px 14px;box-shadow:var(--sh-1);
  transition:transform .15s,border-color .15s,box-shadow .15s;overflow:hidden;color:inherit;}
.viz-root .ci-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--tcol,var(--base));}
.viz-root .ci-card:hover{transform:translateY(-2px);border-color:var(--base);box-shadow:var(--sh-2);}
.viz-root .ci-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
.viz-root .ci-name{font-size:15px;font-weight:700;}
.viz-root .ci-sub{font-size:10px;color:var(--mut);font-family:var(--mono);margin-top:2px;}
.viz-root .ci-tier{font-size:15px;}
.viz-root .ci-track{font-size:12px;color:var(--ink2);font-family:var(--mono);margin-top:9px;}
.viz-root .ci-model{font-size:10.5px;color:var(--ink2);margin-top:5px;}
.viz-root .ci-model b{color:var(--fc);}
.viz-root .none{color:var(--mut);font-style:italic;padding:16px 0;}
.viz-root .cols{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start;}
.viz-root .col{flex:1 1 420px;min-width:340px;}
.viz-root .panelbox{background:var(--s1);border:1px solid var(--bd);border-radius:8px;
  padding:12px 14px;margin:12px 0;}
.viz-root .panelbox h4{margin:0 0 8px;font-size:11px;color:var(--fc);font-family:var(--mono);
  text-transform:uppercase;letter-spacing:.1em;}
.viz-root table.ct{border-collapse:collapse;width:100%;font-size:12.5px;}
.viz-root table.ct th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:right;
  padding:4px 8px;border-bottom:1px solid var(--bd);}
.viz-root table.ct th:first-child{text-align:left;}
.viz-root table.ct td{padding:5px 8px;border-bottom:1px solid var(--grid);font-family:var(--mono);
  font-variant-numeric:tabular-nums;}
.viz-root table.ct td.num{text-align:right;}
.viz-root table.ct tr.pick td{color:var(--fc);font-weight:700;}
.viz-root table.ct tr.win td{color:var(--fin);font-weight:700;}
.viz-root table.ct tr.dead td{color:var(--mut);text-decoration:line-through;}
.viz-root .gv{font-weight:700;white-space:nowrap;}
.viz-root .gv.g-ex{color:var(--fin);} .viz-root .gv.g-t2{color:#ffd23e;}
.viz-root .gv.g-t3{color:#ff8c42;} .viz-root .gv.g-bad{color:#d03b3b;}
.viz-root .links a{color:var(--mkt);margin-right:14px;}
.viz-root .est{font-size:20px;color:var(--live);font-family:var(--mono);font-weight:700;}
.viz-root svg text{font-family:var(--mono);}
.viz-root a{color:var(--mkt);}
"""


def esc(s):
    import html
    return html.escape(str(s), quote=False)


def market_table(code, d, info, mu, sg, live_max):
    unit = STATIONS[code][3]
    width = 2 if unit == "F" else 1
    if not info or not info.get("buckets"):
        return '<p class="subt">sin mercado para esta fecha.</p>'
    priced = [(lab, lo, hi, p) for lab, lo, hi, p in info["buckets"] if p is not None]

    def center(lo, hi):
        lo = lo if lo is not None else (hi - width if hi is not None else 0)
        hi = hi if hi is not None else lo + width
        return (lo + hi) / 2
    priced.sort(key=lambda x: center(x[1], x[2]))
    floor_live = int(math.floor(live_max)) if live_max is not None else None
    fb = int(math.floor(mu)) if mu is not None else None
    winner = info.get("winner")
    rows = []
    for lab, lo, hi, p in priced:
        pb = D.pbot_floor(mu, sg, lo, hi) if mu is not None else None
        cls = []
        if winner and lab == winner:
            cls.append("win")
        elif fb is not None and (lo is None or fb >= lo) and (hi is None or fb <= hi):
            cls.append("pick")
        if floor_live is not None and hi is not None and hi < floor_live:
            cls.append("dead")
        edge = f"{(pb - p) * 100:+.0f}" if pb is not None else "—"
        rows.append(f'<tr class="{" ".join(cls)}"><td>{esc(lab)}</td>'
                    f'<td class="num">{p:.2f}</td>'
                    f'<td class="num">{(f"{pb:.0%}" if pb is not None else "—")}</td>'
                    f'<td class="num">{edge}</td></tr>')
    win_note = f'<p class="subt">🏁 resuelto — gano <b>{esc(winner)}</b></p>' if winner else ""
    return (f'<table class="ct"><thead><tr><th>rango</th><th>mercado (mid)</th><th>p bot</th>'
            f'<th>Δ¢</th></tr></thead><tbody>{"".join(rows)}</tbody></table>{win_note}')


def pws_map_svg(code, ref, cur):
    """Mini-mapa: estacion (estrella) + PWS de referencia posicionadas por lat/lon."""
    if not ref:
        return ""
    lat0, lon0 = STATIONS[code][0], STATIONS[code][1]
    pts = [(float(r["lat"] or 0), float(r["lon"] or 0), r) for r in ref if r.get("lat")]
    if not pts:
        return ""
    lats = [p[0] for p in pts] + [lat0]
    lons = [p[1] for p in pts] + [lon0]
    pad_la = max((max(lats) - min(lats)) * 0.25, 0.01)
    pad_lo = max((max(lons) - min(lons)) * 0.25, 0.01)
    la0, la1 = min(lats) - pad_la, max(lats) + pad_la
    lo0, lo1 = min(lons) - pad_lo, max(lons) + pad_lo
    W, Hh = 360, 250

    def xy(la, lo):
        x = (lo - lo0) / (lo1 - lo0) * (W - 40) + 20
        y = (1 - (la - la0) / (la1 - la0)) * (Hh - 40) + 20
        return x, y
    out = [f'<svg viewBox="0 0 {W} {Hh}" width="100%" style="max-width:420px;background:#0a1016;'
           f'border:1px solid var(--bd);border-radius:6px">']
    sx, sy = xy(lat0, lon0)
    out.append(f'<text x="{sx}" y="{sy + 4}" text-anchor="middle" font-size="16" fill="#ffc247">★</text>')
    out.append(f'<text x="{sx}" y="{sy + 18}" text-anchor="middle" font-size="9" fill="#ffc247">{code}</text>')
    for la, lo, r in pts:
        x, y = xy(la, lo)
        t = cur.get(r["pws_id"])
        lbl = f'{t:.1f}°' if t is not None else f'{float(r["bias"]):+.1f}b'
        out.append(f'<circle cx="{x}" cy="{y}" r="5" fill="#38c6ff" opacity="0.85"/>')
        out.append(f'<text x="{x}" y="{y - 8}" text-anchor="middle" font-size="9" fill="#38c6ff">{lbl}</text>')
        out.append(f'<text x="{x}" y="{y + 14}" text-anchor="middle" font-size="7.5" '
                   f'fill="#587085">{esc(r["pws_id"])}</text>')
    out.append('</svg>')
    return "".join(out)


def chart_svg(code, hist_rows, obs_map, today, days=30):
    """Obs real (linea) vs pick congelado (puntos) ultimos `days` dias."""
    unit = STATIONS[code][3]
    d0 = today - dt.timedelta(days=days)
    xs = [d0 + dt.timedelta(days=k) for k in range(days + 1)]
    obs = [(d, obs_map.get((code, d.isoformat()))) for d in xs]
    mus = {r["target"]: r["mu"] for r in hist_rows if r["station"] == code}
    vals = [v for _, v in obs if v is not None] + [v for v in mus.values()]
    if not vals:
        return ""
    v0, v1 = min(vals) - 1.5, max(vals) + 1.5
    W, Hh = 640, 190

    def xy(d, v):
        x = (d - d0).days / days * (W - 70) + 45
        y = (1 - (v - v0) / (v1 - v0)) * (Hh - 45) + 12
        return x, y
    out = [f'<svg viewBox="0 0 {W} {Hh}" width="100%" style="max-width:720px;background:#0a1016;'
           f'border:1px solid var(--bd);border-radius:6px">']
    for gy in range(int(v0) + 1, int(v1) + 1, (2 if unit == "F" else 1) * (2 if v1 - v0 > 12 else 1)):
        _, y = xy(d0, gy)
        out.append(f'<line x1="45" x2="{W - 25}" y1="{y}" y2="{y}" stroke="#1a2836" stroke-width="1"/>')
        out.append(f'<text x="40" y="{y + 3}" text-anchor="end" font-size="9" fill="#587085">{gy}°</text>')
    pts = [xy(d, v) for d, v in obs if v is not None]
    if len(pts) > 1:
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        out.append(f'<polyline points="{path}" fill="none" stroke="#38c6ff" stroke-width="1.6"/>')
    for d, v in obs:
        if v is None:
            continue
        x, y = xy(d, v)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="#38c6ff"/>')
    for tgt, mu in mus.items():
        if not (d0 <= tgt <= today):
            continue
        x, y = xy(tgt, mu)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="none" stroke="#00e5a0" stroke-width="1.6"/>')
    for k in range(0, days + 1, 5):
        d = d0 + dt.timedelta(days=k)
        x, _ = xy(d, v0)
        out.append(f'<text x="{x:.0f}" y="{Hh - 4}" text-anchor="middle" font-size="8.5" '
                   f'fill="#587085">{d.strftime("%d/%m")}</text>')
    out.append(f'<text x="{W - 25}" y="12" text-anchor="end" font-size="9" fill="#38c6ff">— obs real</text>')
    out.append(f'<text x="{W - 25}" y="24" text-anchor="end" font-size="9" fill="#00e5a0">○ pick congelado</text>')
    out.append('</svg>')
    return "".join(out)


def build_city(code, today, mk, preds, audit, hist_rows, perf, obs_map, pws_ref, live_pws,
               live_obs):
    unit = STATIONS[code][3]
    deg = "°F" if unit == "F" else "°C"
    cont, pais, ciudad, slug, wupath = D.STATION_META[code]
    lat, lon = STATIONS[code][0], STATIONS[code][1]
    updated = D.to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")

    # -------- mercado hoy / mañana --------
    mkt_secs = []
    for d in (today, today + dt.timedelta(days=1)):
        info = mk.get(code, {}).get(d)
        froze = (audit.get(f"{code}|{d.isoformat()}") or {}).get("froze") or {}
        mu = sg = None
        frozen = False
        if froze.get("mu") is not None:
            mu, sg, frozen = froze["mu"], froze.get("sg") or 1.5, True
        elif preds.get((code, d)):
            mu, sg = preds[(code, d)]
        lm = (live_obs.get((code, d)) or {}).get("max")
        head = "HOY" if d == today else "MAÑANA"
        mu_txt = (f'μ <b>{mu:.1f}{deg}</b> σ {sg:.1f} {"🔒 congelado" if frozen else "◷ snapshot"}'
                  if mu is not None else "sin prediccion")
        lm_txt = f' · max en vivo: <b>{lm:.1f}{deg}</b>' if lm is not None else ""
        mkt_secs.append(f'<div class="panelbox"><h4>🎯 Mercado {head} — {D.fecha_es(d)}</h4>'
                        f'<p class="subt" style="margin:0 0 8px">{mu_txt}{lm_txt} · '
                        f'<a href="{I.pm_url(code, d)}" target="_blank">Polymarket ↗</a> · '
                        f'<a href="{D.wu_url(code, d)}" target="_blank">WU ↗</a></p>'
                        + market_table(code, d, info, mu, sg, lm) + '</div>')

    # -------- modelos capturados pre-freeze (hoy) --------
    caps = I.model_captures_pre_freeze().get((code, today)) or {}
    if caps:
        rows = "".join(f'<tr><td>{m}</td><td class="num">{v:.1f}{deg}</td></tr>'
                       for m, v in sorted(caps.items(), key=lambda kv: kv[1]))
        models_box = (f'<div class="panelbox"><h4>📦 Modelos pre-freeze de HOY</h4>'
                      f'<table class="ct"><tbody>{rows}</tbody></table></div>')
    else:
        models_box = ""

    # -------- performance de modelos en esta ciudad --------
    mine = [r for r in perf if r["station"] == code]
    perf_rows = []
    for src in ("vivo", "retro"):
        sub = sorted([r for r in mine if r["src"] == src],
                     key=lambda r: (-(r["rate"] if r["rate"] == r["rate"] else -1),
                                    r["mae"] if r["mae"] == r["mae"] else 99))
        for r in sub[:6]:
            mae = f"{r['mae']:.2f}" if r["mae"] == r["mae"] else "—"
            perf_rows.append(f'<tr><td>{r["model"]}</td><td class="num">{src}</td>'
                             f'<td class="num">{r["hits"]}/{r["n"]}</td>'
                             f'<td class="num">{r["rate"]:.0%}</td><td class="num">{mae}</td></tr>')
    perf_box = (f'<div class="panelbox"><h4>🧪 Que modelo acierta aca</h4>'
                f'<table class="ct"><thead><tr><th>modelo</th><th>fuente</th><th>exactos</th>'
                f'<th>%</th><th>MAE</th></tr></thead><tbody>{"".join(perf_rows)}</tbody></table>'
                f'<p class="subt" style="margin:6px 0 0">vivo = capturas reales pre-freeze · '
                f'retro = Previous-Runs 90d (bug #5, referencia).</p></div>') if perf_rows else ""

    # -------- historial gamelog --------
    mine_h = sorted([r for r in hist_rows if r["station"] == code],
                    key=lambda r: r["target"], reverse=True)
    gl_rows = []
    for r in mine_h[:14]:
        niv = r["nivel"]
        res = (f'<span class="gv {NIV_CLS[niv]}">{NIV_ICON[niv]} {niv}</span>' if niv
               else '<span class="subt">pendiente</span>')
        gl_rows.append(f'<tr><td>{r["target"].strftime("%d/%m")}</td>'
                       f'<td>{esc(r["pick_lbl"] or "—")}</td>'
                       f'<td>{esc(r.get("win_lbl") or "—")}</td><td>{res}</td></tr>')
    sc = [r for r in mine_h if r["nivel"]]
    ex = sum(r["nivel"] == "EXACTO" for r in sc)
    t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in sc)
    hist_box = (f'<div class="panelbox"><h4>🗓 Historial (desde 08/07) — {ex} exactos · {t2} top-2 '
                f'de {len(sc)}</h4><table class="ct"><thead><tr><th>fecha</th><th>pick 🔒</th>'
                f'<th>gano</th><th>resultado</th></tr></thead><tbody>{"".join(gl_rows)}</tbody>'
                f'</table></div>') if gl_rows else ""

    # -------- PWS --------
    ref = pws_ref.get(code) or []
    cur = live_pws.get(code) or {}
    if ref:
        est_vals = [(r["pws_id"], cur[r["pws_id"]], cur[r["pws_id"]] - float(r["bias"]))
                    for r in ref if r["pws_id"] in cur]
        est = (sorted(x[2] for x in est_vals)[len(est_vals) // 2] if est_vals else None)
        prow = []
        for r in ref:
            t = cur.get(r["pws_id"])
            prow.append(f'<tr><td>{esc(r["pws_id"])}</td>'
                        f'<td class="num">{float(r["dist_km"]):.1f}</td>'
                        f'<td class="num">{r["n"]}</td>'
                        f'<td class="num">{float(r["bias"]):+.2f}</td>'
                        f'<td class="num">{float(r["std"]):.2f}</td>'
                        f'<td class="num">{(f"{t:.1f}{deg}" if t is not None else "—")}</td></tr>')
        est_txt = (f'<p class="subt" style="margin:8px 0 0">estimado del sensor oficial AHORA: '
                   f'<span class="est">{est:.1f}{deg}</span> = mediana(PWS vivo − bias)</p>'
                   if est is not None else "")
        pws_box = (f'<div class="panelbox"><h4>📍 PWS de referencia (bias vs {code})</h4>'
                   f'<div class="cols"><div class="col">'
                   f'<table class="ct"><thead><tr><th>pws</th><th>km</th><th>n</th><th>bias</th>'
                   f'<th>σ</th><th>ahora</th></tr></thead><tbody>{"".join(prow)}</tbody></table>'
                   f'{est_txt}</div><div class="col">{pws_map_svg(code, ref, cur)}</div></div>'
                   f'<p class="subt" style="margin:6px 0 0">bias = mediana(PWS − estacion) en la '
                   f'ventana evaluada (hasta 180d, muestreado). σ baja = espejo confiable del '
                   f'sensor que resuelve. Setup: <code>python scripts/pws_setup.py --stations '
                   f'{code}</code></p></div>')
    else:
        pws_box = (f'<div class="panelbox"><h4>📍 PWS de referencia</h4><p class="subt">sin datos '
                   f'aun — correr <code>python scripts/pws_setup.py --stations {code}</code> '
                   f'(baja historial WU de las ~10 mas cercanas y elige las 5 mas estables).</p></div>')

    chart = chart_svg(code, hist_rows, obs_map, today)
    chart_box = (f'<div class="panelbox"><h4>📈 Ultimos 30 dias — obs real vs pick congelado</h4>'
                 f'{chart}</div>') if chart else ""

    body = f"""<div class="viz-root">
<div class="topbar">{nav_html("cities")}<div class="row1"><h1>🏙 {esc(ciudad)} · {code}</h1>
<span class="subt">{esc(pais)} · {cont} · {lat:.4f}, {lon:.4f} · resolucion WU {code}</span></div>
<div class="links" style="margin-top:6px;font-size:12px">
<a href="cities.html">← todas las ciudades</a>
<a href="https://www.windy.com/{lat:.3f}/{lon:.3f}" target="_blank">Windy ↗</a>
<a href="https://zoom.earth/maps/temperature/#view={lat:.2f},{lon:.2f},9z" target="_blank">Zoom Earth ↗</a>
<a href="https://www.wunderground.com/dashboard/pws/{(pws_ref.get(code) or [{}])[0].get('pws_id', '')}" target="_blank">WU PWS ↗</a>
</div>
<div style="font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px">
🕒 {updated} (AR) · regenerar: <code>python scripts/city_pages.py --station {code}</code></div></div>
<div class="cols"><div class="col">{"".join(mkt_secs)}{models_box}</div>
<div class="col">{pws_box}{perf_box}{hist_box}</div></div>
{chart_box}
<p class="subt">Δ¢ = p bot − mid, edge BRUTO sin fees/spread/shrink — screener, no señal.
El pick mostrado es el CONGELADO del audit cuando existe (lo que se opera y se mide).</p></div>"""
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · {esc(ciudad)}</title><style>{D.CSS}{NAV_CSS}{EXTRA_CSS}</style></head>"
            f"<body>{body}</body></html>")


def build_index(today, stability_rows):
    upd = D.to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")
    by_st = {r["station"]: r for r in stability_rows}
    best = _read_rank()
    try:
        from playbook import STRONG, WEAK
    except Exception:
        STRONG, WEAK = set(), set()
    tinfo = {"FUERTE": ("🟢", "var(--fin)"), "MEDIA": ("🟡", "var(--t2)"), "DEBIL": ("🔴", "var(--red)")}
    conts = sorted({D.STATION_META[c][0] for c in STATIONS})
    cards = []
    for code in sorted(STATIONS, key=lambda c: D.STATION_META[c][2]):
        cont, pais, ciudad = D.STATION_META[code][:3]
        r = by_st.get(code)
        tier = "FUERTE" if code in STRONG else ("DEBIL" if code in WEAK else "MEDIA")
        tico, tcol = tinfo[tier]
        if r and r["n"]:
            track = (f'<b style="color:var(--fc)">{r["exact"]}/{r["n"]}</b> exactos · '
                     f'{r["top2"]}/{r["n"]} top-2')
        else:
            track = '<span style="color:var(--mut)">sin track aún</span>'
        bm = best.get(code)
        bmodel = (f'<div class="ci-model">🏅 mejor modelo: <b>{esc(bm[0])}</b> '
                  f'{bm[1]:.0%} <span style="color:var(--mut)">(n={bm[2]}, {bm[3]})</span></div>'
                  if bm else '')
        cards.append(
            f'<a class="ci-card" href="city_{code}.html" style="--tcol:{tcol}" '
            f'data-cont="{cont}" data-tier="{tier}" data-q="{esc(ciudad).lower()} {code.lower()} {esc(pais).lower()}">'
            f'<div class="ci-top"><div><div class="ci-name">{esc(ciudad)}</div>'
            f'<div class="ci-sub">{code} · {esc(pais)} · {cont}</div></div>'
            f'<span class="ci-tier" style="color:{tcol}">{tico}</span></div>'
            f'<div class="ci-track">{track}</div>{bmodel}</a>')
    body = f"""<div class="viz-root">
<div class="topbar">{nav_html("cities")}<div class="row1"><h1>🏙 Ciudades</h1>
<span class="subt">dashboard individual por ciudad: mercado, modelos que mejor aciertan, PWS y track</span>
<span class="clock" style="margin-left:auto">{upd}<small>AR</small></span></div>
<div class="vfilters">
<button class="chip on" data-f="all">Todas ({len(STATIONS)})</button>
{"".join(f'<button class="chip" data-f="{c}">{c}</button>' for c in conts)}
<input type="search" id="csearch" placeholder="buscar ciudad, país o ICAO…"
  style="background:var(--s2);color:var(--ink);border:1px solid var(--bd);border-radius:6px;padding:6px 10px;font-size:12px;margin-left:auto;min-width:220px">
<span class="count" id="ccount"></span></div></div>
<p class="subt" style="margin:10px 0 0">🟢 fuerte (operable) · 🟡 media · 🔴 débil (no operar). El
<b>mejor modelo</b> por ciudad = el que más veces acertó el bucket ganador ahí (vivo pre-freeze o retro).</p>
<div class="cigrid" id="cigrid">{"".join(cards)}</div>
<p class="none" id="cnone" style="display:none">Sin ciudades para ese filtro.</p></div>"""
    cjs = """<script>
(function(){
  var grid=document.getElementById('cigrid'),cnt=document.getElementById('ccount');
  var srch=document.getElementById('csearch'),none=document.getElementById('cnone'),cont='all';
  function apply(){
    var q=(srch.value||'').trim().toLowerCase(),n=0;
    grid.querySelectorAll('.ci-card').forEach(function(c){
      var okc=(cont==='all'||c.dataset.cont===cont);
      var okq=(!q||c.dataset.q.indexOf(q)>=0);
      var show=okc&&okq;c.style.display=show?'':'none';if(show)n++;
    });
    cnt.textContent=n+' ciudades';none.style.display=n?'none':'';
  }
  document.querySelectorAll('.chip[data-f]').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.chip[data-f]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on');cont=b.dataset.f;apply();
    });
  });
  srch.addEventListener('input',apply);apply();
})();
</script>"""
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Ciudades</title><style>{D.CSS}{NAV_CSS}{EXTRA_CSS}</style></head>"
            f"<body>{body}{cjs}</body></html>")


def _read_rank():
    """{station: (model, rate, n, src)} del rank #1 por ciudad (model_city_rank.csv, n>=5)."""
    import csv as _csv
    out = {}
    p = os.path.join(DATA, "model_city_rank.csv")
    if not os.path.exists(p):
        return out
    for r in _csv.DictReader(open(p, encoding="utf-8")):
        if r.get("rank") == "1" and int(r["n"]) >= 5:
            out[r["station"]] = (r["model"], float(r["rate"]), int(r["n"]), r["src"])
    return out


def main(a):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    codes = [a.station.upper()] if a.station else list(STATIONS)
    print(f"Generando paginas por ciudad ({len(codes)})...")
    mk = D.fetch_market_full(today, 1)
    preds = D.load_preds(today)
    audit = I._load_audit()
    hist_rows = I.bot_history(refresh=a.refresh, today=today)
    perf = I.model_perf(days=90, today=today)
    # el ranking de modelos por ciudad (badge del dashboard + telegram) se refresca aca desde que
    # se saco la tab Modelos (2026-07-15): reusa el `perf` ya calculado, no re-computa.
    try:
        I.write_model_rank(perf=perf)
    except Exception as e:
        print(f"[WARN] model_city_rank.csv: {e}", file=sys.stderr)
    live_obs = D.fetch_obs_live(today) if not a.no_live else {}
    obs_map = {}
    obs_path = os.path.join(DATA, "obs.csv")
    if os.path.exists(obs_path):
        import csv as _csv
        for r in _csv.DictReader(open(obs_path, encoding="utf-8")):
            obs_map[(r["station"], r["date"])] = float(r["tmax"])
    # top-up de obs recientes desde winners (mismos valores que scorean el historial)
    for (st, d), w in I.load_winners(today=today).items():
        if w.get("max_real") is not None:
            obs_map.setdefault((st, d.isoformat()), w["max_real"])
    pws_ref = P.read_reference()
    live_pws = {}
    if not a.no_live:
        for code in codes:
            ref = pws_ref.get(code)
            if ref:
                live_pws[code] = P.pws_current([r["pws_id"] for r in ref], STATIONS[code][3])
    for code in codes:
        html = build_city(code, today, mk, preds, audit, hist_rows, perf, obs_map,
                          pws_ref, live_pws, live_obs)
        out = os.path.join(DATA, f"city_{code}.html")
        open(out, "w", encoding="utf-8").write(html)
    idx = build_index(today, I.stability(hist=hist_rows))
    open(os.path.join(DATA, "cities.html"), "w", encoding="utf-8").write(idx)
    print(f"OK -> data/city_<ICAO>.html ({len(codes)}) + data/cities.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Paginas por ciudad: mercado + modelos + PWS + historial.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--station", default=None, help="solo esa estacion (default: todas)")
    ap.add_argument("--no-live", action="store_true", help="sin obs/PWS en vivo (mas rapido)")
    ap.add_argument("--refresh", action="store_true", help="completar ganadores desde Gamma")
    main(ap.parse_args())
