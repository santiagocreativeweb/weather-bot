#!/usr/bin/env python3
"""Backfill point-in-time station MOS daily maxima for nine US markets.

Products are IEM's runtime-explicit archive of GFS/MAV, NAM/MET, MEX,
NBM short (NBS), and NBM extended (NBE).  A deliberately conservative four
hour publication lag is applied to every product.  Daily maxima use the
native max/min field, not a maximum reconstructed from sparse forecast hours.
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
OUT = os.path.join(D, "station_mos_daily.csv")
API = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
STATIONS = ["KLGA", "KORD", "KMIA", "KSFO", "KLAX", "KDAL", "KATL", "KHOU", "KAUS"]
MODELS = {"GFS": "n_x", "NAM": "n_x", "MEX": "n_x", "NBS": "txn", "NBE": "txn"}
DEFAULT_AVAIL_LAG_HOURS = 4.0


def fetch(station, model, start, end):
    params = {"station": station, "model": model,
              "sts": f"{(start-dt.timedelta(days=1)).isoformat()}T00:00Z",
              "ets": f"{(end+dt.timedelta(days=2)).isoformat()}T00:00Z", "format": "csv"}
    last = None
    for attempt in range(4):
        try:
            response = requests.get(API, params=params,
                headers={"User-Agent": "wxbt-station-mos-research/1.0"}, timeout=180)
            response.raise_for_status()
            frame = pd.read_csv(io.StringIO(response.text))
            if frame.empty:
                return frame
            frame["runtime"] = pd.to_datetime(frame.runtime, utc=True, format="mixed")
            frame["ftime"] = pd.to_datetime(frame.ftime, utc=True, format="mixed")
            field = MODELS[model]
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
            return frame.dropna(subset=["runtime", "ftime"])
        except Exception as exc:
            last = exc; time.sleep(2**attempt)
    raise RuntimeError(f"{station}/{model}: {last}")


def select_daily(frame, station, model, start, end,
                 avail_lag_hours=DEFAULT_AVAIL_LAG_HOURS):
    field = MODELS[model]
    lag = dt.timedelta(hours=avail_lag_hours)
    rows = []
    for n in range((end-start).days+1):
        target = start + dt.timedelta(days=n)
        freeze = freeze_utc(station, target).replace(tzinfo=dt.timezone.utc)
        eligible = frame[frame.runtime + lag <= freeze]
        if eligible.empty:
            continue
        runtime = eligible.runtime.max()
        run = eligible[eligible.runtime == runtime].copy()
        run["local"] = run.ftime + pd.to_timedelta(local_offset(station, target), unit="h")
        # The max/min field is populated twice per local day.  The larger value
        # is the native daily maximum; this works across US time zones without
        # relying on a fixed UTC bulletin slot.
        values = run[(run.local.dt.date == target) & run[field].notna()][field]
        if values.empty:
            continue
        rows.append({"station": station, "target": target.isoformat(), "unit": "F",
                     "model": model, "runtime_utc": runtime.isoformat(),
                     "avail_utc": (runtime+lag).isoformat(),
                     "freeze_utc": freeze.isoformat(), "tmax": float(values.max()),
                     "n_native": int(len(values))})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-13")
    ap.add_argument("--end", default="2026-07-11")
    ap.add_argument("--stations", nargs="+", default=STATIONS)
    ap.add_argument("--models", nargs="+", default=list(MODELS))
    ap.add_argument("--avail-lag-hours", type=float, default=DEFAULT_AVAIL_LAG_HOURS)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    rows, failures = [], []
    for station in args.stations:
        for model in args.models:
            try:
                raw = fetch(station, model, start, end)
                selected = select_daily(raw, station, model, start, end, args.avail_lag_hours)
                rows.extend(selected)
                print(f"MOS {station}/{model}: raw={len(raw)} selected={len(selected)}")
            except Exception as exc:
                failures.append(f"{station}/{model}: {exc}")
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["station", "target", "model"])
    out.to_csv(args.out, index=False)
    print(f"Station MOS -> {args.out}: {len(out)} rows; failures={len(failures)}")
    for failure in failures:
        print(f"[WARN] {failure}", file=sys.stderr)


if __name__ == "__main__":
    main()
