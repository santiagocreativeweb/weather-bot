#!/usr/bin/env python3
"""Init-honest NOAA LAMP station-MOS experiment for nine Fahrenheit markets.

Candidate family fixed before opening LAMP holdout results:
LAV alone or a 50/50 LAV-CITYX blend, each with RAW, B60, or X60 correction.
Selection uses DEV through 2026-06-20; global and per-city policies are the two
pre-registered hypotheses (Bonferroni gate p<0.025). Gamma is payout truth and
hourly ASOS is physical truth for bias/MAE.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import (D, D0, DEV_END, TEST0, TEST1, MIN_TRAIN,
    bootstrap_day, exact_offset, gamma_hit, recent, top2_hit)  # noqa: E402
from wxbt.exact_selector import CITYX1_RECIPES, CITYX2_NEW_RECIPES  # noqa: E402


STATIONS = ["KLGA", "KORD", "KMIA", "KSFO", "KLAX", "KDAL", "KATL", "KHOU", "KAUS"]
CORRECTIONS = ("RAW", "B60", "X60")
BASES = ("LAV", "BLEND50")


def cityx_predictions():
    old = pd.read_csv(os.path.join(D, "lab_single_runs_detail.csv"))
    new = pd.read_csv(os.path.join(D, "lab_new_cities_detail.csv"))
    for frame in (old, new):
        frame["d"] = pd.to_datetime(frame.d).dt.date
    recipes = {**CITYX1_RECIPES, **CITYX2_NEW_RECIPES}
    detail = pd.concat([old, new], ignore_index=True)
    return pd.concat([detail[(detail.station == station) &
                             (detail.candidate == recipes[station])]
                      for station in STATIONS], ignore_index=True)[
                          ["station", "d", "mu", "hit", "top2", "ae"]].rename(
                              columns={"mu": "mu_cityx", "hit": "hit_cityx",
                                       "top2": "top2_cityx", "ae": "ae_cityx"})


def payout_truth():
    old = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    old["d"] = pd.to_datetime(old.target).dt.date
    old = old[(old.lead == 2) & old.station.isin(["KLGA", "KORD"]) & old.win_mkt.notna()][
        ["station", "d", "win_mkt"]]
    new = pd.read_csv(os.path.join(D, "gamma_labels.csv"))
    new["d"] = pd.to_datetime(new.target).dt.date
    new = new[new.station.isin(set(STATIONS)-{"KLGA", "KORD"})][["station", "d", "win_mkt"]]
    labels = pd.concat([old, new], ignore_index=True).drop_duplicates(["station", "d"])
    truth = pd.read_csv(os.path.join(D, "lab_metar_precision.csv"))
    truth = truth[truth.candidate == "raw_tmpf"].copy()
    truth["d"] = pd.to_datetime(truth.target).dt.date
    truth = truth[["station", "d", "value"]].rename(columns={"value": "max_real"})
    return labels.merge(truth, on=["station", "d"])


def build_details(frozen_test=False, lamp_path=None):
    lamp = pd.read_csv(lamp_path or os.path.join(D, "lamp_daily.csv"))
    lamp["d"] = pd.to_datetime(lamp.target).dt.date
    data = lamp[["station", "d", "tmax"]].merge(cityx_predictions(), on=["station", "d"]).merge(
        payout_truth(), on=["station", "d"]).sort_values(["station", "d"])
    rows = []
    for station, group in data.groupby("station"):
        histories = {base: [] for base in BASES}
        frozen_offsets = {}
        for r in group.itertuples(index=False):
            base_values = {"LAV": float(r.tmax), "BLEND50": (float(r.tmax)+r.mu_cityx)/2}
            if D0 <= r.d <= TEST1:
                rows.append(dict(station=station, d=r.d, candidate="CITYX2", mu=r.mu_cityx,
                                 hit=gamma_hit(r.mu_cityx, "F", r.win_mkt),
                                 top2=r.top2_cityx, ae=abs(r.mu_cityx-r.max_real)))
            for base, raw in base_values.items():
                history = histories[base]
                h60 = recent(history, r.d, 60)
                live_offsets = {
                    "RAW": 0.0,
                    "B60": -float(np.mean([x[1]-x[2] for x in h60])) if len(h60) >= MIN_TRAIN else 0.0,
                    "X60": exact_offset(history, r.d, 60, "F"),
                }
                if frozen_test and r.d >= TEST0:
                    if base not in frozen_offsets:
                        frozen_offsets[base] = live_offsets
                    offsets = frozen_offsets[base]
                else:
                    offsets = live_offsets
                sigma = max(float(np.std([x[1]-x[2] for x in h60])), 1.0) \
                    if len(h60) >= MIN_TRAIN else 2.5
                if D0 <= r.d <= TEST1:
                    for correction, offset in offsets.items():
                        mu = raw+offset
                        rows.append(dict(station=station, d=r.d,
                            candidate=f"{base}|{correction}", mu=mu,
                            hit=gamma_hit(mu, "F", r.win_mkt),
                            top2=top2_hit(mu, sigma, "F", r.win_mkt),
                            ae=abs(mu-r.max_real)))
                history.append((r.d, raw, float(r.max_real), r.win_mkt))
    return pd.DataFrame(rows)


def rank(part):
    score = part.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
    score = score[(score.candidate != "CITYX2") & (score.n >= .9*score.n.max())]
    return score.sort_values(["exact", "top2", "mae"], ascending=[False, False, True])


def paired(details, policy):
    if isinstance(policy, str):
        chosen = details[details.candidate == policy]
    else:
        chosen = pd.concat([details[(details.station == station) &
                                    (details.candidate == candidate)]
                            for station, candidate in policy.items()], ignore_index=True)
    chosen = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)][
        ["station", "d", "hit", "top2", "ae"]]
    baseline = details[(details.candidate == "CITYX2") & (details.d >= TEST0) &
                       (details.d <= TEST1)][["station", "d", "hit", "top2", "ae"]].rename(
        columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    return chosen.merge(baseline, on=["station", "d"])


def report(name, frame):
    p, ci = bootstrap_day(frame)
    print(f"{name}: n={len(frame)}, exact {frame.hit_base.mean():.1%} -> {frame.hit.mean():.1%} "
          f"({frame.hit.mean()-frame.hit_base.mean():+.1%}), top2 "
          f"{frame.top2_base.mean():.1%} -> {frame.top2.mean():.1%}, "
          f"MAE {frame.ae_base.mean():.3f} -> {frame.ae.mean():.3f}, "
          f"p={p:.5f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen-test", action="store_true",
                    help="freeze B/X offsets at the first holdout target")
    ap.add_argument("--lamp", help="alternative point-in-time LAMP daily CSV")
    args = ap.parse_args()
    details = build_details(args.frozen_test, args.lamp)
    suffix = "_frozen" if args.frozen_test else ""
    details.to_csv(os.path.join(D, f"lab_lamp_detail{suffix}.csv"), index=False)
    dev = details[details.d <= DEV_END]
    global_rank = rank(dev)
    global_winner = global_rank.iloc[0].candidate
    city_winners = {station: rank(part).iloc[0].candidate
                    for station, part in dev.groupby("station")}
    mode = "offsets frozen at holdout start" if args.frozen_test else "rolling prior-day offsets"
    print(f"LAMP exact-first ({mode}): DEV {D0}..{DEV_END}; HOLDOUT {TEST0}..{TEST1}")
    print("\nGlobal DEV ranking:")
    print(global_rank.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"Global winner: {global_winner}")
    print("City winners: " + " | ".join(f"{s}:{c}" for s, c in sorted(city_winners.items())))
    print("\nUntouched LAMP holdout:")
    pg = report("GLOBAL", paired(details, global_winner))
    pc = report("CITY", paired(details, city_winners))
    city = paired(details, city_winners)
    global_test = paired(details, global_winner)
    passed_global = (global_test.hit.mean() > global_test.hit_base.mean() and
                     global_test.top2.mean() >= global_test.top2_base.mean() and pg < .025)
    passed_city = (city.hit.mean() > city.hit_base.mean() and
                   city.top2.mean() >= city.top2_base.mean() and pc < .025)
    print("Gate global: delta>0, top2 no baja, p<0.025 -> " +
          ("PASO" if passed_global else "NO PASO"))
    print("Gate city:   delta>0, top2 no baja, p<0.025 -> " +
          ("PASO" if passed_city else "NO PASO"))
    print("\nPor ciudad (policy city):")
    by = city.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        lamp=("hit", "mean"), top2=("top2", "mean"), mae=("ae", "mean"))
    print(by.to_string(formatters={"base": "{:.1%}".format, "lamp": "{:.1%}".format,
                                   "top2": "{:.1%}".format, "mae": "{:.3f}".format}))


if __name__ == "__main__":
    main()
