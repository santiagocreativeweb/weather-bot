#!/usr/bin/env python3
# scripts/scout_test12.py -- BACKTEST de nuestros modelos sobre 12 ciudades candidatas (pedido
# Santiago 2026-07-13: "testea todas antes de agregarlas para ver como funcionan con nuestros
# modelos"). Reusa el pipeline EXACTO del scout (consenso lead-2 gefs/ecmwf/icon + sesgo rolling
# 60d walk-forward, regla FLOOR de WU) -> comparacion manzanas-con-manzanas con las 12 actuales.
# Solo lee/descarga; NO integra nada. Salida: data/scout_test12.csv + tabla.
import csv
import os
import sys
import datetime as dt
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scout_cities import (AIRPORTS, fetch_forecasts, fetch_obs_iem, fetch_obs_archive,  # noqa: E402
                          simulate, window_metrics)

# ciudad -> (station, unit). unit por pais (US=F; el resto C). El workflow confirma vs Gamma.
CANDID = {
    "hong-kong": ("HKO", "C"), "san-francisco": ("KSFO", "F"), "los-angeles": ("KLAX", "F"),
    "dallas": ("KDAL", "F"), "atlanta": ("KATL", "F"), "houston": ("KHOU", "F"),
    "toronto": ("CYYZ", "C"), "sao-paulo": ("SBGR", "C"), "austin": ("KAUS", "F"),
    "buenos-aires": ("SAEZ", "C"), "mexico-city": ("MMMX", "C"), "helsinki": ("EFHK", "C"),
}
# baseline: las 12 actuales (mismo pipeline) para la mediana de referencia
BASE = {"nyc": ("KLGA", "F"), "chicago": ("KORD", "F"), "london": ("EGLC", "C"),
        "paris": ("LFPB", "C"), "tokyo": ("RJTT", "C"), "seoul": ("RKSI", "C"),
        "shanghai": ("ZSPD", "C"), "madrid": ("LEMD", "C"), "beijing": ("ZBAA", "C"),
        "munich": ("EDDM", "C"), "taipei": ("RCSS", "C"), "milan": ("LIMC", "C")}
END = dt.date(2026, 7, 11)
FC_START = dt.date(2026, 3, 1)     # warmup 60d de sesgo + eval 60d
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "scout_test12.csv")


def run_city(city, station, unit):
    lat, lon, off, net, sid = AIRPORTS[station]
    fc = fetch_forecasts(station, lat, lon, off, unit, FC_START, END)
    if not fc:
        return None
    obs = fetch_obs_iem(net, sid, FC_START, END, unit)
    src = "iem"
    if len(obs) < 0.6 * (END - FC_START).days:
        arch = fetch_obs_archive(lat, lon, FC_START, END, unit)
        if len(arch) > len(obs):
            obs, src = arch, "archive"
    if not obs:
        return None
    recs = simulate(unit, fc, obs, END, eval_days=60)
    m60 = window_metrics(recs, END, 60)
    m30 = window_metrics(recs, END, 30)
    return dict(city=city, station=station, unit=unit, obs_src=src,
                n=m60["n"], hit=m60["hit_cor"], top2=m60["top2_cor"], top3=m60["top3_cor"],
                mae=m60["mae_cor"], bias=m60["bias_cor"], std=m60["std_cor"], score=m60["score"],
                hit30=m30["hit_cor"], mae30=m30["mae_cor"])


def fmt(v, p=2):
    return "-" if v is None else (f"{v:.{p}f}")


def main():
    print(f"BACKTEST 12 candidatas (nuestros modelos, lead-2 + sesgo 60d, floor). Eval {END-dt.timedelta(days=59)}..{END}\n")
    base_hits = []
    for city, (st, u) in BASE.items():
        try:
            r = run_city(city, st, u)
            if r and r["hit"] is not None:
                base_hits.append(r["hit"])
        except Exception as e:
            print(f"[WARN] baseline {city}: {e}", file=sys.stderr)
    base_med = median(base_hits) if base_hits else float("nan")
    print(f"BASELINE 12 actuales: mediana hit exacto 60d = {base_med:.1%} (n={len(base_hits)})\n")

    rows = []
    print(f"{'ciudad':15}{'est':6}{'u':3}{'src':8}{'n':4} {'exacto':>7} {'top2':>6} {'top3':>6} {'MAE':>6} {'sesgo':>6} {'score':>7}")
    for city, (st, u) in CANDID.items():
        try:
            r = run_city(city, st, u)
        except Exception as e:
            print(f"{city:15}{st:6}{u:3} ERROR {e}", file=sys.stderr); continue
        if not r:
            print(f"{city:15}{st:6}{u:3} sin datos (fc/obs)"); continue
        rows.append(r)
        mark = "+" if (r["hit"] or 0) >= base_med else " "
        print(f"{city:15}{r['station']:6}{u:3}{r['obs_src']:8}{r['n']:<4} "
              f"{fmt(r['hit'],3) if r['hit'] is None else format(r['hit'],'.1%'):>7} "
              f"{format(r['top2'],'.0%') if r['top2'] is not None else '-':>6} "
              f"{format(r['top3'],'.0%') if r['top3'] is not None else '-':>6} "
              f"{fmt(r['mae']):>6} {fmt(r['bias'],1):>6} {fmt(r['score'],1):>7} {mark}")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["city", "station", "unit", "obs_src", "n", "hit60", "top2_60", "top3_60",
                    "mae60", "bias60", "std60", "score60", "hit30", "mae30", "baseline_median_hit"])
        for r in rows:
            w.writerow([r["city"], r["station"], r["unit"], r["obs_src"], r["n"],
                        fmt(r["hit"], 4), fmt(r["top2"], 4), fmt(r["top3"], 4), fmt(r["mae"], 3),
                        fmt(r["bias"], 3), fmt(r["std"], 3), fmt(r["score"], 2),
                        fmt(r["hit30"], 4), fmt(r["mae30"], 3), f"{base_med:.4f}"])
    print(f"\n-> {os.path.relpath(OUT)}")
    print("CAVEAT: frescura lead-2 (bug#5) -> niveles optimistas comunes; el RELATIVO vs baseline")
    print("y entre ciudades es lo valido. HK usa obs archive (HKO no tiene METAR) + resuelve por HK")
    print("Observatory a 1 DECIMAL (no WU floor) -> su numero es el MENOS confiable. SF suele tener")
    print("MAE alto (microclima costero). Peak hours + factores: ver analisis del workflow.")


if __name__ == "__main__":
    main()
