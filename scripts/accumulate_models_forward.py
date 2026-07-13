#!/usr/bin/env python3
"""Capture the eight deterministic model maxima genuinely forward in time.

This is evidence for the pre-registered MED8/W8 challengers.  Rows are
append-only; repeated intraday runs are useful because scoring can later take
the latest capture available before each station's operational freeze.
"""
import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from show_live import STATIONS, PREV_RUNS, daily_tmax  # noqa: E402

OUT = "data/models_forward.csv"
LOG = "data/accumulator.log"
MODELS = {
    "gfs13": "ncep_gfs013",
    "ecmwf": "ecmwf_ifs025",
    "aifs": "ecmwf_aifs025_single",
    "icon": "dwd_icon",
    "arpege": "meteofrance_arpege_world025",
    "ukmo": "ukmo_global_deterministic_10km",
    "jma": "jma_gsm",
    "cma": "cma_grapes_global",
}


def log_run(snapshot, status, detail):
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} | models_forward | {snapshot} | {status} | {detail}\n")


def fetch_batch(job, today, end, captured):
    model, unit, codes = job
    params = {
        "latitude": ",".join(str(STATIONS[code][0]) for code in codes),
        "longitude": ",".join(str(STATIONS[code][1]) for code in codes),
        "models": MODELS[model],
        "hourly": "temperature_2m",
        "start_date": today.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
        "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
    }
    response = requests.get(PREV_RUNS, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    payloads = payload if isinstance(payload, list) else [payload]
    if len(payloads) != len(codes):
        raise ValueError(f"multi-location devolvio {len(payloads)} respuestas para {len(codes)} coords")
    rows = []
    for code, payload in zip(codes, payloads):
        hourly = payload["hourly"]
        off = STATIONS[code][2]
        for target, value in daily_tmax(hourly["time"], hourly["temperature_2m"], off).items():
            if today <= target <= end:
                rows.append([captured, code, target.isoformat(), model, unit, round(value, 2)])
    return rows


def main(args):
    today = dt.date.fromisoformat(args.date)
    end = today + dt.timedelta(days=args.horizon)
    captured = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    jobs = []
    for model in MODELS:
        for unit in ("F", "C"):
            codes = [station for station, meta in STATIONS.items() if meta[3] == unit]
            if codes:
                jobs.append((model, unit, codes))
    rows, failures = [], []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_jobs = {pool.submit(fetch_batch, job, today, end, captured): job for job in jobs}
        for future in cf.as_completed(future_jobs):
            try:
                rows.extend(future.result())
            except Exception as exc:
                failures.append((future_jobs[future], str(exc)))
    if not rows:
        log_run(args.date, "FAIL", f"0 rows; failures={len(failures)}")
        raise SystemExit("[ABORT] models_forward: ninguna respuesta util")

    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(["capture_utc", "station", "target", "model", "unit", "tmax"])
        writer.writerows(sorted(rows, key=lambda r: (r[1], r[2], r[3])))
    coverage = len({(r[1], r[3]) for r in rows})
    expected = len(STATIONS) * len(MODELS)
    status = "WARN" if failures or coverage < expected else "OK"
    log_run(args.date, status, f"rows={len(rows)} pairs={coverage}/{expected} failures={len(failures)}")
    print(f"+{len(rows)} filas point-in-time -> {OUT}; modelos/estacion={coverage}/{expected}")
    for job, error in failures[:10]:
        print(f"[WARN] batch {job[0]}/{job[1]}: {error}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Captura forward de ocho modelos para MED8/W8 sombra")
    parser.add_argument("--date", required=True, help="fecha de captura YYYY-MM-DD (debe ser hoy)")
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    main(parser.parse_args())
