#!/usr/bin/env python3
# scripts/fix_obs_f.py — PARCHE QUIRURGICO de data/obs.csv para las 9 estaciones Fahrenheit.
#
# [2026-07-13, fuga detectada en la sesion de leaderboard/fugas] La correccion del ground truth
# F (wxbt/observations.py: maximo horario ASOS en tz local = 98-100% acuerdo con WU/Gamma;
# lab_metar_precision) centralizo la LECTURA, pero data/obs.csv nunca se re-bajo: seguia con los
# valores de IEM daily.py (~+0.5-1 F por encima de la resolucion real, acuerdo 60-84%). Como
# obs.csv alimenta el fit de EMOS y el sesgo rolling 60d, TODO el calibrador F entrenaba contra
# una verdad inflada. Este script re-baja SOLO las K-stations con fetch_iem_maxima (ruta oficial)
# y reemplaza sus tmax/tmax_int conservando llaves (station,date), orden del archivo y las filas
# Celsius byte-identicas. Backup: data/obs.csv.bak-fixF.
#
# Convenciones identicas a download_iem_obs.py: tmax=round(v,2), tmax_int=floor(v+0.5).
import csv
import datetime as dt
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wxbt.observations import fetch_iem_maxima  # noqa: E402

OBS = os.path.join(os.path.dirname(__file__), "..", "data", "obs.csv")
NETWORKS_F = {"KLGA": "NY_ASOS", "KORD": "IL_ASOS", "KMIA": "FL_ASOS",
              "KSFO": "CA_ASOS", "KLAX": "CA_ASOS", "KDAL": "TX_ASOS",
              "KATL": "GA_ASOS", "KHOU": "TX_ASOS", "KAUS": "TX_ASOS"}
CHUNK_DAYS = 730          # requests de ~2 anios para no pasarse del rate limit de IEM
SLEEP_S = 12


def main():
    with open(OBS, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header, body = rows[0], rows[1:]
    span = {}
    for st in NETWORKS_F:
        ds = [dt.date.fromisoformat(r[1]) for r in body if r[0] == st]
        if ds:
            span[st] = (min(ds), max(ds))
    fresh = {}
    for st, (d0, d1) in span.items():
        cur = d0
        while cur <= d1:
            end = min(cur + dt.timedelta(days=CHUNK_DAYS - 1), d1)
            print(f"{st}: bajando horario {cur} -> {end} ...", flush=True)
            for attempt in range(5):
                try:
                    got = fetch_iem_maxima(st, NETWORKS_F[st], cur, end, "F")
                    break
                except Exception as e:                       # 429 de IEM: backoff y reintento
                    wait = 60 * (attempt + 1)
                    print(f"  {e} -> reintento en {wait}s", flush=True)
                    time.sleep(wait)
            else:
                raise RuntimeError(f"{st}: IEM no respondio tras 5 intentos")
            fresh.update({(st, day): v for day, v in got.items()})
            cur = end + dt.timedelta(days=1)
            time.sleep(SLEEP_S)
    bak = OBS + ".bak-fixF"
    if not os.path.exists(bak):
        with open(bak, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
    changed = missing = 0
    deltas = []
    for r in body:
        if r[0] not in NETWORKS_F:
            continue
        v = fresh.get((r[0], dt.date.fromisoformat(r[1])))
        if v is None:
            missing += 1          # sin dato horario ese dia: conservar el valor viejo
            continue
        old = float(r[2])
        if abs(old - v) > 1e-9:
            changed += 1
            deltas.append(old - v)
        r[2] = f"{round(v, 2)}"
        r[3] = str(int(math.floor(v + 0.5)))
    with open(OBS, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(body)
    n_f = sum(1 for r in body if r[0] in NETWORKS_F)
    md = (sum(deltas) / len(deltas)) if deltas else 0.0
    print(f"obs.csv parcheado: {n_f} filas F, {changed} cambiadas (delta medio viejo-nuevo "
          f"{md:+.2f}F), {missing} sin dato horario (conservadas). Backup: {bak}")


if __name__ == "__main__":
    main()
