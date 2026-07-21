#!/usr/bin/env python3
# scripts/city_pages.py — DASHBOARD POR CIUDAD v3 (rediseño 2026-07-16 tarde, pedido Santiago:
# "es estupido crear 20 .html por ciudad; volca todo a UN archivo y reflejalo en UNO solo llamando
# a esa variable").  Ahora se generan SOLO:
#   * data/cities_data.js  -> window.__CITIES = {code: {...}}, window.__CITY_INDEX = [...], __META
#   * data/city.html       -> UN template que lee ?city=CODE y renderiza desde esa variable
#   * data/cities.html     -> indice (grid + buscador) que linkea a city.html?city=CODE
# (los 30 city_<ICAO>.html se borran).  Beneficios: 1 template + 1 data file (menos tokens/archivos),
# auto-refresh trivial (re-fetch del .js), navegacion entre ciudades sin regenerar nada.
#
# TIMELINE v2 (mismo pedido): precios como ENTEROS/1-decimal (0.365 -> 36.5), lineas punteadas
# amarillas en el freeze 24h y 48h, colores CLAROS (🎯 exacto verde / 🥈 top-2 amarillo / 🥉 top-3
# naranja / resto gris), toggle grafico/tabla, ⚙ para elegir buckets, slider para mover el cursor.
# Mapa: Leaflet + tiles dark de CARTO. Charts: Chart.js. (CDN: unpkg + jsdelivr, requieren internet.)
import argparse
import html
import json
import math
import os
import sys
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
from city_js import CITY_JS, INDEX_JS                               # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
CLOB = "https://clob.polymarket.com"
LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
CHARTJS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"

NIV_ICON = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌"}
WMO = {0: ("☀️", "Despejado"), 1: ("🌤", "Mayormente despejado"), 2: ("⛅", "Parcialmente nublado"),
       3: ("☁️", "Nublado"), 45: ("🌫", "Niebla"), 48: ("🌫", "Niebla"),
       51: ("🌦", "Llovizna leve"), 53: ("🌦", "Llovizna"), 55: ("🌧", "Llovizna intensa"),
       61: ("🌧", "Lluvia leve"), 63: ("🌧", "Lluvia"), 65: ("🌧", "Lluvia fuerte"),
       71: ("🌨", "Nieve leve"), 73: ("🌨", "Nieve"), 75: ("❄️", "Nieve fuerte"),
       80: ("🌦", "Chaparrones"), 81: ("🌧", "Chaparrones"), 82: ("⛈", "Chaparrones fuertes"),
       95: ("⛈", "Tormenta"), 96: ("⛈", "Tormenta"), 99: ("⛈", "Tormenta fuerte")}
PICK_ICON = ["🎯", "🥈", "🥉"]


def esc(s):
    return html.escape(str(s), quote=False)


def _pick_lbl(code, mu):
    unit = STATIONS[code][3]
    fb = int(math.floor(mu))
    if unit == "F":
        lo = fb if fb % 2 == 0 else fb - 1
        return f"{lo}-{lo + 1}°F"
    return f"{fb}°C"


def top3(code, mu, sg, buckets, stored_top=None):
    """[label,label,label] top-1/2/3 pick-first. Prefiere el top guardado del freeze."""
    if stored_top:
        return list(stored_top[:3])
    unit = STATIONS[code][3]
    if buckets:
        fb = int(math.floor(mu))
        pick = next((lab for lab, lo, hi in buckets
                     if (lo is None or fb >= lo) and (hi is None or fb <= hi)), None)
        pb = {lab: D.pbot_floor(mu, sg or 1.5, lo, hi) for lab, lo, hi in buckets}
        rest = [l for l, _ in sorted(pb.items(), key=lambda kv: -kv[1]) if l != pick]
        return (([pick] if pick else []) + rest)[:3]
    fb = int(math.floor(mu))

    def lbl(k):
        if unit == "F":
            lo = k if k % 2 == 0 else k - 1
            return f"{lo}-{lo + 1}°F"
        return f"{k}°C"
    return [lbl(fb), lbl(fb + 1), lbl(fb - 1)]


def current_weather(code, unit):
    lat, lon = STATIONS[code][0], STATIONS[code][1]
    out = ("🌡", "—", None)
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params=dict(latitude=lat, longitude=lon,
                                     current="temperature_2m,weather_code",
                                     temperature_unit=("fahrenheit" if unit == "F" else "celsius")),
                         timeout=12)
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


