#!/usr/bin/env python3
"""Exploratory exact-bucket pool of init-honest LAMPX and pre-freeze CLOB prices.

The market and LAMP historical holdouts were already inspected by prior labs,
so this cannot promote a model.  It tests whether a formula is strong enough
to pre-register for forward evaluation.  Candidate family fixed before run:
linear and logarithmic pools with 25/50/75% weight on LAMPX, at the operational
freeze, with prices no more than eight hours old.
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
from dashboard import freeze_utc  # noqa: E402
from lab_lamp import payout_truth  # noqa: E402
from lab_market_consensus import contains, latest_prices, market_grids  # noqa: E402
from lab_single_runs import bootstrap_day  # noqa: E402
from wxbt.market import bucket_prob  # noqa: E402


D = os.path.join(os.path.dirname(__file__), "..", "data")
STATIONS = {"KLGA", "KORD"}
DEV0, DEV1 = dt.date(2026, 5, 10), dt.date(2026, 6, 10)
TEST0, TEST1 = dt.date(2026, 6, 11), dt.date(2026, 6, 30)
WEIGHTS = (.25, .50, .75)
BASE = "BLEND50|X60"
DETAIL = os.path.join(D, "lab_lamp_market_detail.csv")


def pool_rank(buckets, mids, mu, sigma, weight, mode):
    """Rank integer positions in a shared market/LAMP bucket grid."""
    market = np.clip(np.asarray(mids, float), 1e-5, 1.0)
    market /= market.sum()
    lamp = np.asarray([bucket_prob(mu-0.5, sigma, lo, hi) for lo, hi in buckets], float)
    lamp = np.clip(lamp, 1e-8, None)
    lamp /= lamp.sum()
    if mode == "LIN":
        score = weight*lamp+(1-weight)*market
    elif mode == "LOG":
        score = np.exp(weight*np.log(lamp)+(1-weight)*np.log(market))
    else:
        raise ValueError(mode)
    return np.argsort(-score).tolist()


def load_lamp():
    detail = pd.read_csv(os.path.join(D, "lab_lamp_detail_frozen.csv"))
    detail["d"] = pd.to_datetime(detail.d).dt.date
    detail = detail[(detail.station.isin(STATIONS)) & (detail.candidate == BASE)][
        ["station", "d", "mu"]].rename(columns={"mu": "mu_lamp"})
    detail = detail.merge(payout_truth()[["station", "d", "max_real"]],
                          on=["station", "d"])
    provenance = pd.read_csv(os.path.join(D, "lamp_daily_lag2.csv"))
    provenance["d"] = pd.to_datetime(provenance.target).dt.date
    provenance["avail_utc"] = pd.to_datetime(provenance.avail_utc, utc=True).dt.tz_localize(None)
    detail = detail.merge(provenance[["station", "d", "avail_utc"]], on=["station", "d"])
    sigmas, history = [], {}
    for row in detail.sort_values(["d", "station"]).itertuples():
        prior = [error for day, error in history.get(row.station, [])
                 if day < row.d and (row.d-day).days <= 60]
        sigmas.append(max(float(np.std(prior, ddof=1)), 1.0) if len(prior) >= 15 else 2.5)
        history.setdefault(row.station, []).append((row.d, float(row.mu_lamp-row.max_real)))
    detail = detail.sort_values(["d", "station"]).copy()
    detail["sigma"] = sigmas
    return detail


def build_details():
    lamp = load_lamp()
    grids, winners = market_grids()
    prices = pd.read_csv(os.path.join(D, "prices.csv"), parse_dates=["t"])
    prices["d"] = pd.to_datetime(prices.target).dt.date
    prices = prices[prices.station.isin(STATIONS) &
                    (prices.d >= DEV0) & (prices.d <= TEST1)]
    rows = []
    for row in lamp[(lamp.d >= DEV0) & (lamp.d <= TEST1)].itertuples():
        key = (row.station, row.d)
        grid, winner = grids.get(key), winners.get(key)
        if grid is None or winner is None:
            continue
        cutoff = pd.Timestamp(freeze_utc(row.station, row.d))
        if row.avail_utc > cutoff:
            continue
        priced = latest_prices(prices, row.station, row.d, cutoff, grid)
        if priced is None:
            continue
        temperature = math.floor(row.mu_lamp)
        lamp_bucket = next((int(item.bucket) for item in priced.itertuples()
                            if contains(temperature, item.lo, item.hi)), None)
        if lamp_bucket is None:
            continue
        buckets = list(zip(priced.lo.where(priced.lo.notna(), None),
                           priced.hi.where(priced.hi.notna(), None)))
        lamp_order = pool_rank(buckets, priced.mid, row.mu_lamp, row.sigma, 1.0, "LIN")
        ranked_lamp = priced.iloc[lamp_order].bucket.astype(int).tolist()
        common = {"station": row.station, "d": row.d, "winner": winner,
                  "price_age_h": float((cutoff-priced.t.max()).total_seconds()/3600),
                  "hit_base": int(lamp_bucket == winner),
                  "top2_base": int(winner in ranked_lamp[:2])}
        rows.append({**common, "candidate": "BASE_LAMPX", "chosen": lamp_bucket,
                     "hit": common["hit_base"], "top2": common["top2_base"]})
        for mode in ("LIN", "LOG"):
            for weight in WEIGHTS:
                order = pool_rank(buckets, priced.mid, row.mu_lamp, row.sigma, weight, mode)
                ranked = priced.iloc[order].bucket.astype(int).tolist()
                rows.append({**common, "candidate": f"{mode}_W{int(weight*100):02d}",
                    "chosen": ranked[0], "hit": int(ranked[0] == winner),
                    "top2": int(winner in ranked[:2])})
    return pd.DataFrame(rows)


def summarize(frame):
    return frame.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), age=("price_age_h", "mean")).reset_index().sort_values(
            ["exact", "top2", "age"], ascending=[False, False, True])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptive-test", action="store_true",
        help="evaluate the already-exposed later period when validation retains LAMPX")
    args = parser.parse_args()
    details = build_details()
    details.to_csv(DETAIL, index=False)
    dev = details[(details.d >= DEV0) & (details.d <= DEV1)]
    ranking = summarize(dev)
    selected = ranking.iloc[0].candidate
    challenger = ranking[ranking.candidate != "BASE_LAMPX"].iloc[0].candidate
    print(f"LAMP+CLOB exact pool: DEV {DEV0}..{DEV1}; TEST {TEST0}..{TEST1}")
    print(f"paired dev={dev[dev.candidate == 'BASE_LAMPX'].shape[0]}, "
          f"test={details[(details.d >= TEST0) & (details.d <= TEST1) & (details.candidate == 'BASE_LAMPX')].shape[0]}")
    print("\nFrozen DEV ranking:")
    print(ranking.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "age": "{:.2f}h".format}))
    print(f"\nDEV selection: {selected}")
    if selected == "BASE_LAMPX" and not args.descriptive_test:
        print("No probability pool beat LAMPX in development: REJECT before test.")
        return
    policy = selected if selected != "BASE_LAMPX" else challenger
    if selected == "BASE_LAMPX":
        print(f"Descriptive-only challenger: {policy}")
    test = details[(details.d >= TEST0) & (details.d <= TEST1) &
                   (details.candidate == policy)].copy()
    p, ci = bootstrap_day(test)
    print(f"\nHistorical test {policy}: n={len(test)}, exact "
          f"{test.hit_base.mean():.1%} -> {test.hit.mean():.1%} "
          f"({test.hit.mean()-test.hit_base.mean():+.1%}), top2 "
          f"{test.top2_base.mean():.1%} -> {test.top2.mean():.1%}, "
          f"p={p:.5f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = test.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        pool=("hit", "mean"), top2=("top2", "mean"))
    print(by.to_string(formatters={"base": "{:.1%}".format, "pool": "{:.1%}".format,
                                   "top2": "{:.1%}".format}))
    passed = test.hit.mean() > test.hit_base.mean() and \
        test.top2.mean() >= test.top2_base.mean() and p < .05
    print("Exploratory gate delta>0, top2 nondegrade, p<0.05 -> " +
          ("PASS (requires forward shadow)" if passed else "REJECT"))


if __name__ == "__main__":
    main()
