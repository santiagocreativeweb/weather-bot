#!/usr/bin/env python3
"""Combine the two independently frozen CITYX holdouts across all 29 cities."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import D, TEST0, TEST1, bootstrap_day  # noqa: E402
from wxbt.exact_selector import CITYX1_RECIPES, CITYX2_NEW_RECIPES  # noqa: E402

BASELINE = "G3_MEAN|B60"


def paired_from(path, recipes):
    det = pd.read_csv(path); det["d"] = pd.to_datetime(det.d).dt.date
    chosen = pd.concat([det[(det.station == station) & (det.candidate == recipe)]
                        for station, recipe in recipes.items()], ignore_index=True)
    chosen = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)][
        ["station", "d", "hit", "top2", "ae"]]
    base = det[(det.d >= TEST0) & (det.d <= TEST1) & (det.candidate == BASELINE)][
        ["station", "d", "hit", "top2", "ae"]].rename(columns={
            "hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    return chosen.merge(base, on=["station", "d"])


def main():
    old = paired_from(os.path.join(D, "lab_single_runs_detail.csv"), CITYX1_RECIPES).assign(cohort="CITYX1")
    new = paired_from(os.path.join(D, "lab_new_cities_detail.csv"), CITYX2_NEW_RECIPES).assign(cohort="CITYX2_NEW")
    paired = pd.concat([old, new], ignore_index=True)
    p, ci = bootstrap_day(paired)
    print(f"CITYX2 COMBINADO {TEST0}..{TEST1}: n={len(paired)}, días={paired.d.nunique()}, "
          f"ciudades={paired.station.nunique()}")
    print(f" exacto baseline {paired.hit_base.mean():.1%} -> CITYX2 {paired.hit.mean():.1%} "
          f"(delta {paired.hit.mean()-paired.hit_base.mean():+.1%})")
    print(f" top2   {paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}")
    print(f" MAE    {paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}")
    print(f" bootstrap P(delta<=0)={p:.5f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = paired.groupby("cohort").agg(n=("hit", "size"), cities=("station", "nunique"),
        base=("hit_base", "mean"), cityx2=("hit", "mean"), top2=("top2", "mean"))
    print("\nPor cohorte independiente:")
    print(by.to_string(formatters={"base": "{:.1%}".format, "cityx2": "{:.1%}".format,
                                   "top2": "{:.1%}".format}))


if __name__ == "__main__":
    main()
