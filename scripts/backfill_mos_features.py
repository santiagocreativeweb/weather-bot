#!/usr/bin/env python3
"""Backfill physical MOS features from exact regional model initialisations.

Scope frozen 2026-07-13 before evaluation: KLGA, KORD, LEMD and EGLC;
temperature, humidity, cloud, radiation, precipitation and 10 m wind.
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import math
import os
import random
import sys
import time

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backfill_regional_runs import selected_run  # noqa: E402
from dashboard import freeze_utc  # noqa: E402
from show_live import PEAK_HOUR, STATIONS, local_offset  # noqa: E402

API = "https://single-runs-api.open-meteo.com/v1/forecast"
OUT = "data/mos_features.csv"
VARIABLES = ["temperature_2m", "relative_humidity_2m", "cloud_cover",
             "shortwave_radiation", "precipitation", "wind_speed_10m",
             "wind_direction_10m"]
SPECS = {
    "KLGA": {
        "hrrr": ("ncep_hrrr_conus", 3, 3),
        "nbm": ("ncep_nbm_conus", 3, 3),
    },
    "KORD": {
        "hrrr": ("ncep_hrrr_conus", 3, 3),
        "nbm": ("ncep_nbm_conus", 3, 3),
    },
    "LEMD": {
        "icon_eu": ("dwd_icon_eu", 3, 3),
        "arpege_eu": ("meteofrance_arpege_europe", 6, 3),
    },
    "EGLC": {
        "ukv": ("ukmo_uk_deterministic_2km", 3, 3),
        "icon_eu": ("dwd_icon_eu", 3, 3),
        "arpege_eu": ("meteofrance_arpege_europe", 6, 3),
    },
}
FEATURES = ["n_hours", "first_hour", "last_hour", "tmax", "t_peak",
            "tmax_hour", "temp_trend", "rh_at_tmax", "rh_min",
            "cloud_at_tmax", "cloud_mean", "rad_at_tmax", "rad_max",
            "rad_sum", "precip_sum", "wind_at_tmax", "wind_max",
            "wind_u_at_tmax", "wind_v_at_tmax"]


def request_json(params, attempts=6):
    for attempt in range(attempts):
        response = requests.get(API, params=params, timeout=180)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(min(2 ** attempt, 20) + random.random())
            continue
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise ValueError(payload.get("reason"))
        return payload
    response.raise_for_status()


def finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def extract_features(station, target, hourly):
    offset = local_offset(station, target)
    points = []
    for i, stamp in enumerate(hourly["time"]):
        utc = dt.datetime.fromisoformat(stamp)
        local = utc + dt.timedelta(hours=offset)
        temp = hourly["temperature_2m"][i]
        if local.date() == target and finite(temp):
            values = {v: hourly.get(v, [None] * len(hourly["time"]))[i] for v in VARIABLES}
            points.append((local, values))
    if len(points) < 8:
        return None
    temps = np.array([float(p[1]["temperature_2m"]) for p in points])
    imax = int(np.argmax(temps))
    hours = np.array([p[0].hour + p[0].minute / 60 for p in points])
    ipeak = int(np.argmin(np.abs(hours - PEAK_HOUR[station])))

    def at(name, index, default=np.nan):
        value = points[index][1].get(name)
        return float(value) if finite(value) else default

    def vals(name):
        return np.array([float(p[1][name]) for p in points if finite(p[1].get(name))])

    wind = at("wind_speed_10m", imax)
    direction = at("wind_direction_10m", imax)
    angle = math.radians(direction) if finite(direction) else np.nan
    # Trend in the first available six hours of the selected run. This is model
    # output known at publication time, never an observation from the target day.
    k = min(6, len(points))
    trend = float(np.polyfit(hours[:k], temps[:k], 1)[0]) if k >= 3 else 0.0
    rh, cloud, rad, precip, winds = (vals("relative_humidity_2m"), vals("cloud_cover"),
        vals("shortwave_radiation"), vals("precipitation"), vals("wind_speed_10m"))
    return {
        "n_hours": len(points), "first_hour": hours[0], "last_hour": hours[-1],
        "tmax": float(temps[imax]), "t_peak": float(temps[ipeak]),
        "tmax_hour": float(hours[imax]), "temp_trend": trend,
        "rh_at_tmax": at("relative_humidity_2m", imax),
        "rh_min": float(np.min(rh)) if len(rh) else np.nan,
        "cloud_at_tmax": at("cloud_cover", imax),
        "cloud_mean": float(np.mean(cloud)) if len(cloud) else np.nan,
        "rad_at_tmax": at("shortwave_radiation", imax),
        "rad_max": float(np.max(rad)) if len(rad) else np.nan,
        "rad_sum": float(np.sum(rad)) if len(rad) else np.nan,
        "precip_sum": float(np.sum(precip)) if len(precip) else 0.0,
        "wind_at_tmax": wind,
        "wind_max": float(np.max(winds)) if len(winds) else np.nan,
        "wind_u_at_tmax": wind * math.sin(angle) if finite(wind) and finite(angle) else np.nan,
        "wind_v_at_tmax": wind * math.cos(angle) if finite(wind) and finite(angle) else np.nan,
    }


def fetch_one(station, model, api_model, cycle, lag, target):
    run = selected_run(station, target, cycle, lag)
    lat, lon, _, unit = STATIONS[station]
    payload = request_json({
        "latitude": lat, "longitude": lon, "models": api_model,
        "hourly": ",".join(VARIABLES), "run": run.strftime("%Y-%m-%dT%H:%M"),
        "forecast_days": 2, "timezone": "UTC",
        "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
    })
    features = extract_features(station, target, payload["hourly"])
    if features is None:
        raise ValueError("menos de 8 horas utilizables del dia local")
    return [target.isoformat(), station, model, unit, run.isoformat(timespec="minutes"),
            (run + dt.timedelta(hours=lag)).isoformat(timespec="minutes"),
            freeze_utc(station, target).isoformat(timespec="minutes")] + [features[k] for k in FEATURES]


def main(args):
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            done = {(r["target"], r["station"], r["model"]) for r in csv.DictReader(f)}
    jobs, day = [], start
    while day <= end:
        for station, models in SPECS.items():
            for model, (api_model, cycle, lag) in models.items():
                if (day.isoformat(), station, model) not in done:
                    jobs.append((station, model, api_model, cycle, lag, day))
        day += dt.timedelta(days=1)
    rows, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, *job): job for job in jobs}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                rows.append(future.result())
            except Exception as exc:
                failures.append((futures[future], str(exc)))
            if i % 100 == 0:
                print(f"MOS features: {i}/{len(futures)} llamadas")
    if rows:
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(["target", "station", "model", "unit", "run_utc",
                                 "avail_utc", "freeze_utc"] + FEATURES)
            writer.writerows(sorted(rows, key=lambda r: (r[0], r[1], r[2])))
    print(f"MOS features -> {OUT}: +{len(rows)} filas; fallos={len(failures)}")
    for job, error in failures[:20]:
        print(f"[WARN] {job[5]} {job[0]} {job[1]}: {error}", file=sys.stderr)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True); p.add_argument("--end", required=True)
    p.add_argument("--workers", type=int, default=4)
    main(p.parse_args())
