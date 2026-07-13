#!/usr/bin/env python3
"""Point-in-time pre-freeze ASOS innovation against the selected LAV run."""
import argparse
import datetime as dt
import io
import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backfill_lamp import LOCAL_HOURS, fetch_station  # noqa: E402
from dashboard import freeze_utc  # noqa: E402
from lab_metar_precision import NETWORKS  # noqa: E402
from show_live import local_offset  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "lamp_nowcast.csv")
STATIONS = list(NETWORKS)
LAV_LAG_H = 2.0
OBS_LAG_MIN = 15
ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"


def fetch_asos(station, start, end):
    p0, p1 = start-dt.timedelta(days=1), end+dt.timedelta(days=2)
    params = {"network": NETWORKS[station], "station": station[1:], "data": "tmpf",
              "year1": p0.year, "month1": p0.month, "day1": p0.day,
              "year2": p1.year, "month2": p1.month, "day2": p1.day,
              "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no", "elev": "no",
              "missing": "M", "trace": "T", "direct": "yes", "report_type": [3, 4]}
    response = requests.get(ASOS, params=params,
        headers={"User-Agent": "wxbt-lamp-nowcast-research/1.0"}, timeout=180)
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    frame["valid"] = pd.to_datetime(frame.valid, utc=True, format="mixed")
    frame["tmpf"] = pd.to_numeric(frame.tmpf, errors="coerce")
    return frame.dropna(subset=["valid", "tmpf"])


def select_features(lav, obs, station, start, end):
    lav = lav.copy(); obs = obs.copy()
    lav["tmp"] = pd.to_numeric(lav.tmp, errors="coerce")
    rows = []
    for n in range((end-start).days+1):
        target = start+dt.timedelta(days=n)
        freeze = pd.Timestamp(freeze_utc(station, target), tz="UTC")
        lav_eligible = lav[lav.runtime+pd.Timedelta(hours=LAV_LAG_H) <= freeze]
        if lav_eligible.empty:
            continue
        runtime = lav_eligible.runtime.max()
        run = lav_eligible[(lav_eligible.runtime == runtime) & lav_eligible.tmp.notna()].copy()
        offset = local_offset(station, target)
        run["local"] = run.ftime+pd.Timedelta(hours=offset)
        run = run[run.local.dt.date == target]
        cutoff = freeze-pd.Timedelta(minutes=OBS_LAG_MIN)
        observed = obs[obs.valid+pd.Timedelta(minutes=OBS_LAG_MIN) <= freeze].copy()
        observed["local"] = observed.valid+pd.Timedelta(hours=offset)
        observed = observed[observed.local.dt.date == target].sort_values("valid")
        if run.empty or len(observed) < 2:
            continue
        latest = observed.iloc[-1]
        delta = (run.ftime-latest.valid).abs()
        nearest = run.loc[delta.idxmin()]
        if abs((nearest.ftime-latest.valid).total_seconds()) > 75*60:
            continue
        elapsed = max((latest.valid-observed.iloc[0].valid).total_seconds()/3600, 1e-6)
        rows.append({"station": station, "target": target.isoformat(), "unit": "F",
            "runtime_utc": runtime.isoformat(),
            "lav_avail_utc": (runtime+pd.Timedelta(hours=LAV_LAG_H)).isoformat(),
            "freeze_utc": freeze.isoformat(), "obs_valid_utc": latest.valid.isoformat(),
            "obs_avail_utc": (latest.valid+pd.Timedelta(minutes=OBS_LAG_MIN)).isoformat(),
            "n_obs": len(observed), "obs_latest": float(latest.tmpf),
            "obs_min": float(observed.tmpf.min()),
            "obs_trend_fph": float((latest.tmpf-observed.iloc[0].tmpf)/elapsed),
            "lav_at_obs": float(nearest.tmp), "lav_match_utc": nearest.ftime.isoformat(),
            "innovation": float(latest.tmpf-nearest.tmp),
            "lav_tmax": float(run[run.local.dt.hour.isin(LOCAL_HOURS)].tmp.max())})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-13")
    ap.add_argument("--end", default="2026-07-11")
    ap.add_argument("--stations", nargs="+", default=STATIONS)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    rows, failures = [], []
    for station in args.stations:
        try:
            lav = fetch_station(station, start, end)
            obs = fetch_asos(station, start, end)
            selected = select_features(lav, obs, station, start, end)
            rows.extend(selected)
            print(f"NOWCAST {station}: LAV={len(lav)} ASOS={len(obs)} selected={len(selected)}")
        except Exception as exc:
            failures.append(f"{station}: {exc}")
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["station", "target"])
    out.to_csv(args.out, index=False)
    print(f"LAMP nowcast -> {args.out}: {len(out)} rows; failures={len(failures)}")
    for failure in failures:
        print(f"[WARN] {failure}", file=sys.stderr)


if __name__ == "__main__":
    main()
