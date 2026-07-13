"""Observed maxima with an oracle-compatible Fahrenheit path.

IEM's computed daily endpoint is not equivalent to the WU/Gamma settlement
chain for US ASOS sites. Hourly ASOS ``tmpf`` reproduced settled Fahrenheit
buckets at 98.4--100% in the audited sample, so Fahrenheit markets use the
raw hourly archive grouped in the station's local timezone.
"""
import csv
import datetime as dt
import io

import requests


US_F_TZ = {
    "KLGA": "America/New_York", "KORD": "America/Chicago",
    "KMIA": "America/New_York", "KSFO": "America/Los_Angeles",
    "KLAX": "America/Los_Angeles", "KDAL": "America/Chicago",
    "KATL": "America/New_York", "KHOU": "America/Chicago",
    "KAUS": "America/Chicago",
}
ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DAILY_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"


def parse_asos_daily_max(text):
    """Parse ASOS CSV whose timestamps are already in station-local time."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        value = row.get("tmpf")
        if not value or value in ("M", "None", "null"):
            continue
        try:
            day = dt.datetime.fromisoformat(row["valid"].replace(" ", "T")).date()
            temp = float(value)
        except (KeyError, TypeError, ValueError):
            continue
        out[day] = max(out.get(day, temp), temp)
    return out


def parse_iem_daily_max(text, unit):
    out = {}
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 2:
        return out
    for row in csv.DictReader(io.StringIO("\n".join(lines))):
        value = row.get("max_temp_f")
        if not value or value in ("M", "None", "null"):
            continue
        try:
            tf = float(value)
            out[dt.date.fromisoformat(row["day"])] = tf if unit == "F" else (tf - 32) * 5 / 9
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_iem_maxima(station, network, start, end, unit, timeout=120):
    """Return inclusive local-date maxima, using raw hourly ASOS for °F."""
    if unit == "F":
        timezone = US_F_TZ.get(station)
        if timezone is None:
            raise ValueError(f"missing local timezone for Fahrenheit station {station}")
        exclusive_end = end + dt.timedelta(days=1)
        params = dict(network=network, station=station[1:] if station.startswith("K") else station,
                      data="tmpf", year1=start.year, month1=start.month, day1=start.day,
                      year2=exclusive_end.year, month2=exclusive_end.month, day2=exclusive_end.day,
                      tz=timezone, format="onlycomma", latlon="no", elev="no", missing="M",
                      trace="T", direct="yes", report_type=[3, 4])
        response = requests.get(ASOS_URL, params=params,
                                headers={"User-Agent": "wxbt-observations/1.0"}, timeout=timeout)
        response.raise_for_status()
        return parse_asos_daily_max(response.text)

    params = dict(network=network, stations=station[1:] if station.startswith("K") else station,
                  var="max_temp_f", year1=start.year, month1=start.month, day1=start.day,
                  year2=end.year, month2=end.month, day2=end.day, format="csv")
    response = requests.get(DAILY_URL, params=params, timeout=timeout)
    response.raise_for_status()
    return parse_iem_daily_max(response.text, unit)
