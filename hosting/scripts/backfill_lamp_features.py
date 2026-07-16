#!/usr/bin/env python3
"""Build init-honest physical features from archived NOAA LAMP/LAV runs.

For every station-day, this selects the latest LAV runtime whose conservative
availability (runtime + 2 h) precedes the frozen CITYX decision time.  Features
come only from that run's forecast curve; no verifying observations are used.
"""
import argparse
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backfill_lamp import LOCAL_HOURS, STATIONS, fetch_station  # noqa: E402
from dashboard import freeze_utc  # noqa: E402
from show_live import local_offset  # noqa: E402


D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "lamp_physical_features.csv")
AVAIL_LAG_HOURS = 2.0
MIN_HOURS = 10
NUMERIC = ("tmp", "dpt", "wdr", "wsp", "p01", "p06", "cig", "vis")
CLOUD_LEVEL = {"CL": 0, "FW": 1, "SC": 2, "BK": 3, "OV": 4}


def numeric(frame):
    out = frame.copy()
    for column in NUMERIC:
        if column in out:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def at_hour(run, column, hour):
    part = run[run.local.dt.hour == hour]
    if part.empty or column not in part:
        return np.nan
    values = pd.to_numeric(part[column], errors="coerce").dropna()
    return float(values.iloc[-1]) if not values.empty else np.nan


def feature_row(run, station, target, runtime, freeze):
    run = numeric(run).sort_values("local")
    daytime = run[run.local.dt.hour.isin(LOCAL_HOURS)].copy()
    if len(daytime) < MIN_HOURS or daytime.tmp.notna().sum() < MIN_HOURS:
        return None
    peak_index = daytime.tmp.idxmax()
    peak = daytime.loc[peak_index]
    wind_angle = np.deg2rad(daytime.wdr)
    cloud = daytime.cld.astype(str).str.strip().str.upper()
    cloud_level = cloud.map(CLOUD_LEVEL)
    precipitation = pd.to_numeric(daytime.p01, errors="coerce")
    p06 = pd.to_numeric(daytime.p06, errors="coerce")
    row = {
        "station": station, "target": target.isoformat(), "unit": "F",
        "runtime_utc": runtime.isoformat(),
        "avail_utc": (runtime + pd.Timedelta(hours=AVAIL_LAG_HOURS)).isoformat(),
        "freeze_utc": freeze.isoformat(), "n_hours": len(daytime),
        "tmax": float(daytime.tmp.max()), "tmin": float(daytime.tmp.min()),
        "tmp_range": float(daytime.tmp.max()-daytime.tmp.min()),
        "peak_hour_local": int(peak.local.hour),
        "dpt_peak": float(peak.dpt) if pd.notna(peak.dpt) else np.nan,
        "dpt_mean": float(daytime.dpt.mean()), "dpt_max": float(daytime.dpt.max()),
        "depression_peak": float(peak.tmp-peak.dpt) if pd.notna(peak.dpt) else np.nan,
        "wsp_peak": float(peak.wsp) if pd.notna(peak.wsp) else np.nan,
        "wsp_mean": float(daytime.wsp.mean()), "wsp_max": float(daytime.wsp.max()),
        "wind_sin_mean": float(np.sin(wind_angle).mean()),
        "wind_cos_mean": float(np.cos(wind_angle).mean()),
        "wind_sin_peak": math.sin(math.radians(float(peak.wdr))) if pd.notna(peak.wdr) else np.nan,
        "wind_cos_peak": math.cos(math.radians(float(peak.wdr))) if pd.notna(peak.wdr) else np.nan,
        "cloud_level_peak": float(CLOUD_LEVEL.get(str(peak.cld).strip().upper(), np.nan)),
        "cloud_level_mean": float(cloud_level.mean()),
        "cloud_clear_fraction": float(cloud.isin(["CL", "FW"]).mean()),
        "cloud_broken_fraction": float(cloud.isin(["BK", "OV"]).mean()),
        "p01_peak": float(peak.p01) if pd.notna(peak.p01) else np.nan,
        "p01_max": float(precipitation.max()), "p01_mean": float(precipitation.mean()),
        "p06_max": float(p06.max()) if p06.notna().any() else np.nan,
        "cig_min": float(daytime.cig.min()), "vis_min": float(daytime.vis.min()),
    }
    for hour in (8, 11, 14, 17, 20):
        row[f"tmp_h{hour:02d}"] = at_hour(daytime, "tmp", hour)
        row[f"dpt_h{hour:02d}"] = at_hour(daytime, "dpt", hour)
    return row


def select_features(frame, station, start, end):
    frame = numeric(frame)
    rows = []
    for n in range((end-start).days+1):
        target = start+dt.timedelta(days=n)
        freeze = pd.Timestamp(freeze_utc(station, target), tz="UTC")
        eligible = frame[frame.runtime+pd.Timedelta(hours=AVAIL_LAG_HOURS) <= freeze]
        if eligible.empty:
            continue
        runtime = eligible.runtime.max()
        run = eligible[eligible.runtime == runtime].copy()
        run["local"] = run.ftime+pd.Timedelta(hours=local_offset(station, target))
        run = run[run.local.dt.date == target]
        row = feature_row(run, station, target, runtime, freeze)
        if row is not None:
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-13")
    parser.add_argument("--end", default="2026-07-11")
    parser.add_argument("--stations", nargs="+", default=STATIONS)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    rows, failures = [], []
    for station in args.stations:
        try:
            raw = fetch_station(station, start, end)
            selected = select_features(raw, station, start, end)
            rows.extend(selected)
            print(f"LAMP-PHYS {station}: raw={len(raw)} selected={len(selected)}")
        except Exception as exc:
            failures.append(f"{station}: {exc}")
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["station", "target"])
    out.to_csv(args.out, index=False)
    print(f"LAMP physical features -> {args.out}: {len(out)} rows; failures={len(failures)}")
    for failure in failures:
        print(f"[WARN] {failure}", file=sys.stderr)
    if failures or len(out) < len(args.stations)*(end-start).days*.85:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
