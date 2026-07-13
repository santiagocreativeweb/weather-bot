#!/usr/bin/env python3
"""Frozen exact-first test of multivariable NOAA LAMP station guidance.

Protocol fixed before the first run:
* fit: 2026-05-10..2026-05-31
* model selection: 2026-06-01..2026-06-20
* one final evaluation: 2026-06-21..2026-07-11

All candidates correct the already-frozen BLEND50|X60 LAMPX prediction.  Four
regressors learn its physical maximum-temperature residual and two classifiers
learn a coarse exact-bucket correction.  The validation winner is refit through
June 20 and evaluated once.  This historical holdout was used by earlier labs,
so even a positive result remains exploratory and cannot alter the live gate.
"""
import argparse
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import (ExtraTreesRegressor, HistGradientBoostingClassifier,
                              HistGradientBoostingRegressor, RandomForestClassifier,
                              RandomForestRegressor)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_lamp import payout_truth  # noqa: E402
from lab_single_runs import DEV_END, TEST0, TEST1, bootstrap_day, gamma_hit, top2_hit  # noqa: E402


D = os.path.join(os.path.dirname(__file__), "..", "data")
FEATURES = os.path.join(D, "lamp_physical_features.csv")
DETAIL = os.path.join(D, "lab_lamp_physical_detail.csv")
FIT0 = dt.date(2026, 5, 10)
FIT_END = dt.date(2026, 5, 31)
VALID0 = dt.date(2026, 6, 1)
VALID_END = DEV_END
BASE = "BLEND50|X60"
CORRECTIONS = np.arange(-2.0, 2.01, 1.0)
META = {"station", "target", "unit", "runtime_utc", "avail_utc", "freeze_utc",
        "d", "mu_base", "max_real", "win_mkt"}


def factories():
    """Small pre-registered family; every callable returns a fresh estimator."""
    median = lambda model: make_pipeline(SimpleImputer(strategy="median"), model)
    return {
        "RIDGE10": lambda: make_pipeline(SimpleImputer(strategy="median"),
            StandardScaler(), Ridge(alpha=10.0)),
        "HGBR7": lambda: median(HistGradientBoostingRegressor(max_leaf_nodes=7,
            max_iter=200, learning_rate=.04, l2_regularization=10.0,
            min_samples_leaf=15, random_state=20260713)),
        "RFR3": lambda: median(RandomForestRegressor(n_estimators=500, max_depth=3,
            min_samples_leaf=12, max_features=.7, random_state=20260713, n_jobs=-1)),
        "ETR3": lambda: median(ExtraTreesRegressor(n_estimators=500, max_depth=3,
            min_samples_leaf=12, max_features=.7, random_state=20260713, n_jobs=-1)),
        "HGBC7": lambda: median(HistGradientBoostingClassifier(max_leaf_nodes=7,
            max_iter=200, learning_rate=.04, l2_regularization=10.0,
            min_samples_leaf=15, random_state=20260713)),
        "RFC3": lambda: median(RandomForestClassifier(n_estimators=500, max_depth=3,
            min_samples_leaf=12, max_features=.7, class_weight="balanced_subsample",
            random_state=20260713, n_jobs=-1)),
    }


def load_data():
    physical = pd.read_csv(FEATURES)
    physical["d"] = pd.to_datetime(physical.target).dt.date
    base = pd.read_csv(os.path.join(D, "lab_lamp_detail_frozen.csv"))
    base["d"] = pd.to_datetime(base.d).dt.date
    base = base[base.candidate == BASE][["station", "d", "mu"]].rename(
        columns={"mu": "mu_base"})
    data = physical.merge(base, on=["station", "d"]).merge(
        payout_truth(), on=["station", "d"])
    data["doy_sin"] = np.sin(2*np.pi*pd.to_datetime(data.d).dt.dayofyear/365.25)
    data["doy_cos"] = np.cos(2*np.pi*pd.to_datetime(data.d).dt.dayofyear/365.25)
    data["lav_minus_base"] = data.tmax-data.mu_base
    return data.sort_values(["d", "station"]).reset_index(drop=True)


def design(data):
    numeric_columns = [column for column in data.columns
                       if column not in META and pd.api.types.is_numeric_dtype(data[column])]
    frame = data[numeric_columns].copy()
    station = pd.get_dummies(data.station, prefix="station", dtype=float)
    return pd.concat([frame.reset_index(drop=True), station.reset_index(drop=True)], axis=1)


def exact_correction(row):
    residual = float(row.max_real-row.mu_base)
    ranked = []
    for correction in CORRECTIONS:
        hit = gamma_hit(row.mu_base+correction, "F", row.win_mkt)
        ranked.append((hit, -abs(correction-residual), -abs(correction), correction))
    return max(ranked)[-1]