def market_timeline(code, d, audit, hours=48, max_buckets=10):
    """Timeline del mercado para el chart v2: precios por bucket + μ del bot + instantes de freeze
    24h/48h + top-3 fijado (para colorear). None si no hay mercado."""
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
    for lab, _t, _l in toks:
        hh = hist.get(lab, [])
        out, j, lastp = [], 0, None
        for t in grid:
            while j < len(hh) and hh[j]["t"] <= t:
                lastp = hh[j]["p"]; j += 1
            out.append(round(lastp, 4) if lastp is not None else None)
        series[lab] = out
    rec = audit.get(f"{code}|{d.isoformat()}") or {}
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
    frz48 = frz - 24 * 3600
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
    return {"labels": [lab for lab, _t, _l in toks], "times": grid, "series": series,
            "mu": mu_series, "frz": frz, "frz48": frz48,
            "top": (rec.get("froze") or {}).get("top") or []}


def build_city_data(code, today, mk, preds, audit, hist_rows, perf, obs_map, pws_ref, live_pws,
                    live_obs, rank_rows, no_live=False, hist48_rows=()):
    unit = STATIONS[code][3]
    deg = "°F" if unit == "F" else "°C"
    cont, pais, ciudad = D.STATION_META[code][:3]
    lat, lon = STATIONS[code][0], STATIONS[code][1]
    now_utc = dt.datetime.now(dt.timezone.utc)
    d_local = (now_utc.replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, today))).date()
    d_mkt = d_local if mk.get(code, {}).get(d_local) else today

    mine = [r for r in hist_rows if r["station"] == code and r["nivel"]]
    n = len(mine)
    ex = sum(r["nivel"] == "EXACTO" for r in mine)
    t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in mine)
    ico, wtxt, tnow = ("🌡", "—", None) if no_live else current_weather(code, unit)
    lm = (live_obs.get((code, d_local)) or live_obs.get((code, today)) or {}).get("max")
    same = (tnow is not None and lm is not None and abs(float(tnow) - float(lm)) < 0.05)
    pos = next((i + 1 for i, r in enumerate(rank_rows) if r["station"] == code), None)

    # picks proximos dias — AMBOS picks por dia (24h Y 48h, pedido Santiago 2026-07-17: "tendrias
    # 2 pronosticos por dia"), cada uno con su top-1/2/3. prelim solo si no hay ninguno fijado.
    picks = []
    for k in range(0, 3):
        d = d_mkt + dt.timedelta(days=k)
        rec = audit.get(f"{code}|{d.isoformat()}") or {}
        f24, f48 = rec.get("froze") or {}, rec.get("froze48") or {}
        info_d = mk.get(code, {}).get(d)
        bkts = [(lab, lo, hi) for lab, lo, hi, p in info_d["buckets"]] if info_d and info_d.get("buckets") else None
        pr = preds.get((code, d))
        entry = dict(date=d.strftime("%d/%m"))
        if f24.get("mu") is not None:
            entry["p24"] = dict(mu=round(f24["mu"], 1),
                                top=top3(code, f24["mu"], f24.get("sg"), bkts, f24.get("top")))
        if f48.get("mu") is not None:
            entry["p48"] = dict(mu=round(f48["mu"], 1),
                                top=top3(code, f48["mu"], f48.get("sg"), bkts, f48.get("top")))
        if "p24" not in entry and "p48" not in entry and pr:
            entry["prelim"] = dict(mu=round(pr[0], 1), top=top3(code, pr[0], pr[1], bkts))
        if len(entry) > 1:
            picks.append(entry)

    # mercado hoy/mañana — con badges top-1/2/3 CONGELADOS (HOY usa froze.top; MAÑANA usa el
    # froze48.top si ya esta fijado — pedido Santiago 2026-07-21) + tabla de pronosticos NO
    # (buckets fuera del top-3, con el precio del freeze) para las ciudades top-7 del ranking.
    apt_no = bool(pos and pos <= 7)
    markets = []
    for d in (d_mkt, d_mkt + dt.timedelta(days=1)):
        info = mk.get(code, {}).get(d)
        rec = audit.get(f"{code}|{d.isoformat()}") or {}
        fr, f48r = rec.get("froze") or {}, rec.get("froze48") or {}
        mu = sg = None
        frozen = False
        if fr.get("mu") is not None:
            mu, sg, frozen = fr["mu"], fr.get("sg") or 1.5, True
        elif preds.get((code, d)):
            mu, sg = preds[(code, d)]
        # top congelado que manda en esta card: froze (24h) o, si no existe aun, froze48
        tops, tops_src = [], None
        if fr.get("mu") is not None and fr.get("top"):
            tops, tops_src = fr["top"][:3], "24h"
        elif f48r.get("mu") is not None and f48r.get("top"):
            tops, tops_src = f48r["top"][:3], "48h"
        lmx = (live_obs.get((code, d)) or {}).get("max")
        rows = []
        if info and info.get("buckets"):
            width = 2 if unit == "F" else 1

            def cen(lo, hi):
                lo = lo if lo is not None else (hi - width if hi is not None else 0)
                hi = hi if hi is not None else lo + width
                return (lo + hi) / 2
            fl = int(math.floor(lmx)) if lmx is not None else None
            fb = int(math.floor(mu)) if mu is not None else None
            for lab, lo, hi, p in sorted(info["buckets"], key=lambda x: cen(x[1], x[2])):
                if p is None:
                    continue
                pb = D.pbot_floor(mu, sg, lo, hi) if mu is not None else None
                if tops:
                    cls = ("win" if info.get("winner") == lab else
                           "pick" if lab == tops[0] else
                           "t2b" if len(tops) > 1 and lab == tops[1] else
                           "t3b" if len(tops) > 2 and lab == tops[2] else "")
                else:
                    cls = ("win" if info.get("winner") == lab else
                           ("pick" if (fb is not None and (lo is None or fb >= lo) and (hi is None or fb <= hi)) else ""))
                dead = fl is not None and hi is not None and hi < fl
                rows.append(dict(lab=lab, mid=round(p, 3), pbot=(round(pb, 3) if pb is not None else None),
                                 edge=(round((pb - p) * 100) if pb is not None else None),
                                 cls=cls, dead=dead))
        # tabla NO congelada (solo top-7): book + top del freeze que manda
        nos = None
        if apt_no and tops:
            src = fr if tops_src == "24h" else f48r
            book = src.get("book") or []
            if book:
                nos = [dict(lab=lab, px=px,
                            tag=("EXACTO" if lab == tops[0] else
                                 "TOP-2" if len(tops) > 1 and lab == tops[1] else
                                 "TOP-3" if len(tops) > 2 and lab == tops[2] else "NO"))
                       for lab, px in book]
        markets.append(dict(head=("HOY" if d == d_mkt else "MAÑANA"), date=D.fecha_es(d),
                            iso=d.isoformat(),
                            mu=(round(mu, 1) if mu is not None else None), sg=(round(sg, 1) if sg else None),
                            frozen=frozen, tops_src=tops_src,
                            live_max=(round(lmx, 1) if lmx is not None else None),
                            winner=info.get("winner") if info else None,
                            url=I.pm_url(code, d), wu=D.wu_url(code, d), rows=rows, nos=nos))

    tl = None if no_live else market_timeline(code, d_mkt, audit)

    # obs vs picks 30d
    d0 = today - dt.timedelta(days=30)
    obs = [dict(x=(d0 + dt.timedelta(days=k)).isoformat(),
                y=obs_map.get((code, (d0 + dt.timedelta(days=k)).isoformat()))) for k in range(31)]
    picks30 = [dict(x=r["target"].isoformat(), y=round(r["mu"], 1))
               for r in hist_rows if r["station"] == code and d0 <= r["target"] <= today]

    # pws — ahora con max/min PARCIALES del dia ademas de la actual (pedido Santiago 2026-07-21)
    ref = pws_ref.get(code) or []
    cur = live_pws.get(code) or {}
    pws = []
    for r in ref:
        if not r.get("lat"):
            continue
        v = cur.get(r["pws_id"]) or {}
        pws.append(dict(id=r["pws_id"], lat=float(r["lat"] or 0), lon=float(r["lon"] or 0),
                        bias=round(float(r["bias"]), 2), std=round(float(r["std"]), 2),
                        km=round(float(r["dist_km"] or 0), 1),
                        now=v.get("now"), hi=v.get("hi"), lo=v.get("lo")))

    def _med(key):
        vals = sorted(p[key] - p["bias"] for p in pws if p.get(key) is not None)
        return round(vals[len(vals) // 2], 1) if vals else None
    est, est_hi, est_lo = _med("now"), _med("hi"), _med("lo")

    best = _read_rank().get(code)

    # historial — con el pick 48h al lado del de 24h (pedido Santiago 2026-07-17), cada uno con
    # su resultado propio contra el ganador oficial.
    h48 = {r["target"]: r for r in hist48_rows if r["station"] == code}
    hist = [dict(date=r["target"].strftime("%d/%m"), pick=r["pick_lbl"] or "—",
                 pick48=(h48.get(r["target"]) or {}).get("pick_lbl"),
                 niv48=(h48.get(r["target"]) or {}).get("nivel"),
                 win=r.get("win_lbl") or "—", niv=r["nivel"])
            for r in sorted([x for x in hist_rows if x["station"] == code],
                            key=lambda r: r["target"], reverse=True)[:14]]

    # DIA POR DIA para el selector de fechas (2026-07-21, pedido Santiago): desde el arranque de
    # la ciudad (primer registro del audit) hasta mañana. Cada dia: ambos picks congelados, el
    # resultado y el book del freeze (para la tabla NO). El timeline de un dia pasado se pide al
    # server (/timeline) al elegirlo.
    h24map = {r["target"]: r for r in hist_rows if r["station"] == code}
    day_keys = sorted({dt.date.fromisoformat(k.split("|")[1]) for k in audit
                       if k.startswith(code + "|") and I._valid_date(k.split("|")[1])})
    days = []
    for dd in day_keys:
        rc = audit.get(f"{code}|{dd.isoformat()}") or {}
        f24d, f48d = rc.get("froze") or {}, rc.get("froze48") or {}
        r24, r48 = h24map.get(dd), h48.get(dd)
        if f24d.get("mu") is None and f48d.get("mu") is None and not r24:
            continue
        days.append(dict(
            d=dd.isoformat(), lbl=dd.strftime("%d/%m"),
            p24=(dict(mu=f24d.get("mu"), top=(f24d.get("top") or [])[:3])
                 if f24d.get("mu") is not None else None),
            p48=(dict(mu=f48d.get("mu"), top=(f48d.get("top") or [])[:3])
                 if f48d.get("mu") is not None else None),
            win=(r24 or {}).get("win_lbl"), niv=(r24 or {}).get("nivel"),
            niv48=(r48 or {}).get("nivel"),
            book=(f24d.get("book") or f48d.get("book") or None)))

    return dict(
        code=code, city=ciudad, country=pais, cont=cont, unit=unit, deg=deg,
        lat=lat, lon=lon,
        resol=("Hong Kong Observatory · máx diaria 1 decimal (weather.gov.hk, NO WU)" if code == "HKO"
               else f"WU · estación {code}"),
        weather=dict(ico=ico, txt=wtxt, tnow=(round(float(tnow), 1) if tnow is not None else None)),
        stats=dict(ex=ex, n=n, t2=t2, tmax=(round(float(lm), 1) if lm is not None else None),
                   tnow=(round(float(tnow), 1) if tnow is not None else None), same=same,
                   pos=pos, total=len(rank_rows)),
        picks=picks, markets=markets, tl=tl, obs=obs, picks30=picks30,
        station=dict(lat=lat, lon=lon, code=code), pws=pws,
        est=est, est_hi=est_hi, est_lo=est_lo, apt_no=apt_no, days=days,
        best=(list(best) if best else None), history=hist)


def build_index_data(rank_rows, cities_data):
    try:
        from playbook import STRONG, WEAK
    except Exception:
        STRONG, WEAK = set(), set()
    by = {r["station"]: r for r in rank_rows}
    out = []
    for code in sorted(STATIONS, key=lambda c: D.STATION_META[c][2]):
        cont, pais, ciudad = D.STATION_META[code][:3]
        r = by.get(code)
        tier = "FUERTE" if code in STRONG else ("DEBIL" if code in WEAK else "MEDIA")
        cd = cities_data.get(code, {})
        # primeros 2 dias, cada uno con AMBOS picks (p24/p48) — la card los muestra en lineas
        picks = cd.get("picks", [])[:2]
        out.append(dict(code=code, city=ciudad, country=pais, cont=cont, tier=tier,
                        ex=(r["exact"] if r else 0), n=(r["n"] if r else 0),
                        t2=(r["top2"] if r else 0), best=cd.get("best"),
                        picks=picks))
    return out


def _read_rank():
    import csv as _csv
    out = {}
    p = os.path.join(DATA, "model_city_rank.csv")
    if not os.path.exists(p):
        return out
    for r in _csv.DictReader(open(p, encoding="utf-8")):
        if r.get("rank") == "1" and int(r["n"]) >= 5:
            out[r["station"]] = (r["model"], round(float(r["rate"]), 2), int(r["n"]), r["src"])
    return out


def main(a):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    codes = [a.station.upper()] if a.station else list(STATIONS)
    print(f"Generando dashboard consolidado por ciudad ({len(codes)})...", flush=True)
    mk = D.fetch_market_full(today, 1)
    preds = D.load_preds(today)
    audit = I._load_audit()
    hist_rows = I.bot_history(refresh=a.refresh, today=today)
    hist48_rows = I.bot_history(today=today, kind="froze48")   # pick 48h scoreado (historiales)
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
                d_loc = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                         + dt.timedelta(hours=local_offset(code, today))).date()
                live_pws[code] = P.pws_today([r["pws_id"] for r in ref], STATIONS[code][3], d=d_loc)

    # cuando se regenera UNA sola ciudad, preservar el resto del data file existente
    existing = {}
    dj = os.path.join(DATA, "cities_data.js")
    if a.station and os.path.exists(dj):
        try:
            txt = open(dj, encoding="utf-8").read()
            existing = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        except Exception:
            existing = {}
    cities = dict(existing.get("cities", {}))
    for code in codes:
        cities[code] = build_city_data(code, today, mk, preds, audit, hist_rows, perf, obs_map,
                                       pws_ref, live_pws, live_obs, rank_rows, no_live=a.no_live,
                                       hist48_rows=hist48_rows)
        print(f"  {code} OK", flush=True)
    index = build_index_data(rank_rows, cities)
    payload = {"cities": cities, "index": index,
               "generated": D.to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")}
    with open(dj, "w", encoding="utf-8") as f:
        f.write("window.__CITIES_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    with open(os.path.join(DATA, "city.html"), "w", encoding="utf-8") as f:
        f.write(CITY_HTML.replace("{CITY_JS}", CITY_JS))
    with open(os.path.join(DATA, "cities.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML.replace("{INDEX_JS}", INDEX_JS))
    # borrar los city_<ICAO>.html viejos (ya no se usan)
    for fn in os.listdir(DATA):
        if fn.startswith("city_") and fn.endswith(".html"):
            try:
                os.remove(os.path.join(DATA, fn))
            except OSError:
                pass
    print(f"OK -> data/cities_data.js ({len(cities)} ciudades) + city.html + cities.html", flush=True)


# ============================ TEMPLATES (estaticos) ============================
_CSS_EXTRA = """
.viz-root .cigrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-top:16px;}
.viz-root .ci-card{display:block;position:relative;background:linear-gradient(180deg,var(--s1),#0b1119);
  border:1px solid var(--bd);border-radius:var(--r);padding:13px 14px;box-shadow:var(--sh-1);
  transition:transform .15s,border-color .15s,box-shadow .15s;overflow:hidden;color:inherit;}
.viz-root .ci-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--tcol,var(--base));}
.viz-root .ci-card:hover{transform:translateY(-2px);border-color:var(--base);box-shadow:var(--sh-2);}
.viz-root .ci-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
.viz-root .ci-name{font-size:15px;font-weight:700;} .viz-root .ci-sub{font-size:10px;color:var(--mut);font-family:var(--mono);margin-top:2px;}
.viz-root .ci-track{font-size:12px;color:var(--ink2);font-family:var(--mono);margin-top:8px;}
.viz-root .ci-model{font-size:10.5px;color:var(--ink2);margin-top:5px;} .viz-root .ci-model b{color:var(--fc);}
.viz-root .ci-picks{margin-top:8px;font-size:11px;font-family:var(--mono);border-top:1px solid var(--grid);padding-top:7px;}
.viz-root .ci-pk{display:flex;gap:6px;align-items:baseline;padding:1px 0;}
.viz-root .ci-pk .d{color:var(--mut);min-width:66px;} .viz-root .ci-pk .t1{color:var(--pick);font-weight:700;}
.viz-root .ci-pk .t2{color:var(--t2);} .viz-root .ci-pk .t3{color:var(--t3);}
.viz-root .none{color:var(--mut);font-style:italic;padding:16px 0;}
.viz-root .cols{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start;}
.viz-root .col{flex:1 1 430px;min-width:330px;}
.viz-root .panelbox{background:linear-gradient(180deg,var(--s1),#0b1119);border:1px solid var(--bd);
  border-radius:var(--r);padding:14px 16px;margin:12px 0;box-shadow:var(--sh-1);}
.viz-root .panelbox h4{margin:0 0 10px;font-size:11px;color:var(--fc);font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;}
.viz-root table.ct{border-collapse:collapse;width:100%;font-size:12.5px;}
.viz-root table.ct th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:right;padding:4px 8px;border-bottom:1px solid var(--bd);}
.viz-root table.ct th:first-child{text-align:left;}
.viz-root table.ct td{padding:5px 8px;border-bottom:1px solid var(--grid);font-family:var(--mono);font-variant-numeric:tabular-nums;}
.viz-root table.ct td.num{text-align:right;}
.viz-root table.ct tr.pick td{color:var(--fc);font-weight:700;} .viz-root table.ct tr.win td{color:var(--fin);font-weight:700;}
.viz-root table.ct tr.dead td{color:var(--mut);text-decoration:line-through;}
.viz-root .gv{font-weight:700;white-space:nowrap;}
.viz-root .gv.g-ex{color:var(--fin);} .viz-root .gv.g-t2{color:#ffd23e;} .viz-root .gv.g-t3{color:#ff8c42;} .viz-root .gv.g-bad{color:#d03b3b;}
.viz-root #citymap{height:400px;border-radius:var(--r-sm);border:1px solid var(--bd);z-index:1;}
.viz-root .chartbox{position:relative;height:320px;}
.viz-root .tlbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px;}
.viz-root .tlbar .sp{flex:1 1 auto;}
.viz-root .tllegend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;margin:8px 0;font-family:var(--mono);}
.viz-root .tllegend span{display:inline-flex;align-items:center;gap:5px;}
.viz-root .tllegend i{width:16px;height:3px;border-radius:2px;display:inline-block;}
.viz-root .tlcursor{font-size:12px;font-family:var(--mono);color:var(--ink2);margin-top:6px;padding:7px 10px;
  background:var(--s2);border:1px solid var(--grid);border-radius:var(--r-xs);}
.viz-root .tlcursor b{color:var(--ink);}
.viz-root #tlrange{width:100%;accent-color:var(--live);margin-top:8px;}
.viz-root .gearpop{position:absolute;z-index:20;background:#0e151d;border:1px solid var(--bd);border-radius:8px;
  padding:10px 12px;box-shadow:var(--sh-pop);max-height:260px;overflow:auto;font-size:12px;}
.viz-root .gearpop label{display:block;padding:3px 0;cursor:pointer;white-space:nowrap;}
.viz-root table.tltab{border-collapse:collapse;width:100%;font-size:11px;font-family:var(--mono);}
.viz-root table.tltab th{position:sticky;top:0;background:var(--s1);font-size:9px;color:var(--mut);padding:3px 6px;border-bottom:1px solid var(--bd);text-align:right;}
.viz-root table.tltab th:first-child{text-align:left;}
.viz-root table.tltab td{padding:2px 6px;border-bottom:1px solid var(--grid);text-align:right;}
.viz-root table.tltab td:first-child{text-align:left;color:var(--mut);}
.viz-root .tltabwrap{max-height:320px;overflow:auto;}
.viz-root .pickrow{font-size:12.5px;font-family:var(--mono);padding:6px 0;border-bottom:1px solid var(--grid);}
.viz-root .pickrow .top1{color:var(--pick);font-weight:700;} .viz-root .pickrow .top2{color:var(--t2);} .viz-root .pickrow .top3{color:var(--t3);}
.viz-root .links a{color:var(--mkt);margin-right:14px;}
.viz-root select.citysel{background:var(--s2);color:var(--ink);border:1px solid var(--bd);border-radius:6px;
  padding:6px 10px;font-size:13px;font-family:inherit;font-weight:700;}
.viz-root .autoref{font-size:10.5px;color:var(--mut);font-family:var(--mono);display:inline-flex;align-items:center;gap:6px;}
.viz-root .autoref input{accent-color:var(--fc);}
/* [2026-07-21] estilo terminal para la tabla del timeline + dia-por-dia + tabla NO + badges */
.viz-root .links a.chip{display:inline-block;margin:0 6px 4px 0;text-decoration:none;cursor:pointer;}
.viz-root .track{display:inline-block;width:100%;max-width:230px;height:8px;background:var(--s2);
  border-radius:4px;overflow:hidden;vertical-align:middle;}
.viz-root .fill{display:block;height:100%;background:#42c9ff;border-radius:4px;}
.viz-root table.tltab2{border-collapse:collapse;width:100%;font-size:12px;font-family:var(--mono);}
.viz-root table.tltab2 th{font-size:9px;color:var(--mut);text-transform:uppercase;text-align:left;
  padding:3px 6px;border-bottom:1px solid var(--bd);letter-spacing:.05em;}
.viz-root table.tltab2 td{padding:4px 6px;border-bottom:1px solid var(--grid);}
.viz-root table.tltab2 td.num{text-align:right;}
.viz-root table.tltab2 td.trk{width:38%;}
.viz-root tr.tl-r1 td{color:var(--pick);font-weight:700;}
.viz-root tr.tl-r2 td{color:var(--t2);} .viz-root tr.tl-r3 td{color:var(--t3);}
.viz-root tr.nt-ex td{color:var(--pick);font-weight:700;} .viz-root tr.nt-t2 td{color:var(--t2);}
.viz-root tr.nt-t3 td{color:var(--t3);} .viz-root tr.nt-no td{color:var(--mut);}
.viz-root table.ct tr.t2b td{color:var(--t2);font-weight:700;}
.viz-root table.ct tr.t3b td{color:var(--t3);font-weight:700;}
.viz-root #dtl-sl{width:100%;accent-color:var(--live);margin-top:6px;}
.leaflet-container{background:#0a1016;font-family:inherit;}
.leaflet-tooltip.pwstip{background:#0e151d;color:#e8f0f7;border:1px solid #2b3f52;border-radius:6px;
  font-family:"JetBrains Mono","Consolas",monospace;font-size:11px;}
.leaflet-tooltip.pwstip::before{display:none;}
"""


def _page(title, active, body, extra_head="", extra_js=""):
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title>{extra_head}"
            f"<style>{D.CSS}{NAV_CSS}{_CSS_EXTRA}</style></head><body>{body}{extra_js}</body></html>")


CITY_HTML = _page(
    "WXBT · Ciudad", "cities",
    f'<div class="viz-root">{nav_html("cities")}'
    '<div class="topbar"><div class="row1">'
    '<h1 id="ctitle">🏙 …</h1>'
    '<select class="citysel" id="citysel"></select>'
    '<span class="autoref" style="margin-left:auto"><label><input type="checkbox" id="autoref" checked> '
    'auto-refresh</label> · <span id="reftxt"></span></span></div>'
    '<div class="links" id="clinks" style="margin-top:6px;font-size:12px"></div>'
    '<div style="font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px" id="cgen"></div></div>'
    '<div id="cbody"></div></div>',
    extra_head=f"<link rel='stylesheet' href='{LEAFLET_CSS}'>",
    extra_js=(f'<script src="cities_data.js"></script>'
              f'<script src="{LEAFLET_JS}"></script><script src="{CHARTJS}"></script>'
              f'<script>{{CITY_JS}}</script>'))

INDEX_HTML = _page(
    "WXBT · Ciudades", "cities",
    f'<div class="viz-root">{nav_html("cities")}'
    '<div class="topbar"><div class="row1"><h1>🏙 Ciudades</h1>'
    '<span class="subt">mercado, picks 24h/48h (🎯 exacto · 🥈 top-2 · 🥉 top-3), modelos y track</span>'
    '<span class="clock" id="idxgen" style="margin-left:auto"></span></div>'
    '<div class="vfilters" id="idxfilters"></div></div>'
    '<div class="cigrid" id="cigrid"></div>'
    '<p class="none" id="cnone" style="display:none">Sin ciudades para ese filtro.</p></div>',
    extra_js='<script src="cities_data.js"></script><script>{INDEX_JS}</script>')


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Dashboard consolidado por ciudad (1 template + 1 data file).")
    ap.add_argument("--date", default=None)
    ap.add_argument("--station", default=None, help="regenerar SOLO esa estacion (preserva el resto)")
    ap.add_argument("--no-live", action="store_true", help="sin obs/PWS/timeline en vivo (rapido)")
    ap.add_argument("--refresh", action="store_true", help="completar ganadores desde Gamma")
    main(ap.parse_args())
