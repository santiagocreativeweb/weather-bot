#!/usr/bin/env python3
# scripts/pws_setup.py — SISTEMA PWS (pedido Santiago 2026-07-15): para cada estacion de
# resolucion, encontrar las PWS de Weather Underground MAS CERCANAS, medir su BIAS contra la
# estacion oficial en una ventana de hasta 180 dias, y mantener SIEMPRE 3-5 PWS de referencia
# por ciudad (data/pws_reference.csv) para tener termometros redundantes cerca del sensor que
# resuelve el mercado.
#
# Por que sirve: el mercado resuelve por la estacion del aeropuerto (WU). Un panel de PWS
# vecinas con bias conocido = nowcast redundante del sensor oficial (deteccion temprana de
# "el max ya paso" / sensor raro), y es lo que muestran los dashboards tipo PolyQT.
#
# API: api.weather.com (la API publica del sitio de WU).
#   * v3/location/near?product=pws     -> PWS cercanas (id, distancia, lat/lon)
#   * v2/pws/history/daily?date=...    -> resumen diario historico (tempHigh) por PWS
#   * v2/pws/observations/current      -> lectura actual por PWS
# Key: WXBT_WU_KEY / data/.wu_key / la key PUBLICA del frontend de wunderground.com (default).
#
# USO:
#   python scripts/pws_setup.py --stations LIMC,KLGA          # alta/refresh de esas ciudades
#   python scripts/pws_setup.py --all                          # las 29 (miles de requests; resumible)
#   python scripts/pws_setup.py --update                       # extiende dias recientes de las YA elegidas
#   python scripts/pws_setup.py --live --stations LIMC         # lectura actual bias-corregida
# Idempotente: data/pws_history.csv es append-only con dedupe; re-correr NO re-baja lo que ya esta.
import argparse
import csv
import json
import math
import os
import statistics
import sys
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import requests                                   # noqa: E402
from show_live import STATIONS                    # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
HIST_CSV = os.path.join(DATA, "pws_history.csv")
REF_CSV = os.path.join(DATA, "pws_reference.csv")
OBS_CSV = os.path.join(DATA, "obs.csv")
NEAR_JSON = os.path.join(DATA, "pws_near.json")   # cache de candidatas por estacion
# Key publica que usa el propio frontend de wunderground.com (visible en su JS). Si algun dia
# rota: sacar la nueva del network tab del sitio y ponerla en data/.wu_key o WXBT_WU_KEY.
PUBLIC_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
API = "https://api.weather.com"
HIST_FIELDS = ["station", "pws_id", "date", "tmax_pws", "unit"]
REF_FIELDS = ["station", "rank", "pws_id", "dist_km", "lat", "lon", "n", "cover",
              "bias", "std", "mae", "updated"]


def wu_key():
    k = os.environ.get("WXBT_WU_KEY", "").strip()
    if k:
        return k
    p = os.path.join(DATA, ".wu_key")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return PUBLIC_KEY


