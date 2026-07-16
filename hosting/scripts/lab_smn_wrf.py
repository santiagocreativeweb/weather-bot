#!/usr/bin/env python3
# scripts/lab_smn_wrf.py — TEST HONESTO del WRF 4km del SMN argentino para SAEZ (Buenos Aires).
#
# [2026-07-13, pedido Santiago: "encontre informacion valiosa de argentina... los pronosticos
#  que realizan y ademas si luego son los ganadores?"]
#
# Fuente: s3://smn-ar-wrf (AWS Open Data, CC-BY 2.5 AR, acceso anonimo). Archivos
# WRFDETAR_24H_{YYYYMMDD}_{CC}_{PLAZO}.nc con variable `Tmax` CALIBRADA (metodologia RAFK)
# en la ventana del DIA UTC [00Z->00Z) etiquetada al inicio. Para ART (UTC-3) el dia UTC D
# cubre 21:00 ART de D-1 -> 21:00 ART de D: contiene el pico de la tarde (~15-17 ART).
#
# ANTI-LOOK-AHEAD (invariante #2): el `avail` de cada corrida es el LastModified REAL del
# objeto S3 (la latencia historica cambio 8h->5h->2.5h; una constante seria look-ahead).
# Para cada target se usa la corrida MAS RECIENTE con avail <= freeze (04:30 ART = 07:30 UTC,
# la hora a la que Santiago opera; AR no tiene DST).
#
# Scoring identico al bot: pick = bucket que contiene floor(mu); ganador = Gamma outcomePrices.
# Variantes fijadas ANTES de mirar resultados: RAW y B60 (sesgo rolling expanding de hasta 60
# dias previos, solo targets anteriores -> tampoco mira el futuro), mismas familias que
# lab_new_cities. Baseline pareada: candidatos de data/lab_new_cities_detail.csv (mismos dias).
#
# Uso: python scripts/lab_smn_wrf.py [--days 90] [--cache DIR]
import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check_predictions import winner_by_temp                     # noqa: E402

S3 = "https://smn-ar-wrf.s3.us-west-2.amazonaws.com"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
GAMMA = "https://gamma-api.polymarket.com"
SAEZ_SERIES = 10744
SAEZ_LAT, SAEZ_LON = -34.8222, -58.5358
FREEZE_UTC = dt.time(7, 30)          # 04:30 ART
CYCLES = ["18", "12", "06", "00"]    # del dia previo; + "00" del mismo dia se agrega aparte
D = os.path.join(os.path.dirname(__file__), "..", "data")


def list_runs(day, cycle):
    """[(key, avail_utc)] de los 24H de esa corrida, via S3 list (avail = LastModified REAL)."""
    pfx = f"DATA/WRF/DET/{day:%Y/%m/%d}/{cycle}/WRFDETAR_24H"
    r = requests.get(S3, params={"list-type": "2", "prefix": pfx}, timeout=60)
    r.raise_for_status()
    out = []
    for c in ET.fromstring(r.content).iter(f"{NS}Contents"):
        key = c.find(f"{NS}Key").text
        lm = dt.datetime.fromisoformat(c.find(f"{NS}LastModified").text.replace("Z", "+00:00"))
        out.append((key, lm.replace(tzinfo=None)))
    return out


def pick_run(target):
    """Corrida mas fresca con avail<=freeze que cubre el dia UTC `target`. -> (key, avail) o None."""
    freeze = dt.datetime.combine(target, FREEZE_UTC)
    cands = [(target, "00")] + [(target - dt.timedelta(days=1), c) for c in CYCLES]
    for run_day, cyc in cands:
        plazo = (target - run_day).days
        try:
            files = list_runs(run_day, cyc)
        except requests.RequestException:
            continue
        want = f"WRFDETAR_24H_{run_day:%Y%m%d}_{cyc}_{plazo:03d}.nc"
        for key, avail in files:
            if key.endswith(want) and avail <= freeze:
                return key, avail
    return None


def wrf_tmax(key, cache):
    """Tmax del gridpoint mas cercano a SAEZ. Cachea el .nc (8 MB) y el indice de grilla."""
    import netCDF4
    fn = os.path.join(cache, os.path.basename(key))
    if not os.path.exists(fn):
        r = requests.get(f"{S3}/{key}", timeout=300)
        r.raise_for_status()
        with open(fn, "wb") as fh:
            fh.write(r.content)
    ds = netCDF4.Dataset(fn)
    try:
        idxf = os.path.join(cache, "saez_grid_idx.json")
        if os.path.exists(idxf):
            iy, ix = json.load(open(idxf))
        else:
            lat, lon = ds["lat"][:], ds["lon"][:]
            d2 = (lat - SAEZ_LAT) ** 2 + (lon - SAEZ_LON) ** 2
            iy, ix = (int(v) for v in np.unravel_index(np.argmin(d2), d2.shape))
            json.dump([iy, ix], open(idxf, "w"))
        return float(ds["Tmax"][0, iy, ix])
    finally:
        ds.close()


