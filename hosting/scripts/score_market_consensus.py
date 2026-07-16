#!/usr/bin/env python3
"""Score the pre-registered MKTWX1 shadow against resolved Gamma buckets."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from check_predictions import resolved_buckets  # noqa: E402
from lab_single_runs import bootstrap_day  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")


def tup(lo, hi):
    return (None if pd.isna(lo) else float(lo), None if pd.isna(hi) else float(hi))


def main():
    path = os.path.join(D, "market_consensus_forward.csv")
    if not os.path.exists(path):
        print("MKTWX1: sin picks forward"); return
    x = pd.read_csv(path); x["target"] = pd.to_datetime(x.target).dt.date
    info = resolved_buckets(list(x[["station", "target"]].itertuples(index=False, name=None)))
    rows = []
    for r in x.itertuples(index=False):
        winner = info.get((r.station, r.target), {}).get("winner")
        if winner is None:
            continue
        chosen, second, bot = tup(r.chosen_lo, r.chosen_hi), tup(r.second_lo, r.second_hi), tup(r.bot_lo, r.bot_hi)
        rows.append(dict(station=r.station, d=r.target, version=r.version,
            hit_base=int(bot == winner), hit=int(chosen == winner), top2=int(winner in (chosen, second))))
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(D, "market_consensus_scores.csv"), index=False)
    if out.empty:
        print(f"MKTWX1: {len(x)} picks, ninguno resuelto"); return
    delta = out.hit.mean()-out.hit_base.mean(); days = out.d.nunique()
    print(f"MKTWX1: {len(out)} mercados/{days} días; exacto {out.hit_base.mean():.1%} -> "
          f"{out.hit.mean():.1%} ({delta:+.1%}); top2 {out.top2.mean():.1%}")
    if days >= 45:
        p, ci = bootstrap_day(out)
        verdict = "ADOPTAR" if delta > 0 and p < .05 else "NO adoptar"
        print(f"Gate forward p={p:.4f}, CI90 [{ci[0]:+.1%},{ci[1]:+.1%}] -> {verdict}")
    else:
        print(f"Gate forward: {days}/45 días; no decidir antes.")


if __name__ == "__main__":
    main()
