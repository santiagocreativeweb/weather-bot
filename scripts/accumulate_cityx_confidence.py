#!/usr/bin/env python3
"""Capture the frozen CITYCONF1 spread gate from point-in-time CITYX2 inputs."""
import csv
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wxbt.cityx_confidence import (PARENT_VERSION, SHADOW0, VERSION,  # noqa: E402
                                    is_selected, spread_buckets)
from wxbt.exact_selector import RECIPES  # noqa: E402


D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "cityx_confidence_forward.csv")
MODELS = {"gfs13", "ecmwf", "aifs", "icon", "arpege", "ukmo", "jma", "cma"}


def build_rows(exact, models):
    exact = exact.copy(); models = models.copy()
    exact["capture_utc"] = pd.to_datetime(exact.capture_utc, utc=True)
    models["capture_utc"] = pd.to_datetime(models.capture_utc, utc=True)
    exact["target_d"] = pd.to_datetime(exact.target).dt.date
    models["target_d"] = pd.to_datetime(models.target).dt.date
    start = pd.Timestamp(SHADOW0).date()
    exact = exact[(exact.version == PARENT_VERSION) & (exact.target_d >= start)]
    models = models[models.model.isin(MODELS)]
    rows = []
    for r in exact.itertuples(index=False):
        group = models[(models.station == r.station) & (models.target_d == r.target_d) &
                       (models.capture_utc == r.capture_utc)]
        group = group.sort_values("model").drop_duplicates("model", keep="last")
        if len(group) < 3:
            continue
        spread = spread_buckets(group.tmax.to_numpy(), r.unit)
        rows.append(dict(capture_utc=r.capture_utc.isoformat(), station=r.station,
                         target=r.target_d.isoformat(), version=VERSION,
                         parent_version=PARENT_VERSION, unit=r.unit, mu=float(r.mu),
                         spread_buckets=round(spread, 6), selected=int(is_selected(spread)),
                         n_models=len(group), recipe=RECIPES[r.station],
                         freeze_utc=r.freeze_utc))
    return rows


def main():
    exact_path = os.path.join(D, "exact_selector_forward.csv")
    models_path = os.path.join(D, "models_forward.csv")
    if not os.path.exists(exact_path) or not os.path.exists(models_path):
        print(f"{VERSION}: faltan snapshots CITYX/models_forward")
        return
    rows = build_rows(pd.read_csv(exact_path), pd.read_csv(models_path))
    done = set()
    if os.path.exists(OUT):
        old = pd.read_csv(OUT)
        done = set(zip(old.station, old.target.astype(str), old.capture_utc.astype(str), old.version))
    rows = [row for row in rows if (row["station"], row["target"], row["capture_utc"],
                                    row["version"]) not in done]
    if rows:
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            if new:
                writer.writeheader()
            writer.writerows(rows)
    selected = sum(row["selected"] for row in rows)
    print(f"{VERSION}: +{len(rows)} snapshots ({selected} seleccionados) -> {OUT}")


if __name__ == "__main__":
    main()
