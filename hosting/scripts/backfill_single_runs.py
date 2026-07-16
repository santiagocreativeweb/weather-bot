#!/usr/bin/env python3
"""Backfill exact model runs available before each station's 04:30 freeze.

Uses Open-Meteo Single Runs, not Previous Runs.  A conservative +7h global
publication lag and only 00/12 UTC cycles make ``avail_utc <= freeze_utc`` true
by construction for every row.  Multiple models and locations are batched in
one request, keeping a 90-day/all-station backfill comfortably below API limits.
"""
import argparse
import csv
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import freeze_utc  # noqa: E402
from show_live import STATIONS  # noqa: E402

API = "https://single-runs-api.open-meteo.com/v1/forecast"
OUT = "data/single_runs.csv"
LAG_H = 7
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
MODEL_START = {"cma": dt.date(2026, 4, 14), "jma": dt.date(2026, 6, 2)}
MIN_POINTS = {"gfs13": 18, "ecmwf": 6, "aifs": 3, "icon": 18,
              "arpege": 18, "ukmo": 18, "jma": 3, "cma": 6}


def active_models(target):
    return {k: v for k, v in MODELS.items() if target >= MODEL_START.get(k, dt.date.min)}


def model_daily_tmax(times, values, utc_offset, min_points):
    days = {}
    for stamp, value in zip(times, values):
        if value is None:
            continue
        local = dt.datetime.fromisoformat(stamp) + dt.timedelta(hours=utc_offset)
        days.setdefault(local.date(), []).append(float(value))
    return {day: max(vals) for day, vals in days.items() if len(vals) >= min_points}


def conservative_run(station, target):
    """Latest 00/12Z init whose conservative publication precedes freeze."""
    cutoff = freeze_utc(station, target)
    anchor = cutoff - dt.timedelta(hours=LAG_H)
    hour = 12 if anchor.hour >= 12 else 0
    return anchor.replace(hour=hour, minute=0, second=0, microsecond=0)


def request_json(params, attempts=5):
    for attempt in range(attempts):
        response = requests.get(API, params=params, timeout=180)
        if response.status_code != 429:
            response.raise_for_status()
            return response.json()
        time.sleep(min(2 ** attempt, 16))
    response.raise_for_status()


def fetch_group(target, run, unit, codes):
    models = active_models(target)
    base_params = {
        "latitude": ",".join(str(STATIONS[c][0]) for c in codes),
        "longitude": ",".join(str(STATIONS[c][1]) for c in codes),
        "hourly": "temperature_2m",
        "run": run.strftime("%Y-%m-%dT%H:%M"),
        # 48h cubre incluso Wellington: corrida 00Z D-1 hasta el final del dia local target.
        "forecast_days": 2,
        "timezone": "UTC",
        "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
    }

    def parse(payload, subset):
        payloads = payload if isinstance(payload, list) else [payload]
        if len(payloads) != len(codes):
            raise ValueError(f"{len(payloads)} payloads para {len(codes)} estaciones")
        rows = []
        avail = run + dt.timedelta(hours=LAG_H)
        for code, item in zip(codes, payloads):
            hourly = item["hourly"]
            times = hourly["time"]
            off = STATIONS[code][2]
            for model, api_model in subset.items():
                # Una peticion de un solo modelo usa el nombre sin sufijo.
                key = f"temperature_2m_{api_model}" if len(subset) > 1 else "temperature_2m"
                values = hourly.get(key)
                if values is None:
                    continue
                value = model_daily_tmax(times, values, off, MIN_POINTS[model]).get(target)
                if value is not None:
                    rows.append([target.isoformat(), code, model, unit,
                                 run.isoformat(timespec="minutes"),
                                 avail.isoformat(timespec="minutes"),
                                 freeze_utc(code, target).isoformat(timespec="minutes"),
                                 round(float(value), 3)])
        return rows

    try:
        payload = request_json({**base_params, "models": ",".join(models.values())})
        return parse(payload, models)
    except Exception as combined_error:
        # Un miembro ausente invalida el stream combinado. Recuperar los demás uno por uno.
        rows = []
        for model, api_model in models.items():
            try:
                payload = request_json({**base_params, "models": api_model})
                rows.extend(parse(payload, {model: api_model}))
            except Exception as exc:
                print(f"[WARN] split {target} {model} {run}: {exc}", file=sys.stderr)
        if not rows:
            raise combined_error
        return rows


def main(args):
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            done = {(r["target"], r["station"], r["model"]) for r in csv.DictReader(f)}
    header = ["target", "station", "model", "unit", "run_utc", "avail_utc", "freeze_utc", "tmax"]
    new_file = not os.path.exists(OUT)
    total, failures = 0, []
    day = start
    while day <= end:
        groups = {}
        for code, meta in STATIONS.items():
            if all((day.isoformat(), code, model) in done for model in active_models(day)):
                continue
            groups.setdefault((conservative_run(code, day), meta[3]), []).append(code)
        day_rows = []
        for (run, unit), codes in groups.items():
            try:
                rows = fetch_group(day, run, unit, codes)
                day_rows.extend(r for r in rows if (r[0], r[1], r[2]) not in done)
            except Exception as exc:
                failures.append((day, run, unit, str(exc)))
                print(f"[WARN] {day} {run} {unit}: {exc}", file=sys.stderr)
        if day_rows:
            with open(OUT, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(header); new_file = False
                writer.writerows(sorted(day_rows, key=lambda r: (r[1], r[2])))
            total += len(day_rows)
        print(f"{day}: +{len(day_rows)} filas (total nuevo {total})")
        day += dt.timedelta(days=1)
    print(f"Single Runs -> {OUT}: +{total} filas; fallos={len(failures)}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill init-anclado Open-Meteo Single Runs")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    main(parser.parse_args())
