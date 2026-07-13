#!/usr/bin/env python3
"""Download authoritative resolved Gamma winners without touching price history."""
import argparse
import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from show_live import CITY_SERIES, CITY_STATION, STATIONS  # noqa: E402
from wxbt.exact_selector import CITYX1_RECIPES  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
OUT = "data/gamma_labels.csv"


def outcome_yes(market):
    value = market.get("outcomePrices")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    try:
        return float(value[0])
    except (TypeError, ValueError, IndexError):
        return None


def series_events(series_id, attempts=4):
    events = []
    for offset in range(0, 1000, 100):
        for attempt in range(attempts):
            response = requests.get(f"{GAMMA}/events", params={"series_id": series_id,
                "closed": "true", "limit": 100, "offset": offset}, timeout=60)
            if response.status_code == 429:
                time.sleep(2**attempt); continue
            response.raise_for_status(); break
        batch = response.json()
        if not batch:
            break
        events.extend(batch)
        time.sleep(.08)
    return events


def main(args):
    station_to_city = {station: city for city, station in CITY_STATION.items()}
    selected = sorted(set(STATIONS)-set(CITYX1_RECIPES)) if args.new_only else sorted(STATIONS)
    rows = []
    for i, station in enumerate(selected, 1):
        city = station_to_city[station]; series_id = CITY_SERIES[city]
        for event in series_events(series_id):
            target = (event.get("endDate") or "")[:10]
            if not target or not (args.start <= target <= args.end):
                continue
            winners = []
            for market in event.get("markets", []):
                yes = outcome_yes(market)
                resolved = (bool(event.get("closed")) or bool(market.get("closed")) or
                    str(market.get("umaResolutionStatus") or "").lower() == "resolved")
                if resolved and yes is not None and yes >= .99:
                    winners.append(market.get("groupItemTitle"))
            if len(winners) != 1:
                continue
            rules = " ".join(str(event.get(k) or "") for k in ("description", "resolutionSource"))
            aliases = {station.lower(), station.lower().lstrip("k")}
            rows.append(dict(target=target, station=station, city=city,
                unit=STATIONS[station][3], win_mkt=winners[0], event_slug=event.get("slug"),
                event_id=event.get("id"), rules_station_mentioned=int(
                    any(alias and alias in rules.lower() for alias in aliases))))
        print(f"Gamma labels {i}/{len(selected)}: {station}")
    out = pd.DataFrame(rows).sort_values(["target", "station"]).drop_duplicates(
        ["target", "station"], keep="last")
    out.to_csv(OUT, index=False)
    print(f"Gamma labels -> {OUT}: {len(out)} ganadores, {out.station.nunique()} estaciones, "
          f"{out.target.min()}..{out.target.max()}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-04-13"); p.add_argument("--end", default="2026-07-11")
    p.add_argument("--new-only", action="store_true")
    main(p.parse_args())
