#!/usr/bin/env python3
# scripts/onboard_hko.py — ALTA QUIRURGICA de Hong Kong (2026-07-16, pedido Santiago).
# HK quedo afuera del alta masiva porque resuelve por HKO a 1 decimal SIN WU/IEM; ahora entra con
# fuente propia (hko_source.py). Este script:
#   1. obs.csv       <- CLMMAXT del Observatory (lo hace hko_source.append_obs; 1 decimal, floor)
#   2. forecasts.csv <- Previous-Runs 3 modelos por lat/lon del OBSERVATORY (no el aeropuerto),
#                       mismo formato/reglas que onboard_cities (reusa build_forecast_rows).
# Idempotente: si HKO ya esta en forecasts.csv no re-baja. Tras correr: accumulate_predictions ya
# calibra HKO (fit_all lo ve en forecasts+obs) y el dashboard/bot lo muestran.
import csv
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hko_source as H                                           # noqa: E402
from onboard_cities import build_forecast_rows, present_stations, FC   # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def main():
    # 1) obs oficiales (CLMMAXT)
    H.append_obs()
    obs_map = {}
    with open(os.path.join(D, "obs.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["station"] == H.CODE:
                obs_map[dt.date.fromisoformat(r["date"])] = float(r["tmax"])
    print(f"HKO obs en memoria: {len(obs_map)} dias")
    # 2) forecasts Previous-Runs (idempotente)
    if H.CODE in present_stations(FC):
        print("HKO ya esta en forecasts.csv — no re-bajo.")
        return
    rows = build_forecast_rows(H.CODE, H.LAT, H.LON, H.UTC_OFF, H.UNIT, obs_map)
    bad = [r for r in rows if r[6] is None or (r[7] is not None and r[7] <= 0)]
    if bad:
        raise SystemExit(f"[ABORT] {len(bad)} filas con m/s2 invalidos — no escribo nada")
    with open(FC, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print(f"HKO forecasts: +{len(rows)} filas -> forecasts.csv")


if __name__ == "__main__":
    main()
