#!/usr/bin/env python3
"""Exploratory pre-freeze ASOS innovation correction for frozen LAMPX."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_lamp import build_details, payout_truth  # noqa: E402
from lab_single_runs import D, DEV_END, TEST0, TEST1, bootstrap_day, gamma_hit, top2_hit  # noqa: E402

ALPHAS = (0.25, 0.50, 0.75, 1.00)
CLIP_F = 4.0
BASE = "BLEND50|X60"


def corrected_mu(mu, innovation, alpha):
    return float(mu) + float(alpha)*float(np.clip(innovation, -CLIP_F, CLIP_F))


def build():
    lamp_path = os.path.join(D, "lamp_daily_lag2.csv")
    base = build_details(frozen_test=True, lamp_path=lamp_path)
    base = base[base.candidate == BASE][["station", "d", "mu", "ae"]]
    # Fixed uncertainty from pre-test absolute errors; never updated in holdout.
    sigma = base[base.d < TEST0].groupby("station").ae.apply(
        lambda x: max(float(np.sqrt(np.mean(np.square(x)))), 1.0)).to_dict()
    now = pd.read_csv(os.path.join(D, "lamp_nowcast.csv"))
    now["d"] = pd.to_datetime(now.target).dt.date
    truth = payout_truth()
    data = base.merge(now[["station", "d", "innovation"]], on=["station", "d"]).merge(
        truth[["station", "d", "max_real", "win_mkt"]], on=["station", "d"])
    rows = []
    for r in data.itertuples(index=False):
        for alpha in (0.0,)+ALPHAS:
            mu = corrected_mu(r.mu, r.innovation, alpha)
            rows.append({"station": r.station, "d": r.d,
                "candidate": "LAMPX" if alpha == 0 else f"INNOV{int(alpha*100):03d}",
                "alpha": alpha, "mu": mu, "innovation": r.innovation,
                "hit": gamma_hit(mu, "F", r.win_mkt),
                "top2": top2_hit(mu, sigma[r.station], "F", r.win_mkt),
                "ae": abs(mu-r.max_real), "signed_target": r.max_real-r.mu})
    return pd.DataFrame(rows)


def main():
    details = build()
    details.to_csv(os.path.join(D, "lab_lamp_nowcast_detail.csv"), index=False)
    dev = details[details.d <= DEV_END]
    rank = dev[dev.candidate != "LAMPX"].groupby("candidate").agg(
        n=("hit", "size"), exact=("hit", "mean"), top2=("top2", "mean"),
        mae=("ae", "mean")).reset_index().sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])
    winner = rank.iloc[0].candidate
    test = details[(details.d >= TEST0) & (details.d <= TEST1)]
    chosen = test[test.candidate == winner][["station", "d", "hit", "top2", "ae"]]
    base = test[test.candidate == "LAMPX"][["station", "d", "hit", "top2", "ae"]].rename(
        columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    paired = chosen.merge(base, on=["station", "d"])
    p, ci = bootstrap_day(paired)
    dev_base = dev[dev.candidate == "LAMPX"]
    corr = dev_base[["innovation", "signed_target"]].corr().iloc[0, 1]
    print("LAMP nowcast exploratorio: ASOS+15min <= freeze; promoción prohibida.")
    print(f"Correlación DEV innovation vs corrección física requerida: {corr:+.3f}")
    print("\nDEV ranking:")
    print(rank.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"\nGanador DEV: {winner}")
    print(f"TEST n={len(paired)}: exacto {paired.hit_base.mean():.1%} LAMPX -> "
          f"{paired.hit.mean():.1%} nowcast ({paired.hit.mean()-paired.hit_base.mean():+.1%}); "
          f"top2 {paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}; "
          f"MAE {paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}; p={p:.5f}, "
          f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = paired.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        nowcast=("hit", "mean"), top2=("top2", "mean"), mae=("ae", "mean"))
    print("\nPor ciudad:")
    print(by.to_string(formatters={"base": "{:.1%}".format,
        "nowcast": "{:.1%}".format, "top2": "{:.1%}".format, "mae": "{:.3f}".format}))


if __name__ == "__main__":
    main()
