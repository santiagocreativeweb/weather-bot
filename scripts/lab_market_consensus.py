#!/usr/bin/env python3
"""Time-split CITYX1 + CLOB consensus experiment for exact temperature buckets.

Hypothesis frozen 2026-07-13: use only prices timestamped before the operational
freeze, select cutoff/blend on 2026-05-10..06-10, evaluate once on 06-11..07-01.
This is a prediction benchmark, not a PnL backtest.
"""
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import freeze_utc  # noqa: E402
from lab_single_runs import bootstrap_day  # noqa: E402
from show_live import STATIONS  # noqa: E402
from wxbt.exact_selector import RECIPES  # noqa: E402
from wxbt.market import bucket_prob  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
STATION_SET = ["KLGA", "KORD", "LFPB", "RJTT", "RKSI"]
DEV0, DEV1 = dt.date(2026, 5, 10), dt.date(2026, 6, 10)
TEST0, TEST1 = dt.date(2026, 6, 11), dt.date(2026, 7, 1)
CUTOFF_HOURS = (0, 3, 6)
ALPHAS = (0.0, 0.25, 0.5, 0.75)  # weight on bot; 0 = market favourite
MAX_PRICE_AGE_H = 8


def contains(temp, lo, hi):
    return (pd.isna(lo) or temp >= lo) and (pd.isna(hi) or temp <= hi)


def rolling_sigma(base):
    """Strictly prior CITYX1 residual scale for bucket probabilities."""
    sigmas, history = [], {}
    for r in base.sort_values(["d", "station"]).itertuples():
        h = history.get(r.station, [])
        recent = [e for day, e in h if day < r.d and (r.d-day).days <= 60]
        floor = 1.0 if r.unit == "F" else .6
        default = 2.5 if r.unit == "F" else 1.5
        sigmas.append(max(float(np.std(recent, ddof=1)), floor) if len(recent) >= 15 else default)
        history.setdefault(r.station, []).append((r.d, r.mu_base-r.max_real))
    out = base.sort_values(["d", "station"]).copy()
    out["sigma"] = sigmas
    return out


def load_base():
    detail = pd.read_csv(os.path.join(D, "lab_single_runs_detail.csv"))
    detail["d"] = pd.to_datetime(detail.d).dt.date
    chosen = pd.concat([detail[(detail.station == st) & (detail.candidate == RECIPES[st])]
                        for st in STATION_SET], ignore_index=True)
    chosen = chosen[["station", "d", "mu"]].rename(columns={"mu": "mu_base"})
    truth = pd.read_csv(os.path.join(D, "backfill_check.csv"))
    truth["d"] = pd.to_datetime(truth.target).dt.date
    truth = truth[(truth.lead == 2) & truth.station.isin(STATION_SET) &
                  truth.max_real.notna() & truth.win_mkt.notna()]
    truth = truth.sort_values("d").drop_duplicates(["station", "d"], keep="last")
    base = chosen.merge(truth[["station", "d", "unit", "max_real"]], on=["station", "d"])
    return rolling_sigma(base)


def market_grids():
    markets = pd.read_csv(os.path.join(D, "markets.csv"))
    markets["d"] = pd.to_datetime(markets.target).dt.date
    markets = markets[markets.station.isin(STATION_SET)]
    grids, winners = {}, {}
    for (station, day), group in markets.groupby(["station", "d"]):
        group = group.drop_duplicates("bucket", keep="last")
        grids[(station, day)] = group[["bucket", "lo", "hi"]].copy()
        won = group[group.resolved == 1]
        if len(won) == 1:
            winners[(station, day)] = int(won.iloc[0].bucket)
    return grids, winners


def latest_prices(prices, station, day, cutoff, grid):
    p = prices[(prices.station == station) & (prices.d == day) &
               (prices.t <= cutoff) &
               (prices.t >= cutoff-pd.Timedelta(hours=MAX_PRICE_AGE_H))]
    if p.empty:
        return None
    p = p.sort_values("t").drop_duplicates("bucket", keep="last")
    joined = grid.merge(p[["bucket", "mid", "t"]], on="bucket")
    return joined if len(joined) >= 4 else None


def rank_buckets(group, mu, sigma, alpha):
    market = np.clip(group.mid.to_numpy(float), 1e-5, 1.0)
    market /= market.sum()
    bot = np.array([bucket_prob(mu-0.5, sigma,
                    None if pd.isna(lo) else float(lo),
                    None if pd.isna(hi) else float(hi))
                    for lo, hi in zip(group.lo, group.hi)])
    bot = np.clip(bot, 1e-8, None); bot /= bot.sum()
    score = alpha*bot + (1-alpha)*market
    return group.iloc[np.argsort(-score)].bucket.astype(int).tolist()


