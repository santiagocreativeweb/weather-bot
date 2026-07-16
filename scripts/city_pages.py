#!/usr/bin/env python3
# scripts/city_pages.py — DASHBOARD POR CIUDAD v2 (rediseño 2026-07-16, pedidos Santiago):
#   * CARDS de estadisticas: aciertos exactos, % top-2 (n intentos), max registrada del dia y
#     temperatura actual (UNA sola card si coinciden), pick fijado de hoy.
#   * MAPA REAL con Leaflet + tiles dark de CARTO (basemaps.cartocdn.com, (c) CARTO/OSM) con la
#     estacion y las PWS de referencia con su temperatura en vivo.
#   * GRAFICOS INTERACTIVOS (Chart.js): timeline del mercado 24h/48h (precios por bucket + μ del
#     bot) y obs real vs pick congelado 30 dias.
#   * Picks fijados 24h y 48h (froze / froze48 del audit) para los proximos dias.
# CDN: Leaflet 1.9 (unpkg) + Chart.js 4 (jsdelivr) — requieren internet al abrir la pagina.
# Regenerar: python scripts/city_pages.py [--no-live] [--station HKO]
import argparse
import html
import json
import math
import os
import sys
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import requests                                                      # noqa: E402
import wxbt_insights as I                                            # noqa: E402
import dashboard as D                                                # noqa: E402
from show_live import STATIONS, PEAK_HOUR, local_offset             # noqa: E402
import pws_setup as P                                                # noqa: E402
from wxbt_nav import nav_html, NAV_CSS                               # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
CLOB = "https://clob.polymarket.com"

LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
CHARTJS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"
CARTO_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
CARTO_ATTR = ('&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> · '
              '&copy; <a href="https://carto.com/attributions">CARTO</a>')

