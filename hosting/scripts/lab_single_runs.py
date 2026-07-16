#!/usr/bin/env python3
"""Nested time validation of init-anchored model combinations for exact buckets.

Selection is performed only on 2026-05-10..2026-06-20.  The winner is then
evaluated once on the untouched 2026-06-21..2026-07-11 holdout against Gamma.
"""
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from score_forward_history import bucket_grid, overlaps, parse_win, pick_bucket  # noqa: E402
from wxbt.market import bucket_prob  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "lab_single_runs_detail.csv")
D0 = dt.date(2026, 5, 10)
DEV_END = dt.date(2026, 6, 20)
TEST0 = dt.date(2026, 6, 21)
TEST1 = dt.date(2026, 7, 11)
MIN_TRAIN = 15
MODELS = ["gfs13", "ecmwf", "aifs", "icon", "arpege", "ukmo", "jma", "cma"]


def gamma_hit(mu, unit, win):
    winner = parse_win(win)
    if winner is None or not np.isfinite(mu):
        return np.nan
    return int(overlaps(pick_bucket(math.floor(mu), unit), winner))


def top2_hit(mu, sigma, unit, win):
    winner = parse_win(win)
    if winner is None:
        return np.nan
    grid = bucket_grid(math.floor(mu), unit)
    ranked = sorted(grid, key=lambda b: -bucket_prob(mu - 0.5, sigma, b[0], b[1]))
    return int(any(overlaps(bucket, winner) for bucket in ranked[:2]))


def recent(history, day, days):
    return [row for row in history if row[0] < day and (day - row[0]).days <= days]


def exact_offset(history, day, days, unit):
    h = recent(history, day, days)
    if len(h) < MIN_TRAIN:
        return 0.0
    step, limit = (0.5, 4.0) if unit == "F" else (0.25, 2.0)
    offsets = np.arange(-limit, limit + step / 2, step)
    ranked = []
    for offset in offsets:
        hits = [gamma_hit(raw + offset, unit, win) for _, raw, _, win in h]
        mae = np.mean([abs(raw + offset - real) for _, raw, real, _ in h])
        ranked.append((float(np.mean(hits)), -float(mae), -abs(float(offset)), float(offset)))
    return max(ranked)[-1]


def model_weighted(values, histories, day, mode):
    scores = {}
    for model, value in values.items():
        h = recent(histories.get(model, []), day, 60)
        if len(h) >= MIN_TRAIN:
            scores[model] = float(np.mean([row[1] * row[1] for row in h]))
    if not scores:
        return float(np.median(list(values.values())))
    if mode == "best":
        model = min(scores, key=scores.get)
        return values[model]
    selected = sorted(scores, key=scores.get)[:3] if mode == "top3" else list(scores)
    weights = {model: 1.0 / max(scores[model], 0.05) for model in selected}
    return sum(values[m] * weights[m] for m in selected) / sum(weights.values())


def bucket_vote(values, histories, day, unit, weighted=False):
    median = float(np.median(list(values.values())))
    votes = {}
    members = {}
    for model, value in values.items():
        weight = 1.0
        if weighted:
            h = recent(histories.get(model, []), day, 60)
            # Laplace shrink prevents tiny samples from dominating.
            weight = (sum(row[2] for row in h) + 2.0) / (len(h) + 4.0) if h else 0.5
        bucket = pick_bucket(math.floor(value), unit)
        votes[bucket] = votes.get(bucket, 0.0) + weight
        members.setdefault(bucket, []).append(value)
    winner = max(votes, key=lambda b: (votes[b], -abs(np.mean(members[b]) - median)))
    return float(np.median(members[winner]))


def base_predictions(row, model_histories, day, unit):
    values = {m: float(row[m]) for m in MODELS if m in row and pd.notna(row[m])}
    if len(values) < 3:
        return {}
    out = {f"S_{m}": v for m, v in values.items()}
    legacy = [values[m] for m in ("gfs13", "ecmwf", "icon") if m in values]
    if len(legacy) == 3:
        out["G3_MEAN"] = float(np.mean(legacy))
        out["G3_MED"] = float(np.median(legacy))
    all_values = list(values.values())
    out["ALL_MEAN"] = float(np.mean(all_values))
    out["ALL_MED"] = float(np.median(all_values))
    out["ALL_TRIM"] = float(np.mean(sorted(all_values)[1:-1])) if len(all_values) >= 5 else out["ALL_MEAN"]
    out["BUCKET_MODE"] = bucket_vote(values, model_histories, day, unit, weighted=False)
    out["BUCKET_ACC60"] = bucket_vote(values, model_histories, day, unit, weighted=True)
    out["W_MSE60"] = model_weighted(values, model_histories, day, "weighted")
    out["TOP3_MSE60"] = model_weighted(values, model_histories, day, "top3")
    out["BEST_MSE60"] = model_weighted(values, model_histories, day, "best")
    return out