def parse_bucket(t):
    t = t.replace("°C", "").replace("°F", "").strip()
    m = re.match(r"^(-?\d+)\s*or below$", t)
    if m:
        return (None, int(m.group(1)))
    m = re.match(r"^(-?\d+)\s*or (higher|above)$", t)
    if m:
        return (int(m.group(1)), None)
    m = re.match(r"^(-?\d+)-(-?\d+)$", t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(-?\d+)$", t)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return None


def resolved_saez():
    """{target_date: (buckets, winner)} de todos los mercados buenos-aires resueltos en Gamma."""
    evs, off = [], 0
    while True:
        r = requests.get(f"{GAMMA}/events",
                         params={"series_id": SAEZ_SERIES, "closed": "true",
                                 "limit": 100, "offset": off}, timeout=60)
        page = r.json()
        evs += page
        if len(page) < 100:
            break
        off += 100
    out = {}
    for ev in evs:
        # la fecha target: dia/mes del titulo + anio del endDate (el slug no trae anio)
        mt = re.search(r"on (\w+) (\d+)", ev.get("title", ""))
        ed = ev.get("endDate") or ""
        if not mt or not ed:
            continue
        try:
            year = int(ed[:4])
            d = dt.datetime.strptime(f"{mt.group(1)} {mt.group(2)} {year}", "%B %d %Y").date()
        except ValueError:
            continue
        if abs((d - dt.date.fromisoformat(ed[:10])).days) > 3:   # cruce de anio dic/ene
            d = d.replace(year=year - 1 if d.month == 12 else year + 1)
        if d > dt.date.today():
            continue
        win, buckets = None, []
        for m in ev.get("markets", []):
            b = parse_bucket(m.get("groupItemTitle", ""))
            if b is None:
                continue
            buckets.append(b)
            op = m.get("outcomePrices")
            try:
                op = json.loads(op) if isinstance(op, str) else op
            except (TypeError, ValueError):
                op = None
            if op and float(op[0]) == 1.0:
                win = b
        if win and buckets:
            out[d] = (buckets, win)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="targets hacia atras desde hoy")
    ap.add_argument("--cache", default=os.path.join(D, "wrf_cache"))
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    today = dt.date.today()
    mk = resolved_saez()
    targets = sorted(d for d in mk if (today - d).days <= a.days)
    print(f"SAEZ: {len(targets)} mercados resueltos en ventana ({targets[0]}..{targets[-1]})")

    obs = pd.read_csv(os.path.join(D, "obs.csv"), parse_dates=["date"])
    obs["date"] = obs.date.dt.date
    omap = {r.date: float(r.tmax) for r in obs[obs.station == "SAEZ"].itertuples()}

    rows = []
    for tg in targets:
        got = pick_run(tg)
        if not got:
            print(f"  {tg}: SIN corrida con avail<=freeze (hueco del bucket)")
            continue
        key, avail = got
        try:
            mu = wrf_tmax(key, a.cache)
        except Exception as e:
            print(f"  {tg}: error leyendo {os.path.basename(key)}: {e}")
            continue
        rows.append(dict(target=tg, run=os.path.basename(key), avail=avail.isoformat(),
                         mu_raw=round(mu, 2), obs=omap.get(tg)))
    df = pd.DataFrame(rows).sort_values("target").reset_index(drop=True)

    # B60: sesgo expanding (media de mu-obs de targets ANTERIORES, cap 60) — sin mirar el futuro
    bias = []
    hist = []
    for r in df.itertuples():
        bias.append(float(np.mean(hist[-60:])) if len(hist) >= 5 else 0.0)
        if r.obs is not None and r.obs == r.obs:
            hist.append(r.mu_raw - r.obs)
    df["mu_b60"] = df.mu_raw - np.array(bias)

    for name, col in [("WRF RAW", "mu_raw"), ("WRF B60", "mu_b60")]:
        hits = off1 = n = 0
        aes = []
        for r in df.itertuples():
            bk, win = mk[r.target]
            pick = winner_by_temp(bk, int(math.floor(getattr(r, col))))
            n += 1
            if pick == win:
                hits += 1
            else:
                def ctr(b):
                    return b[0] if b[1] is None else (b[1] if b[0] is None else (b[0] + b[1]) / 2)
                if abs(ctr(pick) - ctr(win)) <= 1.01:
                    off1 += 1
            if r.obs is not None and r.obs == r.obs:
                aes.append(abs(getattr(r, col) - r.obs))
        mae = float(np.mean(aes)) if aes else float("nan")
        print(f"{name}: n={n} exacto {hits}/{n} = {hits/n:.1%}  off-by-1 {off1}  MAE {mae:.2f}")

    # Baseline pareada: candidatos del scout en los MISMOS dias
    try:
        lab = pd.read_csv(os.path.join(D, "lab_new_cities_detail.csv"))
        lab = lab[lab.station == "SAEZ"].copy()
        lab["d"] = pd.to_datetime(lab.d).dt.date
        common = sorted(set(df.target) & set(lab.d.unique()))
        sub = lab[lab.d.isin(common)]
        top = (sub.groupby("candidate").hit.agg(["mean", "count"])
               .sort_values("mean", ascending=False))
        wrf_sub = df[df.target.isin(common)]
        wh = sum(winner_by_temp(mk[r.target][0], int(math.floor(r.mu_b60))) == mk[r.target][1]
                 for r in wrf_sub.itertuples())
        print(f"\nPAREADO en {len(common)} dias comunes: WRF B60 {wh}/{len(wrf_sub)} = "
              f"{wh/len(wrf_sub):.1%} vs top-5 candidatos del scout:")
        print(top.head(5).to_string())
    except (OSError, ValueError) as e:
        print(f"(baseline scout no disponible: {e})")

    out = os.path.join(D, "lab_smn_wrf_detail.csv")
    df.to_csv(out, index=False)
    print(f"\ndetalle -> {out}")


if __name__ == "__main__":
    main()
