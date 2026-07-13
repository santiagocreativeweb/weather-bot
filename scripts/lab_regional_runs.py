#!/usr/bin/env python3
"""Nested audit of high-resolution regional runs versus the global city selector.

All recipe and city selection uses 2026-05-10..2026-06-20 only.  The final
comparison is made once on 2026-06-21..2026-07-11.
"""
import datetime as dt
import os

import numpy as np
import pandas as pd

from lab_single_runs import (D, D0, DEV_END, TEST0, TEST1, MIN_TRAIN,
                             bootstrap_day, exact_offset, gamma_hit, recent,
                             top2_hit)


def main():
    global_det = pd.read_csv(os.path.join(D, "lab_single_runs_detail.csv"))
    global_det["d"] = pd.to_datetime(global_det.d).dt.date
    gdev = global_det[global_det.d <= DEV_END]
    global_winner = {}
    for station, part in gdev.groupby("station"):
        score = part.groupby("candidate").agg(
            n=("hit", "size"), exact=("hit", "mean"), top2=("top2", "mean"),
            mae=("ae", "mean")).reset_index()
        score = score[score.n >= .9 * score.n.max()].sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])
        global_winner[station] = score.iloc[0].candidate
    selected_global = pd.concat([
        global_det[(global_det.station == station) &
                   (global_det.candidate == candidate)].assign(candidate="GLOBAL_CITY")
        for station, candidate in global_winner.items()
    ], ignore_index=True)

    rr = pd.read_csv(os.path.join(D, "regional_runs.csv"))
    rr["d"] = pd.to_datetime(rr.target).dt.date
    wide = rr.pivot_table(index=["station", "d", "unit"], columns="model",
                          values="tmax", aggfunc="last").reset_index()
    truth = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    truth = truth[(truth.lead == 2) & truth.win_mkt.notna() & truth.max_real.notna()].copy()
    truth["d"] = pd.to_datetime(truth.target).dt.date
    truth = truth.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    data = wide.merge(truth[["station", "d", "max_real", "win_mkt"]],
                      on=["station", "d"]).sort_values(["station", "d"])
    global_mu = selected_global.set_index(["station", "d"])["mu"].to_dict()

    details = []
    fixed = {"station", "d", "unit", "max_real", "win_mkt"}
    for station, group in data.groupby("station"):
        histories = {}
        for _, row in group.iterrows():
            day, unit, real, win = row.d, row.unit, float(row.max_real), row.win_mkt
            values = {m: float(row[m]) for m in row.index if m not in fixed and pd.notna(row[m])}
            if not values:
                continue
            bases = {f"R_{m}": v for m, v in values.items()}
            vals = list(values.values())
            bases["R_MEAN"] = float(np.mean(vals)); bases["R_MED"] = float(np.median(vals))
            gm = global_mu.get((station, day))
            if gm is not None:
                bases["MIX_ALL"] = float(np.median(vals + [gm]))
                for model, value in values.items():
                    bases[f"MIX_{model}"] = (value + gm) / 2
            for base, raw in bases.items():
                history = histories.get(base, [])
                h30, h60 = recent(history, day, 30), recent(history, day, 60)
                corrections = {
                    "RAW": 0.0,
                    "B30": -float(np.mean([x[1] - x[2] for x in h30])) if len(h30) >= MIN_TRAIN else 0.0,
                    "B60": -float(np.mean([x[1] - x[2] for x in h60])) if len(h60) >= MIN_TRAIN else 0.0,
                    "X30": exact_offset(history, day, 30, unit),
                    "X60": exact_offset(history, day, 60, unit),
                }
                sigma = (max(float(np.std([x[1] - x[2] for x in h60])),
                             1.0 if unit == "F" else .6) if len(h60) >= MIN_TRAIN
                         else (2.5 if unit == "F" else 1.5))
                if D0 <= day <= TEST1:
                    for correction, offset in corrections.items():
                        mu = raw + offset
                        details.append(dict(station=station, d=day,
                                            candidate=f"{base}|{correction}", mu=mu,
                                            hit=gamma_hit(mu, unit, win),
                                            top2=top2_hit(mu, sigma, unit, win),
                                            ae=abs(mu-real)))
                histories.setdefault(base, []).append((day, raw, real, win))
    det = pd.concat([pd.DataFrame(details), selected_global[
        ["station", "d", "candidate", "mu", "hit", "top2", "ae"]]], ignore_index=True)
    det.to_csv(os.path.join(D, "lab_regional_detail.csv"), index=False)

    winners = {}
    for station, part in det[det.d <= DEV_END].groupby("station"):
        s = part.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
            top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
        s = s[s.n >= .8 * s.n.max()].sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])
        winners[station] = s.iloc[0].candidate
    chosen = pd.concat([det[(det.station == st) & (det.candidate == candidate)]
                        for st, candidate in winners.items()], ignore_index=True)
    test_chosen = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)]
    base = selected_global[(selected_global.d >= TEST0) & (selected_global.d <= TEST1)][
        ["station", "d", "hit", "top2", "ae"]].rename(columns={
            "hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    paired = test_chosen.merge(base, on=["station", "d"])
    print("REGIONALES honestos contra selector global por ciudad")
    print("Ganadores DEV: " + " | ".join(f"{s}:{c}" for s, c in sorted(winners.items())))
    if paired.empty:
        print("Sin pares de holdout suficientes."); return
    p, ci = bootstrap_day(paired)
    print(f"HOLDOUT n={len(paired)}, dias={paired.d.nunique()}")
    print(f" exacto {paired.hit_base.mean():.1%} -> {paired.hit.mean():.1%} "
          f"(delta {paired.hit.mean()-paired.hit_base.mean():+.1%})")
    print(f" top2   {paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}")
    print(f" MAE    {paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}")
    print(f" bootstrap P(delta<=0)={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    print("Gate exploratorio regional: p<0.0167, delta>0 y top2 no degradado.")


if __name__ == "__main__":
    main()
