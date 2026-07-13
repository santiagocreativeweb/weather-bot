#!/usr/bin/env python3
# scripts/score_forward_history.py — Score de los pronosticos walk-forward (lead-2 de
# data/backfill_check.csv) contra los BUCKETS OFICIALES del mercado (win_mkt = ganador Gamma
# ya resuelto) en ventanas de 30 y 45 dias hasta END_DATE.
#
# REGLA DE RESOLUCION (floor real de WU):
#   * WU FLOOREA la observacion (35.9 -> 35 SIEMPRE) -> pick del bot = floor(mu_cal).
#   * prob de bucket floor-consistente = bucket_prob(mu - 0.5, sigma, lo, hi)
#     (wxbt/market.py es half-up; el shift -0.5 lo convierte en floor — NO se toca wxbt/).
#   * Buckets F = pares par-impar alineados (84-85 => lo par); buckets C = 1 grado.
#
# Scoring:
#   * exact_mkt : pick (bucket que contiene floor(mu_cal)) coincide/solapa con win_mkt.
#   * top2/top3 : win_mkt solapa alguno de los k buckets mas probables de la grilla
#     implicita (+-5 buckets alrededor de mu). Nota: si el ganador es cola abierta
#     (">= X"), cualquier bucket de la grilla dentro de la cola cuenta como hit (en el
#     mercado real esos grados caian en el MISMO bucket-cola).
#   * exact_fis : pick coincide con el bucket que contiene floor(max_real) (fisica pura,
#     sin pasar por la resolucion Gamma).
#   * mae/rmse  : sobre mu_cal - max_real (continuo).
#
# CAVEAT (bug #5): el lead-2 del backfill tiene frescura residual (avail optimista) ->
# los NIVELES son optimistas; las comparaciones INTERNAS (entre estaciones/ventanas)
# siguen siendo validas.
#
# Salida: data/score_forward_history.csv + tabla resumen ASCII por stdout.
# Uso: python scripts/score_forward_history.py
import csv
import datetime as dt
import math
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wxbt.market import bucket_prob  # noqa: E402

IN_CSV = "data/backfill_check.csv"
OUT_CSV = "data/score_forward_history.csv"
END_DATE = dt.date(2026, 7, 8)
WINDOWS = {"30d": 30, "45d": 45}
LEAD = "2"
GRID_HALF = 5  # buckets a cada lado del pick
STATION_ORDER = ["KLGA", "KORD", "EGLC", "LFPB", "LEMD", "EDDM",
                 "LIMC", "RJTT", "RKSI", "ZSPD", "ZBAA", "RCSS"]


def parse_win(label):
    """Label del ganador Gamma -> (lo, hi); None = cola abierta. Formatos vistos en el CSV:
    '72-73<deg>F', '<= 95<deg>F', '>= 56<deg>F', '13<deg>C'; defensivo: 'or higher/below'."""
    t = (label or "").strip()
    if not t:
        return None
    nums = [int(x) for x in re.findall(r"\d+", t)]
    if not nums:
        return None
    if t.startswith("<=") or re.search(r"or (below|lower|less)", t, re.I):
        return (None, nums[0])
    if t.startswith(">=") or re.search(r"or (above|higher|more|greater)", t, re.I):
        return (nums[0], None)
    if len(nums) >= 2 and re.search(r"\d+\s*[-–]\s*\d+", t):
        return (nums[0], nums[1])
    return (nums[0], nums[0])


def pick_bucket(t_int, unit):
    """Bucket implicito que contiene el grado entero t_int."""
    if unit == "F":
        lo = t_int - (t_int % 2)  # pares par-impar: lo siempre par
        return (lo, lo + 1)
    return (t_int, t_int)  # C: 1 grado


def bucket_grid(t_int, unit):
    """Grilla de buckets implicitos +-GRID_HALF alrededor del bucket que contiene t_int."""
    lo0, _ = pick_bucket(t_int, unit)
    step = 2 if unit == "F" else 1
    out = []
    for k in range(-GRID_HALF, GRID_HALF + 1):
        lo = lo0 + k * step
        out.append((lo, lo + step - 1))
    return out


def overlaps(b, w):
    """Bucket cerrado b=(lo,hi) solapa al ganador w=(wlo,whi) con colas abiertas None."""
    lo, hi = b
    wlo, whi = w
    return (wlo is None or hi >= wlo) and (whi is None or lo <= whi)


