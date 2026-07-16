#!/usr/bin/env python3
# scripts/capture_jma.py — Capturador FORWARD de JMA (Agencia Meteorologica de Japon) para RJTT/Tokio.
# [Creado 2026-07-11.] Fuente oficial gratis sin key. Companion de capture_cwa/capture_mosmix.
#
# QUE DA: www.jma.go.jp/bosai/forecast/data/forecast/130000.json (region Tokyo). El bloque semanal
# trae tempsMax por dia + BANDA de incertidumbre del proveedor (tempsMaxLower/Upper) para el punto
# 44132 (Tokyo/Otemachi). OJO: Otemachi NO es Haneda (RJTT, la estacion de resolucion, costera y que
# pica a media manana). Por eso esto es una SEGUNDA OPINION del proveedor nacional para comparar y
# validar forward — NO se mezcla al mu del bot hasta que la validacion lo justifique.
#
# avail = instante de fetch (invariante #2). Se guarda tambien reportDatetime del payload (= corrida).
# Ciclos JMA: 05/11/17 JST (semanal en 11 y 17). Sin archivo historico -> captura forward.
# USO: python scripts/capture_jma.py --date YYYY-MM-DD   (varias veces/dia; idempotente por corrida)
import argparse, csv, os, sys
import datetime as dt
import requests

URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/130000.json"
AREA = "44132"        # Tokyo / Otemachi (punto con tempsMax + banda)
STATION = "RJTT"
OUT = "data/jma_forward.csv"
LOG = "data/accumulator.log"
HORIZON_DAYS = 3
TMAX_SANE = (-5.0, 45.0)


def log_run(script, snapshot, status, detail):
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def fetch():
    """-> (report_utc, [(target_date_local, tmax, lo, hi), ...]) del bloque semanal de JMA."""
    r = requests.get(URL, timeout=40)
    r.raise_for_status()
    blocks = r.json()
    wk = blocks[-1]        # ultimo bloque = pronostico semanal (tempsMax + banda)
    report = dt.datetime.fromisoformat(wk["reportDatetime"]).astimezone(dt.timezone.utc)
    out = []
    for ts in wk["timeSeries"]:
        a0 = ts["areas"][0]
        if "tempsMax" not in a0:
            continue
        tdef = ts["timeDefines"]
        area = next((a for a in ts["areas"] if a["area"]["code"] == AREA), None)
        if area is None:
            raise ValueError(f"area {AREA} (Tokyo) ausente en el feed JMA")
        tmax = area.get("tempsMax", [])
        lo = area.get("tempsMaxLower", [""] * len(tmax))
        hi = area.get("tempsMaxUpper", [""] * len(tmax))
        for i, t in enumerate(tdef):
            v = tmax[i] if i < len(tmax) else ""
            if v in ("", None):
                continue
            tmx = float(v)
            if not (TMAX_SANE[0] <= tmx <= TMAX_SANE[1]):
                continue
            target = dt.datetime.fromisoformat(t).date()   # 00:00 JST -> dia local
            def num(arr):
                x = arr[i] if i < len(arr) else ""
                return float(x) if x not in ("", None) else None
            out.append((target, tmx, num(lo), num(hi)))
        break   # el primer timeSeries con tempsMax es el semanal completo
    if not out:
        raise ValueError("feed JMA sin tempsMax utilizables")
    return report, out


def main(a):
    today = dt.date.fromisoformat(a.date)
    seen = set()
    if os.path.exists(OUT) and os.path.getsize(OUT) > 0:
        with open(OUT) as f:
            for r in csv.DictReader(f):
                seen.add((r["report_utc"], r["station"]))
    cap = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        report, periods = fetch()
    except Exception as e:
        print(f"[FAIL] JMA: {e}", file=sys.stderr)
        log_run("jma", a.date, "FAIL", str(e)); sys.exit(1)
    rep_iso = report.isoformat(timespec="seconds")
    if (rep_iso, STATION) in seen:
        print(f"[SKIP] corrida JMA {rep_iso} ya capturada.")
        log_run("jma", a.date, "SKIP", f"report={rep_iso} ya capturado"); sys.exit(1)
    rows = [[cap, rep_iso, STATION, tgt.isoformat(), f"{tmx:.1f}",
             ("" if lo is None else f"{lo:.1f}"), ("" if hi is None else f"{hi:.1f}")]
            for tgt, tmx, lo, hi in periods
            if today <= tgt <= today + dt.timedelta(days=HORIZON_DAYS)]
    if not rows:
        print("[WARN] corrida nueva pero sin targets en ventana hoy..+3.")
        log_run("jma", a.date, "WARN", f"report={rep_iso} 0 targets"); return
    new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["capture_utc", "report_utc", "station", "target", "tmax_c", "tmax_lo", "tmax_hi"])
        w.writerows(rows)
    print(f"+{len(rows)} filas a {OUT} (corrida {rep_iso}, targets {rows[0][3]}..{rows[-1][3]}).")
    log_run("jma", a.date, "OK", f"rows={len(rows)} report={rep_iso}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Captura forward del pronostico JMA (Tokyo) para RJTT.")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    if not a.date:
        a.date = dt.date.today().isoformat()
    main(a)
