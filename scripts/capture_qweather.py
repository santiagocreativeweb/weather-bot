#!/usr/bin/env python3
# scripts/capture_qweather.py — Capturador FORWARD de QWeather para ZBAA (Beijing) y ZSPD (Shanghai).
# [Creado 2026-07-11.] Unica fuente puntual decente para China. Free tier 50k req/mes.
#
# CREDENCIAL: data/.qweather_key (json con api_key + api_host). El host es DEDICADO por cuenta
# (console.qweather.com -> Settings -> API Host); sin el host correcto todo devuelve 403.
# Endpoint: GET https://{host}/v7/weather/7d?location=LON,LAT  (header X-QW-Api-Key).
# daily[].fxDate + tempMax (°C, dia local de la ciudad). avail = instante de fetch (invariante #2);
# se guarda updateTime del payload como corrida. Sin archivo historico -> forward only.
# USO: python scripts/capture_qweather.py --date YYYY-MM-DD   (varias veces/dia; idempotente)
import argparse, csv, json, os, sys
import datetime as dt
import requests

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
KEYFILE = os.path.join(D, ".qweather_key")
OUT = os.path.join(D, "qweather_forward.csv")
LOG = os.path.join(D, "accumulator.log")
STATIONS_QW = {"ZBAA": "116.59,40.08", "ZSPD": "121.81,31.15"}   # lon,lat (formato QWeather)
HORIZON_DAYS = 3
TMAX_SANE = (-10.0, 48.0)


def log_run(script, snapshot, status, detail):
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def load_cred():
    c = json.load(open(KEYFILE, encoding="utf-8"))
    host = c.get("api_host", "")
    if not host or "PEGAR" in host or " " in host:
        raise ValueError("api_host sin configurar en data/.qweather_key "
                         "(console.qweather.com -> Settings -> API Host)")
    return host.strip().rstrip("/"), c["api_key"].strip()


def fetch(host, key, loc):
    r = requests.get(f"https://{host}/v7/weather/7d", params={"location": loc},
                     headers={"X-QW-Api-Key": key}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("code") not in ("200", 200):
        raise ValueError(f"QWeather code={j.get('code')}")
    upd = dt.datetime.fromisoformat(j["updateTime"]).astimezone(dt.timezone.utc)
    rows = []
    for day in j.get("daily", []):
        v = day.get("tempMax")
        if v in (None, ""):
            continue
        tmx = float(v)
        if not (TMAX_SANE[0] <= tmx <= TMAX_SANE[1]):
            continue
        rows.append((dt.date.fromisoformat(day["fxDate"]), tmx))
    if not rows:
        raise ValueError("respuesta sin daily/tempMax")
    return upd, rows


def main(a):
    today = dt.date.fromisoformat(a.date)
    try:
        host, key = load_cred()
    except Exception as e:
        print(f"[SKIP] credencial QWeather: {e}", file=sys.stderr)
        log_run("qweather", a.date, "SKIP", str(e)); sys.exit(1)
    seen = set()
    if os.path.exists(OUT) and os.path.getsize(OUT) > 0:
        with open(OUT) as f:
            for r in csv.DictReader(f):
                seen.add((r["update_utc"], r["station"]))
    rows_out, errs, skipped = [], 0, 0
    for code, loc in STATIONS_QW.items():
        cap = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        try:
            upd, rows = fetch(host, key, loc)
        except Exception as e:
            print(f"[WARN] {code}: {e}", file=sys.stderr); errs += 1; continue
        upd_iso = upd.isoformat(timespec="seconds")
        if (upd_iso, code) in seen:
            skipped += 1; continue
        for tgt, tmx in rows:
            if today <= tgt <= today + dt.timedelta(days=HORIZON_DAYS):
                rows_out.append([cap, upd_iso, code, tgt.isoformat(), f"{tmx:.1f}"])
    if rows_out:
        new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
        with open(OUT, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["capture_utc", "update_utc", "station", "target", "tmax_c"])
            w.writerows(rows_out)
    status = "OK" if rows_out else ("SKIP" if skipped and not errs else "WARN")
    print(f"+{len(rows_out)} filas a {OUT} ({skipped} corridas ya capturadas, {errs} errores).")
    log_run("qweather", a.date, status, f"rows={len(rows_out)} skipped={skipped} errores={errs}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Captura forward QWeather (ZBAA/ZSPD).")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    if not a.date:
        a.date = dt.date.today().isoformat()
    main(a)
