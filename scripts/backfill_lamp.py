#!/usr/bin/env python3
"""Backfill init-explicit NOAA LAMP (LAV) station temperature curves from IEM.

IEM archives each MOS runtime explicitly.  We conservatively declare guidance
available one hour after runtime, select the latest run available at the 04:30
local CITYX freeze, and retain the maximum hourly LAV temperature from 08-22
local time.  This avoids using same-day observations published after freeze.
"""
import argparse
import datetime as dt
import io
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import freeze_utc  # noqa: E402
from show_live import local_offset  # noqa: E402


D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "lamp_daily.csv")
API = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
STATIONS = ["KLGA", "KORD", "KMIA", "KSFO", "KLAX", "KDAL", "KATL", "KHOU", "KAUS"]
DEFAULT_AVAIL_LAG_HOURS = 1
LOCAL_HOURS = range(8, 23)
MIN_HOURS = 10


def fetch_station(station, start, end):
    params = dict(station=station, model="LAV",
                  sts=f"{(start-dt.timedelta(days=1)).isoformat()}T00:00Z",
                  ets=f"{(end+dt.timedelta(days=2)).isoformat()}T00:00Z", format="csv")
    last = None
    for attempt in range(3):
        try:
            response = requests.get(API, params=params,
                                    headers={"User-Agent": "wxbt-lamp-research/1.0"}, timeout=180)
            response.raise_for_status()
            frame = pd.read_csv(io.StringIO(response.text))
            if frame.empty:
                return frame
            frame["runtime"] = pd.to_datetime(frame.runtime, utc=True)
            frame["ftime"] = pd.to_datetime(frame.ftime, utc=True)
            frame["tmp"] = pd.to_numeric(frame.tmp, errors="coerce")
            return frame.dropna(subset=["runtime", "ftime", "tmp"])
        except Exception as exc:
            last = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"{station}: {last}")


def select_daily(frame, station, start, end, avail_lag_hours=DEFAULT_AVAIL_LAG_HOURS):
    avail_lag = dt.timedelta(hours=avail_lag_hours)
    rows = []
    for n in range((end-start).days+1):
        target = start+dt.timedelta(days=n)
        freeze = freeze_utc(station, target).replace(tzinfo=dt.timezone.utc)
        eligible = frame[frame.runtime + avail_lag <= freeze]
        if eligible.empty:
            continue
        runtime = eligible.runtime.max()
        run = eligible[eligible.runtime == runtime].copy()
        offset = local_offset(station, target)
        run["local"] = run.ftime + pd.to_timedelta(offset, unit="h")
        run = run[(run.local.dt.date == target) & run.local.dt.hour.isin(LOCAL_HOURS)]
        if len(run) < MIN_HOURS:
            continue
        rows.append(dict(station=station, target=target.isoformat(), unit="F",
                         runtime_utc=runtime.isoformat(),
                         avail_utc=(runtime+avail_lag).isoformat(),
                         freeze_utc=freeze.isoformat(), n_hours=len(run),
                         first_local=run.local.min().isoformat(),
                         last_local=run.local.max().isoformat(), tmax=float(run.tmp.max())))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-13")
    ap.add_argument("--end", default="2026-07-11")
    ap.add_argument("--stations", nargs="+", default=STATIONS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--avail-lag-hours", type=float, default=DEFAULT_AVAIL_LAG_HOURS,
                    help="conservative delay between runtime and usable publication")
    args = ap.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    rows = []
    for station in args.stations:
        try:
            raw = fetch_station(station, start, end)
            selected = select_daily(raw, station, start, end, args.avail_lag_hours)
            rows.extend(selected)
            print(f"LAMP {station}: raw={len(raw)} selected={len(selected)}")
        except Exception as exc:
            print(f"[WARN] LAMP {station}: {exc}", file=sys.stderr)
    out = pd.DataFrame(rows).sort_values(["station", "target"])
    out.to_csv(args.out, index=False)
    print(f"LAMP -> {args.out}: {len(out)} station-days")


if __name__ == "__main__":
    main()
