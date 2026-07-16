#!/usr/bin/env python3
"""Pre-registered four-city physical MOS experiment.

Split fixed before feature download on 2026-07-13:
train 2026-05-10..06-06, validation 06-07..06-27, test 06-28..07-11.
The final test is evaluated once after selecting one algorithm on validation.
"""
import argparse
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lab_single_runs import bootstrap_day, gamma_hit, top2_hit  # noqa: E402
from wxbt.exact_selector import RECIPES  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
TRAIN_END = dt.date(2026, 6, 6)
VALID0, VALID1 = dt.date(2026, 6, 7), dt.date(2026, 6, 27)
TEST0, TEST1 = dt.date(2026, 6, 28), dt.date(2026, 7, 11)
STATIONS = ["KLGA", "KORD", "LEMD", "EGLC"]
TEMP_SUFFIXES = ("_tmax", "_t_peak", "_temp_trend")


def apply_metar_truth(truth):
    """Override only Fahrenheit physical targets with audited hourly ASOS maxima."""
    path = os.path.join(D, "lab_metar_precision.csv")
    if not os.path.exists(path):
        raise FileNotFoundError("run scripts/lab_metar_precision.py first")
    precision = pd.read_csv(path)
    precision = precision[precision.candidate == "raw_tmpf"].copy()
    precision["d"] = pd.to_datetime(precision.target).dt.date
    precision = precision[["station", "d", "value"]].drop_duplicates(["station", "d"])
    out = truth.merge(precision, on=["station", "d"], how="left")
    out["max_real"] = out.value.fillna(out.max_real)
    return out.drop(columns="value")


def load_data(oracle_truth=False):
    f = pd.read_csv(os.path.join(D, "mos_features.csv"))
    f["d"] = pd.to_datetime(f.target).dt.date
    id_cols = ["station", "d", "unit"]
    value_cols = [c for c in f.columns if c not in
                  {"target", "station", "model", "unit", "run_utc", "avail_utc", "freeze_utc", "d"}]
    wide = f.pivot_table(index=id_cols, columns="model", values=value_cols, aggfunc="last")
    wide.columns = [f"{model}_{feature}" for feature, model in wide.columns]
    wide = wide.reset_index()

    gd = pd.read_csv(os.path.join(D, "lab_single_runs_detail.csv"))
    gd["d"] = pd.to_datetime(gd.d).dt.date
    base = pd.concat([gd[(gd.station == station) & (gd.candidate == RECIPES[station])]
                      for station in STATIONS], ignore_index=True)
    base = base[["station", "d", "mu"]].rename(columns={"mu": "mu_base"})
    truth = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    truth["d"] = pd.to_datetime(truth.target).dt.date
    truth = truth[(truth.lead == 2) & truth.station.isin(STATIONS) &
                  truth.max_real.notna() & truth.win_mkt.notna()]
    truth = truth.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    if oracle_truth:
        truth = apply_metar_truth(truth)
    data = wide.merge(base, on=["station", "d"]).merge(
        truth[["station", "d", "max_real", "win_mkt"]], on=["station", "d"])
    return data.sort_values(["d", "station"]).reset_index(drop=True)


def make_xy(data, feature_columns=None):
    meta = {"station", "d", "unit", "mu_base", "max_real", "win_mkt"}
    raw = data[[c for c in data.columns if c not in meta]].copy()
    # Absolute model temperature becomes a departure from CITYX1. Fahrenheit
    # departures and slopes are converted to Celsius so the pooled target is coherent.
    for column in raw.columns:
        if column.endswith(("_tmax", "_t_peak")):
            raw[column] = raw[column] - data.mu_base
        if column.endswith(TEMP_SUFFIXES):
            raw.loc[data.unit == "F", column] = raw.loc[data.unit == "F", column] * 5 / 9
    raw["base_fraction"] = data.mu_base - np.floor(data.mu_base)
    doy = np.array([d.timetuple().tm_yday for d in data.d])
    raw["doy_sin"] = np.sin(2*np.pi*doy/365.25); raw["doy_cos"] = np.cos(2*np.pi*doy/365.25)
    station = pd.get_dummies(data.station, prefix="station", dtype=float)
    x = pd.concat([raw, station], axis=1)
    if feature_columns is not None:
        x = x.reindex(columns=feature_columns, fill_value=0.0)
    y = data.max_real - data.mu_base
    y = y.where(data.unit != "F", y * 5/9)
    return x, y.astype(float)


def candidates():
    return {
        "RIDGE_1": make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=1.0)),
        "RIDGE_10": make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=10.0)),
        "RIDGE_100": make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=100.0)),
        "RF_D2": make_pipeline(SimpleImputer(), RandomForestRegressor(
            n_estimators=500, max_depth=2, min_samples_leaf=8, max_features=.7,
            random_state=20260713, n_jobs=-1)),
        "RF_D3": make_pipeline(SimpleImputer(), RandomForestRegressor(
            n_estimators=500, max_depth=3, min_samples_leaf=6, max_features=.7,
            random_state=20260713, n_jobs=-1)),
        "ET_D2": make_pipeline(SimpleImputer(), ExtraTreesRegressor(
            n_estimators=500, max_depth=2, min_samples_leaf=8, max_features=.8,
            random_state=20260713, n_jobs=-1)),
        "ET_D3": make_pipeline(SimpleImputer(), ExtraTreesRegressor(
            n_estimators=500, max_depth=3, min_samples_leaf=6, max_features=.8,
            random_state=20260713, n_jobs=-1)),
        "HGB": make_pipeline(SimpleImputer(), HistGradientBoostingRegressor(
            max_iter=150, max_leaf_nodes=5, min_samples_leaf=12,
            learning_rate=.04, l2_regularization=5.0, random_state=20260713)),
    }


