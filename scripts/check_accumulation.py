#!/usr/bin/env python3
# scripts/check_accumulation.py — validate_world() PARA EL PROCESO de acumulacion forward.
# [Creado 2026-07-08.] Correr SEMANAL (no a los 90 dias, cuando ya no hay tiempo de corregir).
#
# POR QUE: un accumulator idempotente y append-only NO garantiza que corrio cada dia. Si el 9-jul
# fallo silencioso y te enteras el 6-oct, no distingues "90 dias de data" de "60 dias con 30 huecos".
# Este check valida FORMA, no solo existencia: dias presentes (sin gaps), cobertura de ciudades/
# estaciones, y rangos plausibles (hs_eff, s2). Falla RUIDOSO (exit!=0) para que el scheduler alerte.
#
# USO: python scripts/check_accumulation.py --through YYYY-MM-DD   (fecha hasta la que se espera data)
# Exit 0 = OK; exit 1 = hay huecos/anomalias (revisar data/accumulator.log).
import argparse, sys
import datetime as dt
import pandas as pd

BOOKS = "data/books_forward.csv"
ENSEMBLE = "data/ensemble_forward.csv"
PRED = "data/predictions_forward.csv"
MODELS_FORWARD = "data/models_forward.csv"
LOG = "data/accumulator.log"
CITIES = {"nyc", "chicago", "london", "paris", "tokyo", "seoul"}
STATIONS = {"KLGA", "KORD", "EGLC", "LFPB", "RJTT", "RKSI"}
START = dt.date(2026, 7, 8)   # 1er snapshot forward
MODELS_START = dt.date(2026, 7, 12)


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def check_days(name, dates_present, through, issues, start=START):
    expected = set(daterange(start, through)) if through >= start else set()
    missing = sorted(expected - dates_present)
    if missing:
        issues.append(f"[{name}] {len(missing)} dias SIN snapshot: "
                      f"{', '.join(str(d) for d in missing[:8])}{' ...' if len(missing) > 8 else ''}")
    else:
        print(f"[{name}] OK: {len(expected)} dias presentes, sin huecos ({start}..{through})")


