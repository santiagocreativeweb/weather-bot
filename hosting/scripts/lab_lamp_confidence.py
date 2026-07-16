#!/usr/bin/env python3
"""Selective confidence gates for frozen +2h LAMPX predictions."""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_cityx_confidence import day_bootstrap, wilson_lower  # noqa: E402
from lab_lamp import build_details, cityx_predictions  # noqa: E402
from lab_single_runs import D, DEV_END, MODELS, TEST0, TEST1  # noqa: E402
from score_forward_history import pick_bucket  # noqa: E402
from wxbt.cityx_confidence import spread_buckets  # noqa: E402

MIN_DEV_COVERAGE = .35
BASE = "BLEND50|X60"
GATES = {
    "ALL": lambda x: pd.Series(True, index=x.index),
    "SAME": lambda x: x.same == 1,
    "SAME_SPREAD11": lambda x: (x.same == 1) & (x.spread <= 1.1),
    "SAME_SPREAD15": lambda x: (x.same == 1) & (x.spread <= 1.5),
    "DIST1": lambda x: x.distance <= 1,
    "DIST1_SPREAD11": lambda x: (x.distance <= 1) & (x.spread <= 1.1),
    "SPREAD11": lambda x: x.spread <= 1.1,
    "SPREAD15": lambda x: x.spread <= 1.5,
}


def add_features(lamp):
    city = cityx_predictions()[["station", "d", "mu_cityx"]]
    runs = pd.read_csv(os.path.join(D, "single_runs.csv"))
    runs["d"] = pd.to_datetime(runs.target).dt.date
    wide = runs.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                            aggfunc="last").reset_index()
    data = lamp.merge(city, on=["station", "d"]).merge(wide, on=["station", "d"])
    rows = []
    for r in data.itertuples(index=False):
        values = [float(getattr(r, model)) for model in MODELS
                  if hasattr(r, model) and pd.notna(getattr(r, model))]
        lb = pick_bucket(math.floor(r.mu), "F")
        cb = pick_bucket(math.floor(r.mu_cityx), "F")
        rows.append({"same": int(lb == cb), "distance": abs(lb[0]-cb[0])/2,
                     "spread": spread_buckets(values, "F"), "n_models": len(values)})
    return pd.concat([data.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def summaries(frame):
    rows = []
    for name, gate in GATES.items():
        selected = frame[gate(frame)]
        n, hits = len(selected), int(selected.hit.sum())
        rows.append({"gate": name, "n": n, "coverage": n/len(frame),
                     "exact": hits/n if n else np.nan, "top2": selected.top2.mean(),
                     "lower90": wilson_lower(hits, n)})
    return pd.DataFrame(rows)


def main():
    path = os.path.join(D, "lamp_daily_lag2.csv")
    lamp = build_details(frozen_test=True, lamp_path=path)
    lamp = lamp[lamp.candidate == BASE].copy(); lamp["unit"] = "F"
    data = add_features(lamp)
    data.to_csv(os.path.join(D, "lab_lamp_confidence_detail.csv"), index=False)
    dev = data[data.d <= DEV_END]
    rank = summaries(dev)
    eligible = rank[rank.coverage >= MIN_DEV_COVERAGE].sort_values(
        ["lower90", "exact", "coverage"], ascending=False)
    winner = eligible.iloc[0].gate
    print(f"LAMP confidence DEV through {DEV_END}; n={len(dev)}, "
          f"min coverage={MIN_DEV_COVERAGE:.0%}")
    print(rank.sort_values("lower90", ascending=False).to_string(index=False, formatters={
        "coverage": "{:.1%}".format, "exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "lower90": "{:.1%}".format}))
    print(f"\nGate seleccionado sólo en DEV: {winner}")
    test = data[(data.d >= TEST0) & (data.d <= TEST1)]
    selected = test[GATES[winner](test)]
    p, ci = day_bootstrap(selected, test)
    print(f"\nHoldout descriptivo {TEST0}..{TEST1}:")
    print(f" all LAMPX: n={len(test)}, exact={test.hit.mean():.1%}, top2={test.top2.mean():.1%}")
    print(f" selected:  n={len(selected)} ({len(selected)/len(test):.1%}), "
          f"exact={selected.hit.mean():.1%}, top2={selected.top2.mean():.1%}")
    print(f" delta={selected.hit.mean()-test.hit.mean():+.1%}, p={p:.5f}, "
          f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    print("Promoción prohibida desde este holdout ya abierto; cualquier gate requiere forward.")


if __name__ == "__main__":
    main()
