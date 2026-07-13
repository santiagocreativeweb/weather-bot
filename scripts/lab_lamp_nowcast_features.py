#!/usr/bin/env python3
"""Exploratory exact-first extension of the frozen LAMPNOW25 correction.

Candidate family fixed before first execution.  Rules use only observations
available 15 minutes before freeze; shallow models learn a residual correction
on 2026-05-10..05-31, select on 06-01..06-20, and evaluate once on the already
exposed 06-21..07-11 period.  A positive result could only start a new forward
shadow; it cannot modify LAMPNOW1's frozen formula or gate.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import (ExtraTreesRegressor, HistGradientBoostingRegressor,
                              RandomForestClassifier, RandomForestRegressor)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_lamp import build_details, payout_truth  # noqa: E402
from lab_single_runs import D, DEV_END, TEST0, TEST1, bootstrap_day, gamma_hit, top2_hit  # noqa: E402
from wxbt.lamp_shadow import now_prediction  # noqa: E402


FIT0, FIT1 = dt.date(2026, 5, 10), dt.date(2026, 5, 31)
VALID0, VALID1 = dt.date(2026, 6, 1), DEV_END
BASE = "BLEND50|X60"
DETAIL = os.path.join(D, "lab_lamp_nowcast_features_detail.csv")
CORRECTIONS = np.arange(-2.0, 2.01, 1.0)
META = {"station", "target", "unit", "runtime_utc", "lav_avail_utc", "freeze_utc",
        "obs_valid_utc", "obs_avail_utc", "lav_match_utc", "lav_peak_utc", "d",
        "mu_lamp", "mu_now25", "max_real", "win_mkt"}


def rule_predictions(data):
    clipped = data.innovation.clip(-4, 4)
    trend_alpha = np.where(data.innovation*data.obs_trend_fph > 0, .50, .25)
    peak_alpha = np.where(data.hours_to_lav_peak >= 6, .50, .25)
    trend = data.mu_lamp+trend_alpha*clipped
    peak = data.mu_lamp+peak_alpha*clipped
    return {
        "BASE_NOW25": data.mu_now25.to_numpy(float),
        "OBSFLOOR": np.maximum(data.mu_now25, data.obs_max).to_numpy(float),
        "TREND_DYN": trend.to_numpy(float),
        "PEAK_DYN": peak.to_numpy(float),
        "FLOOR_PEAK": np.maximum(peak, data.obs_max).to_numpy(float),
    }


def factories():
    median = lambda model: make_pipeline(SimpleImputer(strategy="median"), model)
    return {
        "RIDGE10": ("reg", lambda: make_pipeline(SimpleImputer(strategy="median"),
            StandardScaler(), Ridge(alpha=10.0))),
        "HGBR7": ("reg", lambda: median(HistGradientBoostingRegressor(max_leaf_nodes=7,
            max_iter=200, learning_rate=.04, l2_regularization=10,
            min_samples_leaf=15, random_state=20260713))),
        "RFR3": ("reg", lambda: median(RandomForestRegressor(n_estimators=500, max_depth=3,
            min_samples_leaf=12, max_features=.7, random_state=20260713, n_jobs=-1))),
        "RFC3": ("class", lambda: median(RandomForestClassifier(n_estimators=500, max_depth=3,
            min_samples_leaf=12, max_features=.7, class_weight="balanced_subsample",
            random_state=20260713, n_jobs=-1))),
    }


def load_data():
    base = build_details(frozen_test=True, lamp_path=os.path.join(D, "lamp_daily_lag2.csv"))
    base = base[base.candidate == BASE][["station", "d", "mu"]].rename(
        columns={"mu": "mu_lamp"})
    now = pd.read_csv(os.path.join(D, "lamp_nowcast.csv"))
    now["d"] = pd.to_datetime(now.target).dt.date
    truth = payout_truth()
    data = base.merge(now, on=["station", "d"]).merge(
        truth[["station", "d", "max_real", "win_mkt"]], on=["station", "d"])
    data["mu_now25"] = [now_prediction(mu, innovation)
                        for mu, innovation in zip(data.mu_lamp, data.innovation)]
    data["obs_range"] = data.obs_max-data.obs_min
    data["obs_max_minus_now"] = data.obs_max-data.mu_now25
    data["latest_minus_now"] = data.obs_latest-data.mu_now25
    data["lav_headroom"] = data.lav_tmax-data.lav_at_obs
    data["forecast_headroom"] = data.lav_tmax-data.obs_latest
    data["doy_sin"] = np.sin(2*np.pi*pd.to_datetime(data.d).dt.dayofyear/365.25)
    data["doy_cos"] = np.cos(2*np.pi*pd.to_datetime(data.d).dt.dayofyear/365.25)
    return data.sort_values(["d", "station"]).reset_index(drop=True)


def design(data):
    columns = [column for column in data.columns if column not in META and
               pd.api.types.is_numeric_dtype(data[column])]
    station = pd.get_dummies(data.station, prefix="station", dtype=float)
    return pd.concat([data[columns].reset_index(drop=True),
                      station.reset_index(drop=True)], axis=1)


def exact_correction(row):
    residual = float(row.max_real-row.mu_now25)
    ranked = [(gamma_hit(row.mu_now25+c, "F", row.win_mkt), -abs(c-residual),
               -abs(c), c) for c in CORRECTIONS]
    return max(ranked)[-1]


def model_prediction(name, specification, data, x, train_index, predict_index):
    kind, make = specification
    model = make()
    if kind == "class":
        target = data.loc[train_index].apply(exact_correction, axis=1)
    else:
        target = (data.loc[train_index, "max_real"]-
                  data.loc[train_index, "mu_now25"]).clip(-3, 3)
    model.fit(x.loc[train_index], target)
    correction = np.asarray(model.predict(x.loc[predict_index]), float)
    return data.loc[predict_index, "mu_now25"].to_numpy()+np.clip(correction, -3, 3)


def sigma_map(data):
    return data.groupby("station").apply(lambda group: max(float(np.sqrt(np.mean(
        np.square(group.mu_now25-group.max_real)))), 1.0), include_groups=False).to_dict()


def score(data, indices, predictions, candidate, sigmas):
    rows = []
    for (_, row), mu in zip(data.loc[indices].iterrows(), predictions):
        rows.append({"station": row.station, "d": row.d, "candidate": candidate,
            "mu": mu, "hit": gamma_hit(mu, "F", row.win_mkt),
            "top2": top2_hit(mu, sigmas[row.station], "F", row.win_mkt),
            "ae": abs(mu-row.max_real)})
    return rows


def summarize(frame):
    return frame.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), mae=("ae", "mean")).reset_index().sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])


def main():
    data, models = load_data(), factories()
    x = design(data)
    fit_index = data.index[(data.d >= FIT0) & (data.d <= FIT1)]
    valid_index = data.index[(data.d >= VALID0) & (data.d <= VALID1)]
    dev_index = data.index[(data.d >= FIT0) & (data.d <= DEV_END)]
    test_index = data.index[(data.d >= TEST0) & (data.d <= TEST1)]
    rules = rule_predictions(data)
    validation_rows, sigmas = [], sigma_map(data.loc[fit_index])
    for name, values in rules.items():
        validation_rows.extend(score(data, valid_index, values[valid_index], name, sigmas))
    for name, specification in models.items():
        prediction = model_prediction(name, specification, data, x, fit_index, valid_index)
        validation_rows.extend(score(data, valid_index, prediction, name, sigmas))
    validation = pd.DataFrame(validation_rows)
    ranking = summarize(validation)
    selected = ranking.iloc[0].candidate
    print(f"Expanded LAMP nowcast: FIT {FIT0}..{FIT1}; VALID {VALID0}..{VALID1}; "
          f"TEST {TEST0}..{TEST1}")
    print(f"rows fit={len(fit_index)} valid={len(valid_index)} test={len(test_index)}, "
          f"features={x.shape[1]}")
    print("\nFrozen validation ranking:")
    print(ranking.to_string(index=False, formatters={"exact": "{:.1%}".format,
        "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"\nValidation selection: {selected}")
    if selected == "BASE_NOW25":
        print("No expanded nowcast beat LAMPNOW25 in validation: REJECT before test.")
        return
    test_sigmas = sigma_map(data.loc[dev_index])
    if selected in rules:
        prediction = rules[selected][test_index]
    else:
        prediction = model_prediction(selected, models[selected], data, x, dev_index, test_index)
    rows = score(data, test_index, prediction, selected, test_sigmas)
    rows.extend(score(data, test_index, rules["BASE_NOW25"][test_index],
                      "BASE_NOW25", test_sigmas))
    test = pd.DataFrame(rows)
    test.to_csv(DETAIL, index=False)
    chosen = test[test.candidate == selected]
    base = test[test.candidate == "BASE_NOW25"].rename(
        columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    paired = chosen.merge(base[["station", "d", "hit_base", "top2_base", "ae_base"]],
                          on=["station", "d"])
    p, ci = bootstrap_day(paired)
    print(f"Historical test {selected}: n={len(paired)}, exact "
          f"{paired.hit_base.mean():.1%} -> {paired.hit.mean():.1%} "
          f"({paired.hit.mean()-paired.hit_base.mean():+.1%}), top2 "
          f"{paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}, MAE "
          f"{paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}, p={p:.5f}, "
          f"CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    passed = (paired.hit.mean() > paired.hit_base.mean() and
              paired.top2.mean() >= paired.top2_base.mean() and p < .05)
    print("Exploratory gate delta>0, top2 nondegrade, p<0.05 -> " +
          ("PASS (new forward shadow required)" if passed else "REJECT"))


if __name__ == "__main__":
    main()