def discover(code, n_cand=10, force=False):
    """PWS candidatas cercanas a la estacion (cacheadas en data/pws_near.json)."""
    cache = {}
    if os.path.exists(NEAR_JSON):
        try:
            cache = json.load(open(NEAR_JSON, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    if code in cache and not force and len(cache[code]) >= min(n_cand, 5):
        return cache[code][:n_cand]
    lat, lon = STATIONS[code][0], STATIONS[code][1]
    r = requests.get(f"{API}/v3/location/near",
                     params=dict(geocode=f"{lat},{lon}", product="pws", format="json",
                                 apiKey=wu_key()), timeout=25)
    r.raise_for_status()
    loc = r.json().get("location", {})
    out = []
    for i in range(len(loc.get("stationId", []))):
        out.append(dict(pws_id=loc["stationId"][i],
                        dist_km=loc.get("distanceKm", [None] * 99)[i],
                        lat=loc.get("latitude", [None] * 99)[i],
                        lon=loc.get("longitude", [None] * 99)[i]))
    cache[code] = out
    json.dump(cache, open(NEAR_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return out[:n_cand]


def fetch_daily(pws_id, d, unit):
    """tempHigh del resumen diario de la PWS para la fecha d, en la unidad de la estacion.
    None si la PWS no reporto ese dia (204/da vacio)."""
    units = "e" if unit == "F" else "m"
    key = "imperial" if unit == "F" else "metric"
    try:
        r = requests.get(f"{API}/v2/pws/history/daily",
                         params=dict(stationId=pws_id, format="json", units=units,
                                     date=d.strftime("%Y%m%d"), numericPrecision="decimal",
                                     apiKey=wu_key()), timeout=25)
        if r.status_code != 200:
            return None
        obs = r.json().get("observations") or []
        if not obs:
            return None
        v = (obs[0].get(key) or {}).get("tempHigh")
        return float(v) if v is not None else None
    except Exception:
        return None


def pws_current(pws_ids, unit):
    """{pws_id: temp_actual} en la unidad pedida (para dashboards / telegram)."""
    units = "e" if unit == "F" else "m"
    key = "imperial" if unit == "F" else "metric"
    out = {}

    def one(pid):
        try:
            r = requests.get(f"{API}/v2/pws/observations/current",
                             params=dict(stationId=pid, format="json", units=units,
                                         numericPrecision="decimal", apiKey=wu_key()), timeout=20)
            if r.status_code != 200:
                return pid, None
            obs = r.json().get("observations") or []
            if not obs:
                return pid, None
            return pid, (obs[0].get(key) or {}).get("temp")
        except Exception:
            return pid, None
    with ThreadPoolExecutor(max_workers=8) as tp:
        for pid, v in tp.map(one, pws_ids):
            if v is not None:
                out[pid] = float(v)
    return out


def pws_today(pws_ids, unit, d=None):
    """{pws_id: {"hi","lo","now"}} del dia d (hoy local de la ciudad): history/daily con la fecha
    de HOY devuelve el resumen PARCIAL del dia en curso (max/min hasta el momento) + current.
    [2026-07-21, pedido Santiago: registrar max/min/actual por PWS en las city pages]."""
    import datetime as _dt
    d = d or _dt.date.today()
    units = "e" if unit == "F" else "m"
    key = "imperial" if unit == "F" else "metric"
    out = {}

    def one(pid):
        hi = lo = now = None
        try:
            r = requests.get(f"{API}/v2/pws/history/daily",
                             params=dict(stationId=pid, format="json", units=units,
                                         date=d.strftime("%Y%m%d"), numericPrecision="decimal",
                                         apiKey=wu_key()), timeout=20)
            if r.status_code == 200:
                obs = r.json().get("observations") or []
                if obs:
                    m = obs[0].get(key) or {}
                    hi = m.get("tempHigh")
                    lo = m.get("tempLow")
        except Exception:
            pass
        try:
            r = requests.get(f"{API}/v2/pws/observations/current",
                             params=dict(stationId=pid, format="json", units=units,
                                         numericPrecision="decimal", apiKey=wu_key()), timeout=20)
            if r.status_code == 200:
                obs = r.json().get("observations") or []
                if obs:
                    now = (obs[0].get(key) or {}).get("temp")
        except Exception:
            pass
        return pid, dict(hi=(float(hi) if hi is not None else None),
                         lo=(float(lo) if lo is not None else None),
                         now=(float(now) if now is not None else None))
    with ThreadPoolExecutor(max_workers=8) as tp:
        for pid, v in tp.map(one, pws_ids):
            if any(x is not None for x in v.values()):
                out[pid] = v
    return out


def load_hist():
    """{(station, pws_id, date_iso): tmax} de data/pws_history.csv."""
    out = {}
    if not os.path.exists(HIST_CSV):
        return out
    with open(HIST_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(r["station"], r["pws_id"], r["date"])] = float(r["tmax_pws"])
            except (KeyError, ValueError):
                continue
    return out


def append_hist(rows):
    new_file = not os.path.exists(HIST_CSV)
    with open(HIST_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HIST_FIELDS)
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def load_station_obs():
    """{(station, date_iso): tmax} verdad oficial (obs.csv, ya en unidad de la estacion,
    con el fix de ground-truth °F aplicado)."""
    out = {}
    if not os.path.exists(OBS_CSV):
        return out
    with open(OBS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(r["station"], r["date"])] = float(r["tmax"])
            except (KeyError, ValueError):
                continue
    return out


def backfill_station(code, days, step, n_cand, hist, obs, end=None):
    """Baja tempHigh de las candidatas para los dias muestreados que falten. Devuelve filas nuevas.
    Muestreo cada `step` dias sobre la ventana `days` (ej. 180/4 = 45 puntos por PWS) — suficiente
    para estimar bias/std sin quemar miles de requests; la ventana completa se densifica sola con
    --update diario."""
    unit = STATIONS[code][3]
    end = end or (dt.date.today() - dt.timedelta(days=1))
    dates = [end - dt.timedelta(days=k) for k in range(0, days, step)]
    cands = discover(code, n_cand)
    todo = [(c["pws_id"], d) for c in cands for d in dates
            if (code, c["pws_id"], d.isoformat()) not in hist
            and (code, d.isoformat()) in obs]        # solo dias con verdad oficial
    if not todo:
        return []

    def one(t):
        pid, d = t
        return pid, d, fetch_daily(pid, d, unit)
    rows = []
    with ThreadPoolExecutor(max_workers=8) as tp:
        for pid, d, v in tp.map(one, todo):
            hist[(code, pid, d.isoformat())] = v if v is not None else float("nan")
            if v is not None:
                rows.append(dict(station=code, pws_id=pid, date=d.isoformat(),
                                 tmax_pws=round(v, 1), unit=unit))
    return rows


def rank_station(code, hist, obs, keep=5, min_n=12):
    """Stats por PWS y seleccion de las `keep` referencias (bias estable = std baja)."""
    unit = STATIONS[code][3]
    cands = {c["pws_id"]: c for c in discover(code, 99)}
    by_pws = {}
    for (st, pid, ds), v in hist.items():
        if st != code or v != v:
            continue
        truth = obs.get((code, ds))
        if truth is None:
            continue
        d = v - truth
        if abs(d) > (15 if unit == "F" else 8):     # descartar basura (sensor roto/unidad)
            continue
        by_pws.setdefault(pid, []).append(d)
    stats = []
    for pid, diffs in by_pws.items():
        if len(diffs) < min_n:
            continue
        stats.append(dict(
            station=code, pws_id=pid,
            dist_km=(cands.get(pid) or {}).get("dist_km"),
            lat=(cands.get(pid) or {}).get("lat"), lon=(cands.get(pid) or {}).get("lon"),
            n=len(diffs), bias=round(statistics.median(diffs), 2),
            std=round(statistics.pstdev(diffs), 2),
            mae=round(sum(abs(x) for x in diffs) / len(diffs), 2)))
    # referencia = bias ESTABLE (std baja) y cerca; el nivel del bias se corrige, la varianza no
    stats.sort(key=lambda s: (s["std"], abs(s["bias"]), s["dist_km"] or 99))
    return stats[:keep], stats


def write_reference(all_kept):
    today = dt.date.today().isoformat()
    with open(REF_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REF_FIELDS)
        w.writeheader()
        for code in sorted(all_kept):
            kept, n_samp = all_kept[code]
            for i, s in enumerate(kept, 1):
                w.writerow(dict(station=code, rank=i, pws_id=s["pws_id"],
                                dist_km=s["dist_km"], lat=s["lat"], lon=s["lon"],
                                n=s["n"], cover=round(s["n"] / max(n_samp, 1), 2),
                                bias=s["bias"], std=s["std"], mae=s["mae"], updated=today))


def read_reference():
    out = {}
    if not os.path.exists(REF_CSV):
        return out
    with open(REF_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["station"], []).append(r)
    return out


def estimate_now(code):
    """Estimacion del sensor oficial AHORA: mediana(PWS_actual − bias) de las referencias."""
    ref = read_reference().get(code) or []
    if not ref:
        return None, []
    unit = STATIONS[code][3]
    cur = pws_current([r["pws_id"] for r in ref], unit)
    vals = []
    for r in ref:
        v = cur.get(r["pws_id"])
        if v is not None:
            vals.append((r["pws_id"], v, v - float(r["bias"])))
    if not vals:
        return None, []
    est = statistics.median(x[2] for x in vals)
    return est, vals


def main(a):
    codes = ([s.strip().upper() for s in a.stations.split(",") if s.strip()] if a.stations
             else (list(STATIONS) if (a.all or a.update) else []))
    if not codes and not a.live:
        print("Indicar --stations LIMC,KLGA / --all / --update. Ver header del script.")
        return
    bad = [c for c in codes if c not in STATIONS]
    if bad:
        print(f"Estaciones desconocidas: {bad}")
        return
    if a.live:
        for code in codes or list(read_reference()):
            est, vals = estimate_now(code)
            if est is None:
                print(f"{code}: sin referencia PWS (correr --stations {code} primero)")
                continue
            deg = "F" if STATIONS[code][3] == "F" else "C"
            det = "  ".join(f"{pid}:{v:.1f}" for pid, v, _ in vals)
            print(f"{code}: estimado sensor oficial AHORA ~ {est:.1f}{deg}  ({len(vals)} PWS: {det})")
        return

    hist = load_hist()
    obs = load_station_obs()
    if not obs:
        print("[ERROR] data/obs.csv no esta — el bias se mide contra la verdad oficial. Abortando.")
        sys.exit(1)
    kept_all = {}
    ref_prev = read_reference()
    for code in codes:
        days, step = a.days, a.step
        if a.update:
            days, step = a.update_days, 1   # densificar los dias recientes de las ya elegidas
            if code not in ref_prev:
                continue
        try:
            new = backfill_station(code, days, step, a.cand, hist, obs)
        except requests.RequestException as e:
            print(f"[WARN] {code}: {e} — sigo con la proxima", file=sys.stderr)
            continue
        if new:
            append_hist(new)
        kept, stats = rank_station(code, hist, obs, keep=a.keep, min_n=a.min_n)
        n_samp = max((s["n"] for s in stats), default=0)
        kept_all[code] = (kept, n_samp)
        tag = " [UPDATE]" if a.update else ""
        print(f"{code}{tag}: +{len(new)} muestras nuevas · {len(stats)} PWS con n>={a.min_n} · "
              f"referencia: " + (", ".join(f"{s['pws_id']}(bias {s['bias']:+.1f}, std {s['std']:.1f}, "
                                           f"{s['dist_km']:.0f}km)" for s in kept) or "NINGUNA aun"))
        if kept and len(kept) < 3:
            print(f"  [!] {code}: solo {len(kept)} PWS confiables — bajar --min-n o subir --cand/--days")
    # merge con lo que ya existia (no pisar ciudades no tocadas en esta corrida)
    for code, rows in ref_prev.items():
        if code not in kept_all:
            kept_all[code] = ([dict(station=code, pws_id=r["pws_id"], dist_km=float(r["dist_km"] or 0),
                                    lat=float(r["lat"] or 0), lon=float(r["lon"] or 0), n=int(r["n"]),
                                    bias=float(r["bias"]), std=float(r["std"]), mae=float(r["mae"]))
                               for r in rows], max(int(r["n"]) for r in rows))
    if kept_all:
        write_reference(kept_all)
        print(f"Referencia -> {os.path.abspath(REF_CSV)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PWS cercanas + bias vs estacion oficial (WU api).")
    ap.add_argument("--stations", default=None, help="lista ICAO separada por coma")
    ap.add_argument("--all", action="store_true", help="las 29 estaciones (miles de requests; resumible)")
    ap.add_argument("--update", action="store_true",
                    help="modo diario: densifica los ultimos --update-days de las referencias existentes")
    ap.add_argument("--update-days", type=int, default=4)
    ap.add_argument("--days", type=int, default=180, help="ventana de evaluacion (default 180)")
    ap.add_argument("--step", type=int, default=4, help="muestrear cada N dias (default 4 -> ~45 puntos)")
    ap.add_argument("--cand", type=int, default=10, help="candidatas cercanas a evaluar (default 10)")
    ap.add_argument("--keep", type=int, default=5, help="referencias a mantener (default 5, min util 3)")
    ap.add_argument("--min-n", type=int, default=12, help="dias minimos con dato para calificar")
    ap.add_argument("--live", action="store_true", help="lectura actual bias-corregida de las referencias")
    main(ap.parse_args())
