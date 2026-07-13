#!/usr/bin/env python3
"""Backfill the pre-registered MKTWX1 pick from CLOB prices before its cutoff."""
import datetime as dt
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check_predictions import CITY_OF, MONTHS_EN, parse_bucket  # noqa: E402
from dashboard import freeze_utc  # noqa: E402
from wxbt.exact_selector import RECIPES  # noqa: E402
from wxbt.market_consensus import (CUTOFF_HOURS_BEFORE_FREEZE, MAX_PRICE_AGE_H,
    SHADOW0, STATIONS, VERSION, rank_consensus)  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "market_consensus_forward.csv")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def event_for(station, target):
    city = CITY_OF[station]
    slug = f"highest-temperature-in-{city}-on-{MONTHS_EN[target.month-1]}-{target.day}-{target.year}"
    r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30)
    r.raise_for_status(); events = r.json()
    return events[0] if events else None


def token_price_before(token, cutoff):
    start = int((cutoff-dt.timedelta(hours=MAX_PRICE_AGE_H)).replace(
        tzinfo=dt.timezone.utc).timestamp())
    cutoff_ts = int(cutoff.replace(tzinfo=dt.timezone.utc).timestamp())
    end = int((cutoff+dt.timedelta(minutes=5)).replace(tzinfo=dt.timezone.utc).timestamp())
    r = requests.get(f"{CLOB}/prices-history", params={"market": token,
        "startTs": start, "endTs": end, "fidelity": 5}, timeout=60)
    r.raise_for_status()
    eligible = [p for p in r.json().get("history", []) if p["t"] <= cutoff_ts]
    if not eligible:
        return None
    point = max(eligible, key=lambda p: p["t"])
    stamp = dt.datetime.fromtimestamp(point["t"], dt.timezone.utc).replace(tzinfo=None)
    if stamp > cutoff or cutoff-stamp > dt.timedelta(hours=MAX_PRICE_AGE_H):
        return None
    return float(point["p"]), stamp


def historical_sigma(station, target, unit):
    detail = pd.read_csv(os.path.join(D, "lab_single_runs_detail.csv"))
    detail["d"] = pd.to_datetime(detail.d).dt.date
    x = detail[(detail.station == station) & (detail.candidate == RECIPES[station]) &
               (detail.d < target) & ((target-detail.d).map(lambda z: z.days) <= 60)]
    # ae alone has no sign but its RMS is a valid dispersion estimate.
    sigma = float(np.sqrt(np.mean(x.ae*x.ae))) if len(x) >= 15 else (2.5 if unit == "F" else 1.5)
    return max(sigma, 1.0 if unit == "F" else .6)


def main():
    src = os.path.join(D, "exact_selector_forward.csv")
    if not os.path.exists(src):
        print("MKTWX1: exact_selector_forward.csv no existe"); return
    x = pd.read_csv(src, parse_dates=["capture_utc"])
    x["target"] = pd.to_datetime(x.target).dt.date
    x = x[x.station.isin(STATIONS) & (x.target >= dt.date.fromisoformat(SHADOW0))]
    done = set()
    if os.path.exists(OUT):
        old = pd.read_csv(OUT)
        done = set(zip(old.station, old.target.astype(str), old.version))
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = []
    for (station, target), group in x.groupby(["station", "target"]):
        key = (station, target.isoformat(), VERSION)
        cutoff = freeze_utc(station, target)-dt.timedelta(hours=CUTOFF_HOURS_BEFORE_FREEZE)
        if key in done or now < cutoff:
            continue
        captures = group[group.capture_utc.dt.tz_convert("UTC").dt.tz_localize(None) <= cutoff]
        if captures.empty:
            continue
        forecast = captures.sort_values("capture_utc").iloc[-1]
        try:
            event = event_for(station, target)
            if not event:
                continue
            priced = []
            for market in event.get("markets", []):
                lo, hi = parse_bucket(market.get("groupItemTitle"))
                if lo is None and hi is None:
                    continue
                tokens = market.get("clobTokenIds")
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                if not tokens:
                    continue
                pv = token_price_before(tokens[0], cutoff)
                if pv is not None:
                    priced.append((lo, hi, pv[0], pv[1]))
                time.sleep(.05)
            if len(priced) < 4:
                continue
            buckets = [(p[0], p[1]) for p in priced]
            sigma = historical_sigma(station, target, forecast.unit)
            order = rank_consensus(buckets, [p[2] for p in priced], float(forecast.mu), sigma)
            bot_temp = math.floor(float(forecast.mu))
            bot = next((b for b in buckets if (b[0] is None or bot_temp >= b[0]) and
                        (b[1] is None or bot_temp <= b[1])), (None, None))
            first, second = buckets[order[0]], buckets[order[1]]
            latest_price = max(p[3] for p in priced)
            rows.append(dict(capture_utc=forecast.capture_utc.isoformat(), station=station,
                target=target.isoformat(), version=VERSION, cutoff_utc=cutoff.isoformat(),
                price_utc=latest_price.isoformat(), unit=forecast.unit, mu=float(forecast.mu),
                sigma=sigma, bot_lo=bot[0], bot_hi=bot[1], chosen_lo=first[0], chosen_hi=first[1],
                second_lo=second[0], second_hi=second[1], n_priced=len(priced)))
        except Exception as exc:
            print(f"[WARN] MKTWX1 {station} {target}: {exc}", file=sys.stderr)
    if rows:
        pd.DataFrame(rows).to_csv(OUT, mode="a", header=not os.path.exists(OUT), index=False)
    print(f"MKTWX1: +{len(rows)} picks forward -> {OUT}")


if __name__ == "__main__":
    main()
