#!/usr/bin/env python3
"""Expanding walk-forward classifier that targets the paid Gamma bucket directly.

Unlike MOS regressors, the loss here is the discrete correction to CITYX1's
bucket. Each outer day is predicted using targets strictly earlier than it.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import bootstrap_day, gamma_hit  # noqa: E402
from wxbt.exact_selector import RECIPES  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
START = dt.date(2026, 6, 10)
END = dt.date(2026, 7, 11)
MIN_TRAIN_DAYS = 25
MODELS = ["gfs13", "ecmwf", "aifs", "icon", "arpege", "ukmo", "jma", "cma"]


def correction_label(mu, unit, winner):
    step = 2.0 if unit == "F" else 1.0
    hits = [k for k in range(-4, 5) if gamma_hit(mu+k*step, unit, winner) == 1]
    return min(hits, key=abs) if hits else np.nan


def load_data():
    sr = pd.read_csv(os.path.join(D, "single_runs.csv"))
    sr["d"] = pd.to_datetime(sr.target).dt.date
    wide = sr.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                          aggfunc="last").reset_index()
    detail = pd.read_csv(os.path.join(D, "lab_single_runs_detail.csv"))
    detail["d"] = pd.to_datetime(detail.d).dt.date
    chosen = pd.concat([detail[(detail.station == st) & (detail.candidate == recipe)]
                        for st, recipe in RECIPES.items()], ignore_index=True)
    chosen = chosen[["station", "d", "mu", "hit", "top2"]].rename(
        columns={"mu": "mu_base", "hit": "hit_base", "top2": "top2_base"})
    truth = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    truth["d"] = pd.to_datetime(truth.target).dt.date
    truth = truth[(truth.lead == 2) & truth.win_mkt.notna()]
    truth = truth.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    data = wide.merge(chosen, on=["station", "d"]).merge(
        truth[["station", "d", "win_mkt"]], on=["station", "d"])
    data["y"] = [correction_label(mu, unit, win) for mu, unit, win in
                 zip(data.mu_base, data.unit, data.win_mkt)]
    return data[data.y.notna()].sort_values(["d", "station"]).reset_index(drop=True)


def features(data, columns=None):
    x = pd.DataFrame(index=data.index)
    scale = np.where(data.unit == "F", 2.0, 1.0)
    model_values = []
    for model in MODELS:
        if model in data:
            x[f"d_{model}"] = (data[model]-data.mu_base)/scale
            model_values.append(model)
    matrix = data[model_values].to_numpy(float)
    x["model_spread"] = np.nanstd(matrix/scale[:, None], axis=1)
    x["model_mean_delta"] = (np.nanmean(matrix, axis=1)-data.mu_base)/scale
    x["model_median_delta"] = (np.nanmedian(matrix, axis=1)-data.mu_base)/scale
    x["base_fraction"] = data.mu_base-np.floor(data.mu_base)
    x["n_models"] = np.isfinite(matrix).sum(axis=1)
    doy = np.array([d.timetuple().tm_yday for d in data.d])
    x["doy_sin"] = np.sin(2*np.pi*doy/365.25); x["doy_cos"] = np.cos(2*np.pi*doy/365.25)
    x = pd.concat([x, pd.get_dummies(data.station, prefix="station", dtype=float)], axis=1)
    return x if columns is None else x.reindex(columns=columns, fill_value=0.0)


def main():
    data = load_data(); all_x = features(data)
    rows = []
    for day in sorted(d for d in data.d.unique() if START <= d <= END):
        train = data[data.d < day]
        test = data[data.d == day]
        if train.d.nunique() < MIN_TRAIN_DAYS or test.empty:
            continue
        xtr = all_x.loc[train.index]; xte = all_x.loc[test.index]
        model = make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(
            C=1.0, max_iter=2000, random_state=20260713))
        model.fit(xtr, train.y.astype(int))
        prob = model.predict_proba(xte); classes = model.classes_.astype(int)
        order = np.argsort(-prob, axis=1)
        for i, (_, r) in enumerate(test.iterrows()):
            first, second = classes[order[i, 0]], classes[order[i, 1]]
            step = 2.0 if r.unit == "F" else 1.0
            hit = gamma_hit(r.mu_base+first*step, r.unit, r.win_mkt)
            hit2 = max(hit, gamma_hit(r.mu_base+second*step, r.unit, r.win_mkt))
            rows.append(dict(station=r.station, d=day, y=int(r.y), correction=int(first),
                hit_base=int(r.hit_base), top2_base=int(r.top2_base), hit=int(hit), top2=int(hit2)))
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(D, "lab_direct_bucket_detail.csv"), index=False)
    if out.empty:
        raise SystemExit("Sin predicciones walk-forward")
    p, ci = bootstrap_day(out)
    print(f"CLASIFICADOR DIRECTO walk-forward {START}..{END}: n={len(out)}, "
          f"dias={out.d.nunique()}, estaciones={out.station.nunique()}")
    print(f" exacto CITYX1 {out.hit_base.mean():.1%} -> directo {out.hit.mean():.1%} "
          f"(delta {out.hit.mean()-out.hit_base.mean():+.1%})")
    print(f" top2   {out.top2_base.mean():.1%} -> {out.top2.mean():.1%}")
    print(f" bootstrap P(delta<=0)={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    print(" distribución corrección elegida: " +
          ", ".join(f"{int(k):+d}:{v:.0%}" for k, v in out.correction.value_counts(
              normalize=True).sort_index().items()))
    by = out.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        direct=("hit", "mean"), top2=("top2", "mean"))
    print("\nPor estación:")
    print(by.to_string(formatters={"base": "{:.1%}".format, "direct": "{:.1%}".format,
                                   "top2": "{:.1%}".format}))
    print("\nGate exploratorio corregido p<0.025: " +
          ("PASO" if out.hit.mean() > out.hit_base.mean() and p < .025 and
           out.top2.mean() >= out.top2_base.mean() else "NO PASO"))


if __name__ == "__main__":
    main()