def fit_predict(name, make, train, x, train_index, predict_index):
    model = make()
    if name.endswith("C7") or name.startswith("RFC"):
        y = train.loc[train_index].apply(exact_correction, axis=1)
    else:
        y = (train.loc[train_index, "max_real"]-train.loc[train_index, "mu_base"]).clip(-3, 3)
    model.fit(x.loc[train_index], y)
    correction = np.asarray(model.predict(x.loc[predict_index]), dtype=float)
    return train.loc[predict_index, "mu_base"].to_numpy()+np.clip(correction, -3, 3)


def sigma_map(train):
    return train.groupby("station").apply(
        lambda g: max(float(np.std(g.max_real-g.mu_base)), 1.0), include_groups=False).to_dict()


def score_rows(data, indices, prediction, candidate, sigmas):
    rows = []
    for (_, row), mu in zip(data.loc[indices].iterrows(), prediction):
        rows.append({"station": row.station, "d": row.d, "candidate": candidate,
            "mu": mu, "hit": gamma_hit(mu, "F", row.win_mkt),
            "top2": top2_hit(mu, sigmas.get(row.station, 2.0), "F", row.win_mkt),
            "ae": abs(mu-row.max_real)})
    return rows


def summary(frame):
    return frame.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), mae=("ae", "mean")).reset_index().sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptive-test", action="store_true",
        help="show the already-exposed holdout even when validation selects LAMPX")
    args = parser.parse_args()
    data = load_data()
    x = design(data)
    fit_index = data.index[(data.d >= FIT0) & (data.d <= FIT_END)]
    valid_index = data.index[(data.d >= VALID0) & (data.d <= VALID_END)]
    dev_index = data.index[(data.d >= FIT0) & (data.d <= DEV_END)]
    test_index = data.index[(data.d >= TEST0) & (data.d <= TEST1)]
    if min(len(fit_index), len(valid_index), len(test_index)) < 100:
        raise SystemExit("insufficient temporal split")

    validation_rows = []
    sigmas = sigma_map(data.loc[fit_index])
    validation_rows.extend(score_rows(data, valid_index,
        data.loc[valid_index, "mu_base"].to_numpy(), "BASE_LAMPX", sigmas))
    models = factories()
    for name, make in models.items():
        prediction = fit_predict(name, make, data, x, fit_index, valid_index)
        validation_rows.extend(score_rows(data, valid_index, prediction, name, sigmas))
    validation = pd.DataFrame(validation_rows)
    ranking = summary(validation)
    selected = ranking.iloc[0].candidate
    winner = ranking[ranking.candidate != "BASE_LAMPX"].iloc[0].candidate

    print(f"LAMP physical exact-first: FIT {FIT0}..{FIT_END}; "
          f"VALID {VALID0}..{VALID_END}; TEST {TEST0}..{TEST1}")
    print(f"rows fit={len(fit_index)} valid={len(valid_index)} test={len(test_index)} "
          f"features={x.shape[1]}")
    print("\nFrozen validation ranking:")
    print(ranking.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"\nValidation selection: {selected}")
    if selected == "BASE_LAMPX" and not args.descriptive_test:
        print("No physical challenger beat LAMPX in validation: REJECT before holdout.")
        print("Use --descriptive-test only to reproduce the holdout exposed by the first lab run.")
        return
    if selected != "BASE_LAMPX":
        winner = selected
    else:
        print(f"Descriptive-only challenger: {winner}")

    test_sigmas = sigma_map(data.loc[dev_index])
    prediction = fit_predict(winner, models[winner], data, x, dev_index, test_index)
    test_rows = score_rows(data, test_index, prediction, winner, test_sigmas)
    test_rows.extend(score_rows(data, test_index,
        data.loc[test_index, "mu_base"].to_numpy(), "BASE_LAMPX", test_sigmas))
    test = pd.DataFrame(test_rows)
    test.to_csv(DETAIL, index=False)
    candidate = test[test.candidate == winner]
    base = test[test.candidate == "BASE_LAMPX"].rename(
        columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    paired = candidate.merge(base[["station", "d", "hit_base", "top2_base", "ae_base"]],
                             on=["station", "d"])
    p, ci = bootstrap_day(paired)

    print(f"\nEvaluated challenger: {winner}")
    print(f"Final historical holdout: n={len(paired)}, exact "
          f"{paired.hit_base.mean():.1%} -> {paired.hit.mean():.1%} "
          f"({paired.hit.mean()-paired.hit_base.mean():+.1%}), top2 "
          f"{paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}, MAE "
          f"{paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}, p={p:.5f}, "
          f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = paired.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        physical=("hit", "mean"), top2=("top2", "mean"), mae=("ae", "mean"))
    print("\nBy station:")
    print(by.to_string(formatters={"base": "{:.1%}".format,
        "physical": "{:.1%}".format, "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    passed = (paired.hit.mean() > paired.hit_base.mean() and
              paired.top2.mean() >= paired.top2_base.mean() and p < .05)
    print("\nExploratory gate delta>0, top2 nondegrade, p<0.05 -> " +
          ("PASS (forward shadow still required)" if passed else "REJECT"))


if __name__ == "__main__":
    main()
