#!/usr/bin/env python3
"""Score the immutable LAMPX1 forward shadow against official Gamma winners."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check_predictions import resolved_buckets  # noqa: E402
from lab_single_runs import gamma_hit, top2_hit  # noqa: E402
from wxbt.lamp_shadow import (GATE_DAYS, MIN_RESOLVED_COVERAGE, NOW_VERSION,  # noqa: E402
                              SIGMA_F, VERSION, gate, now_gate)

D = os.path.join(os.path.dirname(__file__), "..", "data")
SOURCE = os.path.join(D, "lamp_shadow_forward.csv")
OUT = os.path.join(D, "lamp_shadow_results.csv")


def bootstrap(frame, reps=20000):
    daily = frame.assign(delta=frame.hit_lampx-frame.hit_cityx).groupby(
        "target").delta.mean().to_numpy()
    rng = np.random.default_rng(20260713)
    boot = rng.choice(daily, size=(reps, len(daily)), replace=True).mean(axis=1)
    return float(np.mean(boot <= 0)), np.quantile(boot, [.05, .95])


def bootstrap_now(frame, reps=20000):
    daily = frame.assign(delta=frame.hit_nowx-frame.hit_lampx).groupby(
        "target").delta.mean().to_numpy()
    rng = np.random.default_rng(20260714)
    boot = rng.choice(daily, size=(reps, len(daily)), replace=True).mean(axis=1)
    return float(np.mean(boot <= 0)), np.quantile(boot, [.05, .95])


def winner_label(bucket):
    lo, hi = bucket
    if lo is None:
        return f"<= {hi}°F"
    if hi is None:
        return f">= {lo}°F"
    return f"{lo}-{hi}°F" if lo != hi else f"{lo}°F"


def main():
    if not os.path.exists(SOURCE):
        print(f"{VERSION}: sin capturas forward todavía"); return
    data = pd.read_csv(SOURCE)
    data = data[data.version == VERSION].copy()
    data["target"] = pd.to_datetime(data.target).dt.date
    data = data.sort_values("capture_utc").drop_duplicates(["station", "target"], keep="last")
    info = resolved_buckets(list(data[["station", "target"]].itertuples(index=False, name=None)))
    rows = []
    for r in data.itertuples(index=False):
        market = info.get((r.station, r.target))
        if not market or market.get("winner") is None:
            continue
        winner = winner_label(market["winner"])
        rows.append({"station": r.station, "target": r.target,
                     "mu_lampx": r.mu_lampx, "mu_nowx": r.mu_nowx,
                     "mu_cityx": r.mu_cityx,
                     "hit_lampx": gamma_hit(r.mu_lampx, "F", winner),
                     "hit_nowx": gamma_hit(r.mu_nowx, "F", winner),
                     "hit_cityx": gamma_hit(r.mu_cityx, "F", winner),
                     "top2_lampx": top2_hit(r.mu_lampx, SIGMA_F[r.station], "F", winner),
                     "top2_nowx": top2_hit(r.mu_nowx, SIGMA_F[r.station], "F", winner),
                     "top2_cityx": top2_hit(r.mu_cityx, SIGMA_F[r.station], "F", winner)})
    result = pd.DataFrame(rows)
    if result.empty:
        print(f"{VERSION}: {len(data)} capturas, 0 mercados resueltos"); return
    result.to_csv(OUT, index=False)
    days = result.target.nunique()
    eligible_captures = data[data.target <= result.target.max()]
    resolved_coverage = len(result)/len(eligible_captures) if len(eligible_captures) else 0.0
    exact, base = result.hit_lampx.mean(), result.hit_cityx.mean()
    top2, base_top2 = result.top2_lampx.mean(), result.top2_cityx.mean()
    p, ci = bootstrap(result)
    verdict = gate(exact, base, top2, base_top2, p, days, resolved_coverage)
    print(f"{VERSION}: {len(result)} mercados/{days} días; exacto {base:.1%} CITYX -> "
          f"{exact:.1%} LAMPX ({exact-base:+.1%}); top2 {base_top2:.1%} -> {top2:.1%}; "
          f"p={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}], "
          f"cobertura resuelta={resolved_coverage:.1%}.")
    if days < GATE_DAYS:
        print(f"Gate: {days}/{GATE_DAYS} días; no decidir antes.")
    else:
        print(f"Gate cobertura>={MIN_RESOLVED_COVERAGE:.0%}, exacto>39.6%, delta>0, "
              "top2 no baja, p<0.05 -> " +
              ("ADOPTAR" if verdict else "NO adoptar"))
    now_exact, now_top2 = result.hit_nowx.mean(), result.top2_nowx.mean()
    pn, cin = bootstrap_now(result)
    now_verdict = now_gate(verdict, now_exact, exact, now_top2, top2, pn, days,
                           resolved_coverage)
    print(f"{NOW_VERSION}: exacto {exact:.1%} LAMPX -> {now_exact:.1%} NOW "
          f"({now_exact-exact:+.1%}); top2 {top2:.1%} -> {now_top2:.1%}; "
          f"p={pn:.4f}, CI90 [{cin[0]:+.1%},{cin[1]:+.1%}].")
    if days < GATE_DAYS:
        print(f"Gate jerárquico NOW: {days}/{GATE_DAYS} días; no decidir antes.")
    else:
        print("Gate NOW requiere primero LAMP=ADOPTAR, delta>0, top2 no baja, p<0.05 -> " +
              ("ADOPTAR" if now_verdict else "NO adoptar"))


if __name__ == "__main__":
    main()
