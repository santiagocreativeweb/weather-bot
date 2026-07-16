#!/usr/bin/env python3
"""Diagnose the WU/Gamma oracle transformation using raw METAR temperatures.

This is a truth-source audit, not a forecast-model search.  For Fahrenheit
markets, METAR exposes both the integer Celsius body group (``23/17``) and,
usually, a tenths-Celsius T group (``T02330167``).  IEM's processed ``tmpf``
can preserve a different rounding path.  We compare each representation with
the bucket actually paid by Polymarket, without tuning a forecasting recipe.
"""
import argparse
import csv
import datetime as dt
import io
import math
import os
import re
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
from lab_v7 import hit_mkt_floor, parse_win  # noqa: E402
from show_live import local_offset  # noqa: E402


D = os.path.join(os.path.dirname(__file__), "..", "data")
NETWORKS = {
    "KLGA": "NY_ASOS", "KORD": "IL_ASOS", "KMIA": "FL_ASOS",
    "KSFO": "CA_ASOS", "KLAX": "CA_ASOS", "KDAL": "TX_ASOS",
    "KATL": "GA_ASOS", "KHOU": "TX_ASOS", "KAUS": "TX_ASOS",
}
TGROUP = re.compile(r"(?:^|\s)T([01])(\d{3})[01]\d{3}(?=\s|$)")
BODY = re.compile(r"(?:^|\s)(M?\d{2})/(?:M?\d{2})(?=\s)")


def signed_c(sign, digits):
    value = int(digits) / 10.0
    return -value if sign == "1" else value


def parse_tgroup_f(metar):
    match = TGROUP.search(str(metar))
    if not match:
        return None
    return signed_c(match.group(1), match.group(2)) * 9.0 / 5.0 + 32.0


def parse_body_f(metar):
    match = BODY.search(str(metar))
    if not match:
        return None
    token = match.group(1)
    value = -int(token[1:]) if token.startswith("M") else int(token)
    return value * 9.0 / 5.0 + 32.0


def half_up(value):
    return math.floor(value + 0.5)


def fetch_metar(station, start, end):
    """Fetch [start,end] local-day coverage plus one UTC buffer day."""
    p0, p1 = start - dt.timedelta(days=1), end + dt.timedelta(days=2)
    params = dict(network=NETWORKS[station], station=station[1:], data=["tmpf", "metar"],
                  year1=p0.year, month1=p0.month, day1=p0.day,
                  year2=p1.year, month2=p1.month, day2=p1.day,
                  tz="Etc/UTC", format="onlycomma", latlon="no", elev="no",
                  missing="M", trace="T", direct="yes", report_type=[3, 4])
    response = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
                            params=params, headers={"User-Agent": "wxbt-oracle-audit/1.0"},
                            timeout=120)
    response.raise_for_status()
    return pd.read_csv(io.StringIO(response.text))


def daily_raw(station, raw):
    rows = []
    for r in raw.itertuples(index=False):
        try:
            stamp = dt.datetime.fromisoformat(str(r.valid).replace(" ", "T"))
        except (AttributeError, TypeError, ValueError):
            continue
        local_day = (stamp + dt.timedelta(hours=local_offset(station, stamp.date()))).date()
        tmpf = pd.to_numeric(getattr(r, "tmpf", None), errors="coerce")
        rows.append(dict(target=local_day, tmpf=float(tmpf) if pd.notna(tmpf) else None,
                         tgroup=parse_tgroup_f(getattr(r, "metar", "")),
                         body=parse_body_f(getattr(r, "metar", ""))))
    frame = pd.DataFrame(rows)
    return frame.groupby("target", as_index=False).agg(
        tmpf=("tmpf", "max"), tgroup=("tgroup", "max"), body=("body", "max"),
        reports=("target", "size"), t_reports=("tgroup", "count"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", nargs="+", default=list(NETWORKS))
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--out", default=os.path.join(D, "lab_metar_precision.csv"))
    a = ap.parse_args()

    backfill = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    backfill["target"] = pd.to_datetime(backfill.target).dt.date
    backfill = backfill[(backfill.lead == 2) & backfill.station.isin(a.stations) &
                        backfill.win_mkt.notna()][["station", "target", "max_real", "win_mkt"]]
    gamma_path = os.path.join(D, "gamma_labels.csv")
    if os.path.exists(gamma_path):
        gamma = pd.read_csv(gamma_path)
        gamma["target"] = pd.to_datetime(gamma.target).dt.date
        obs = pd.read_csv(os.path.join(D, "obs.csv"))
        obs["target"] = pd.to_datetime(obs.date).dt.date
        gamma = gamma[gamma.station.isin(a.stations)].merge(
            obs[["station", "target", "tmax"]], on=["station", "target"], how="left")
        gamma = gamma.rename(columns={"tmax": "max_real"})[
            ["station", "target", "max_real", "win_mkt"]]
        labels = pd.concat([backfill, gamma], ignore_index=True)
    else:
        labels = backfill
    labels = labels.dropna(subset=["win_mkt"]).drop_duplicates(["station", "target"])
    start = dt.date.fromisoformat(a.start) if a.start else min(labels.target)
    end = dt.date.fromisoformat(a.end) if a.end else max(labels.target)
    labels = labels[(labels.target >= start) & (labels.target <= end)]

    joined = []
    for station in a.stations:
        raw = fetch_metar(station, start, end)
        day = daily_raw(station, raw)
        part = labels[labels.station == station].merge(day, on="target", how="left")
        joined.append(part)
    out = pd.concat(joined, ignore_index=True)

    candidates = {
        "iem_daily": lambda r: r.max_real,
        "raw_tmpf": lambda r: r.tmpf,
        "metar_tenths_floor": lambda r: r.tgroup,
        "metar_tenths_halfup": lambda r: half_up(r.tgroup),
        "metar_body_floor": lambda r: r.body,
        "metar_body_halfup": lambda r: half_up(r.body),
    }
    records = []
    for r in out.itertuples():
        wb = parse_win(r.win_mkt)
        for name, getter in candidates.items():
            try:
                value = getter(r)
                hit = None if pd.isna(value) or wb is None else hit_mkt_floor(float(value), "F", wb)
            except (TypeError, ValueError):
                value, hit = None, None
            records.append(dict(station=r.station, target=r.target, candidate=name,
                                value=value, hit=hit, winner=r.win_mkt,
                                reports=r.reports, t_reports=r.t_reports))
    scored = pd.DataFrame(records)
    summary = scored.groupby(["station", "candidate"]).agg(
        n=("hit", "count"), exact=("hit", "mean")).reset_index()
    print(f"METAR oracle audit {start}..{end}; labels={len(out)}")
    print(summary.pivot(index="candidate", columns="station", values="exact")
          .to_string(float_format=lambda x: f"{x:.1%}"))
    coverage = out.groupby("station").agg(days=("target", "size"),
        raw_days=("tmpf", "count"), tgroup_days=("tgroup", "count"),
        median_reports=("reports", "median"))
    print("\nCoverage:")
    print(coverage.to_string())
    scored.to_csv(a.out, index=False, quoting=csv.QUOTE_MINIMAL)
    print(f"\nDetail -> {a.out}")


if __name__ == "__main__":
    main()
