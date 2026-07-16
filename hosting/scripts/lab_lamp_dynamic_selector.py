#!/usr/bin/env python3
"""Causal rolling selector between frozen LAMPX and CITYX predictions.

The family is fixed before first execution.  Every decision uses station-level
outcomes no newer than target-2 days, avoiding an assumption that yesterday's
Gamma market has already resolved by the early-morning freeze.  Selection is
on development through June 20; the already-exposed June 21-July 11 interval
is descriptive and cannot promote a live model without a forward gate.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import D, DEV_END, TEST0, TEST1, bootstrap_day  # noqa: E402


LAMP = "BLEND50|X60"
CITY = "CITYX2"
LABEL_LAG_DAYS = 2
MIN_TRAIN = 7
SPECS = {
    "ACC14_M00": ("exact", 14, 0.00),
    "ACC30_M00": ("exact", 30, 0.00),
    "ACC30_M05": ("exact", 30, 0.05),
    "ACC60_M00": ("exact", 60, 0.00),
    "MAE14_M00": ("mae", 14, 0.00),
    "MAE30_M10": ("mae", 30, 0.10),
    "HYBRID30": ("hybrid", 30, 0.05),
}
DETAIL = os.path.join(D, "lab_lamp_dynamic_selector_detail.csv")


def load_pairs():
    detail = pd.read_csv(os.path.join(D, "lab_lamp_detail_frozen.csv"))
    detail["d"] = pd.to_datetime(detail.d).dt.date
    lamp = detail[detail.candidate == LAMP][["station", "d", "mu", "hit", "top2", "ae"]]
    lamp = lamp.rename(columns={column: f"{column}_lamp" for column in ("mu", "hit", "top2", "ae")})
    city = detail[detail.candidate == CITY][["station", "d", "mu", "hit", "top2", "ae"]]
    city = city.rename(columns={column: f"{column}_city" for column in ("mu", "hit", "top2", "ae")})
    return lamp.merge(city, on=["station", "d"]).sort_values(["station", "d"])


def eligible_history(group, day, window):
    newest = day-dt.timedelta(days=LABEL_LAG_DAYS)
    oldest = day-dt.timedelta(days=window)
    return group[(group.d >= oldest) & (group.d <= newest)]


def choose_city(group, day, specification):
    metric, window, margin = specification
    history = eligible_history(group, day, window)
    if len(history) < MIN_TRAIN:
        return False
    exact_advantage = float(history.hit_city.mean()-history.hit_lamp.mean())
    mae_advantage = float(history.ae_lamp.mean()-history.ae_city.mean())
    if metric == "exact":
        return exact_advantage > margin
    if metric == "mae":
        return mae_advantage > margin
    if metric == "hybrid":
        return exact_advantage > margin and mae_advantage > 0
    raise ValueError(metric)


def build_details(pairs):
    rows = []
    for station, group in pairs.groupby("station"):
        group = group.sort_values("d")
        for row in group.itertuples(index=False):
            common = {"station": station, "d": row.d,
                "hit_base": row.hit_lamp, "top2_base": row.top2_lamp,
                "ae_base": row.ae_lamp}
            rows.append({**common, "candidate": "BASE_LAMPX", "source": "LAMPX",
                "mu": row.mu_lamp, "hit": row.hit_lamp, "top2": row.top2_lamp,
                "ae": row.ae_lamp})
            for name, specification in SPECS.items():
                city = choose_city(group, row.d, specification)
                suffix, source = ("city", "CITYX") if city else ("lamp", "LAMPX")
                rows.append({**common, "candidate": name, "source": source,
                    "mu": getattr(row, f"mu_{suffix}"), "hit": getattr(row, f"hit_{suffix}"),
                    "top2": getattr(row, f"top2_{suffix}"), "ae": getattr(row, f"ae_{suffix}")})
    return pd.DataFrame(rows)


def summarize(frame):
    return frame.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), mae=("ae", "mean"),
        city_share=("source", lambda values: float(np.mean(values == "CITYX")))).reset_index().sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])


def main():
    pairs = load_pairs()
    details = build_details(pairs)
    details.to_csv(DETAIL, index=False)
    dev = details[details.d <= DEV_END]
    ranking = summarize(dev)
    selected = ranking.iloc[0].candidate
    print(f"Dynamic LAMP/CITY selector: label lag={LABEL_LAG_DAYS}d, "
          f"DEV <= {DEV_END}, TEST {TEST0}..{TEST1}")
    print("\nFrozen DEV ranking:")
    print(ranking.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "mae": "{:.3f}".format,
        "city_share": "{:.1%}".format}))
    print(f"\nDEV selection: {selected}")
    if selected == "BASE_LAMPX":
        print("No rolling selector beat LAMPX in development: REJECT before test.")
        return
    test = details[(details.d >= TEST0) & (details.d <= TEST1) &
                   (details.candidate == selected)].copy()
    p, ci = bootstrap_day(test)
    print(f"Historical test {selected}: n={len(test)}, exact "
          f"{test.hit_base.mean():.1%} -> {test.hit.mean():.1%} "
          f"({test.hit.mean()-test.hit_base.mean():+.1%}), top2 "
          f"{test.top2_base.mean():.1%} -> {test.top2.mean():.1%}, MAE "
          f"{test.ae_base.mean():.3f} -> {test.ae.mean():.3f}, "
          f"CITYX share={(test.source == 'CITYX').mean():.1%}, p={p:.5f}, "
          f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = test.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        selector=("hit", "mean"), top2=("top2", "mean"),
        city_share=("source", lambda values: float(np.mean(values == "CITYX"))))
    print(by.to_string(formatters={"base": "{:.1%}".format,
        "selector": "{:.1%}".format, "top2": "{:.1%}".format,
        "city_share": "{:.1%}".format}))
    passed = (test.hit.mean() > test.hit_base.mean() and
              test.top2.mean() >= test.top2_base.mean() and p < .05)
    print("Exploratory gate delta>0, top2 nondegrade, p<0.05 -> " +
          ("PASS (forward shadow required)" if passed else "REJECT"))


if __name__ == "__main__":
    main()