def bootstrap_day(j, reps=30000):
    daily = j.assign(delta=j.hit - j.hit_base).groupby("d")["delta"].mean().to_numpy()
    rng = np.random.default_rng(20260713)
    boot = rng.choice(daily, size=(reps, len(daily)), replace=True).mean(axis=1)
    return float(np.mean(boot <= 0)), np.quantile(boot, [0.05, 0.95])


def main():
    sr = pd.read_csv(os.path.join(D, "single_runs.csv"))
    sr["d"] = pd.to_datetime(sr.target).dt.date
    wide = sr.pivot_table(index=["station", "d", "unit"], columns="model", values="tmax",
                          aggfunc="last").reset_index()
    bf = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    bf = bf[(bf.lead == 2) & bf.win_mkt.notna() & bf.max_real.notna()].copy()
    bf["d"] = pd.to_datetime(bf.target).dt.date
    bf = bf.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    data = wide.merge(bf[["station", "d", "max_real", "win_mkt"]], on=["station", "d"])
    data = data.sort_values(["station", "d"])

    details = []
    for station, group in data.groupby("station"):
        model_histories, base_histories = {}, {}
        for _, row in group.iterrows():
            day, unit, real, win = row.d, row.unit, float(row.max_real), row.win_mkt
            bases = base_predictions(row, model_histories, day, unit)
            for base, raw in bases.items():
                history = base_histories.get(base, [])
                corrections = {"RAW": 0.0}
                h30, h60 = recent(history, day, 30), recent(history, day, 60)
                corrections["B30"] = -float(np.mean([x[1] - x[2] for x in h30])) if len(h30) >= MIN_TRAIN else 0.0
                corrections["B60"] = -float(np.mean([x[1] - x[2] for x in h60])) if len(h60) >= MIN_TRAIN else 0.0
                corrections["X30"] = exact_offset(history, day, 30, unit)
                corrections["X60"] = exact_offset(history, day, 60, unit)
                sigma = max(float(np.std([x[1] - x[2] for x in h60])), 1.0 if unit == "F" else 0.6) \
                    if len(h60) >= MIN_TRAIN else (2.5 if unit == "F" else 1.5)
                if D0 <= day <= TEST1:
                    for correction, offset in corrections.items():
                        mu = raw + offset
                        details.append(dict(station=station, d=day, unit=unit,
                                            candidate=f"{base}|{correction}", mu=mu,
                                            hit=gamma_hit(mu, unit, win),
                                            top2=top2_hit(mu, sigma, unit, win),
                                            ae=abs(mu - real)))
                base_histories.setdefault(base, []).append((day, raw, real, win))
            for model in MODELS:
                if model in row and pd.notna(row[model]):
                    value = float(row[model])
                    model_histories.setdefault(model, []).append(
                        (day, value - real, gamma_hit(value, unit, win)))

    det = pd.DataFrame(details)
    det.to_csv(OUT, index=False)
    dev = det[(det.d <= DEV_END)]
    summary = dev.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
                                             top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
    max_n = summary.n.max()
    eligible = summary[summary.n >= 0.9 * max_n].sort_values(
        ["exact", "top2", "mae"], ascending=[False, False, True])
    winner = eligible.iloc[0].candidate
    baseline = "G3_MEAN|B60"
    city_winners = {}
    for station, station_dev in dev.groupby("station"):
        ss = station_dev.groupby("candidate").agg(
            n=("hit", "size"), exact=("hit", "mean"), top2=("top2", "mean"), mae=("ae", "mean")).reset_index()
        ss = ss[ss.n >= 0.9 * ss.n.max()].sort_values(
            ["exact", "top2", "mae"], ascending=[False, False, True])
        city_winners[station] = ss.iloc[0].candidate
    print(f"SINGLE RUNS honesto: dev {D0}..{DEV_END}, test {TEST0}..{TEST1}")
    print("\nTop desarrollo (seleccion ANTES de mirar holdout):")
    print(eligible.head(12).to_string(index=False, formatters={"exact": "{:.1%}".format,
          "top2": "{:.1%}".format, "mae": "{:.3f}".format}))
    print(f"\nGanador congelado por dev: {winner}")

    test = det[(det.d >= TEST0) & (det.d <= TEST1) & det.candidate.isin([winner, baseline])]
    w = test[test.candidate == winner][["station", "d", "hit", "top2", "ae"]]
    b = test[test.candidate == baseline][["station", "d", "hit", "top2", "ae"]].rename(
        columns={"hit": "hit_base", "top2": "top2_base", "ae": "ae_base"})
    paired = w.merge(b, on=["station", "d"])
    if paired.empty:
        print("Holdout sin pares suficientes todavia.")
        return
    p, ci = bootstrap_day(paired)
    print(f"\nHOLDOUT INTOCADO ({paired.d.nunique()} dias, n={len(paired)}):")
    print(f"  exacto {baseline} {paired.hit_base.mean():.1%} -> {winner} {paired.hit.mean():.1%} "
          f"(delta {paired.hit.mean()-paired.hit_base.mean():+.1%})")
    print(f"  top2   {paired.top2_base.mean():.1%} -> {paired.top2.mean():.1%}")
    print(f"  MAE    {paired.ae_base.mean():.3f} -> {paired.ae.mean():.3f}")
    print(f"  bootstrap por dia P(delta<=0)={p:.4f}, CI90 delta [{ci[0]:+.1%},{ci[1]:+.1%}]")
    print("\nSeleccion por ciudad congelada en desarrollo:")
    print("  " + " | ".join(f"{st}:{candidate}" for st, candidate in sorted(city_winners.items())))
    chosen = pd.concat([det[(det.station == st) & (det.candidate == candidate)]
                        for st, candidate in city_winners.items()], ignore_index=True)
    chosen = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)][
        ["station", "d", "hit", "top2", "ae"]]
    city_paired = chosen.merge(b, on=["station", "d"])
    cp, cci = bootstrap_day(city_paired)
    print(f"\nHOLDOUT selector-por-ciudad (n={len(city_paired)}):")
    print(f"  exacto {city_paired.hit_base.mean():.1%} -> {city_paired.hit.mean():.1%} "
          f"(delta {city_paired.hit.mean()-city_paired.hit_base.mean():+.1%})")
    print(f"  top2   {city_paired.top2_base.mean():.1%} -> {city_paired.top2.mean():.1%}")
    print(f"  bootstrap P(delta<=0)={cp:.4f}, CI90 [{cci[0]:+.1%},{cci[1]:+.1%}]")
    by_city = city_paired.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
                                                  chosen=("hit", "mean"), top2=("top2", "mean"))
    print(by_city.to_string(formatters={"base": "{:.1%}".format, "chosen": "{:.1%}".format,
                                        "top2": "{:.1%}".format}))
    # Context-only comparison with the old production V2 series. V2 was built
    # from Previous Runs and therefore retains bug #5 freshness; it is not an
    # equally clean benchmark, but reporting it prevents cherry-picked claims.
    v2_path = os.path.join(D, "lab_city_models_detail.csv")
    if os.path.exists(v2_path):
        v2 = pd.read_csv(v2_path)
        v2 = v2[v2.variant == "V2"][["st", "d", "mu"]].rename(
            columns={"st": "station", "mu": "mu_v2"})
        v2["d"] = pd.to_datetime(v2.d).dt.date
        truth = bf[["station", "d", "unit", "win_mkt"]]
        cx = chosen[(chosen.d >= TEST0) & (chosen.d <= TEST1)][
            ["station", "d", "hit"]].merge(v2, on=["station", "d"]).merge(
                truth, on=["station", "d"])
        cx["hit_base"] = [gamma_hit(mu, unit, win) for mu, unit, win in
                           zip(cx.mu_v2, cx.unit, cx.win_mkt)]
        vp, vci = bootstrap_day(cx)
        print(f"\nContexto contra V2 Previous-Runs (benchmark favorecido por frescura, n={len(cx)}):")
        print(f"  exacto V2 {cx.hit_base.mean():.1%} -> selector honesto {cx.hit.mean():.1%} "
              f"(delta {cx.hit.mean()-cx.hit_base.mean():+.1%}, p={vp:.4f}, "
              f"CI90 [{vci[0]:+.1%},{vci[1]:+.1%}])")
    print("Promover solo con delta>0, p<0.025 (dos hipotesis: pooled/city) y top2 no degradado.")


if __name__ == "__main__":
    main()
