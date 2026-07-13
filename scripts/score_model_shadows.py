#!/usr/bin/env python3
"""Forward-only MED8 challenger versus the frozen production V2 forecast."""
import datetime as dt
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check_predictions import fetch_obs_iem, resolved_buckets, winner_by_temp  # noqa: E402
from dashboard import freeze_utc  # noqa: E402
from wxbt.forward_scoring import frozen_forecast  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
MODELS = {"gfs13", "ecmwf", "aifs", "icon", "arpege", "ukmo", "jma", "cma"}
MIN_BIAS_N = 15
BIAS_DAYS = 60


def historical_med8_errors():
    # Prefer the init-anchored archive: same explicit eight-model composition as forward.
    path = os.path.join(D, "single_runs.csv")
    if os.path.exists(path):
        lab = pd.read_csv(path)
        lab = lab[lab.model.isin(MODELS)].copy()
        lab["target"] = pd.to_datetime(lab.target).dt.date
        counts = lab.groupby(["station", "target"]).model.nunique()
        good = counts[counts >= 6].index
        lab = lab.set_index(["station", "target"]).loc[good].reset_index()
        raw = lab.groupby(["station", "target"]).tmax.median().rename("med8").reset_index()
    else:
        lab = pd.read_csv(os.path.join(D, "lab_m8.csv"))
        lab = lab[lab.lead == 2].copy()
        lab["target"] = pd.to_datetime(lab.target).dt.date
        raw = lab.groupby(["station", "target"]).m.median().rename("med8").reset_index()
    obs = pd.read_csv(os.path.join(D, "obs.csv"))
    obs["date"] = pd.to_datetime(obs.date).dt.date
    out = raw.merge(obs[["station", "date", "tmax"]], left_on=["station", "target"],
                    right_on=["station", "date"])
    out["error"] = out.med8 - out.tmax
    return out[["station", "target", "error"]]


def bias_before(errors, station, target):
    g = errors[(errors.station == station) & (errors.target < target)].copy()
    g = g[(target - g.target).map(lambda x: x.days) <= BIAS_DAYS]
    return float(g.error.mean()) if len(g) >= MIN_BIAS_N else 0.0


def frozen_med8_rows():
    mf = pd.read_csv(os.path.join(D, "models_forward.csv"), parse_dates=["capture_utc"])
    mf["target"] = pd.to_datetime(mf.target).dt.date
    eligible = []
    for r in mf.itertuples(index=False):
        cutoff = freeze_utc(r.station, r.target).replace(tzinfo=dt.timezone.utc)
        if r.capture_utc.to_pydatetime() <= cutoff and r.model in MODELS:
            eligible.append(r)
    if not eligible:
        return pd.DataFrame(columns=["station", "target", "mu_med8", "capture_utc"])
    e = pd.DataFrame(eligible, columns=mf.columns)
    # Pick one coherent capture, not a mixture of runs from different timestamps.
    counts = e.groupby(["station", "target", "capture_utc"]).model.nunique().reset_index(name="n")
    complete = counts[counts.n == len(MODELS)].sort_values("capture_utc").drop_duplicates(
        ["station", "target"], keep="last")
    e = e.merge(complete[["station", "target", "capture_utc"]],
                on=["station", "target", "capture_utc"])
    return e.groupby(["station", "target", "capture_utc"]).tmax.median().rename(
        "mu_med8_raw").reset_index()


def production_frozen():
    p = pd.read_csv(os.path.join(D, "predictions_forward.csv"))
    p["target"] = pd.to_datetime(p.target).dt.date
    p = p.sort_values("lead_h").drop_duplicates(["station", "target"], keep="first")
    try:
        with open(os.path.join(D, "forecast_audit.json"), encoding="utf-8") as f:
            audit = json.load(f)
    except (OSError, ValueError):
        audit = {}
    rows = []
    for r in p.itertuples(index=False):
        mu, sg, source = frozen_forecast(audit, r.station, r.target, r.mu_cal, r.sigma_cal)
        if source != "forward-fallback":
            rows.append((r.station, r.target, mu, source))
    return pd.DataFrame(rows, columns=["station", "target", "mu_v2", "v2_source"])


