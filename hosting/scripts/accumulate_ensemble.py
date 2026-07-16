#!/usr/bin/env python3
# scripts/accumulate_ensemble.py — Junta el spread REAL de ensemble FORWARD, un dia a la vez.
# [Creado 2026-07-07. Plan paralelo a T1: no bloquea el backtest.]
#
# POR QUE EXISTE: el backtest usa s2 MODELADO (varianza de residuos historicos por modelo/lead,
# ver download_openmeteo.py) porque Open-Meteo free NO archiva ensembles historicos. Este script
# guarda, desde HOY hacia adelante, el spread REAL entre miembros (que si esta disponible en la
# ventana forward de ensemble-api). En ~90 dias habra muestra para responder empiricamente:
#   "el s2 modelado ~ varianza real entre miembros?"  Si difiere sistematicamente -> recalibrar.
#
# USO: correr UNA vez por dia (cron/scheduler). Cada corrida agrega las corridas ensemble vigentes
# hoy para D+1..D+3 de cada estacion. append-only; idempotente por (fecha_snapshot, station, model, target).
#
# Salida data/ensemble_forward.csv: snapshot_date,station,model,target,lead_day,n_members,m_real,s2_real
# (m_real=media entre miembros, s2_real=varianza entre miembros -> comparar contra s2 de forecasts.csv)
import csv, os, sys
import datetime as dt
import requests

ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
STATIONS = {  # = download_openmeteo.py (coords/unidad)  [VERIFICAR-VIVO]
    "KLGA": (40.7794, -73.8803, -5, "F"), "KORD": (41.9786, -87.9048, -6, "F"),
    "EGLC": (51.5050,  0.0553,  0, "C"),  "LFPB": (48.9694,  2.4414,   1, "C"),
    "RJTT": (35.5533, 139.7811, 9, "C"),  "RKSI": (37.4602, 126.4407,  9, "C"),
    "ZSPD": (31.1434, 121.8052, 8, "C"),  "ZBAA": (40.0801, 116.5846,  8, "C"),
    "RCSS": (25.0694, 121.5521, 8, "C"),  "LEMD": (40.4722,  -3.5609,  1, "C"),
    "EDDM": (48.3538,  11.7861, 1, "C"),  "LIMC": (45.6301,   8.7231,  1, "C"),
}
# ids ENSEMBLE (con miembros) — distinto de los determinísticos de previous-runs.
MODELS = {"gefs": "gfs025", "ecmwf": "ecmwf_ifs025", "icon": "icon_seamless_eps"}
OUT = "data/ensemble_forward.csv"
LOG = "data/accumulator.log"   # registro append-only compartido con accumulate_books.py
MIN_MEMBERS = 5   # fail-loud: si la respuesta no trae >=5 miembros, no inventar s2


def log_run(script, snapshot, status, detail):
    """Una linea por corrida (sobrevive reinicios): distingue '90 dias de data' de huecos silenciosos."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def members(hourly):
    return {k: v for k, v in hourly.items() if "_member" in k}


def daily_stats(times, mem_cols, off):
    """Por dia local: tmax de cada miembro -> (media, varianza) entre miembros. Devuelve {date:(m,s2,n)}."""
    per_day = {}   # date -> list de tmax por miembro
    # 1) tmax por (miembro, dia)
    tmax = {}      # (member, date) -> max
    for key, vals in mem_cols.items():
        for t, v in zip(times, vals):
            if v is None:
                continue
            u = dt.datetime.fromisoformat(t) + dt.timedelta(hours=off)
            k = (key, u.date())
            tmax[k] = max(tmax.get(k, -1e9), float(v))
    for (key, d), val in tmax.items():
        per_day.setdefault(d, []).append(val)
    out = {}
    for d, vs in per_day.items():
        if len(vs) < MIN_MEMBERS:
            continue
        m = sum(vs) / len(vs)
        s2 = sum((x - m) ** 2 for x in vs) / (len(vs) - 1)
        out[d] = (m, max(s2, 1e-3), len(vs))
    return out


def main():
    today = dt.date.fromisoformat(SNAPSHOT) if SNAPSHOT else None
    if today is None:
        # Date.now no disponible en algunos entornos; exigir --date explicito para reproducibilidad.
        print("[ABORT] pasar --date YYYY-MM-DD (fecha del snapshot).", file=sys.stderr); sys.exit(1)
    # guard anti doble-corrida (el scheduler desatendido puede disparar 2x): duplicar filas sesgaria
    # el s2. Simetrico con accumulate_books.py. --force para re-agregar a proposito.
    if os.path.exists(OUT) and not FORCE:
        with open(OUT) as f:
            if any(row.startswith(SNAPSHOT + ",") for row in f):
                print(f"[ABORT] ya hay filas para snapshot {SNAPSHOT} en {OUT}. --force para re-agregar.",
                      file=sys.stderr)
                log_run("ensemble", SNAPSHOT, "SKIP", "snapshot ya existe (no re-corri)")
                sys.exit(1)
    rows = []
    for code, (lat, lon, off, unit) in STATIONS.items():
        for model, om in MODELS.items():
            p = dict(latitude=lat, longitude=lon, models=om, hourly="temperature_2m",
                     forecast_days=4, timezone="UTC",
                     temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
            try:
                r = requests.get(ENSEMBLE, params=p, timeout=60); r.raise_for_status()
                h = r.json()["hourly"]
            except Exception as e:
                print(f"[WARN] {code} {model}: {e}", file=sys.stderr); continue
            mem = members(h)
            if len(mem) < MIN_MEMBERS:
                print(f"[WARN] {code} {model}: {len(mem)} miembros (<{MIN_MEMBERS}) -> salto", file=sys.stderr)
                continue
            for d, (m, s2, n) in daily_stats(h["time"], mem, off).items():
                ld = (d - today).days
                if ld < 1 or ld > 3:      # solo D+1..D+3 (horizonte operado)
                    continue
                rows.append([today.isoformat(), code, model, d.isoformat(), ld, n, round(m, 2), round(s2, 3)])
    if not rows:
        print("[WARN] 0 filas este snapshot (API caida?). No agrego nada.", file=sys.stderr)
        log_run("ensemble", today.isoformat(), "WARN", "0 filas (API caida?)")
        return
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["snapshot_date", "station", "model", "target", "lead_day", "n_members", "m_real", "s2_real"])
        w.writerows(rows)
    print(f"+{len(rows)} filas a {OUT} (snapshot {today}). Correr a diario; validar s2 en ~90 dias.")
    log_run("ensemble", today.isoformat(), "OK",
            f"rows={len(rows)} stations={len({r[1] for r in rows})}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="fecha del snapshot YYYY-MM-DD (hoy)")
    ap.add_argument("--force", action="store_true", help="permitir re-agregar el mismo snapshot_date")
    args = ap.parse_args()
    SNAPSHOT, FORCE = args.date, args.force
    main()
