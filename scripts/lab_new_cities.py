#!/usr/bin/env python3
"""Untouched holdout for the 17 cities absent from the original CITYX1 audit.

Method frozen before downloading their Gamma labels: same candidate family as
lab_single_runs.py; DEV 2026-05-10..06-20, TEST 06-21..07-11.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import (D, D0, DEV_END, TEST0, TEST1, MIN_TRAIN, MODELS,
    base_predictions, bootstrap_day, exact_offset, gamma_hit, recent, top2_hit)  # noqa: E402
from show_live import STATIONS  # noqa: E402
from wxbt.exact_selector import CITYX1_RECIPES  # noqa: E402

NEW_STATIONS = sorted(set(STATIONS)-set(CITYX1_RECIPES))
PORTFOLIO_DEV_MIN = .40


def build_details():
    sr = pd.read_csv(os.path.join(D, "single_runs.csv")); sr["d"] = pd.to_datetime(sr.target).dt.date
    sr = sr[sr.station.isin(NEW_STATIONS)]
    wide = sr.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                          aggfunc="last").reset_index()
    labels = pd.read_csv(os.path.join(D, "gamma_labels.csv"))
    labels["d"] = pd.to_datetime(labels.target).dt.date
    obs = pd.read_csv(os.path.join(D, "obs.csv")); obs["d"] = pd.to_datetime(obs.date).dt.date
    data = wide.merge(labels[["station", "d", "win_mkt"]], on=["station", "d"]).merge(
        obs[["station", "d", "tmax"]].rename(columns={"tmax": "max_real"}), on=["station", "d"])
    data = data.sort_values(["station", "d"])
    details = []
    for station, group in data.groupby("station"):
        model_histories, base_histories = {}, {}
        for _, row in group.iterrows():
            day, unit, real, win = row.d, row.unit, float(row.max_real), row.win_mkt
            bases = base_predictions(row, model_histories, day, unit)
            for base, raw in bases.items():
                history = base_histories.get(base, [])
                h30, h60 = recent(history, day, 30), recent(history, day, 60)
                corrections = {"RAW": 0.0,
                    "B30": -float(np.mean([x[1]-x[2] for x in h30])) if len(h30) >= MIN_TRAIN else 0.0,
                    "B60": -float(np.mean([x[1]-x[2] for x in h60])) if len(h60) >= MIN_TRAIN else 0.0,
                    "X30": exact_offset(history, day, 30, unit),
                    "X60": exact_offset(history, day, 60, unit)}
                sigma = (max(float(np.std([x[1]-x[2] for x in h60])),
                             1.0 if unit == "F" else .6) if len(h60) >= MIN_TRAIN
                         else (2.5 if unit == "F" else 1.5))
                if D0 <= day <= TEST1:
                    for correction, offset in corrections.items():
                        mu = raw+offset
                        details.append(dict(station=station, d=day, unit=unit,
                            candidate=f"{base}|{correction}", mu=mu,
                            hit=gamma_hit(mu, unit, win), top2=top2_hit(mu, sigma, unit, win),
                            ae=abs(mu-real)))
                base_histories.setdefault(base, []).append((day, raw, real, win))
            for model in MODELS:
                if model in row and pd.notna(row[model]):
                    value = float(row[model]); model_histories.setdefault(model, []).append(
                        (day, value-real, gamma_hit(value, unit, win)))
    return pd.DataFrame(details)


def main():
    det = build_details(); det.to_csv(os.path.join(D, "lab_new_cities_detail.csv"), index=False)
    dev = det[det.d <= DEV_END]
    baseline = "G3_MEAN|B60"; winners, dev_scores = {}, {}
    for station, part in dev.groupby("station"):
        score = part.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
            top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
        score = score[score.n >= .9*score.n.max()].sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])
        winners[station] = score.iloc[0].candidate; dev_scores[station] = float(score.iloc[0].exact)
    print(f"NUEVAS ciudades: DEV {D0}..{DEV_END}; HOLDOUT {TEST0}..{TEST1}")
    print("Recetas congeladas: " + " | ".join(f"{s}:{c}" for s, c in sorted(winners.items())))
    chosen = pd.concat([det[(det.station == st) & (det.candidate == candidate)]
                        for st, candidate in winners.items()], ignore_index=True)
    chosen = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)][
        ["station", "d", "hit", "top2", "ae"]]
    base = det[(det.d >= TEST0) & (det.d <= TEST1) & (det.candidate == baseline)][
        ["station", "d", "hit", "top2", "ae"]].rename(columns={
            "hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    paired = chosen.merge(base, on=["station", "d"])
    p, ci = bootstrap_day(paired)
    print(f"\nHOLDOUT primario n={len(paired)}, días={paired.d.nunique()}, ciudades={paired.station.nunique()}:")
    print(f" exacto baseline {paired.hit_base.mean():.1%} -> selector {paired.hit.mean():.1%} "
          f"(delta {paired.hit.mean()-paired.hit_base.mean():+.1%})")
    print(f" top2   {paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}")
    print(f" MAE    {paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}")
    print(f" bootstrap P(delta<=0)={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = paired.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        chosen=("hit", "mean"), top2=("top2", "mean"))
    print("\nPor ciudad:")
    print(by.to_string(formatters={"base": "{:.1%}".format, "chosen": "{:.1%}".format,
                                   "top2": "{:.1%}".format}))
    primary = paired.hit.mean() > paired.hit_base.mean() and p < .05 and \
              paired.top2.mean() >= paired.top2_base.mean()
    print("Gate primario: " + ("PASO" if primary else "NO PASO"))

    portfolio_stations = [s for s, score in dev_scores.items() if score >= PORTFOLIO_DEV_MIN]
    portfolio = paired[paired.station.isin(portfolio_stations)]
    daily = portfolio.groupby("d").hit.mean().to_numpy()
    rng = np.random.default_rng(20260713)
    boot = rng.choice(daily, size=(30000, len(daily)), replace=True).mean(axis=1)
    pp = float(np.mean(boot <= .396)); pci = np.quantile(boot, [.05, .95])
    print(f"\nPORTFOLIO calidad DEV>=40%: {portfolio_stations}")
    print(f" holdout exacto {portfolio.hit.mean():.1%}, top2 {portfolio.top2.mean():.1%}, n={len(portfolio)}")
    print(f" bootstrap P(exacto<=39.6%)={pp:.4f}, CI90 [{pci[0]:.1%},{pci[1]:.1%}]")
    print("Gate secundario p<0.025 y top2>=64.8%: " +
          ("PASO" if portfolio.hit.mean() > .396 and pp < .025 and
           portfolio.top2.mean() >= .648 else "NO PASO"))


if __name__ == "__main__":
    main()