def exact_selector_frozen():
    path = os.path.join(D, "exact_selector_forward.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["station", "target", "mu_cityx"])
    x = pd.read_csv(path, parse_dates=["capture_utc"])
    x["target"] = pd.to_datetime(x.target).dt.date
    x = x.sort_values("capture_utc").drop_duplicates(["station", "target"], keep="last")
    return x[["station", "target", "mu"]].rename(columns={"mu": "mu_cityx"})


def main():
    med = frozen_med8_rows()
    cityx = exact_selector_frozen()
    if med.empty and cityx.empty:
        print("SOMBRAS: sin capturas anteriores al freeze todavia."); return
    if not med.empty:
        errors = historical_med8_errors()
        med["bias60"] = [bias_before(errors, r.station, r.target) for r in med.itertuples()]
        med["mu_med8"] = med.mu_med8_raw - med.bias60
    keys = pd.concat([x[["station", "target"]] for x in (med, cityx) if not x.empty]).drop_duplicates()
    paired = keys.merge(production_frozen(), on=["station", "target"])
    if not med.empty:
        paired = paired.merge(med[["station", "target", "capture_utc", "mu_med8"]],
                              on=["station", "target"], how="left")
    else:
        paired["capture_utc"] = pd.NaT; paired["mu_med8"] = np.nan
    paired = paired.merge(cityx, on=["station", "target"], how="left")
    info = resolved_buckets(list(paired[["station", "target"]].itertuples(index=False, name=None)))
    rows = []
    for r in paired.itertuples(index=False):
        market = info.get((r.station, r.target))
        if not market or not market["buckets"] or market["winner"] is None:
            continue
        buckets, winner = market["buckets"], market["winner"]
        hit_m = (int(winner_by_temp(buckets, math.floor(r.mu_med8)) == winner)
                 if pd.notna(r.mu_med8) else np.nan)
        hit_v = int(winner_by_temp(buckets, math.floor(r.mu_v2)) == winner)
        hit_c = (int(winner_by_temp(buckets, math.floor(r.mu_cityx)) == winner)
                 if pd.notna(r.mu_cityx) else np.nan)
        obs = fetch_obs_iem(r.station, r.target)
        rows.append(dict(station=r.station, target=r.target, capture_utc=r.capture_utc,
                         mu_med8=(round(r.mu_med8, 3) if pd.notna(r.mu_med8) else np.nan),
                         mu_v2=round(r.mu_v2, 3),
                         mu_cityx=(round(r.mu_cityx, 3) if pd.notna(r.mu_cityx) else np.nan),
                         hit_med8=hit_m, hit_v2=hit_v, hit_cityx=hit_c,
                         ae_med8=(abs(r.mu_med8 - obs) if obs is not None and pd.notna(r.mu_med8)
                                  else np.nan),
                         ae_v2=(abs(r.mu_v2 - obs) if obs is not None else np.nan),
                         ae_cityx=(abs(r.mu_cityx - obs) if obs is not None and pd.notna(r.mu_cityx)
                                   else np.nan)))
    out = pd.DataFrame(rows)
    path = os.path.join(D, "model_shadows_forward.csv")
    out.to_csv(path, index=False)
    if out.empty:
        print(f"SOMBRAS: {len(paired)} pares point-in-time, ninguno resuelto aun.")
        return
    med_out = out.dropna(subset=["hit_med8"])
    if not med_out.empty:
        days = med_out.target.nunique()
        delta = med_out.hit_med8.mean() - med_out.hit_v2.mean()
        print(f"SOMBRA MED8: {len(med_out)} mercados/{days} dias; exacto "
              f"{med_out.hit_v2.mean():.1%} V2 -> {med_out.hit_med8.mean():.1%} MED8 ({delta:+.1%}).")
    city = out.dropna(subset=["hit_cityx"])
    if not city.empty:
        print(f"SOMBRA CITYX1: {len(city)} mercados/{city.target.nunique()} dias; exacto "
              f"{city.hit_v2.mean():.1%} V2 -> {city.hit_cityx.mean():.1%} CITYX1 "
              f"({city.hit_cityx.mean()-city.hit_v2.mean():+.1%}).")
    if not med_out.empty and days >= 45:
        daily = med_out.assign(delta_row=med_out.hit_med8 - med_out.hit_v2).groupby("target")["delta_row"].mean().to_numpy()
        rng = np.random.default_rng(20260712)
        boot = rng.choice(daily, size=(10000, len(daily)), replace=True).mean(axis=1)
        p_le_zero = float(np.mean(boot <= 0))
        verdict = "ADOPTAR" if delta > 0 and p_le_zero < 0.05 else "NO adoptar"
        print(f"Gate MED8: bootstrap por dia p(delta<=0)={p_le_zero:.4f} -> {verdict}.")
    elif not med_out.empty:
        print(f"Gate pre-registrado: {days}/45 dias; no evaluar antes de completar la muestra.")


if __name__ == "__main__":
    main()
