#!/usr/bin/env python3
# scripts/capture_smn.py — Capturador FORWARD del pronostico OFICIAL del SMN argentino para
# SAEZ (Buenos Aires / Ezeiza). [Creado 2026-07-13, investigacion fuentes AR de Santiago.]
#
# El SMN publica su pronostico oficial (temp_max/temp_min por dia, 8 dias) en la API interna
# ws1.smn.gob.ar/v1 con JWT. El token (TTL 1h) esta embebido en el HTML del sitio; se scrapea
# de https://ws2.smn.gob.ar/ (mismo Drupal que www pero SIN el challenge de Cloudflare).
# location_id 4841 = "Jose Maria Ezeiza" (estacion 87576 = SAEZ, la de resolucion del mercado).
#
# SIN ARCHIVO HISTORICO -> forward only (igual que CWA/JMA/QWeather). avail = instante de
# captura (invariante #2); se guarda `updated` del payload como id de emision (idempotencia).
# El WRF 4km del SMN NO necesita capturador: tiene archivo point-in-time en s3://smn-ar-wrf
# con LastModified real (ver scripts/lab_smn_wrf.py).
#
# API interna sin SLA: si el scrape del token o el schema cambian, degradar con WARN (no romper
# la cadena del acumulador). USO: python scripts/capture_smn.py --date YYYY-MM-DD (idempotente).
import argparse
import csv
import datetime as dt
import os
import re
import sys

import requests

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT = os.path.join(D, "smn_forward.csv")
LOG = os.path.join(D, "accumulator.log")
LOCATION_ID = 4841          # Jose Maria Ezeiza -> estacion 87576 (SAEZ)
STATION = "SAEZ"
HORIZON_DAYS = 3
TMAX_SANE = (-10.0, 48.0)
UA = {"User-Agent": "Mozilla/5.0 (wxbt research; contact via repo)"}


def log_run(script, snapshot, status, detail):
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def get_token():
    r = requests.get("https://ws2.smn.gob.ar/", headers=UA, timeout=30)
    r.raise_for_status()
    m = re.search(r"localStorage\.setItem\(['\"]token['\"]\s*,\s*['\"]([^'\"]+)['\"]", r.text)
    if not m:
        raise ValueError("token JWT no encontrado en el HTML de ws2.smn.gob.ar")
    return m.group(1)


def fetch_forecast(token):
    r = requests.get(f"https://ws1.smn.gob.ar/v1/forecast/location/{LOCATION_ID}",
                     headers={**UA, "Authorization": f"JWT {token}"}, timeout=30)
    r.raise_for_status()
    j = r.json()
    upd = j.get("updated") or j.get("update") or ""
    days = j.get("forecast", j)
    if isinstance(days, dict):
        days = days.get("forecast") or days.get("days") or []
    rows = []
    for day in days:
        date = day.get("date")
        tmx = day.get("temp_max")
        if date is None or tmx is None:
            continue
        tmx = float(tmx)
        if not (TMAX_SANE[0] <= tmx <= TMAX_SANE[1]):
            continue
        rows.append((dt.date.fromisoformat(str(date)[:10]), tmx))
    if not rows:
        raise ValueError(f"payload sin forecast/temp_max (keys={list(j)[:8]})")
    return str(upd), rows


def main(a):
    today = dt.date.fromisoformat(a.date)
    try:
        upd, rows = fetch_forecast(get_token())
    except Exception as e:
        print(f"[WARN] SMN: {e}", file=sys.stderr)
        log_run("smn", a.date, "WARN", str(e))
        sys.exit(0)          # fuente sin SLA: no romper la cadena del acumulador
    seen = set()
    if os.path.exists(OUT) and os.path.getsize(OUT) > 0:
        with open(OUT) as f:
            for r in csv.DictReader(f):
                seen.add((r["update_src"], r["station"], r["target"]))
    cap = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows_out = []
    for tgt, tmx in rows:
        if not (today <= tgt <= today + dt.timedelta(days=HORIZON_DAYS)):
            continue
        if (upd, STATION, tgt.isoformat()) in seen:
            continue
        rows_out.append([cap, upd, STATION, tgt.isoformat(), f"{tmx:.1f}"])
    if rows_out:
        new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
        with open(OUT, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["capture_utc", "update_src", "station", "target", "tmax_c"])
            w.writerows(rows_out)
    status = "OK" if rows_out else "SKIP"
    print(f"+{len(rows_out)} filas a {OUT} (emision SMN: {upd})")
    log_run("smn", a.date, status, f"rows={len(rows_out)} updated={upd}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Captura forward del pronostico oficial SMN (SAEZ).")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    if not a.date:
        a.date = dt.date.today().isoformat()
    main(a)