def main():
    base = load_base()
    grids, winners = market_grids()
    prices = pd.read_csv(os.path.join(D, "prices.csv"), parse_dates=["t"])
    prices["d"] = pd.to_datetime(prices.target).dt.date
    prices = prices[prices.station.isin(STATION_SET) &
                    (prices.d >= DEV0) & (prices.d <= TEST1)]
    rows = []
    for r in base[(base.d >= DEV0) & (base.d <= TEST1)].itertuples():
        key = (r.station, r.d)
        grid, winner = grids.get(key), winners.get(key)
        if grid is None or winner is None:
            continue
        bot_temp = math.floor(r.mu_base)
        bot_bucket = next((int(g.bucket) for g in grid.itertuples()
                           if contains(bot_temp, g.lo, g.hi)), None)
        if bot_bucket is None:
            continue
        for hours in CUTOFF_HOURS:
            cutoff = pd.Timestamp(freeze_utc(r.station, r.d)-dt.timedelta(hours=hours))
            pg = latest_prices(prices, r.station, r.d, cutoff, grid)
            if pg is None:
                continue
            for alpha in ALPHAS:
                ranked = rank_buckets(pg, r.mu_base, r.sigma, alpha)
                rows.append(dict(station=r.station, d=r.d, cutoff_h=hours, alpha=alpha,
                    candidate=f"F{hours}_A{alpha:.2f}", winner=winner,
                    bot_bucket=bot_bucket, chosen=ranked[0],
                    hit_base=int(bot_bucket == winner), hit=int(ranked[0] == winner),
                    top2=int(winner in ranked[:2]), price_age_h=float(
                        (cutoff-pg.t.max()).total_seconds()/3600)))
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(D, "lab_market_consensus_detail.csv"), index=False)
    dev = out[(out.d >= DEV0) & (out.d <= DEV1)]
    summary = dev.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
        top2=("top2", "mean"), age=("price_age_h", "mean")).reset_index()
    summary = summary[summary.n >= .9*summary.n.max()].sort_values(
        ["exact", "top2", "age"], ascending=[False, False, True])
    if summary.empty:
        raise SystemExit("Sin cobertura suficiente de precios en desarrollo")
    winner_name = summary.iloc[0].candidate
    print(f"CONSENSO pre-freeze: dev {DEV0}..{DEV1}; test {TEST0}..{TEST1}")
    print("\nDESARROLLO (cutoff/alpha seleccionados antes del test):")
    print(summary.to_string(index=False, formatters={"exact": "{:.1%}".format,
          "top2": "{:.1%}".format, "age": "{:.2f}h".format}))
    print(f"\nGanador congelado: {winner_name}")
    test = out[(out.d >= TEST0) & (out.d <= TEST1) & (out.candidate == winner_name)].copy()
    if test.empty:
        raise SystemExit("Sin test pareado")
    p, ci = bootstrap_day(test)
    print(f"\nTEST FINAL n={len(test)}, dias={test.d.nunique()}, estaciones={test.station.nunique()}:")
    print(f" exacto CITYX1 {test.hit_base.mean():.1%} -> consenso {test.hit.mean():.1%} "
          f"(delta {test.hit.mean()-test.hit_base.mean():+.1%})")
    print(f" top2 consenso {test.top2.mean():.1%}; precio medido {test.price_age_h.mean():.2f}h antes del cutoff")
    print(f" bootstrap P(delta<=0)={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}]")
    by = test.groupby("station").agg(n=("hit", "size"), base=("hit_base", "mean"),
        consensus=("hit", "mean"), top2=("top2", "mean"))
    print("\nPor estación:")
    print(by.to_string(formatters={"base": "{:.1%}".format, "consensus": "{:.1%}".format,
                                   "top2": "{:.1%}".format}))
    passed = test.hit.mean() > test.hit_base.mean() and p < .05
    print("\nGate exploratorio delta>0 y p<0.05: " + ("PASO" if passed else "NO PASO"))

    # Secondary, multiplicity-penalised hypothesis: choose independently by
    # station on DEV, retaining CITYX1 whenever no blend beats it there.
    policies = {}
    for station, sd in dev.groupby("station"):
        base_exact = sd.drop_duplicates(["station", "d"]).hit_base.mean()
        ss = sd.groupby("candidate").agg(n=("hit", "size"), exact=("hit", "mean"),
            top2=("top2", "mean")).reset_index()
        ss = ss[ss.n >= .9*ss.n.max()].sort_values(
            ["exact", "top2"], ascending=[False, False])
        best = ss.iloc[0]
        policies[station] = best.candidate if best.exact > base_exact else "BASE_CITYX1"
    secondary = []
    all_test = out[(out.d >= TEST0) & (out.d <= TEST1)]
    for station, policy in policies.items():
        if policy == "BASE_CITYX1":
            part = all_test[all_test.station == station].drop_duplicates(["station", "d"]).copy()
            part["hit"] = part.hit_base
        else:
            part = all_test[(all_test.station == station) & (all_test.candidate == policy)].copy()
        secondary.append(part)
    city = pd.concat(secondary, ignore_index=True)
    cp, cci = bootstrap_day(city)
    print("\nSECUNDARIA selector por estación (elegido solo en DEV):")
    print("  " + " | ".join(f"{st}:{policy}" for st, policy in sorted(policies.items())))
    print(f"  test n={len(city)}: CITYX1 {city.hit_base.mean():.1%} -> selector "
          f"{city.hit.mean():.1%} (delta {city.hit.mean()-city.hit_base.mean():+.1%})")
    print(f"  bootstrap P(delta<=0)={cp:.4f}, CI90 [{cci[0]:+.1%},{cci[1]:+.1%}]")
    print("  Gate secundario corregido p<0.025: " +
          ("PASO" if city.hit.mean() > city.hit_base.mean() and cp < .025 else "NO PASO"))


if __name__ == "__main__":
    main()
