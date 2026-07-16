#!/usr/bin/env python3
"""Exploratory exact-first stack of archived US station MOS products.

The candidate family was fixed before inspecting its bucket-hit results:
five native products, robust MOS/NBM/LAMP stacks, and 50/50 CITYX blends;
each RAW or X60. Selection is global on DEV and offsets are frozen at TEST0.

The old holdout labels were already seen by prior CITYX/LAMP work, so this is
feature discovery, not a promotable untouched test. Any winner needs forward
confirmation under a new version.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_lamp import cityx_predictions, payout_truth  # noqa: E402
from lab_single_runs import (D, D0, DEV_END, TEST0, TEST1, bootstrap_day, exact_offset,
                             gamma_hit, top2_hit)  # noqa: E402

MODELS = ["GFS", "NAM", "MEX", "NBS", "NBE"]
BASES = MODELS + ["MOSMED", "NBM2", "STACK6", "MOSCITY50", "NBMCITY50", "STACKCITY50"]


def load_data():
    mos = pd.read_csv(os.path.join(D, "station_mos_daily.csv"))
    mos["d"] = pd.to_datetime(mos.target).dt.date
    wide = mos.pivot(index=["station", "d"], columns="model", values="tmax").reset_index()
    lamp = pd.read_csv(os.path.join(D, "lamp_daily.csv"))
    lamp["d"] = pd.to_datetime(lamp.target).dt.date
    lamp = lamp[["station", "d", "tmax"]].rename(columns={"tmax": "LAV"})
    return (wide.merge(lamp, on=["station", "d"])
            .merge(cityx_predictions(), on=["station", "d"])
            .merge(payout_truth(), on=["station", "d"])
            .sort_values(["station", "d"]))


def base_values(row):
    models = {model: float(getattr(row, model)) for model in MODELS}
    mosmed = float(np.median(list(models.values())))
    nbm2 = (models["NBS"] + models["NBE"]) / 2
    stack6 = float(np.median(list(models.values()) + [float(row.LAV)]))
    return {**models, "MOSMED": mosmed, "NBM2": nbm2, "STACK6": stack6,
            "MOSCITY50": (mosmed + row.mu_cityx) / 2,
            "NBMCITY50": (nbm2 + row.mu_cityx) / 2,
            "STACKCITY50": (stack6 + row.mu_cityx) / 2}


def build_details():
    rows = []
    for station, group in load_data().groupby("station"):
        histories = {base: [] for base in BASES}
        frozen = {}
        for r in group.itertuples(index=False):
            if D0 <= r.d <= TEST1:
                rows.append({"station": station, "d": r.d, "candidate": "CITYX2",
                    "mu": r.mu_cityx, "hit": gamma_hit(r.mu_cityx, "F", r.win_mkt),
                    "top2": r.top2_cityx, "ae": abs(r.mu_cityx-r.max_real)})
            for base, raw in base_values(r).items():
                history = histories[base]
                live = {"RAW": 0.0, "X60": exact_offset(history, r.d, 60, "F")}
                if r.d >= TEST0:
                    if base not in frozen:
                        frozen[base] = live.copy()
                    offsets = frozen[base]
                else:
                    offsets = live
                h60 = [x for x in history if x[0] < r.d and (r.d-x[0]).days <= 60]
                sigma = max(float(np.std([x[1]-x[2] for x in h60])), 1.0) if len(h60) >= 15 else 2.5
                if D0 <= r.d <= TEST1:
                    for correction, offset in offsets.items():
                        mu = raw + offset
                        rows.append({"station": station, "d": r.d,
                            "candidate": f"{base}|{correction}", "mu": mu,
                            "hit": gamma_hit(mu, "F", r.win_mkt),
                            "top2": top2_hit(mu, sigma, "F", r.win_mkt),
                            "ae": abs(mu-r.max_real)})
                history.append((r.d, raw, float(r.max_real), r.win_mkt))
    return pd.DataFrame(rows)


def ranking(frame):
    out = frame.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
    out = out[(out.candidate != "CITYX2") & (out.n >= .9*out.n.max())]
    return out.sort_values(["exact", "top2", "mae"], ascending=[False, False, True])


def paired(details, candidate):
    test = details[(details.d >= TEST0) & (details.d <= TEST1)]
    chosen = test[test.candidate == candidate][["station", "d", "hit", "top2", "ae"]]
    base = test[test.candidate == "CITYX2"][["station", "d", "hit", "top2", "ae"]].rename(
        columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    return chosen.merge(base, on=["station", "d"])


def report(frame):
    p, ci = bootstrap_day(frame.rename(columns={"d": "d"}))
    return (f"n={len(frame)}, exact {frame.hit_base.mean():.1%} -> {frame.hit.mean():.1%} "
            f"({frame.hit.mean()-frame.hit_base.mean():+.1%}), top2 "
            f"{frame.top2_base.mean():.1%} -> {frame.top2.mean():.1%}, "
            f"MAE {frame.ae_base.mean():.3f} -> {frame.ae.mean():.3f}, p={p:.5f}, "
            f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")


def main():
    details = build_details()
    details.to_csv(os.path.join(D, "lab_station_mos_detail.csv"), index=False)
    dev = details[details.d <= DEV_END]
    rank = ranking(dev)
    winner = rank.iloc[0].candidate
    print("Station MOS exploratorio: labels del holdout ya conocidos; promoción prohibida.")
    print(f"DEV {D0}..{DEV_END}; TEST descriptivo {TEST0}..{TEST1}")
    print("\nDEV ranking:")
    print(rank.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"\nGanador DEV global: {winner}")
    test = paired(details, winner)
    print("TEST vs CITYX2: " + report(test))
    print("\nPor ciudad:")
    by = test.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        mos=("hit", "mean"), top2=("top2", "mean"), mae=("ae", "mean"))
    print(by.to_string(formatters={"base": "{:.1%}".format, "mos": "{:.1%}".format,
                                   "top2": "{:.1%}".format, "mae": "{:.3f}".format}))


if __name__ == "__main__":
    main()