NIV_ICON = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌"}
NIV_CLS = {"EXACTO": "g-ex", "TOP-2": "g-t2", "TOP-3": "g-t3", "PERDIDA": "g-bad"}
WMO = {0: ("☀️", "Despejado"), 1: ("🌤", "Mayormente despejado"), 2: ("⛅", "Parcialmente nublado"),
       3: ("☁️", "Nublado"), 45: ("🌫", "Niebla"), 48: ("🌫", "Niebla"),
       51: ("🌦", "Llovizna leve"), 53: ("🌦", "Llovizna"), 55: ("🌧", "Llovizna intensa"),
       61: ("🌧", "Lluvia leve"), 63: ("🌧", "Lluvia"), 65: ("🌧", "Lluvia fuerte"),
       71: ("🌨", "Nieve leve"), 73: ("🌨", "Nieve"), 75: ("❄️", "Nieve fuerte"),
       80: ("🌦", "Chaparrones"), 81: ("🌧", "Chaparrones"), 82: ("⛈", "Chaparrones fuertes"),
       95: ("⛈", "Tormenta"), 96: ("⛈", "Tormenta"), 99: ("⛈", "Tormenta fuerte")}

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
/* pagina de ciudad v2 */
.viz-root .cols{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start;}
.viz-root .col{flex:1 1 420px;min-width:340px;}
.viz-root .panelbox{background:linear-gradient(180deg,var(--s1),#0b1119);border:1px solid var(--bd);
  border-radius:var(--r);padding:14px 16px;margin:12px 0;box-shadow:var(--sh-1);}
.viz-root .panelbox h4{margin:0 0 10px;font-size:11px;color:var(--fc);font-family:var(--mono);
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
.viz-root #citymap{height:420px;border-radius:var(--r-sm);border:1px solid var(--bd);z-index:1;}
.viz-root .chartbox{position:relative;height:300px;}
.viz-root .chartbox.tall{height:340px;}
.viz-root .tlbtns{display:flex;gap:6px;margin-bottom:8px;}
.viz-root .pickrow{font-size:12.5px;font-family:var(--mono);padding:5px 0;border-bottom:1px solid var(--grid);}
.viz-root .pickrow b{color:var(--fc);}
.leaflet-container{background:#0a1016;font-family:inherit;}
.leaflet-tooltip.pwstip{background:#0e151d;color:#e8f0f7;border:1px solid #2b3f52;border-radius:6px;
  font-family:"JetBrains Mono","Consolas",monospace;font-size:11px;}
.leaflet-tooltip.pwstip::before{display:none;}
"""


def esc(s):
    return html.escape(str(s), quote=False)


def current_weather(code, unit):
    """(emoji, texto, temp_actual) de Open-Meteo current (para HKO, temp exacta del Observatory)."""
    lat, lon = STATIONS[code][0], STATIONS[code][1]
    out = ("🌡", "—", None)
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params=dict(latitude=lat, longitude=lon,
                                     current="temperature_2m,weather_code",
                                     temperature_unit=("fahrenheit" if unit == "F" else "celsius")),
                         timeout=15)
        cur = r.json().get("current", {})
        ico, txt = WMO.get(int(cur.get("weather_code", -1)), ("🌡", "—"))
        out = (ico, txt, cur.get("temperature_2m"))
    except Exception:
        pass
    if code == "HKO":
        try:
            import hko_source
            t = hko_source.live_now()
            if t is not None:
                out = (out[0], out[1], t)
        except Exception:
            pass
    return out


def market_timeline(code, d, hours=48, max_buckets=8):
    """Series de precios del mercado (CLOB prices-history) para el chart interactivo 24/48h.
    {labels:[bucket], times:[epoch], series:{bucket:[px]}, mu:[...]} — None si no hay mercado."""
    slug = D.pm_slug(code, d)
    try:
        r = requests.get(f"{D.GAMMA}/events", params={"slug": slug}, timeout=25)
        evs = r.json() if r.status_code == 200 else []
    except Exception:
        evs = []
    if not evs:
        return None
    now_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    start = now_ts - hours * 3600
    toks = []
    for mk in evs[0].get("markets", []):
        lab = mk.get("groupItemTitle")
        try:
            tok = json.loads(mk.get("clobTokenIds") or "[]")[0]
            last = D._mkt_price(mk) or 0
        except Exception:
            continue
        if lab and tok:
            toks.append((lab, tok, last))
    toks.sort(key=lambda x: -x[2])
    toks = toks[:max_buckets]

    def fetch(t):
        lab, tok, _ = t
        try:
            hh = requests.get(f"{CLOB}/prices-history",
                              params={"market": tok, "startTs": start, "endTs": now_ts,
                                      "fidelity": 30}, timeout=20).json().get("history", [])
        except Exception:
            hh = []
        return lab, hh
    with ThreadPoolExecutor(max_workers=6) as tp:
        hist = dict(tp.map(fetch, toks))
    grid = [start + i * 1800 for i in range((hours * 2) + 1)]
    series = {}
    for lab, hh in hist.items():
        out, j, lastp = [], 0, None
        for t in grid:
            while j < len(hh) and hh[j]["t"] <= t:
                lastp = hh[j]["p"]; j += 1
            out.append(round(lastp, 3) if lastp is not None else None)
        series[lab] = out
    # μ del bot desde el audit (revisiones + freeze clavado)
    key = f"{code}|{d.isoformat()}"
    rec = I._load_audit().get(key) or {}
    revs = []
    for ts_s, mu in rec.get("hist", []):
        if ts_s == "snapshot":
            revs.append((grid[0] - 1, float(mu)))
            continue
        try:
            dd_, hhmm = ts_s.split(" ")
            day, mon = dd_.split("/")
            hh_, mm_ = hhmm.split(":")
            t_art = dt.datetime(d.year, int(mon), int(day), int(hh_), int(mm_))
            revs.append((int((t_art + dt.timedelta(hours=3)).replace(
                tzinfo=dt.timezone.utc).timestamp()), float(mu)))
        except Exception:
            continue
    revs.sort()
    frz = int(D.freeze_utc(code, d).replace(tzinfo=dt.timezone.utc).timestamp())
    froze_mu = (rec.get("froze") or {}).get("mu")
    mu_series = []
    for t in grid:
        cur = None
        for rt, mv in revs:
            if rt <= t:
                cur = mv
        if t >= frz and froze_mu is not None:
            cur = froze_mu
        mu_series.append(round(cur, 1) if cur is not None else None)
    return {"labels": list(series.keys()), "times": grid, "series": series, "mu": mu_series,
            "frz": frz}


def stat_card(lbl, big, sub, cls=""):
    return (f'<div class="scard {cls}"><div class="lbl">{lbl}</div>'
            f'<div class="big">{big}</div><div class="sub">{sub}</div></div>')


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
    win_note = f'<p class="subt">🏁 resuelto — ganó <b>{esc(winner)}</b></p>' if winner else ""
    return (f'<table class="ct"><thead><tr><th>rango</th><th>mercado (mid)</th><th>p bot</th>'
            f'<th>Δ¢</th></tr></thead><tbody>{"".join(rows)}</tbody></table>{win_note}')


def build_city(code, today, mk, preds, audit, hist_rows, perf, obs_map, pws_ref, live_pws,
               live_obs, rank_rows, no_live=False):
    unit = STATIONS[code][3]
    deg = "°F" if unit == "F" else "°C"
    cont, pais, ciudad = D.STATION_META[code][:3]
    lat, lon = STATIONS[code][0], STATIONS[code][1]
    now_utc = dt.datetime.now(dt.timezone.utc)
    updated = D.to_art(now_utc).strftime("%d/%m/%Y %H:%M")
    d_local = (now_utc.replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, today))).date()
    d_mkt = d_local if mk.get(code, {}).get(d_local) else today

    # ---------- stat cards (pedido explicito) ----------
    mine = [r for r in hist_rows if r["station"] == code and r["nivel"]]
    n = len(mine)
    ex = sum(r["nivel"] == "EXACTO" for r in mine)
    t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in mine)
    ico, wtxt, tnow = ("🌡", "—", None) if no_live else current_weather(code, unit)
    lm = (live_obs.get((code, d_local)) or live_obs.get((code, today)) or {}).get("max")
    rec_hoy = audit.get(f"{code}|{d_mkt.isoformat()}") or {}
    froze = rec_hoy.get("froze") or {}
    pick_hoy = (f"{_pick_lbl(code, froze['mu'])} (μ {froze['mu']:.1f}{deg})"
                if froze.get("mu") is not None else "aún no fijado")
    cards = [
        stat_card("✅ aciertos exactos", f"{ex}/{n}" if n else "—",
                  f"{ex / n:.0%} de {n} mercados" if n else "sin resueltos aún"),
        stat_card("🟡 acierto top-2", f"{t2 / n:.0%}" if n else "—",
                  f"{t2}/{n} intentos" if n else "sin resueltos aún", "y"),
    ]
    same = (tnow is not None and lm is not None and abs(float(tnow) - float(lm)) < 0.05)
    if same:
        cards.append(stat_card("🌡 temperatura", f"{float(tnow):.1f}{deg}",
                               "la actual ES la máxima registrada hasta el momento"))
    else:
        if lm is not None:
            cards.append(stat_card("🔺 máx registrada hoy", f"{float(lm):.1f}{deg}",
                                   f"día local {d_local.strftime('%d/%m')}"))
        if tnow is not None:
            cards.append(stat_card("🌡 temperatura actual", f"{float(tnow):.1f}{deg}",
                                   f"{ico} {wtxt}"))
    cards.append(stat_card("🔒 pick de hoy", pick_hoy.split(" (")[0],
                           pick_hoy.split("(")[1].rstrip(")") if "(" in pick_hoy else "se fija 04:30 local"))
    pos = next((i + 1 for i, r in enumerate(rank_rows) if r["station"] == code), None)
    if pos:
        cards.append(stat_card("🏆 estabilidad", f"#{pos}", f"de {len(rank_rows)} ciudades"))
    cards_html = f'<div class="sgrid">{"".join(cards)}</div>'

    # ---------- picks fijados 24h/48h proximos dias ----------
    pk_rows = []
    for k in range(0, 3):
        d = d_mkt + dt.timedelta(days=k)
        rec = audit.get(f"{code}|{d.isoformat()}") or {}
        f24, f48 = rec.get("froze") or {}, rec.get("froze48") or {}
        pr = preds.get((code, d))
        if f24.get("mu") is not None:
            pk_rows.append(f'<div class="pickrow">{d.strftime("%d/%m")} · '
                           f'<b>{_pick_lbl(code, f24["mu"])}</b> (μ {f24["mu"]:.1f}{deg}) 🔒 fijado 24h</div>')
        elif f48.get("mu") is not None:
            pk_rows.append(f'<div class="pickrow">{d.strftime("%d/%m")} · '
                           f'<b>{_pick_lbl(code, f48["mu"])}</b> (μ {f48["mu"]:.1f}{deg}) ⏳ fijado 48h '
                           f'<span style="color:var(--mut)">(el definitivo se fija 04:30 local)</span></div>')
        elif pr:
            pk_rows.append(f'<div class="pickrow">{d.strftime("%d/%m")} · '
                           f'{_pick_lbl(code, pr[0])} (μ {pr[0]:.1f}{deg}) ◷ preliminar</div>')
    picks_box = (f'<div class="panelbox"><h4>🔒 Picks fijados (24h y 48h)</h4>{"".join(pk_rows)}'
                 f'<p class="subt" style="margin:8px 0 0">24h = fijado 04:30 local del día del '
                 f'mercado (lo que se opera y se mide). 48h = fijado un día antes — lo mide el '
                 f'tab 48hs de 📊 Estadísticas.</p></div>') if pk_rows else ""

    # ---------- mercado hoy / mañana ----------
    mkt_secs = []
    for d in (d_mkt, d_mkt + dt.timedelta(days=1)):
        info = mk.get(code, {}).get(d)
        rec = audit.get(f"{code}|{d.isoformat()}") or {}
        fr = rec.get("froze") or {}
        mu = sg = None
        frozen = False
        if fr.get("mu") is not None:
            mu, sg, frozen = fr["mu"], fr.get("sg") or 1.5, True
        elif preds.get((code, d)):
            mu, sg = preds[(code, d)]
        lmx = (live_obs.get((code, d)) or {}).get("max")
        head = "HOY" if d == d_mkt else "MAÑANA"
        mu_txt = (f'μ <b>{mu:.1f}{deg}</b> σ {sg:.1f} {"🔒" if frozen else "◷"}'
                  if mu is not None else "sin predicción")
        lm_txt = f' · máx en vivo: <b>{lmx:.1f}{deg}</b>' if lmx is not None else ""
        mkt_secs.append(f'<div class="panelbox"><h4>🎯 Mercado {head} — {D.fecha_es(d)}</h4>'
                        f'<p class="subt" style="margin:0 0 8px">{mu_txt}{lm_txt} · '
                        f'<a href="{I.pm_url(code, d)}" target="_blank">Polymarket ↗</a> · '
                        f'<a href="{D.wu_url(code, d)}" target="_blank">'
                        f'{"HKO ↗" if code == "HKO" else "WU ↗"}</a></p>'
                        + market_table(code, d, info, mu, sg, lmx) + '</div>')

    # ---------- timeline interactivo del mercado (24h/48h) ----------
    tl = None if no_live else market_timeline(code, d_mkt, hours=48)
    tl_box = ""
    if tl and tl["labels"]:
        tl_box = (f'<div class="panelbox"><h4>⏱ Timeline del mercado — {D.fecha_es(d_mkt)}</h4>'
                  f'<div class="tlbtns"><button class="chip on" data-h="24">24 hs</button>'
                  f'<button class="chip" data-h="48">48 hs</button></div>'
                  f'<div class="chartbox tall"><canvas id="tlchart"></canvas></div>'
                  f'<p class="subt" style="margin:6px 0 0">precios por bucket (CLOB, pasos de 30 '
                  f'min, hora AR) + μ del bot (línea punteada verde; clavada desde el 🔒). '
                  f'Interactivo: hover para valores, click en la leyenda para ocultar series.</p></div>')

    # ---------- mapa Leaflet + CARTO ----------
    ref = pws_ref.get(code) or []
    cur = live_pws.get(code) or {}
    pws_json = [dict(id=r["pws_id"], lat=float(r["lat"] or 0), lon=float(r["lon"] or 0),
                     bias=float(r["bias"]), std=float(r["std"]), km=float(r["dist_km"] or 0),
                     now=cur.get(r["pws_id"])) for r in ref if r.get("lat")]
    est_vals = sorted(v["now"] - v["bias"] for v in pws_json if v["now"] is not None)
    est = est_vals[len(est_vals) // 2] if est_vals else None
    prow = []
    for r in ref:
        t = cur.get(r["pws_id"])
        prow.append(f'<tr><td>{esc(r["pws_id"])}</td>'
                    f'<td class="num">{float(r["dist_km"]):.1f}</td>'
                    f'<td class="num">{float(r["bias"]):+.2f}</td>'
                    f'<td class="num">{float(r["std"]):.2f}</td>'
                    f'<td class="num">{(f"{t:.1f}{deg}" if t is not None else "—")}</td></tr>')
    est_txt = (f'<p class="subt" style="margin:8px 0 0">estimado del sensor oficial AHORA: '
               f'<b style="color:var(--live);font-size:16px">{est:.1f}{deg}</b> = mediana(PWS − bias)</p>'
               if est is not None else "")
    map_box = (f'<div class="panelbox"><h4>🗺 Estación + PWS de referencia (mapa CARTO)</h4>'
               f'<div id="citymap"></div>{est_txt}'
               + (f'<table class="ct" style="margin-top:10px"><thead><tr><th>pws</th><th>km</th>'
                  f'<th>bias</th><th>σ</th><th>ahora</th></tr></thead><tbody>{"".join(prow)}</tbody></table>'
                  if prow else '<p class="subt">sin PWS de referencia aún — correr '
                               f'<code>python scripts/pws_setup.py --stations {code}</code></p>')
               + '</div>')

    # ---------- obs vs pick 30d (chart.js) ----------
    d0 = today - dt.timedelta(days=30)
    obs_series = []
    for k in range(31):
        d = d0 + dt.timedelta(days=k)
        v = obs_map.get((code, d.isoformat()))
        obs_series.append(dict(x=d.isoformat(), y=v))
    picks_series = [dict(x=r["target"].isoformat(), y=r["mu"])
                    for r in hist_rows if r["station"] == code and d0 <= r["target"] <= today]
    hist_chart = (f'<div class="panelbox"><h4>📈 Últimos 30 días — obs real vs pick congelado</h4>'
                  f'<div class="chartbox"><canvas id="histchart"></canvas></div></div>')

    # ---------- modelos + performance + gamelog ----------
    caps = I.model_captures_pre_freeze().get((code, d_mkt)) or {}
    models_box = ""
    if caps:
        rows = "".join(f'<tr><td>{m}</td><td class="num">{v:.1f}{deg}</td></tr>'
                       for m, v in sorted(caps.items(), key=lambda kv: kv[1]))
        models_box = (f'<div class="panelbox"><h4>📦 Modelos pre-freeze de HOY</h4>'
                      f'<table class="ct"><tbody>{rows}</tbody></table></div>')
    mine_p = [r for r in perf if r["station"] == code]
    perf_rows = []
    for src in ("vivo", "retro"):
        sub = sorted([r for r in mine_p if r["src"] == src],
                     key=lambda r: (-(r["rate"] if r["rate"] == r["rate"] else -1),
                                    r["mae"] if r["mae"] == r["mae"] else 99))
        for r in sub[:6]:
            mae = f"{r['mae']:.2f}" if r["mae"] == r["mae"] else "—"
            perf_rows.append(f'<tr><td>{r["model"]}</td><td class="num">{src}</td>'
                             f'<td class="num">{r["hits"]}/{r["n"]}</td>'
                             f'<td class="num">{r["rate"]:.0%}</td><td class="num">{mae}</td></tr>')
    perf_box = (f'<div class="panelbox"><h4>🧪 Qué modelo acierta acá</h4>'
                f'<table class="ct"><thead><tr><th>modelo</th><th>fuente</th><th>exactos</th>'
                f'<th>%</th><th>MAE</th></tr></thead><tbody>{"".join(perf_rows)}</tbody></table>'
                f'<p class="subt" style="margin:6px 0 0">los modelos que MÁS pesan para el '
                f'pronóstico son los que mejor aciertan en ESTA ciudad. vivo = capturas reales '
                f'pre-freeze · retro = Previous-Runs 90d (referencia).</p></div>') if perf_rows else ""
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
    hist_box = (f'<div class="panelbox"><h4>🗓 Historial — {ex} exactos · {t2} top-2 de {n}</h4>'
                f'<table class="ct"><thead><tr><th>fecha</th><th>pick 🔒</th>'
                f'<th>ganó</th><th>resultado</th></tr></thead><tbody>{"".join(gl_rows)}</tbody>'
                f'</table></div>') if gl_rows else ""

    resol_note = ('Resolución: <b>Hong Kong Observatory</b>, máx diaria a 1 decimal '
                  '(weather.gov.hk — NO WU/aeropuerto)' if code == "HKO"
                  else f'Resolución WU: estación <b>{code}</b>')
    payload = dict(tl=tl, obs=obs_series, picks=picks_series, deg=deg,
                   station=dict(lat=lat, lon=lon, code=code), pws=pws_json)
    body = f"""<div class="viz-root">
<div class="topbar">{nav_html("cities")}<div class="row1"><h1>🏙 {esc(ciudad)} · {code}</h1>
<span class="subt">{esc(pais)} · {cont} · {resol_note}</span></div>
<div class="links" style="margin-top:6px;font-size:12px">
<a href="cities.html">← todas las ciudades</a>
<a href="{I.pm_url(code, d_mkt)}" target="_blank">Polymarket ↗</a>
<a href="https://www.windy.com/{lat:.3f}/{lon:.3f}" target="_blank">Windy ↗</a>
<a href="https://zoom.earth/maps/temperature/#view={lat:.2f},{lon:.2f},9z" target="_blank">Zoom Earth ↗</a>
</div>
<div style="font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px">
🕒 {updated} (AR) · regenerar: <code>python scripts/city_pages.py --station {code}</code></div></div>
{cards_html}
{tl_box}
<div class="cols"><div class="col">{"".join(mkt_secs)}{picks_box}{models_box}</div>
<div class="col">{map_box}{perf_box}{hist_box}</div></div>
{hist_chart}
<p class="subt">Δ¢ = p bot − mid, edge BRUTO sin fees/spread — screener, no señal. El pick
mostrado es el CONGELADO del audit (lo que se opera y se mide).</p></div>
<script>window.__CITY = {json.dumps(payload)};</script>
<script src="{LEAFLET_JS}"></script><script src="{CHARTJS}"></script>
<script>{CITY_JS}</script>"""
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · {esc(ciudad)}</title>"
            f"<link rel='stylesheet' href='{LEAFLET_CSS}'>"
            f"<style>{D.CSS}{NAV_CSS}{EXTRA_CSS}</style></head>"
            f"<body>{body}</body></html>")


def _pick_lbl(code, mu):
    unit = STATIONS[code][3]
    fb = int(math.floor(mu))
    if unit == "F":
        lo = fb if fb % 2 == 0 else fb - 1
        return f"{lo}-{lo + 1}°F"
    return f"{fb}°C"


CITY_JS = r"""
(function(){
  var C = window.__CITY || {};
  // ---------- MAPA (Leaflet + CARTO dark) ----------
  var mapEl = document.getElementById('citymap');
  if (mapEl && window.L) {
    var st = C.station;
    var map = L.map('citymap', {scrollWheelZoom:false}).setView([st.lat, st.lon], 12);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      {attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &middot; &copy; <a href="https://carto.com/attributions">CARTO</a>',
       subdomains:'abcd', maxZoom:19}).addTo(map);
    var stIcon = L.divIcon({className:'', html:'<div style="font-size:22px;text-shadow:0 0 8px #ffc24a">★</div>', iconSize:[22,22], iconAnchor:[11,11]});
    L.marker([st.lat, st.lon], {icon: stIcon}).addTo(map)
      .bindTooltip('<b>'+st.code+'</b> · estación de resolución', {className:'pwstip'});
    var pts = [[st.lat, st.lon]];
    (C.pws||[]).forEach(function(p){
      if(!p.lat) return;
      pts.push([p.lat, p.lon]);
      var lbl = (p.now!=null ? p.now.toFixed(1)+'°' : (p.bias>=0?'+':'')+p.bias.toFixed(1)+'b');
      var mk = L.circleMarker([p.lat, p.lon], {radius:9, color:'#42c9ff', weight:1.5,
        fillColor:'#42c9ff', fillOpacity:.55}).addTo(map);
      mk.bindTooltip('<b>'+p.id+'</b><br>'+(p.now!=null?('ahora '+p.now.toFixed(1)+'°<br>'):'')+
        'bias '+(p.bias>=0?'+':'')+p.bias.toFixed(2)+' · σ '+p.std.toFixed(2)+' · '+p.km.toFixed(1)+' km',
        {className:'pwstip'});
      var t = L.divIcon({className:'', html:'<div style="color:#8fe3ff;font:10px JetBrains Mono,monospace;text-shadow:0 1px 2px #000;transform:translate(-50%,-190%);text-align:center;white-space:nowrap">'+lbl+'</div>', iconSize:[0,0]});
      L.marker([p.lat, p.lon], {icon:t, interactive:false}).addTo(map);
    });
    if (pts.length>1) map.fitBounds(pts, {padding:[36,36]});
  }
  if (!window.Chart) return;
  Chart.defaults.color = '#a6bccd';
  Chart.defaults.borderColor = 'rgba(33,48,66,.7)';
  Chart.defaults.font.family = "'JetBrains Mono','Consolas',monospace";
  Chart.defaults.font.size = 10.5;
  function ts2ar(t){ var d=new Date((t-3*3600)*1000);
    function f2(x){return (x<10?'0':'')+x;}
    return f2(d.getUTCDate())+'/'+f2(d.getUTCMonth()+1)+' '+f2(d.getUTCHours())+':'+f2(d.getUTCMinutes()); }
  // ---------- TIMELINE mercado 24/48h ----------
  var tl = C.tl, tlChart = null;
  var PAL = ['#42c9ff','#25e6a4','#ffd23e','#ff9142','#ff5d70','#b28dff','#7bd1ff','#9be89b'];
  function buildTL(hours){
    var el = document.getElementById('tlchart');
    if (!el || !tl) return;
    var nAll = tl.times.length, keep = hours*2+1, i0 = Math.max(0, nAll-keep);
    var labels = tl.times.slice(i0).map(ts2ar);
    var ds = tl.labels.map(function(lab, i){
      return {label: lab, data: tl.series[lab].slice(i0), borderColor: PAL[i%PAL.length],
              backgroundColor: PAL[i%PAL.length], borderWidth: 1.6, pointRadius: 0,
              pointHitRadius: 8, spanGaps: true, yAxisID: 'y'};
    });
    var muv = tl.mu.slice(i0);
    if (muv.some(function(v){return v!=null;}))
      ds.push({label: 'μ bot ('+C.deg+')', data: muv, borderColor: '#25e6a4',
               borderDash: [6,4], borderWidth: 2, pointRadius: 0, spanGaps: true, yAxisID: 'y2'});
    if (tlChart) tlChart.destroy();
    tlChart = new Chart(el, {type: 'line',
      data: {labels: labels, datasets: ds},
      options: {responsive: true, maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
        plugins: {legend: {labels: {boxWidth: 10, boxHeight: 10}},
                  tooltip: {backgroundColor: '#0e151d', borderColor: '#2b3f52', borderWidth: 1}},
        scales: {x: {ticks: {maxTicksLimit: 10, maxRotation: 0}},
                 y: {min: 0, max: 1, title: {display: true, text: 'precio'}},
                 y2: {position: 'right', grid: {display: false},
                      title: {display: true, text: 'μ '+C.deg}}}}});
  }
  buildTL(24);
  document.querySelectorAll('.tlbtns .chip').forEach(function(b){
    b.addEventListener('click', function(){
      document.querySelectorAll('.tlbtns .chip').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on'); buildTL(+b.dataset.h);
    });
  });
  // ---------- obs vs picks 30d ----------
  var hc = document.getElementById('histchart');
  if (hc && C.obs) {
    var labels = C.obs.map(function(o){ return o.x.slice(5).split('-').reverse().join('/'); });
    var obsData = C.obs.map(function(o){ return o.y; });
    var pickMap = {};
    (C.picks||[]).forEach(function(p){ pickMap[p.x] = p.y; });
    var pickData = C.obs.map(function(o){ return pickMap[o.x] != null ? pickMap[o.x] : null; });
    new Chart(hc, {type: 'line',
      data: {labels: labels, datasets: [
        {label: 'obs real', data: obsData, borderColor: '#42c9ff', backgroundColor: 'rgba(66,201,255,.12)',
         borderWidth: 2, pointRadius: 2, fill: true, spanGaps: true},
        {label: 'pick congelado (μ)', data: pickData, borderColor: '#25e6a4', backgroundColor: '#25e6a4',
         borderWidth: 0, pointRadius: 4, pointStyle: 'circle', showLine: false}]},
      options: {responsive: true, maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
        plugins: {legend: {labels: {boxWidth: 10}},
                  tooltip: {backgroundColor: '#0e151d', borderColor: '#2b3f52', borderWidth: 1}},
        scales: {x: {ticks: {maxTicksLimit: 12, maxRotation: 0}},
                 y: {title: {display: true, text: C.deg}}}}});
  }
})();
"""


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
<b>mejor modelo</b> por ciudad = el que más veces acertó el bucket ganador ahí.</p>
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
    print(f"Generando paginas por ciudad v2 ({len(codes)})...")
    mk = D.fetch_market_full(today, 1)
    preds = D.load_preds(today)
    audit = I._load_audit()
    hist_rows = I.bot_history(refresh=a.refresh, today=today)
    rank_rows = I.stability(hist=hist_rows)
    perf = I.model_perf(days=90, today=today)
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
        html_doc = build_city(code, today, mk, preds, audit, hist_rows, perf, obs_map,
                              pws_ref, live_pws, live_obs, rank_rows, no_live=a.no_live)
        open(os.path.join(DATA, f"city_{code}.html"), "w", encoding="utf-8").write(html_doc)
        print(f"  {code} OK", flush=True)
    idx = build_index(today, rank_rows)
    open(os.path.join(DATA, "cities.html"), "w", encoding="utf-8").write(idx)
    print(f"OK -> data/city_<ICAO>.html ({len(codes)}) + data/cities.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Paginas por ciudad v2: mapa CARTO + charts interactivos.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--station", default=None, help="solo esa estacion (default: todas)")
    ap.add_argument("--no-live", action="store_true", help="sin obs/PWS/timeline en vivo (rapido)")
    ap.add_argument("--refresh", action="store_true", help="completar ganadores desde Gamma")
    main(ap.parse_args())
