#!/usr/bin/env python3
"""CITYX sensitivity using WU-compatible METAR truth for Fahrenheit sites.

Recipes are selected only on the original DEV window.  The old holdout is
reported as a source-correction sensitivity analysis, not claimed as a new
untouched holdout.  Any promotion still requires a new forward gate.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import (D, D0, DEV_END, TEST0, TEST1, MIN_TRAIN, MODELS,
    base_predictions, bootstrap_day, exact_offset, gamma_hit, recent, top2_hit)  # noqa: E402
from wxbt.exact_selector import RECIPES  # noqa: E402


BASELINE = "G3_MEAN|B60"


def corrected_truth():
    old = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    old["d"] = pd.to_datetime(old.target).dt.date
    old = old[(old.lead == 2) & old.win_mkt.notna() & old.max_real.notna()][
        ["station", "d", "max_real", "win_mkt"]]

    gamma = pd.read_csv(os.path.join(D, "gamma_labels.csv"))
    gamma["d"] = pd.to_datetime(gamma.target).dt.date
    obs = pd.read_csv(os.path.join(D, "obs.csv"))
    obs["d"] = pd.to_datetime(obs.date).dt.date
    new = gamma[["station", "d", "win_mkt"]].merge(
        obs[["station", "d", "tmax"]], on=["station", "d"]).rename(
            columns={"tmax": "max_real"})
    truth = pd.concat([old, new], ignore_index=True).drop_duplicates(["station", "d"])

    precision = pd.read_csv(os.path.join(D, "lab_metar_precision.csv"))
    precision = precision[precision.candidate == "raw_tmpf"].copy()
    precision["d"] = pd.to_datetime(precision.target).dt.date
    override = precision[["station", "d", "value"]].drop_duplicates(["station", "d"])
    truth = truth.merge(override, on=["station", "d"], how="left")
    truth["source"] = np.where(truth.value.notna(), "METAR_HOURLY", "IEM_DAILY")
    truth["max_real"] = truth.value.fillna(truth.max_real)
    return truth.drop(columns="value")


def build_details():
    sr = pd.read_csv(os.path.join(D, "single_runs.csv"))
    sr["d"] = pd.to_datetime(sr.target).dt.date
    wide = sr.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                          aggfunc="last").reset_index()
    data = wide.merge(corrected_truth(), on=["station", "d"]).sort_values(["station", "d"])
    details = []
    for station, group in data.groupby("station"):
        model_histories, base_histories = {}, {}
        for _, row in group.iterrows():
            day, unit, real, win = row.d, row.unit, float(row.max_real), row.win_mkt
            bases = base_predictions(row, model_histories, day, unit)
            for base, raw in bases.items():
                history = base_histories.get(base, [])
                h30, h60 = recent(history, day, 30), recent(history, day, 60)
                corrections = {
                    "RAW": 0.0,
                    "B30": -float(np.mean([x[1]-x[2] for x in h30])) if len(h30) >= MIN_TRAIN else 0.0,
                    "B60": -float(np.mean([x[1]-x[2] for x in h60])) if len(h60) >= MIN_TRAIN else 0.0,
                    "X30": exact_offset(history, day, 30, unit),
                    "X60": exact_offset(history, day, 60, unit),
                }
                sigma = (max(float(np.std([x[1]-x[2] for x in h60])),
                             1.0 if unit == "F" else .6) if len(h60) >= MIN_TRAIN
                         else (2.5 if unit == "F" else 1.5))
                if D0 <= day <= TEST1:
                    for correction, offset in corrections.items():
                        mu = raw + offset
                        details.append(dict(station=station, d=day, unit=unit,
                            candidate=f"{base}|{correction}", mu=mu,
                            hit=gamma_hit(mu, unit, win), top2=top2_hit(mu, sigma, unit, win),
                            ae=abs(mu-real)))
                base_histories.setdefault(base, []).append((day, raw, real, win))
            for model in MODELS:
                if model in row and pd.notna(row[model]):
                    value = float(row[model])
                    model_histories.setdefault(model, []).append(
                        (day, value-real, gamma_hit(value, unit, win)))
    return pd.DataFrame(details)


def select_dev(details):
    winners = {}
    for station, part in details[details.d <= DEV_END].groupby("station"):
        score = part.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
            top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
        score = score[score.n >= .9*score.n.max()].sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])
        winners[station] = score.iloc[0].candidate
    return winners


def paired(details, recipes):
    chosen = pd.concat([details[(details.station == station) & (details.candidate == recipe)]
                        for station, recipe in recipes.items()], ignore_index=True)
    chosen = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)][
        ["station", "d", "hit", "top2", "ae"]]
    base = details[(details.d >= TEST0) & (details.d <= TEST1) &
                   (details.candidate == BASELINE)][["station", "d", "hit", "top2", "ae"]]
    base = base.rename(columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    return chosen.merge(base, on=["station", "d"])


def report(name, frame):
    p, ci = bootstrap_day(frame)
    print(f"{name}: exact {frame.hit_base.mean():.1%} -> {frame.hit.mean():.1%} "
          f"({frame.hit.mean()-frame.hit_base.mean():+.1%}), top2 "
          f"{frame.top2_base.mean():.1%} -> {frame.top2.mean():.1%}, "
          f"MAE {frame.ae_base.mean():.3f} -> {frame.ae.mean():.3f}, "
          f"p={p:.5f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="reuse the ignored detail CSV")
    args = ap.parse_args()
    detail_path = os.path.join(D, "lab_cityx_oracle_truth_detail.csv")
    if args.reuse and os.path.exists(detail_path):
        details = pd.read_csv(detail_path)
        details["d"] = pd.to_datetime(details.d).dt.date
    else:
        details = build_details()
        details.to_csv(detail_path, index=False)
    selected = select_dev(details)
    changes = {station: (RECIPES[station], recipe) for station, recipe in selected.items()
               if RECIPES.get(station) != recipe}
    print(f"Oracle-corrected DEV selection; changed recipes {len(changes)}/{len(selected)}")
    for station, (old, new) in sorted(changes.items()):
        print(f"  {station}: {old} -> {new}")
    print(f"\nSensitivity on previously opened {TEST0}..{TEST1} holdout (not a new gate):")
    report("CITYX2 recipes / corrected truth", paired(details, RECIPES))
    report("DEV-selected / corrected truth", paired(details, selected))
    f_stations = set(pd.read_csv(os.path.join(D, "lab_metar_precision.csv")).station.unique())
    report("DEV-selected Fahrenheit only", paired(details, selected).query("station in @f_stations"))
    print("\nPromotion rule: none from this opened holdout; pre-register a new forward version.")


if __name__ == "__main__":
    main()