def main(a):
    through = dt.date.fromisoformat(a.through)
    issues = []

    # --- books ---
    try:
        b = pd.read_csv(BOOKS, parse_dates=["snapshot_date"])
        b["d"] = b.snapshot_date.dt.date
        check_days("books", set(b.d.unique()), through, issues)
        # cobertura de ciudades por dia (algunas pueden faltar legitimamente si no hay mercado en
        # ventana ese dia; se AVISA si <5 de 6, se marca ISSUE si <=3 sostenido)
        cov = b.groupby("d").city.nunique()
        low = cov[cov <= 6]
        if len(low):
            issues.append(f"[books] {len(low)} dias con <=6 ciudades (esperadas 12): "
                          f"{', '.join(str(d) for d in low.index[:6])}")
        # rangos plausibles de hs_eff (dato central del check): fuera de [-0.02, 0.5] = sospechoso
        hs = b.hs_eff_40.dropna()
        bad = hs[(hs < -0.02) | (hs > 0.5)]
        if len(bad):
            issues.append(f"[books] {len(bad)} hs_eff_40 fuera de rango plausible [-0.02,0.5]")
        med = hs.median() if len(hs) else float("nan")
        # book_json presente (el dato irreconstruible)
        if "book_json" not in b.columns or b.book_json.isna().any():
            issues.append("[books] filas sin book_json (book crudo faltante — irreconstruible)")
        print(f"[books] filas={len(b)}  hs_eff_40 mediano={med:.3f} (break-even ~0.06)  "
              f"cobertura ciudades/dia: min={cov.min()} med={int(cov.median())}")
    except FileNotFoundError:
        issues.append(f"[books] {BOOKS} no existe — el accumulator nunca escribio")

    # --- ensemble ---
    try:
        e = pd.read_csv(ENSEMBLE, parse_dates=["snapshot_date"])
        e["d"] = e.snapshot_date.dt.date
        check_days("ensemble", set(e.d.unique()), through, issues)
        cov = e.groupby("d").station.nunique()
        low = cov[cov <= 6]
        if len(low):
            issues.append(f"[ensemble] {len(low)} dias con <=6 estaciones (esperadas 12)")
        s2 = e.s2_real.dropna()
        bad = s2[(s2 <= 0) | (s2 > 50)]   # varianza en (0, ~50] grados^2; fuera = sospechoso
        if len(bad):
            issues.append(f"[ensemble] {len(bad)} s2_real fuera de rango plausible (0,50]")
        print(f"[ensemble] filas={len(e)}  s2_real mediano={s2.median():.2f}  "
              f"cobertura estaciones/dia: min={cov.min()} med={int(cov.median())}")
    except FileNotFoundError:
        issues.append(f"[ensemble] {ENSEMBLE} no existe — el accumulator nunca escribio")

    # --- predicciones ---
    try:
        pr = pd.read_csv(PRED, parse_dates=["snapshot_date"])
        pr["d"] = pr.snapshot_date.dt.date
        check_days("predictions", set(pr.d.unique()), through, issues)
        cov = pr.groupby("d").station.nunique()
        low = cov[cov <= 6]
        if len(low):
            issues.append(f"[predictions] {len(low)} dias con <=6 estaciones (esperadas hasta 12)")
        # sigma calibrada debe ser positiva y plausible (0, ~10]; mu no-nulo
        bad = pr.sigma_cal.dropna()
        bad = bad[(bad <= 0) | (bad > 10)]
        if len(bad):
            issues.append(f"[predictions] {len(bad)} sigma_cal fuera de rango plausible (0,10]")
        print(f"[predictions] filas={len(pr)}  sigma_cal mediano={pr.sigma_cal.median():.2f}  "
              f"cobertura estaciones/dia: min={cov.min()} med={int(cov.median())}")
    except FileNotFoundError:
        issues.append(f"[predictions] {PRED} no existe — el accumulator nunca escribio")

    # --- ocho modelos deterministas point-in-time (MED8/W8 shadow) ---
    if through >= MODELS_START:
        try:
            mf = pd.read_csv(MODELS_FORWARD, parse_dates=["capture_utc"])
            # El wrapper usa fecha calendario Argentina; una corrida nocturna puede caer en el
            # dia UTC siguiente. Convertir antes de chequear huecos evita un falso faltante.
            mf["d"] = mf.capture_utc.dt.tz_convert("America/Argentina/Buenos_Aires").dt.date
            check_days("models_forward", set(mf.d.unique()), through, issues, MODELS_START)
            pairs = mf.groupby("d").apply(
                lambda g: g[["station", "model"]].drop_duplicates().shape[0],
                include_groups=False)
            expected_pairs = mf.station.nunique() * 8
            low = pairs[pairs < expected_pairs]
            if len(low):
                issues.append(f"[models_forward] {len(low)} dias incompletos: minimo "
                              f"{int(pairs.min())}/{expected_pairs} pares estacion-modelo")
            bad = mf.tmax.dropna()
            bad = bad[(bad < -100) | (bad > 150)]
            if len(bad):
                issues.append(f"[models_forward] {len(bad)} tmax fuera de rango [-100,150]")
            print(f"[models_forward] filas={len(mf)} pares/dia min={int(pairs.min())} "
                  f"esperados={expected_pairs}")
        except FileNotFoundError:
            issues.append(f"[models_forward] {MODELS_FORWARD} no existe")

    # --- log de corridas: los OK del log deben cubrir los dias esperados ---
    try:
        with open(LOG) as f:
            logged = {ln.split(" | ")[2].strip() for ln in f if " | OK | " in ln}
        miss = sorted(str(d) for d in daterange(START, through) if str(d) not in logged)
        if miss:
            issues.append(f"[log] {len(miss)} dias sin corrida OK registrada: "
                          f"{', '.join(miss[:8])}{' ...' if len(miss) > 8 else ''}")
    except FileNotFoundError:
        issues.append(f"[log] {LOG} no existe — sin registro de corridas")

    print("-" * 60)
    if issues:
        print(f"CHECK FALLIDO — {len(issues)} problema(s):")
        for i in issues:
            print("  ! " + i)
        sys.exit(1)
    print(f"CHECK OK — acumulacion integra {START}..{through}. Faltan "
          f"{(dt.date(2026, 10, 6) - through).days} dias para el hito de validacion (~2026-10-06).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Chequeo semanal de completitud de la acumulacion forward.")
    ap.add_argument("--through", required=True, help="fecha hasta la que se espera data YYYY-MM-DD")
    main(ap.parse_args())
