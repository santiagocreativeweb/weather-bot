#!/usr/bin/env python3
# scripts/onboard_cities.py -- ALTA de ciudades nuevas SIN tocar la historia validada de las 12
# (pedido Santiago 2026-07-13). download_openmeteo.py / download_iem_obs.py SOBRESCRIBEN los CSV
# reconstruyendo las 18 ciudades -> re-bajar arriesga los datos sobre los que corre todo el
# backtest/labs. Este script baja SOLO las estaciones nuevas y las APPENDEA (idempotente: salta
# las que ya estan en el archivo). Formatos identicos a los downloaders originales.
#
# QUE BAJA por estacion nueva:
#   obs.csv       (station,date,tmax,tmax_int): IEM daily max, °C para no-US (floor+0.5 = half-up WU)
#   forecasts.csv (station,target,model,init,avail,lead_h,m,s2): Previous-Runs 3 modelos, s2 = var
#                 de residuos ventana-expandiente (identico a download_openmeteo).
# Tras correrlo: accumulate_predictions ya calibra las nuevas (fit_all las ve en forecasts+obs).
import csv
import math
import os
import sys
import time
import datetime as dt
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from download_openmeteo import (PREV_RUNS, MODELS, LEAD_COL, SIGMA_FLOOR,   # noqa: E402
                                MIN_DAY_HOURS, daily_tmax, expanding_s2)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wxbt.observations import fetch_iem_maxima  # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OBS = os.path.join(D, "obs.csv")
FC = os.path.join(D, "forecasts.csv")

# station -> (lat, lon, utc_off, unit, iem_net). = show_live.STATIONS + NETWORKS (verificados).
# Idempotente: las que ya esten en obs.csv/forecasts.csv se saltan (present_stations).
NEW = {
    "NZWN": (-41.3272, 174.8053, 12, "C", "NF__ASOS"),
    "LTAC": (40.1281,  32.9951,  3,  "C", "TR__ASOS"),
    "KMIA": (25.7932, -80.2906, -5,  "F", "FL_ASOS"),
    "WSSS": (1.3502,  103.9944,  8,  "C", "SG__ASOS"),
    "WMKK": (2.7456,  101.7099,  8,  "C", "MY__ASOS"),
    "ZGSZ": (22.6393, 113.8108,  8,  "C", "CN__ASOS"),
    # [2026-07-13 tarde] +11 (HK afuera: resolucion decimal HKO)
    "KSFO": (37.6188, -122.3750, -8, "F", "CA_ASOS"),
    "KLAX": (33.9425, -118.4081, -8, "F", "CA_ASOS"),
    "KDAL": (32.8471, -96.8518,  -6, "F", "TX_ASOS"),
    "KATL": (33.6367, -84.4281,  -5, "F", "GA_ASOS"),
    "KHOU": (29.6454, -95.2789,  -6, "F", "TX_ASOS"),
    "KAUS": (30.1945, -97.6699,  -6, "F", "TX_ASOS"),
    "CYYZ": (43.6772, -79.6306,  -5, "C", "CA_ON_ASOS"),
    "SBGR": (-23.4356, -46.4731, -3, "C", "BR__ASOS"),
    "SAEZ": (-34.8222, -58.5358, -3, "C", "AR__ASOS"),
    "MMMX": (19.4363, -99.0721,  -6, "C", "MX__ASOS"),
    "EFHK": (60.3172, 24.9633,    2, "C", "FI__ASOS"),
}
OBS_START = "2024-06-01"                       # ~2 anios: climatologia estacional + fit EMOS
FC_START = "2025-01-01"                         # = ventana de forecasts.csv de las 12
YDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def present_stations(path, col="station"):
    out = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.add(r[col])
    return out


def iem_station(code):
    return code.lstrip("K") if code.startswith("K") else code   # KMIA -> MIA (convencion IEM US)


