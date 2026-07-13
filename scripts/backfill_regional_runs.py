#!/usr/bin/env python3
"""Backfill high-resolution regional runs available before the 04:30 freeze."""
import argparse
import csv
import concurrent.futures
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import freeze_utc  # noqa: E402
from show_live import STATIONS  # noqa: E402
from backfill_single_runs import model_daily_tmax  # noqa: E402

API = "https://single-runs-api.open-meteo.com/v1/forecast"
OUT = "data/regional_runs.csv"
US = ["KLGA", "KORD", "KMIA", "KSFO", "KLAX", "KDAL", "KATL", "KHOU", "KAUS"]
EU = ["EGLC", "LFPB", "LEMD", "EDDM", "LIMC", "LTAC", "EFHK"]
NORTH_EU = ["EGLC", "LFPB", "EDDM", "EFHK"]

# cycle hours and publication lags are deliberately conservative.
SPECS = {
    "hrrr": dict(api="ncep_hrrr_conus", stations=US, cycle=3, lag=3, min_points=18),
    "nbm": dict(api="ncep_nbm_conus", stations=US, cycle=3, lag=3, min_points=18),
    # RDPS run archive is advertised but returned modelRunUnavailable throughout
    # the retained window in the 2026-07 audit; HRDPS remains enabled where valid.
    "hrdps": dict(api="cmc_gem_hrdps", stations=["KLGA", "KORD", "CYYZ"], cycle=6, lag=4, min_points=18),
    "icon_eu": dict(api="dwd_icon_eu", stations=EU, cycle=3, lag=3, min_points=18),
    "arpege_eu": dict(api="meteofrance_arpege_europe", stations=EU, cycle=6, lag=3, min_points=18),
    "harmonie_dmi": dict(api="dmi_harmonie_arome_europe", stations=NORTH_EU, cycle=3, lag=3, min_points=18),
    "harmonie_knmi": dict(api="knmi_harmonie_arome_europe", stations=NORTH_EU, cycle=3, lag=3, min_points=18),
    "ukv": dict(api="ukmo_uk_deterministic_2km", stations=["EGLC"], cycle=3, lag=3, min_points=18),
    "arome": dict(api="meteofrance_arome_france0025", stations=["LFPB"], cycle=3, lag=3, min_points=18),
    "jma_msm": dict(api="jma_msm", stations=["RJTT", "RKSI"], cycle=3, lag=3, min_points=18,
                    active_after=dt.date(2026, 6, 2)),
    "kma_ldps": dict(api="kma_ldps", stations=["RKSI"], cycle=6, lag=3, min_points=18,
                     active_after=dt.date(2026, 6, 2)),
    "icon_2i": dict(api="italia_meteo_arpae_icon_2i", stations=["LIMC"], cycle=12, lag=3, min_points=18),
}


def selected_run(station, target, cycle, lag):
    anchor = freeze_utc(station, target) - dt.timedelta(hours=lag)
    hour = (anchor.hour // cycle) * cycle
    return anchor.replace(hour=hour, minute=0, second=0, microsecond=0)


def request_json(params, attempts=4):
    last = None
    for attempt in range(attempts):
        response = requests.get(API, params=params, timeout=180)
        last = response
        if response.status_code == 429:
            time.sleep(min(2 ** attempt, 8)); continue
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(response.text[:180]) from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise ValueError(payload.get("reason"))
        return payload
    last.raise_for_status()


def fetch(model, spec, target, run, unit, codes):
    params = {
        "latitude": ",".join(str(STATIONS[c][0]) for c in codes),
        "longitude": ",".join(str(STATIONS[c][1]) for c in codes),
        "models": spec["api"], "hourly": "temperature_2m",
        "run": run.strftime("%Y-%m-%dT%H:%M"), "forecast_days": 2,
        "timezone": "UTC", "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
    }
    payload = request_json(params)
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(codes):
        raise ValueError(f"{len(items)} payloads para {len(codes)} estaciones")
    avail = run + dt.timedelta(hours=spec["lag"])
    rows = []
    for code, item in zip(codes, items):
        h = item["hourly"]
        value = model_daily_tmax(h["time"], h["temperature_2m"], STATIONS[code][2],
                                 spec["min_points"]).get(target)
        if value is not None:
            rows.append([target.isoformat(), code, model, unit, run.isoformat(timespec="minutes"),
                         avail.isoformat(timespec="minutes"),
                         freeze_utc(code, target).isoformat(timespec="minutes"), round(value, 3)])
    return rows


def main(args):
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            done = {(r["target"], r["station"], r["model"]) for r in csv.DictReader(f)}
    new_file = not os.path.exists(OUT)
    header = ["target", "station", "model", "unit", "run_utc", "avail_utc", "freeze_utc", "tmax"]
    total = failures = 0
    all_jobs = []
    day = start
    while day <= end:
        for model, spec in SPECS.items():
            if day < spec.get("active_after", dt.date.min):
                continue
            groups = {}
            for code in spec["stations"]:
                if (day.isoformat(), code, model) in done:
                    continue
                key = (selected_run(code, day, spec["cycle"], spec["lag"]), STATIONS[code][3])
                groups.setdefault(key, []).append(code)
            for (run, unit), codes in groups.items():
                all_jobs.append((day, model, spec, run, unit, codes))
        day += dt.timedelta(days=1)

    # Independent model/location calls are safe to fetch concurrently.  Twelve
    # workers remain moderate for the endpoint while making the audit practical.
    rows_by_day = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(fetch, model, spec, day, run, unit, codes): (day, model, run)
            for day, model, spec, run, unit, codes in all_jobs
        }
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            day, model, run = futures[future]
            try:
                rows_by_day.setdefault(day, []).extend(future.result())
            except Exception as exc:
                failures += 1
                print(f"[WARN] {day} {model} {run}: {exc}", file=sys.stderr)
            if i % 100 == 0:
                print(f"progreso regional: {i}/{len(futures)} llamadas")
    for day in sorted(rows_by_day):
        day_rows = rows_by_day[day]
        if day_rows:
            with open(OUT, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(header); new_file = False
                writer.writerows(sorted(day_rows, key=lambda r: (r[1], r[2])))
            total += len(day_rows)
        print(f"{day}: +{len(day_rows)} regionales")
    print(f"Regional Single Runs -> {OUT}: +{total}; fallos tolerados={failures}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill regional init-anclado")
    parser.add_argument("--start", required=True); parser.add_argument("--end", required=True)
    main(parser.parse_args())
