#!/usr/bin/env python3
"""Score frozen LAMP shadows and persist their forward gate state."""
import datetime as dt
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
STATUS = os.path.join(D, "lamp_shadow_verdict.csv")


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


def gate_state(days, resolved_coverage, lamp_passed=False, now_passed=False):
    """Separate evidence readiness from the eventual pass/fail decision."""
    if days < GATE_DAYS:
        return "ACCUMULATING"
    if resolved_coverage < MIN_RESOLVED_COVERAGE:
        return "WAITING_RESOLUTION"
    if not lamp_passed:
        return "REJECT_LAMP_AND_NOW"
    return "ADOPT_NOW" if now_passed else "ADOPT_LAMP_REJECT_NOW"


def write_status(**values):
    """Atomically materialize the latest gate state for dashboards and exports."""
    row = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "lamp_version": VERSION, "now_version": NOW_VERSION,
        "state": values.pop("state"), **values,
    }
    temporary = STATUS+".tmp"
    pd.DataFrame([row]).to_csv(temporary, index=False)
    os.replace(temporary, STATUS)


def winner_label(bucket):
    lo, hi = bucket
    if lo is None:
        return f"<= {hi}°F"
    if hi is None:
        return f">= {lo}°F"
    return f"{lo}-{hi}°F" if lo != hi else f"{lo}°F"


def main():
    if not os.path.exists(SOURCE):
        write_status(state="NO_CAPTURES", captured_markets=0, resolved_markets=0,
                     resolved_days=0, resolved_coverage=0.0,
                     decision_ready=0, lamp_decision="PENDING", now_decision="PENDING")
        print(f"{VERSION}: sin capturas forward todavía")
        return
    data = pd.read_csv(SOURCE)
    data = data[data.version == VERSION].copy()
    data["target"] = pd.to_datetime(data.target).dt.date
    data = data.sort_values("capture_utc").drop_duplicates(["station", "target"], keep="last")
    info = resolved_buckets(list(data[["station", "target"]].itertuples(index=False, name=None)))
    rows = []
    for row in data.itertuples(index=False):
        market = info.get((row.station, row.target))
        if not market or market.get("winner") is None:
            continue
        winner = winner_label(market["winner"])
        rows.append({"station": row.station, "target": row.target,
                     "mu_lampx": row.mu_lampx, "mu_nowx": row.mu_nowx,
                     "mu_cityx": row.mu_cityx,
                     "hit_lampx": gamma_hit(row.mu_lampx, "F", winner),
                     "hit_nowx": gamma_hit(row.mu_nowx, "F", winner),
                     "hit_cityx": gamma_hit(row.mu_cityx, "F", winner),
                     "top2_lampx": top2_hit(row.mu_lampx, SIGMA_F[row.station], "F", winner),
                     "top2_nowx": top2_hit(row.mu_nowx, SIGMA_F[row.station], "F", winner),
                     "top2_cityx": top2_hit(row.mu_cityx, SIGMA_F[row.station], "F", winner)})
    result = pd.DataFrame(rows)
    if result.empty:
        write_status(state="NO_RESOLUTIONS", captured_markets=len(data), resolved_markets=0,
                     resolved_days=0, resolved_coverage=0.0,
                     decision_ready=0, lamp_decision="PENDING", now_decision="PENDING")
        print(f"{VERSION}: {len(data)} capturas, 0 mercados resueltos")
        return
    result.to_csv(OUT, index=False)
    days = result.target.nunique()
    eligible = data[data.target <= result.target.max()]
    coverage = len(result)/len(eligible) if len(eligible) else 0.0
    exact, base = result.hit_lampx.mean(), result.hit_cityx.mean()
    top2, base_top2 = result.top2_lampx.mean(), result.top2_cityx.mean()
    p_value, interval = bootstrap(result)
    lamp_passed = gate(exact, base, top2, base_top2, p_value, days, coverage)
    now_exact, now_top2 = result.hit_nowx.mean(), result.top2_nowx.mean()
    now_p, now_interval = bootstrap_now(result)
    now_passed = now_gate(lamp_passed, now_exact, exact, now_top2, top2, now_p, days, coverage)
    ready = days >= GATE_DAYS and coverage >= MIN_RESOLVED_COVERAGE
    state = gate_state(days, coverage, lamp_passed, now_passed)
    write_status(state=state, captured_markets=len(data), resolved_markets=len(result),
        resolved_days=days, resolved_coverage=coverage,
        exact_cityx=base, exact_lampx=exact, exact_lamp_delta=exact-base,
        top2_cityx=base_top2, top2_lampx=top2, lamp_p_value=p_value,
        lamp_ci90_low=interval[0], lamp_ci90_high=interval[1],
        exact_nowx=now_exact, exact_now_delta=now_exact-exact,
        top2_nowx=now_top2, now_p_value=now_p,
        now_ci90_low=now_interval[0], now_ci90_high=now_interval[1],
        decision_ready=int(ready),
        lamp_decision=("ADOPT" if lamp_passed else "REJECT") if ready else "PENDING",
        now_decision=(("ADOPT" if now_passed else "REJECT") if ready and lamp_passed
                      else ("REJECT_PARENT" if ready else "PENDING")))

    print(f"{VERSION}: {len(result)} mercados/{days} días; exacto {base:.1%} CITYX -> "
          f"{exact:.1%} LAMPX ({exact-base:+.1%}); top2 {base_top2:.1%} -> {top2:.1%}; "
          f"p={p_value:.4f}, CI90 [{interval[0]:+.1%},{interval[1]:+.1%}], "
          f"cobertura resuelta={coverage:.1%}.")
    if days < GATE_DAYS:
        print(f"Gate: {days}/{GATE_DAYS} días; no decidir antes.")
    elif coverage < MIN_RESOLVED_COVERAGE:
        print(f"Gate: {days} días pero cobertura {coverage:.1%} < "
              f"{MIN_RESOLVED_COVERAGE:.0%}; esperar resoluciones Gamma.")
    else:
        print(f"Gate cobertura>={MIN_RESOLVED_COVERAGE:.0%}, exacto>39.6%, delta>0, "
              "top2 no baja, p<0.05 -> " + ("ADOPTAR" if lamp_passed else "NO adoptar"))

    print(f"{NOW_VERSION}: exacto {exact:.1%} LAMPX -> {now_exact:.1%} NOW "
          f"({now_exact-exact:+.1%}); top2 {top2:.1%} -> {now_top2:.1%}; "
          f"p={now_p:.4f}, CI90 [{now_interval[0]:+.1%},{now_interval[1]:+.1%}].")
    if days < GATE_DAYS:
        print(f"Gate jerárquico NOW: {days}/{GATE_DAYS} días; no decidir antes.")
    elif coverage < MIN_RESOLVED_COVERAGE:
        print("Gate jerárquico NOW: esperar cobertura Gamma suficiente.")
    else:
        print("Gate NOW requiere primero LAMP=ADOPTAR, delta>0, top2 no baja, p<0.05 -> " +
              ("ADOPTAR" if now_passed else "NO adoptar"))


if __name__ == "__main__":
    main()
