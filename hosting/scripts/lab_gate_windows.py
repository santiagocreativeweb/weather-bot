#!/usr/bin/env python3
"""Audit MED8 vs V2 against the official Gamma winner over fixed recent windows.

This is a retrospective *relative* test.  It does not promote MED8 because the
historical model inputs come from Previous Runs and inherit bug #5 freshness.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from score_forward_history import overlaps, parse_win, pick_bucket  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
WINDOWS = (90, 60, 30, 15, 7)


def gamma_hit(mu, unit, winner_label):
    winner = parse_win(winner_label)
    if winner is None or pd.isna(mu):
        return np.nan
    return int(overlaps(pick_bucket(int(np.floor(float(mu))), unit), winner))


def day_bootstrap_p(j, reps=20000):
    daily = j.assign(delta_row=j.hit_med8 - j.hit_v2).groupby("d")["delta_row"].mean().to_numpy()
    if not len(daily):
        return np.nan
    rng = np.random.default_rng(20260713)
    boot = rng.choice(daily, size=(reps, len(daily)), replace=True).mean(axis=1)
    return float(np.mean(boot <= 0))


def main():
    det = pd.read_csv(os.path.join(D, "lab_city_models_detail.csv"))
    det["d"] = pd.to_datetime(det.d).dt.date
    pred = det[det.variant.isin(["V2", "MED8"])].pivot_table(
        index=["st", "d"], columns="variant", values="mu", aggfunc="last").reset_index()

    bf = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    bf = bf[(bf.lead == 2) & bf.win_mkt.notna()].copy()
    bf["d"] = pd.to_datetime(bf.target).dt.date
    bf = bf.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    j = pred.merge(bf[["station", "d", "unit", "win_mkt"]],
                   left_on=["st", "d"], right_on=["station", "d"])
    j = j.dropna(subset=["V2", "MED8", "win_mkt"])
    j["hit_v2"] = [gamma_hit(mu, u, w) for mu, u, w in zip(j.V2, j.unit, j.win_mkt)]
    j["hit_med8"] = [gamma_hit(mu, u, w) for mu, u, w in zip(j.MED8, j.unit, j.win_mkt)]
    j = j.dropna(subset=["hit_v2", "hit_med8"])
    if j.empty:
        raise SystemExit("Sin filas pareadas V2/MED8 con ganador Gamma")
    end = max(j.d)
    print(f"GATE RETROSPECTIVO GAMMA — fin={end}, pares={len(j)}, estaciones={j.st.nunique()}")
    print(" ventana  dias  mercados       V2     MED8    delta   p_boot<=0  discord MED8/V2")
    for days in WINDOWS:
        start = end - dt.timedelta(days=days - 1)
        x = j[(j.d >= start) & (j.d <= end)]
        n_days = x.d.nunique()
        delta = x.hit_med8.mean() - x.hit_v2.mean()
        p = day_bootstrap_p(x)
        med_only = int(((x.hit_med8 == 1) & (x.hit_v2 == 0)).sum())
        v2_only = int(((x.hit_med8 == 0) & (x.hit_v2 == 1)).sum())
        print(f" {days:>3}d    {n_days:>3}      {len(x):>4}     {x.hit_v2.mean():>6.1%}  "
              f"{x.hit_med8.mean():>6.1%}  {delta:>+6.1%}     {p:>7.4f}       {med_only:>3}/{v2_only:<3}")
    print("\nLectura válida: comparación relativa MED8-vs-V2 contra payout oficial Gamma.")
    print("Lectura NO válida: nivel absoluto operable; Previous Runs conserva bug #5 de frescura.")


if __name__ == "__main__":
    main()
