#!/usr/bin/env python3
# scripts/hko_source.py — FUENTE OFICIAL de Hong Kong (pedido Santiago 2026-07-16).
# El mercado de HK NO resuelve por aeropuerto/WU: resuelve por el HONG KONG OBSERVATORY,
# "Absolute Daily Max (deg. C)" a UN DECIMAL, del Daily Extract de weather.gov.hk (CIS).
# Buckets del mercado = enteros de 1°C ("31°C" = [31.0, 31.9]) -> la regla FLOOR del motor
# sigue siendo EXACTA (floor(31.4)=31), no hace falta tocar pbot_floor ni el pick.
#
# Endpoints (API abierta del HKO, sin key):
#   * CLMMAXT (historico oficial, 1 decimal — LA verdad de resolucion):
#     https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=CLMMAXT&station=HKO
#   * max/min del dia EN CURSO (1 decimal, "HK Observatory"):
#     .../hko_data/regional-weather/latest_since_midnight_maxmin.csv
#   * temperatura ACTUAL 1-min (1 decimal): .../latest_1min_temperature.csv
#
# Uso operativo:
#   python scripts/hko_source.py --append-obs    # appendea a obs.csv los dias faltantes (idempotente;
#                                                  encadenado en run_daily — IEM no cubre HKO)
import argparse
import csv
import datetime as dt
import math
import os
import sys

import requests

CODE = "HKO"
LAT, LON, UTC_OFF, UNIT = 22.3020, 114.1741, 8, "C"
OPENDATA = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
REGIONAL = "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather"
CIS_URL = "https://www.weather.gov.hk/en/cis/climat.htm"     # fuente de resolucion (link)
STATION_ROW = "HK Observatory"                                # nombre en los CSV regionales
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OBS = os.path.join(D, "obs.csv")


def clmmaxt_month(year, month, timeout=30):
    """{date: tmax_1dec} del mes desde CLMMAXT (la verdad oficial de resolucion)."""
    r = requests.get(OPENDATA, params={"dataType": "CLMMAXT", "lang": "en", "rformat": "json",
                                       "station": "HKO", "year": year, "month": month},
                     timeout=timeout)
    r.raise_for_status()
    out = {}
    for row in r.json().get("data", []):
        try:
            y, m, dd, v = int(row[0]), int(row[1]), int(row[2]), float(row[3])
        except (TypeError, ValueError, IndexError):
            continue
        out[dt.date(y, m, dd)] = v
    return out


def daily_extract_month(year, month, timeout=30):
    """{date: tmax_1dec} del Daily Extract del CIS (la MISMA tabla que citan las reglas del
    mercado: 'Absolute Daily Max'). A diferencia de CLMMAXT, cubre el MES EN CURSO dia a dia.
    Formato: dailyExtract_YYYYMM.xml (JSON) -> stn.data[0].dayData = [dia, presion, MAX, media,
    min, ...]. Se ignoran filas no numericas (dias sin finalizar / totales del mes)."""
    url = f"https://www.weather.gov.hk/cis/dailyExtract/dailyExtract_{year}{month:02d}.xml"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    out = {}
    for blk in r.json().get("stn", {}).get("data", []):
        if int(blk.get("month", 0)) != month:
            continue
        for row in blk.get("dayData", []):
            try:
                dd = int(row[0])
                v = float(row[2])
            except (TypeError, ValueError, IndexError):
                continue
            out[dt.date(year, month, dd)] = v
    return out


def clmmaxt_range(start, end):
    """{date: tmax} para [start, end]. CLMMAXT (oficial, meses completos) + Daily Extract como
    fallback para el mes en curso (CLMMAXT devuelve vacio hasta que el mes cierra)."""
    out = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        try:
            month_data = clmmaxt_month(y, m)
            if not month_data:                     # mes aun no publicado -> Daily Extract
                month_data = daily_extract_month(y, m)
            out.update(month_data)
        except Exception as e:
            print(f"[WARN] HKO {y}-{m:02d}: {e}", file=sys.stderr)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return {d: v for d, v in out.items() if start <= d <= end}


def live_maxmin(timeout=20):
    """(max_hoy, min_hoy) del dia EN CURSO en el Observatory (1 decimal). None si falla."""
    r = requests.get(f"{REGIONAL}/latest_since_midnight_maxmin.csv", timeout=timeout)
    r.raise_for_status()
    for line in r.text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) >= 4 and parts[1].strip() == STATION_ROW:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError:
                return None
    return None


def live_now(timeout=20):
    """Temperatura ACTUAL (1-min) del Observatory. None si falla."""
    r = requests.get(f"{REGIONAL}/latest_1min_temperature.csv", timeout=timeout)
    r.raise_for_status()
    for line in r.text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) >= 3 and parts[1].strip() == STATION_ROW:
            try:
                return float(parts[2])
            except ValueError:
                return None
    return None


def append_obs(start=None, verbose=True):
    """Appendea a obs.csv los dias de HKO que falten (idempotente). Devuelve filas nuevas.
    tmax_int = FLOOR del valor 1-decimal (asi resuelve el mercado: 31.9 -> bucket 31°C)."""
    have = set()
    if os.path.exists(OBS):
        with open(OBS, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["station"] == CODE:
                    have.add(r["date"])
    yday = dt.date.today() - dt.timedelta(days=1)
    start = start or (dt.date.fromisoformat(min(have)) if have else dt.date(2024, 6, 1))
    data = clmmaxt_range(start, yday)
    rows = [[CODE, d.isoformat(), round(v, 1), int(math.floor(v))]
            for d, v in sorted(data.items()) if d.isoformat() not in have]
    if rows:
        with open(OBS, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
    if verbose:
        print(f"HKO obs: +{len(rows)} dias nuevos (hasta {yday}) -> obs.csv")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fuente HKO (resolucion oficial de Hong Kong).")
    ap.add_argument("--append-obs", action="store_true", help="appendear dias faltantes a obs.csv")
    ap.add_argument("--live", action="store_true", help="mostrar max/min de hoy + temp actual")
    a = ap.parse_args()
    if a.append_obs:
        append_obs()
    if a.live or not a.append_obs:
        mm = live_maxmin()
        now = live_now()
        print(f"HKO hoy: max {mm[0] if mm else '?'}C · min {mm[1] if mm else '?'}C · ahora {now}C")
