#!/usr/bin/env python3
"""Capture frozen CITYX forecasts from coherent eight-model snapshots before freeze."""
import csv
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import freeze_utc  # noqa: E402
from lab_single_runs import MODELS, base_predictions, exact_offset, recent  # noqa: E402
from wxbt.exact_selector import RECIPES, VERSION  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "exact_selector_forward.csv")


def station_truth(station, backfill, labels=None, obs=None):
    """Build resolved training truth, falling back to Gamma+IEM for CITYX2 cities."""
    # VERSION CITYX2-20260713 was frozen with this exact historical source chain.
    # Do not inject the later METAR-hourly correction mid-gate: that would change
    # B/MSE histories and make forward snapshots incomparable. A future selector
    # version may use wxbt.observations, but must start a new forward gate.
    truth = backfill[(backfill.station == station) & (backfill.lead == 2) &
                     backfill.max_real.notna() & backfill.win_mkt.notna()][
                         ["station", "d", "max_real", "win_mkt"]].copy()
    if truth.empty and labels is not None and obs is not None:
        truth = labels[labels.station == station][["station", "d", "win_mkt"]].merge(
            obs[obs.station == station][["station", "d", "tmax"]], on=["station", "d"])
        truth = truth.rename(columns={"tmax": "max_real"})
    return truth.sort_values("d").drop_duplicates(["station", "d"], keep="last")


def histories_before(station, target):
    sr = pd.read_csv(os.path.join(D, "single_runs.csv"))
    sr["d"] = pd.to_datetime(sr.target).dt.date
    wide = sr[sr.station == station].pivot_table(
        index=["station", "d", "unit"], columns="model", values="tmax",
        aggfunc="last").reset_index()
    backfill = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    backfill["d"] = pd.to_datetime(backfill.target).dt.date
    labels = obs = None
    if os.path.exists(os.path.join(D, "gamma_labels.csv")):
        labels = pd.read_csv(os.path.join(D, "gamma_labels.csv"))
        labels["d"] = pd.to_datetime(labels.target).dt.date
        obs = pd.read_csv(os.path.join(D, "obs.csv"))
        obs["d"] = pd.to_datetime(obs.date).dt.date
    truth = station_truth(station, backfill, labels, obs)
    data = wide.merge(truth[["station", "d", "max_real", "win_mkt"]],
                      on=["station", "d"]).sort_values("d")
    mh, bh = {}, {}
    for _, row in data[data.d < target].iterrows():
        real = float(row.max_real)
        bases = base_predictions(row, mh, row.d, row.unit)
        for name, raw in bases.items():
            bh.setdefault(name, []).append((row.d, raw, real, row.win_mkt))
        from lab_single_runs import gamma_hit
        for model in MODELS:
            if model in row and pd.notna(row[model]):
                value = float(row[model])
                mh.setdefault(model, []).append(
                    (row.d, value-real, gamma_hit(value, row.unit, row.win_mkt)))
    return mh, bh


def city_mu(station, target, unit, values):
    chosen = RECIPES[station]
    base, correction = chosen.split("|")
    mh, bh = histories_before(station, target)
    row = pd.Series({**values, "unit": unit})
    bases = base_predictions(row, mh, target, unit)
    if base not in bases:
        return None
    raw, history = bases[base], bh.get(base, [])
    if correction == "RAW":
        offset = 0.0
    elif correction.startswith("B"):
        days = int(correction[1:]); h = recent(history, target, days)
        offset = -float(np.mean([x[1]-x[2] for x in h])) if len(h) >= 15 else 0.0
    else:
        offset = exact_offset(history, target, int(correction[1:]), unit)
    return raw + offset


def main():
    path = os.path.join(D, "models_forward.csv")
    if not os.path.exists(path):
        print(f"{VERSION}: models_forward.csv no existe"); return
    mf = pd.read_csv(path, parse_dates=["capture_utc"])
    mf["target"] = pd.to_datetime(mf.target).dt.date
    mf = mf[mf.station.isin(RECIPES) & mf.model.isin(MODELS)]
    eligible = []
    for r in mf.itertuples(index=False):
        cutoff = freeze_utc(r.station, r.target).replace(tzinfo=dt.timezone.utc)
        if r.capture_utc.to_pydatetime() <= cutoff:
            eligible.append(r)
    if not eligible:
        print(f"{VERSION}: sin snapshots anteriores al freeze"); return
    e = pd.DataFrame(eligible, columns=mf.columns)
    counts = e.groupby(["station", "target", "capture_utc"]).model.nunique().reset_index(name="n")
    complete = counts[counts.n >= 3].sort_values("capture_utc").drop_duplicates(
        ["station", "target"], keep="last")
    e = e.merge(complete[["station", "target", "capture_utc"]],
                on=["station", "target", "capture_utc"])
    done = set()
    if os.path.exists(OUT):
        old = pd.read_csv(OUT)
        done = set(zip(old.station, old.target.astype(str), old.capture_utc.astype(str), old.version))
    rows = []
    for (station, target, capture), group in e.groupby(["station", "target", "capture_utc"]):
        key = (station, target.isoformat(), capture.isoformat(), VERSION)
        if key in done:
            continue
        values = dict(zip(group.model, group.tmax.astype(float)))
        mu = city_mu(station, target, group.unit.iloc[0], values)
        if mu is not None:
            rows.append([capture.isoformat(), station, target.isoformat(), VERSION,
                         RECIPES[station], group.unit.iloc[0], round(mu, 4),
                         freeze_utc(station, target).isoformat()])
    if rows:
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["capture_utc", "station", "target", "version", "recipe",
                            "unit", "mu", "freeze_utc"])
            w.writerows(rows)
    print(f"{VERSION}: +{len(rows)} snapshots -> {OUT}")


if __name__ == "__main__":
    main()
