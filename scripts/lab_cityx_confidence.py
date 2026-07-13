#!/usr/bin/env python3
"""Exact-first selective confidence gate for frozen CITYX2 predictions.

The gate family and minimum 40% DEV coverage are fixed before inspecting its
holdout performance.  Recipes remain the frozen CITYX2 recipes.  This lab may
identify a useful abstention signal, but the already-opened historical holdout
cannot promote it; any operational use requires a new forward shadow.
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import D, DEV_END, MODELS, TEST0, TEST1  # noqa: E402
from score_forward_history import pick_bucket  # noqa: E402
from wxbt.cityx_confidence import MAX_SPREAD_BUCKETS, spread_buckets  # noqa: E402
from wxbt.exact_selector import CITYX1_RECIPES, CITYX2_NEW_RECIPES, RECIPES  # noqa: E402


MIN_DEV_COVERAGE = .40
GATES = {
    # H6b added after H6a's >=50% vote family had <40% DEV coverage.
    # Thresholds came only from DEV feature coverage; no holdout hits were inspected.
    "ALL": lambda x: pd.Series(True, index=x.index),
    "VOTE125": lambda x: x.vote >= .125,
    "VOTE25": lambda x: x.vote >= .25,
    "SPREAD11": lambda x: x.spread_buckets <= MAX_SPREAD_BUCKETS,
    "SPREAD15": lambda x: x.spread_buckets <= 1.5,
    "MARGIN25": lambda x: x.margin >= .25,
    "MARGIN50": lambda x: x.margin >= .50,
    "VOTE125_SPREAD15": lambda x: (x.vote >= .125) & (x.spread_buckets <= 1.5),
    "VOTE50": lambda x: x.vote >= .50,
    "VOTE625": lambda x: x.vote >= .625,
    "VOTE75": lambda x: x.vote >= .75,
    "MEAN_MEDIAN": lambda x: (x.mean_agree == 1) & (x.median_agree == 1),
    "VOTE50_MEAN_MEDIAN": lambda x: ((x.vote >= .50) & (x.mean_agree == 1) &
                                      (x.median_agree == 1)),
    "VOTE625_SPREAD15": lambda x: (x.vote >= .625) & (x.spread_buckets <= 1.5),
    "VOTE625_MARGIN25": lambda x: (x.vote >= .625) & (x.margin >= .25),
}


def chosen_details(path, recipes):
    detail = pd.read_csv(path)
    detail["d"] = pd.to_datetime(detail.d).dt.date
    return pd.concat([detail[(detail.station == station) & (detail.candidate == recipe)]
                      for station, recipe in recipes.items()], ignore_index=True)


def add_confidence(detail):
    runs = pd.read_csv(os.path.join(D, "single_runs.csv"))
    runs["d"] = pd.to_datetime(runs.target).dt.date
    wide = runs.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                            aggfunc="last").reset_index()
    joined = detail.merge(wide, on=["station", "d", "unit"], how="inner")
    rows = []
    for r in joined.itertuples(index=False):
        values = np.array([float(getattr(r, model)) for model in MODELS
                           if hasattr(r, model) and pd.notna(getattr(r, model))])
        selected = pick_bucket(math.floor(r.mu), r.unit)
        votes = [pick_bucket(math.floor(value), r.unit) == selected for value in values]
        width = 2.0 if r.unit == "F" else 1.0
        lo = float(selected[0]); upper = float(selected[1] + 1)
        edge_distance = max(0.0, min(float(r.mu)-lo, upper-float(r.mu)))
        rows.append(dict(vote=float(np.mean(votes)), n_models=len(values),
                         spread_buckets=spread_buckets(values, r.unit),
                         mean_agree=int(pick_bucket(math.floor(float(np.mean(values))), r.unit) == selected),
                         median_agree=int(pick_bucket(math.floor(float(np.median(values))), r.unit) == selected),
                         margin=edge_distance/(width/2.0)))
    return pd.concat([joined.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def wilson_lower(hits, n, z=1.645):
    if n == 0:
        return 0.0
    p = hits/n
    den = 1 + z*z/n
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / den


def summaries(frame, reference_n):
    rows = []
    for name, gate in GATES.items():
        selected = frame[gate(frame)]
        n, hits = len(selected), int(selected.hit.sum())
        rows.append(dict(gate=name, n=n, coverage=n/reference_n, exact=hits/n if n else np.nan,
                         top2=selected.top2.mean(), lower90=wilson_lower(hits, n)))
    return pd.DataFrame(rows)


def day_bootstrap(selected, all_rows, reps=30000):
    days = sorted(set(all_rows.d))
    sel = selected.groupby("d").hit.agg(["sum", "count"])
    all_daily = all_rows.groupby("d").hit.agg(["sum", "count"])
    rng = np.random.default_rng(20260713)
    out = []
    for _ in range(reps):
        sampled = rng.choice(days, len(days), replace=True)
        sh = sum(sel.loc[d, "sum"] if d in sel.index else 0 for d in sampled)
        sn = sum(sel.loc[d, "count"] if d in sel.index else 0 for d in sampled)
        ah = sum(all_daily.loc[d, "sum"] for d in sampled)
        an = sum(all_daily.loc[d, "count"] for d in sampled)
        out.append(sh/max(sn, 1) - ah/max(an, 1))
    values = np.asarray(out)
    return float(np.mean(values <= 0)), np.quantile(values, [.05, .95])


def main():
    old = chosen_details(os.path.join(D, "lab_single_runs_detail.csv"), CITYX1_RECIPES)
    new = chosen_details(os.path.join(D, "lab_new_cities_detail.csv"), CITYX2_NEW_RECIPES)
    data = add_confidence(pd.concat([old, new], ignore_index=True))
    data.to_csv(os.path.join(D, "lab_cityx_confidence_detail.csv"), index=False)
    dev = data[data.d <= DEV_END]
    ranking = summaries(dev, len(dev))
    eligible = ranking[ranking.coverage >= MIN_DEV_COVERAGE].sort_values(
        ["lower90", "exact", "coverage"], ascending=False)
    if eligible.empty:
        raise SystemExit("No confidence gate met the pre-registered DEV coverage")
    winner = eligible.iloc[0].gate
    print(f"CITYX confidence DEV through {DEV_END}; n={len(dev)}, min coverage={MIN_DEV_COVERAGE:.0%}")
    print(ranking.sort_values("lower90", ascending=False).to_string(index=False, formatters={
        "coverage": "{:.1%}".format, "exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "lower90": "{:.1%}".format}))
    print(f"\nGate selected only on DEV: {winner}")

    test = data[(data.d >= TEST0) & (data.d <= TEST1)]
    selected = test[GATES[winner](test)]
    p, ci = day_bootstrap(selected, test)
    print(f"\nPreviously opened holdout {TEST0}..{TEST1} (sensitivity, not promotion):")
    print(f" all CITYX2: n={len(test)}, exact={test.hit.mean():.1%}, top2={test.top2.mean():.1%}")
    print(f" selected:   n={len(selected)} ({len(selected)/len(test):.1%}), "
          f"exact={selected.hit.mean():.1%}, top2={selected.top2.mean():.1%}")
    print(f" delta exact={selected.hit.mean()-test.hit.mean():+.1%}, "
          f"bootstrap-day p(delta<=0)={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    print("Promotion rule: none from this holdout; only a newly pre-registered forward shadow.")


if __name__ == "__main__":
    main()