def native_mu(data, predicted_residual_c):
    residual = np.asarray(predicted_residual_c, dtype=float).copy()
    residual[data.unit.to_numpy() == "F"] *= 9/5
    return data.mu_base.to_numpy() + residual


def metrics(data, mu, sigma_by_station):
    hit = np.array([gamma_hit(m, u, w) for m, u, w in zip(mu, data.unit, data.win_mkt)])
    top2 = np.array([top2_hit(m, sigma_by_station[s], u, w)
                     for m, s, u, w in zip(mu, data.station, data.unit, data.win_mkt)])
    return hit, top2, np.abs(mu-data.max_real.to_numpy())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle-truth", action="store_true",
                    help="replace KLGA/KORD daily labels with hourly ASOS maxima")
    args = ap.parse_args()
    data = load_data(args.oracle_truth)
    train = data[data.d <= TRAIN_END].copy()
    valid = data[(data.d >= VALID0) & (data.d <= VALID1)].copy()
    test = data[(data.d >= TEST0) & (data.d <= TEST1)].copy()
    if min(len(train), len(valid), len(test)) == 0:
        raise SystemExit("Cobertura insuficiente para el split pre-registrado")
    sigma = train.assign(error=train.mu_base-train.max_real).groupby("station").error.std().to_dict()
    for station in STATIONS:
        sigma[station] = max(float(sigma.get(station, 1.5)), 1.0 if station in ("KLGA", "KORD") else .6)
    xtr, ytr = make_xy(train); xva, _ = make_xy(valid, xtr.columns)
    rows = []
    bh, bt, bae = metrics(valid, valid.mu_base.to_numpy(), sigma)
    rows.append(("BASE_CITYX1", bh.mean(), bt.mean(), bae.mean()))
    models = candidates()
    for name, model in models.items():
        model.fit(xtr, ytr)
        mu = native_mu(valid, model.predict(xva))
        h, t, ae = metrics(valid, mu, sigma)
        rows.append((name, h.mean(), t.mean(), ae.mean()))
    ranking = pd.DataFrame(rows, columns=["candidate", "exact", "top2", "mae"]).sort_values(
        ["exact", "top2", "mae"], ascending=[False, False, True])
    eligible = ranking[ranking.candidate != "BASE_CITYX1"]
    winner = eligible.iloc[0].candidate
    truth_name = "METAR-hourly °F" if args.oracle_truth else "legacy IEM-daily"
    print(f"MOS fisico pre-registrado ({truth_name}): train n={len(train)}, "
          f"valid n={len(valid)}, test n={len(test)}")
    if args.oracle_truth:
        print("SENSIBILIDAD POSTERIOR por corrección de fuente: no constituye un holdout nuevo.")
    print("\nVALIDACION (seleccion del algoritmo):")
    print(ranking.to_string(index=False, formatters={"exact": "{:.1%}".format,
          "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"\nGanador congelado antes del test: {winner}")

    dev = data[data.d <= VALID1].copy()
    xdev, ydev = make_xy(dev); xte, _ = make_xy(test, xdev.columns)
    final_model = clone(models[winner]).fit(xdev, ydev)
    mu = native_mu(test, final_model.predict(xte))
    hit, top2, ae = metrics(test, mu, sigma)
    hit_b, top2_b, ae_b = metrics(test, test.mu_base.to_numpy(), sigma)
    detail = test[["station", "d", "unit", "mu_base", "max_real", "win_mkt"]].copy()
    detail["mu_mos"] = mu; detail["hit_base"] = hit_b; detail["hit"] = hit
    detail["top2_base"] = top2_b; detail["top2"] = top2
    detail["ae_base"] = ae_b; detail["ae"] = ae
    suffix = "_oracle" if args.oracle_truth else ""
    detail.to_csv(os.path.join(D, f"lab_physical_mos_detail{suffix}.csv"), index=False)
    p, ci = bootstrap_day(detail)
    print(f"\nTEST FINAL UNA SOLA VEZ ({TEST0}..{TEST1}, n={len(test)}):")
    print(f" exacto CITYX1 {hit_b.mean():.1%} -> MOS {hit.mean():.1%} "
          f"(delta {hit.mean()-hit_b.mean():+.1%})")
    print(f" top2   {top2_b.mean():.1%} -> {top2.mean():.1%}")
    print(f" MAE    {ae_b.mean():.3f} -> {ae.mean():.3f}")
    print(f" bootstrap por dia P(delta<=0)={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    passed = hit.mean() > .396 and hit.mean() > hit_b.mean() and top2.mean() >= top2_b.mean() and p < .05
    print("GATE >39.6%, mejora pareada, top2 no baja, p<0.05: " + ("PASO" if passed else "NO PASO"))
    if args.oracle_truth:
        print("Promoción prohibida desde este test ya abierto; cualquier variante requiere gate forward.")
    print("\nPor ciudad:")
    by = detail.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        mos=("hit", "mean"), top2=("top2", "mean"), mae=("ae", "mean"))
    print(by.to_string(formatters={"base": "{:.1%}".format, "mos": "{:.1%}".format,
                                   "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    estimator = final_model.steps[-1][1]
    if hasattr(estimator, "feature_importances_"):
        importance = pd.Series(estimator.feature_importances_, index=xdev.columns).sort_values(
            ascending=False).head(12)
        print("\nFeatures dominantes (diagnostico, no nueva seleccion):")
        print(importance.to_string(float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
