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
from wxbt.exact_selector import SHADOW0 as CITYX_SHADOW0, VERSION as CITYX_VERSION  # noqa: E402
from wxbt.cityx_confidence import (GATE_DAYS as CONF_GATE_DAYS,  # noqa: E402
    MIN_FORWARD_COVERAGE, MIN_FORWARD_EXACT, VERSION as CONF_VERSION)

D = os.path.join(os.path.dirname(__file__), "..", "data")
MODELS = {"gfs13", "ecmwf", "aifs", "icon", "arpege", "ukmo", "jma", "cma"}
MIN_BIAS_N = 15
BIAS_DAYS = 60
OUTPUT_COLUMNS = [
    "station", "target", "capture_utc", "mu_med8", "mu_v2", "mu_cityx",
    "hit_med8", "hit_v2", "hit_cityx", "conf_selected", "spread_buckets",
    "ae_med8", "ae_v2", "ae_cityx",
]


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
    if "version" in x.columns:
        x = x[x.version == CITYX_VERSION]
    x = x[x.target >= dt.date.fromisoformat(CITYX_SHADOW0)]
    x = x.sort_values("capture_utc").drop_duplicates(["station", "target"], keep="last")
    return x[["station", "target", "mu"]].rename(columns={"mu": "mu_cityx"})


def confidence_frozen():
    path = os.path.join(D, "cityx_confidence_forward.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["station", "target", "conf_selected", "spread_buckets"])
    x = pd.read_csv(path, parse_dates=["capture_utc"])
    x["target"] = pd.to_datetime(x.target).dt.date
    x = x[x.version == CONF_VERSION].sort_values("capture_utc").drop_duplicates(
        ["station", "target"], keep="last")
    return x[["station", "target", "selected", "spread_buckets"]].rename(
        columns={"selected": "conf_selected"})


def confidence_bootstrap(selected, all_city, reps=20000):
    days = sorted(all_city.target.unique())
    sel = selected.groupby("target").hit_cityx.agg(["sum", "count"])
    whole = all_city.groupby("target").hit_cityx.agg(["sum", "count"])
    rng = np.random.default_rng(20260713)
    deltas = []
    for _ in range(reps):
        sample = rng.choice(days, len(days), replace=True)
        sh = sum(sel.loc[d, "sum"] if d in sel.index else 0 for d in sample)
        sn = sum(sel.loc[d, "count"] if d in sel.index else 0 for d in sample)
        ah = sum(whole.loc[d, "sum"] for d in sample)
        an = sum(whole.loc[d, "count"] for d in sample)
        deltas.append(sh/max(sn, 1)-ah/max(an, 1))
    values = np.asarray(deltas)
    return float(np.mean(values <= 0)), np.quantile(values, [.05, .95])


def main():
    med = frozen_med8_rows()
    cityx = exact_selector_frozen()
    confidence = confidence_frozen()
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
    paired = paired.merge(confidence, on=["station", "target"], how="left")
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
                         conf_selected=(int(r.conf_selected) if pd.notna(r.conf_selected) else np.nan),
                         spread_buckets=(round(r.spread_buckets, 4)
                                         if pd.notna(r.spread_buckets) else np.nan),
                         ae_med8=(abs(r.mu_med8 - obs) if obs is not None and pd.notna(r.mu_med8)
                                  else np.nan),
                         ae_v2=(abs(r.mu_v2 - obs) if obs is not None else np.nan),
                         ae_cityx=(abs(r.mu_cityx - obs) if obs is not None and pd.notna(r.mu_cityx)
                                   else np.nan)))
    out = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
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
        city_days = city.target.nunique()
        city_delta = city.hit_cityx.mean()-city.hit_v2.mean()
        print(f"SOMBRA {CITYX_VERSION}: {len(city)} mercados/{city.target.nunique()} dias; exacto "
              f"{city.hit_v2.mean():.1%} V2 -> {city.hit_cityx.mean():.1%} CITYX "
              f"({city_delta:+.1%}).")
        if city_days >= 45:
            daily_city = city.assign(delta_row=city.hit_cityx-city.hit_v2).groupby(
                "target")["delta_row"].mean().to_numpy()
            rng_city = np.random.default_rng(20260713)
            boot_city = rng_city.choice(daily_city, size=(20000, len(daily_city)), replace=True).mean(axis=1)
            p_city = float(np.mean(boot_city <= 0))
            verdict_city = "ADOPTAR" if city_delta > 0 and p_city < .05 else "NO adoptar"
            print(f"Gate {CITYX_VERSION}: p(delta<=0)={p_city:.4f} -> {verdict_city}.")
        else:
            print(f"Gate {CITYX_VERSION}: {city_days}/45 días; no decidir antes.")
        conf = city[city.conf_selected == 1]
        coverage = len(conf)/len(city)
        if not conf.empty:
            conf_delta = conf.hit_cityx.mean()-city.hit_cityx.mean()
            print(f"SOMBRA {CONF_VERSION}: {len(conf)}/{len(city)} mercados "
                  f"({coverage:.1%}); exacto CITYX all {city.hit_cityx.mean():.1%} -> "
                  f"seleccionados {conf.hit_cityx.mean():.1%} ({conf_delta:+.1%}).")
            if city_days >= CONF_GATE_DAYS:
                p_conf, ci_conf = confidence_bootstrap(conf, city)
                adopt = (coverage >= MIN_FORWARD_COVERAGE and
                         conf.hit_cityx.mean() >= MIN_FORWARD_EXACT and
                         conf_delta > 0 and p_conf < .05)
                print(f"Gate {CONF_VERSION}: cobertura>={MIN_FORWARD_COVERAGE:.0%}, "
                      f"exacto>={MIN_FORWARD_EXACT:.0%}, p={p_conf:.4f}, "
                      f"CI90 [{ci_conf[0]:+.1%},{ci_conf[1]:+.1%}] -> "
                      f"{'ADOPTAR' if adopt else 'NO adoptar'}.")
            else:
                print(f"Gate {CONF_VERSION}: {city_days}/{CONF_GATE_DAYS} dias; no decidir antes.")
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