def fetch_obs(code, net, unit):
    # Raw hourly ASOS is mandatory for Fahrenheit settlement compatibility.
    maxima = fetch_iem_maxima(code, net, dt.date.fromisoformat(OBS_START),
                              dt.date.fromisoformat(YDAY), unit)
    rows = [[code, day.isoformat(), round(value, 2), int(math.floor(value + 0.5))]
            for day, value in sorted(maxima.items())]
    obs_map = {day: int(math.floor(value + 0.5)) for day, value in maxima.items()}
    return rows, obs_map

    # Legacy daily-endpoint implementation retained below only for historical diff context.
    """[(station,date,tmax,tmax_int)] de IEM daily max (mismo formato/redondeo que download_iem_obs)."""
    p = dict(network=net, stations=iem_station(code), var="max_temp_f",
             year1=OBS_START[:4], month1=OBS_START[5:7], day1=OBS_START[8:10],
             year2=YDAY[:4], month2=YDAY[5:7], day2=YDAY[8:10], format="csv")
    r = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py",
                     params=p, timeout=120); r.raise_for_status()
    lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
    hdr = lines[0].split(",")
    rows, obs_map = [], {}
    for l in lines[1:]:
        d = dict(zip(hdr, l.split(",")))
        v = d.get("max_temp_f")
        if not v or v in ("None", "M"):
            continue
        tf = float(v)
        val = tf if code.startswith("K") else (tf - 32) * 5 / 9      # °C para no-US
        day = d.get("day")
        rows.append([code, day, round(val, 2), int(math.floor(val + 0.5))])
        obs_map[dt.date.fromisoformat(day)] = int(math.floor(val + 0.5))
    return rows, obs_map


def fetch_fc(code, lat, lon, unit, om):
    p = dict(latitude=lat, longitude=lon, models=om,
             hourly=",".join(["temperature_2m", "temperature_2m_previous_day1",
                              "temperature_2m_previous_day2"]),
             start_date=FC_START, end_date=YDAY, timezone="UTC",
             temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
    r = requests.get(PREV_RUNS, params=p, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    return r.json()["hourly"]


def build_forecast_rows(code, lat, lon, off, unit, obs_map):
    floor2 = SIGMA_FLOOR[unit] ** 2
    rows = []
    for model, (om, lag_h) in MODELS.items():
        h = fetch_fc(code, lat, lon, unit, om)
        times = h["time"]
        for lead, col in LEAD_COL.items():
            if col not in h:
                raise RuntimeError(f"{code} {model}: falta columna {col}")
            dmax = daily_tmax(times, h[col], off)
            resid = {d: dmax[d] - obs_map[d] for d in dmax if d in obs_map}
            s2map = expanding_s2(sorted(dmax), resid, floor2)
            for d, m in dmax.items():
                init = dt.datetime.combine(d, dt.time()) - dt.timedelta(days=lead - 1)
                avail = init + dt.timedelta(hours=lag_h)
                peak = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=15 - off)   # =downloader
                lead_h = (peak - avail).total_seconds() / 3600.0
                if not (1.0 < lead_h <= 78.0):
                    continue
                rows.append([code, d.isoformat(), model, init.isoformat(), avail.isoformat(),
                             round(lead_h, 1), round(m, 2), round(s2map[d], 3)])
        time.sleep(0.3)
    return rows


def main():
    have_obs = present_stations(OBS)
    have_fc = present_stations(FC)
    todo = [c for c in NEW if c not in have_fc or c not in have_obs]
    if not todo:
        print("todas las nuevas ya estan en obs.csv y forecasts.csv; nada que hacer."); return
    print(f"onboarding: {todo} (obs {OBS_START}..{YDAY}, forecasts {FC_START}..{YDAY})")

    obs_rows_all, fc_rows_all = [], []
    for code in todo:
        lat, lon, off, unit, net = NEW[code]
        try:
            orows, omap = fetch_obs(code, net, unit)
        except Exception as e:
            print(f"[WARN] {code} obs: {e} -> salto la estacion", file=sys.stderr); continue
        if len(omap) < 200:
            print(f"[WARN] {code}: solo {len(omap)} dias de obs -> insuficiente, salto", file=sys.stderr)
            continue
        try:
            frows = build_forecast_rows(code, lat, lon, off, unit, omap)
        except Exception as e:
            print(f"[WARN] {code} forecasts: {e} -> salto la estacion", file=sys.stderr); continue
        if code not in have_obs:
            obs_rows_all += orows
        if code not in have_fc:
            fc_rows_all += frows
        print(f"  {code}: obs {len(orows)} filas ({min(omap)}..{max(omap)}), forecasts {len(frows)} filas")

    # APPEND (nunca reescribir): preserva exacto los datos validados de las 12.
    if obs_rows_all:
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(obs_rows_all)
        print(f"+{len(obs_rows_all)} filas a obs.csv")
    if fc_rows_all:
        with open(FC, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(fc_rows_all)
        print(f"+{len(fc_rows_all)} filas a forecasts.csv")
    print("listo. Correr: python scripts/accumulate_predictions.py --date <hoy> (ya calibra las nuevas).")


if __name__ == "__main__":
    main()
