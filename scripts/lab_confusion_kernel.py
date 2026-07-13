#!/usr/bin/env python3
"""Exact-bucket posterior from rolling per-model confusion kernels.

This is distinct from point voting and the rejected global classifier: every
model contributes its empirical distribution of paid-bucket displacements.
All stations for a target date are predicted before that date updates either
local or pooled histories, preventing cross-city same-day leakage.

The old holdout labels are already known, so results are exploratory only.
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import D, D0, DEV_END, MODELS, TEST0, TEST1, bootstrap_day  # noqa: E402
from score_forward_history import overlaps, parse_win, pick_bucket  # noqa: E402
from wxbt.exact_selector import CITYX1_RECIPES, CITYX2_NEW_RECIPES  # noqa: E402

KS = np.arange(-4, 5)
ALPHA = 0.5
PRIOR_STRENGTH = 15.0
CANDIDATES = ("LOCAL30", "LOCAL60", "SHRINK60")


def closest_displacement(value, unit, winner):
    actual = parse_win(winner)
    if actual is None:
        return None
    step = 2.0 if unit == "F" else 1.0
    hits = [int(k) for k in KS
            if overlaps(pick_bucket(math.floor(value+k*step), unit), actual)]
    return min(hits, key=lambda k: (abs(k), k)) if hits else None


def recent_labels(history, day, days):
    return [k for d, k in history if d < day and (day-d).days <= days]


def kernel(local, pooled, mode):
    days = 30 if mode == "LOCAL30" else 60
    loc = recent_labels(local[0], local[1], days)
    pool = recent_labels(pooled[0], pooled[1], days)
    local_counts = np.array([loc.count(int(k)) for k in KS], dtype=float)
    if mode == "SHRINK60":
        pooled_counts = np.array([pool.count(int(k)) for k in KS], dtype=float)
        prior = (pooled_counts+ALPHA)/(pooled_counts.sum()+ALPHA*len(KS))
        counts = local_counts + PRIOR_STRENGTH*prior
        return counts/counts.sum()
    counts = local_counts+ALPHA
    return counts/counts.sum()


def posterior_pick(row, day, local_hist, pooled_hist, mode):
    scores = {}
    values = [float(row[m]) for m in MODELS if m in row.index and pd.notna(row[m])]
    if len(values) < 3:
        return None, []
    step = 2.0 if row.unit == "F" else 1.0
    median = float(np.median(values))
    for model in MODELS:
        if model not in row.index or pd.isna(row[model]):
            continue
        probs = kernel((local_hist.get((row.station, model), []), day),
                       (pooled_hist.get((row.unit, model), []), day), mode)
        for k, probability in zip(KS, probs):
            bucket = pick_bucket(math.floor(float(row[model])+int(k)*step), row.unit)
            scores[bucket] = scores.get(bucket, 0.0) + float(probability)
    ranked = sorted(scores, key=lambda bucket: (-scores[bucket],
        abs(((bucket[0]+bucket[1])/2)-median), bucket[0]))
    return ranked[0], ranked


def load_data():
    sr = pd.read_csv(os.path.join(D, "single_runs.csv"))
    sr["d"] = pd.to_datetime(sr.target).dt.date
    wide = sr.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                          aggfunc="last").reset_index()
    old = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    old["d"] = pd.to_datetime(old.target).dt.date
    old = old[(old.lead == 2) & old.win_mkt.notna()][["station", "d", "win_mkt"]]
    new = pd.read_csv(os.path.join(D, "gamma_labels.csv"))
    new["d"] = pd.to_datetime(new.target).dt.date
    truth = pd.concat([old, new[["station", "d", "win_mkt"]]], ignore_index=True)
    truth = truth.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    return wide.merge(truth, on=["station", "d"]).sort_values(["d", "station"])


def cityx_control():
    recipes = {**CITYX1_RECIPES, **CITYX2_NEW_RECIPES}
    frames = []
    for filename, subset in [("lab_single_runs_detail.csv", CITYX1_RECIPES),
                             ("lab_new_cities_detail.csv", CITYX2_NEW_RECIPES)]:
        detail = pd.read_csv(os.path.join(D, filename)); detail["d"] = pd.to_datetime(detail.d).dt.date
        frames.extend(detail[(detail.station == station) & (detail.candidate == recipes[station])]
                      for station in subset)
    return pd.concat(frames, ignore_index=True)[["station", "d", "hit", "top2"]].rename(
        columns={"hit": "hit_base", "top2": "top2_base"})


def build_details(data=None):
    data = load_data() if data is None else data.sort_values(["d", "station"])
    local_hist, pooled_hist, rows = {}, {}, []
    for day, group in data.groupby("d", sort=True):
        pending = []
        for _, row in group.iterrows():
            winner = parse_win(row.win_mkt)
            if winner is None:
                continue
            if D0 <= day <= TEST1:
                for mode in CANDIDATES:
                    selected, ranked = posterior_pick(row, day, local_hist, pooled_hist, mode)
                    if selected is not None:
                        rows.append({"station": row.station, "d": day, "candidate": mode,
                            "hit": int(overlaps(selected, winner)),
                            "top2": int(any(overlaps(bucket, winner) for bucket in ranked[:2])),
                            "selected": str(selected)})
            for model in MODELS:
                if model in row.index and pd.notna(row[model]):
                    k = closest_displacement(float(row[model]), row.unit, row.win_mkt)
                    if k is not None:
                        pending.append((row.station, row.unit, model, k))
        # No station on `day` can influence another station on the same target.
        for station, unit, model, k in pending:
            local_hist.setdefault((station, model), []).append((day, k))
            pooled_hist.setdefault((unit, model), []).append((day, k))
    return pd.DataFrame(rows)


def main():
    details = build_details().merge(cityx_control(), on=["station", "d"])
    details.to_csv(os.path.join(D, "lab_confusion_kernel_detail.csv"), index=False)
    dev = details[details.d <= DEV_END]
    rank = dev.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean")).reset_index().sort_values(
            ["exact", "top2"], ascending=[False, False])
    winner = rank.iloc[0].candidate
    test = details[(details.candidate == winner) & (details.d >= TEST0) & (details.d <= TEST1)]
    test = test.copy()
    test["hybrid_top2"] = test[["hit_base", "hit"]].max(axis=1)
    p, ci = bootstrap_day(test)
    print("Confusion-kernel exploratorio: holdout labels ya conocidos; promoción prohibida.")
    print("\nDEV ranking:")
    print(rank.to_string(index=False, formatters={"exact": "{:.1%}".format,
                                                  "top2": "{:.1%}".format}))
    print(f"\nGanador DEV: {winner}")
    print(f"TEST n={len(test)}, ciudades={test.station.nunique()}: exacto "
          f"{test.hit_base.mean():.1%} CITYX2 -> {test.hit.mean():.1%} kernel "
          f"({test.hit.mean()-test.hit_base.mean():+.1%}); top2 "
          f"{test.top2_base.mean():.1%} -> {test.top2.mean():.1%}; p={p:.5f}, "
          f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    print(f"Top2 híbrido CITYX-top1 + kernel-top1: {test.hybrid_top2.mean():.1%} "
          f"(vs top2 CITYX {test.top2_base.mean():.1%})")
    by = test.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        kernel=("hit", "mean"), top2=("top2", "mean"))
    print("\nPor ciudad:")
    print(by.to_string(formatters={"base": "{:.1%}".format,
        "kernel": "{:.1%}".format, "top2": "{:.1%}".format}))


if __name__ == "__main__":
    main()