def score_row(r):
    """-> dict con hits/errores de una fila, o None si la fila no es usable."""
    try:
        mu = float(r["mu_cal"])
        sigma = float(r["sigma_cal"])
        mx = float(r["max_real"])
    except (ValueError, KeyError):
        return None
    if math.isnan(mu) or math.isnan(sigma) or math.isnan(mx):
        return None
    unit = r["unit"]
    t_pick = math.floor(mu)
    pk = pick_bucket(t_pick, unit)

    # --- fisica: bucket que contiene floor(max_real)
    fb = pick_bucket(math.floor(mx), unit)
    exact_fis = 1.0 if pk == fb else 0.0

    out = {"err": mu - mx, "exact_fis": exact_fis,
           "exact_mkt": None, "top2": None, "top3": None}

    # --- mercado: ganador Gamma
    w = parse_win(r.get("win_mkt"))
    if w is not None:
        out["exact_mkt"] = 1.0 if overlaps(pk, w) else 0.0
        # ranking floor-consistente: shift -0.5 sobre la grilla implicita
        grid = bucket_grid(t_pick, unit)
        ranked = sorted(grid, key=lambda b: -bucket_prob(mu - 0.5, sigma, b[0], b[1]))
        out["top2"] = 1.0 if any(overlaps(b, w) for b in ranked[:2]) else 0.0
        out["top3"] = 1.0 if any(overlaps(b, w) for b in ranked[:3]) else 0.0
    return out


def aggregate(scored):
    """Lista de dicts score_row -> metricas agregadas."""
    n = len(scored)
    mkt = [s for s in scored if s["exact_mkt"] is not None]
    n_mkt = len(mkt)
    if n == 0:
        return None
    mae = sum(abs(s["err"]) for s in scored) / n
    rmse = math.sqrt(sum(s["err"] ** 2 for s in scored) / n)
    return {
        "n": n,
        "n_mkt": n_mkt,
        "exact_mkt": (sum(s["exact_mkt"] for s in mkt) / n_mkt) if n_mkt else float("nan"),
        "top2": (sum(s["top2"] for s in mkt) / n_mkt) if n_mkt else float("nan"),
        "top3": (sum(s["top3"] for s in mkt) / n_mkt) if n_mkt else float("nan"),
        "mae": mae,
        "rmse": rmse,
        "exact_fis": sum(s["exact_fis"] for s in scored) / n,
    }


def main():
    base = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(base, IN_CSV), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["lead"] == LEAD]
    for r in rows:
        r["_date"] = dt.date.fromisoformat(r["target"])

    results = []  # (window, station, metrics)
    for wname, days in WINDOWS.items():
        start = END_DATE - dt.timedelta(days=days - 1)
        wrows = [r for r in rows if start <= r["_date"] <= END_DATE]
        scored_by_st = {}
        for r in wrows:
            s = score_row(r)
            if s is not None:
                scored_by_st.setdefault(r["station"], []).append(s)
        all_scored = [s for lst in scored_by_st.values() for s in lst]
        m = aggregate(all_scored)
        if m:
            results.append((wname, "GLOBAL", m))
        stations = [st for st in STATION_ORDER if st in scored_by_st]
        stations += sorted(st for st in scored_by_st if st not in STATION_ORDER)
        for st in stations:
            m = aggregate(scored_by_st[st])
            if m:
                results.append((wname, st, m))

    # --- CSV
    out_path = os.path.join(base, OUT_CSV)
    cols = ["station", "window", "n", "n_mkt", "exact_mkt", "top2", "top3",
            "mae", "rmse", "exact_fis"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for wname, st, m in results:
            w.writerow([st, wname, m["n"], m["n_mkt"],
                        round(m["exact_mkt"], 4), round(m["top2"], 4), round(m["top3"], 4),
                        round(m["mae"], 3), round(m["rmse"], 3), round(m["exact_fis"], 4)])
    print("[OK] escrito %s (%d filas)" % (OUT_CSV, len(results)))
    print()

    # --- tabla resumen ASCII
    hdr = "%-8s %-6s %4s %5s | %9s %6s %6s | %6s %6s | %9s" % (
        "station", "win", "n", "n_mkt", "exact_mkt", "top2", "top3", "mae", "rmse", "exact_fis")
    for wname in WINDOWS:
        print("=== ventana %s (hasta %s) ===" % (wname, END_DATE.isoformat()))
        print(hdr)
        print("-" * len(hdr))
        for rw, st, m in results:
            if rw != wname:
                continue
            print("%-8s %-6s %4d %5d | %8.1f%% %5.1f%% %5.1f%% | %6.2f %6.2f | %8.1f%%" % (
                st, rw, m["n"], m["n_mkt"], 100 * m["exact_mkt"], 100 * m["top2"],
                100 * m["top3"], m["mae"], m["rmse"], 100 * m["exact_fis"]))
        print()
    print("CAVEAT bug #5: lead-2 del backfill tiene frescura residual (avail optimista);")
    print("niveles optimistas -> usar para comparaciones internas, no como edge absoluto.")


if __name__ == "__main__":
    main()
