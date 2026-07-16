#!/usr/bin/env python3
# scripts/fetch_lab_m8.py — Cache point-in-time de los 8 modelos Previous-Runs (3 base + 5 extra)
# para el estudio por-ciudad de combinaciones (pedido Santiago 2026-07-12). Rango 2026-02-10..ayer:
# warm-up de 60d para que el bias rolling este caliente al inicio de la eval de 90 dias (04-12).
# Leads 2/3 SOLO (previous_day1/2) — NUNCA temperature_2m (nowcast bug #5).
# Salida: data/lab_m8.csv (station,target,model,lead,m). Reanudable: salta pares ya completos.
import os
import sys
import datetime as dt
import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from show_live import STATIONS, PREV_RUNS, daily_tmax   # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "lab_m8.csv")
D0 = dt.date(2026, 2, 10)
D1 = dt.date.today() - dt.timedelta(days=1)
LEAD_COL = {2: "temperature_2m_previous_day1", 3: "temperature_2m_previous_day2"}
MODELS_OM = {"gefs": "gfs_seamless", "ecmwf": "ecmwf_ifs025", "icon": "icon_seamless",
             "meteofrance": "meteofrance_seamless", "gem": "gem_seamless",
             "ukmo": "ukmo_seamless", "jma": "jma_seamless", "knmi": "knmi_seamless"}


def main():
    done = set()
    rows_prev = []
    if os.path.exists(OUT):
        prev = pd.read_csv(OUT)
        # par completo = cubre hasta D1 (los targets del final pueden faltar por huecos del modelo:
        # exigimos el 90% del rango para no re-bajar eternamente pares con huecos reales)
        for (st, mo), g in prev.groupby(["station", "model"]):
            if len(g) >= 0.9 * 2 * ((D1 - D0).days + 1) or g.target.max() >= (D1 - dt.timedelta(days=2)).isoformat():
                done.add((st, mo))
        rows_prev = [prev[prev.set_index(["station", "model"]).index.isin(done)]]
        print(f"cache previo: {len(prev)} filas, {len(done)} pares completos (se conservan)")
    rows = []
    todo = [(c, m) for c in STATIONS for m in MODELS_OM if (c, m) not in done]
    for i, (code, model) in enumerate(todo, 1):
        lat, lon, off, unit = STATIONS[code]
        p = dict(latitude=lat, longitude=lon, models=MODELS_OM[model],
                 hourly=",".join(LEAD_COL.values()),
                 start_date=(D0 - dt.timedelta(days=1)).isoformat(),
                 end_date=(D1 + dt.timedelta(days=1)).isoformat(), timezone="UTC",
                 temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
        try:
            r = requests.get(PREV_RUNS, params=p, timeout=120); r.raise_for_status()
            h = r.json()["hourly"]
        except Exception as e:
            print(f"[WARN] {code} {model}: {e}", file=sys.stderr); continue
        n0 = len(rows)
        for lead, col in LEAD_COL.items():
            if col not in h:
                continue
            for d, mval in daily_tmax(h["time"], h[col], off).items():
                if D0 <= d <= D1 and mval is not None:
                    rows.append([code, d.isoformat(), model, lead, round(mval, 2)])
        print(f"  [{i}/{len(todo)}] {code} {model}: +{len(rows)-n0}")
    new = pd.DataFrame(rows, columns=["station", "target", "model", "lead", "m"])
    out = pd.concat(rows_prev + [new], ignore_index=True) if rows_prev else new
    out = out.drop_duplicates(["station", "target", "model", "lead"], keep="last")
    out = out.sort_values(["station", "model", "lead", "target"])
    out.to_csv(OUT, index=False)
    print(f"total {len(out)} filas -> {OUT} ({D0}..{D1})")


if __name__ == "__main__":
    main()
