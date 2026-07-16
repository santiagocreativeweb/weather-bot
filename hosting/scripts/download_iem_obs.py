#!/usr/bin/env python3
"""Download station daily maxima into data/obs.csv.

Fahrenheit markets use raw hourly ASOS because IEM's computed daily endpoint
does not reproduce WU/Gamma settlement. Celsius stations retain IEM daily.
"""
import argparse
import csv
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wxbt.observations import fetch_iem_maxima  # noqa: E402


NETWORKS = {
    "KLGA": "NY_ASOS", "KORD": "IL_ASOS", "EGLC": "GB__ASOS",
    "LFPB": "FR__ASOS", "RJTT": "JP__ASOS", "RKSI": "KR__ASOS",
    "ZSPD": "CN__ASOS", "ZBAA": "CN__ASOS", "RCSS": "TW__ASOS",
    "LEMD": "ES__ASOS", "EDDM": "DE__ASOS", "LIMC": "IT__ASOS",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="data/obs.csv")
    a = ap.parse_args()
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    rows = []
    for station, network in NETWORKS.items():
        unit = "F" if station.startswith("K") else "C"
        try:
            maxima = fetch_iem_maxima(station, network, start, end, unit)
        except Exception as exc:
            print(f"[WARN] {station}: {exc}", file=sys.stderr)
            continue
        for day, value in sorted(maxima.items()):
            rows.append([station, day.isoformat(), round(value, 2),
                         int(math.floor(value + 0.5))])
    with open(a.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station", "date", "tmax", "tmax_int"])
        writer.writerows(rows)
    print(f"escrito {a.out}: {len(rows)} filas")
    print("°F usa METAR horario local; °C usa IEM daily. Gamma sigue siendo verdad de pago.")


if __name__ == "__main__":
    main()
