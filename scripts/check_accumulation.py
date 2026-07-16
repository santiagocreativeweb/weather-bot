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
import numpy as np
import pandas as pd

from dashboard import freeze_utc
from wxbt.exact_selector import (RECIPES as CITYX_RECIPES, SHADOW0 as CITYX_SHADOW0,
                                 VERSION as CITYX_VERSION)
from wxbt.market_consensus import (CUTOFF_HOURS_BEFORE_FREEZE, MAX_PRICE_AGE_H,
                                   SHADOW0, STATIONS as MKT_STATIONS)
from wxbt.cityx_confidence import (MAX_SPREAD_BUCKETS, PARENT_VERSION as CONF_PARENT,
    SHADOW0 as CONF_SHADOW0, VERSION as CONF_VERSION)
from wxbt.lamp_shadow import (AVAIL_LAG_HOURS as LAMP_LAG, NOW_VERSION,
    OFFSETS_F as LAMP_OFFSETS, PARENT_VERSION as LAMP_PARENT, SHADOW0 as LAMP_SHADOW0,
    VERSION as LAMP_VERSION, now_prediction, prediction as lamp_prediction)

BOOKS = "data/books_forward.csv"
ENSEMBLE = "data/ensemble_forward.csv"
PRED = "data/predictions_forward.csv"
MODELS_FORWARD = "data/models_forward.csv"
EXACT_SELECTOR = "data/exact_selector_forward.csv"
MARKET_CONSENSUS = "data/market_consensus_forward.csv"
CITYX_CONFIDENCE = "data/cityx_confidence_forward.csv"
LAMP_SHADOW = "data/lamp_shadow_forward.csv"
LAMP_VERDICT = "data/lamp_shadow_verdict.csv"
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
            # [FIX 2026-07-16] esperado POR DIA con las estaciones de ESE dia (con el nunique
            # global, dar de alta una ciudad — HKO — hacia fallar todos los dias previos al alta).
            st_day = mf.groupby("d").station.nunique()
            low = pairs[pairs < st_day * 8]
            if len(low):
                issues.append(f"[models_forward] {len(low)} dias incompletos (pares < estaciones*8)")
            # una estacion que DESAPARECE (ayer estaba, hoy no) sigue siendo detectable:
            days_sorted = sorted(st_day.index)
            drops = [str(d) for prev, d in zip(days_sorted, days_sorted[1:])
                     if st_day[d] < st_day[prev]]
            if drops:
                issues.append(f"[models_forward] estaciones cayeron en: {', '.join(drops)}")
            expected_pairs = int(st_day.iloc[-1]) * 8
            bad = mf.tmax.dropna()
            bad = bad[(bad < -100) | (bad > 150)]
            if len(bad):
                issues.append(f"[models_forward] {len(bad)} tmax fuera de rango [-100,150]")
            print(f"[models_forward] filas={len(mf)} pares/dia min={int(pairs.min())} "
                  f"esperados={expected_pairs}")
        except FileNotFoundError:
            issues.append(f"[models_forward] {MODELS_FORWARD} no existe")

    # --- CITYX2: one frozen recipe for every station with an eligible model snapshot ---
    cityx0 = dt.date.fromisoformat(CITYX_SHADOW0)
    if through >= cityx0:
        try:
            cx = pd.read_csv(EXACT_SELECTOR, parse_dates=["capture_utc"])
            cx["target_d"] = pd.to_datetime(cx.target).dt.date
            cx = cx[(cx.version == CITYX_VERSION) & (cx.target_d >= cityx0)]
            cx_capture = cx.capture_utc.dt.tz_convert("UTC").dt.tz_localize(None)
            bad = []
            for idx, r in cx.iterrows():
                if cx_capture.loc[idx] > freeze_utc(r.station, r.target_d):
                    bad.append(idx)
            if bad:
                issues.append(f"[CITYX2] {len(bad)} forecasts posteriores al freeze")
            if cx.duplicated(["station", "target", "capture_utc", "version"]).any():
                issues.append("[CITYX2] snapshots duplicados")
            mf2 = pd.read_csv(MODELS_FORWARD, parse_dates=["capture_utc"])
            mf2["target_d"] = pd.to_datetime(mf2.target).dt.date
            eligible = set()
            for (station, target, capture), group in mf2.groupby(["station", "target_d", "capture_utc"]):
                cap = capture.tz_convert("UTC").tz_localize(None)
                if (station in CITYX_RECIPES and cityx0 <= target <= through and
                        cap <= freeze_utc(station, target) and group.model.nunique() >= 3):
                    eligible.add((station, target))
            actual = set(zip(cx.station, cx.target_d))
            missing = sorted(eligible-actual)
            if missing:
                issues.append(f"[CITYX2] {len(missing)} estaciones-target elegibles sin pick: " +
                              ", ".join(f"{s}/{d}" for s, d in missing[:8]))
            print(f"[CITYX2] filas={len(cx)} pares={len(actual)}/{len(eligible)} elegibles")
        except FileNotFoundError:
            issues.append(f"[CITYX2] {EXACT_SELECTOR} no existe")

    # --- MKTWX1: consenso meteorología + CLOB, totalmente anterior al cutoff ---
    # CITYCONF1: deterministic spread gate over the coherent CITYX2 snapshot.
    conf0 = dt.date.fromisoformat(CONF_SHADOW0)
    if through >= conf0:
        try:
            cf = pd.read_csv(CITYX_CONFIDENCE, parse_dates=["capture_utc"])
            cf["target_d"] = pd.to_datetime(cf.target).dt.date
            cf = cf[(cf.version == CONF_VERSION) & (cf.target_d >= conf0)]
            if (cf.parent_version != CONF_PARENT).any():
                issues.append("[CITYCONF1] parent_version incorrecta")
            if cf.duplicated(["station", "target", "capture_utc", "version"]).any():
                issues.append("[CITYCONF1] snapshots duplicados")
            expected_selected = (cf.spread_buckets <= MAX_SPREAD_BUCKETS).astype(int)
            if (cf.selected.astype(int) != expected_selected).any():
                issues.append("[CITYCONF1] selected no coincide con el threshold congelado")
            captures = cf.capture_utc.dt.tz_convert("UTC").dt.tz_localize(None)
            late = [idx for idx, r in cf.iterrows()
                    if captures.loc[idx] > freeze_utc(r.station, r.target_d)]
            if late:
                issues.append(f"[CITYCONF1] {len(late)} capturas posteriores al freeze")
            ex = pd.read_csv(EXACT_SELECTOR, parse_dates=["capture_utc"])
            ex["target_d"] = pd.to_datetime(ex.target).dt.date
            ex = ex[(ex.version == CONF_PARENT) & (ex.target_d >= conf0) &
                    (ex.target_d <= through)]
            expected = set(zip(ex.station, ex.target_d, ex.capture_utc.dt.tz_convert("UTC")))
            actual = set(zip(cf.station, cf.target_d, cf.capture_utc.dt.tz_convert("UTC")))
            missing = expected-actual
            if missing:
                issues.append(f"[CITYCONF1] {len(missing)} snapshots CITYX elegibles sin gate")
            print(f"[CITYCONF1] filas={len(cf)} selected={int(cf.selected.sum())} "
                  f"coverage={cf.selected.mean():.1%}")
        except FileNotFoundError:
            issues.append(f"[CITYCONF1] {CITYX_CONFIDENCE} no existe")

    # --- LAMPX1: archived LAV runtime and CITYX parent both strictly pre-freeze. ---
    lamp0 = dt.date.fromisoformat(LAMP_SHADOW0)
    if through >= lamp0:
        try:
            lx = pd.read_csv(LAMP_SHADOW, parse_dates=["capture_utc", "lav_runtime_utc",
                "lav_avail_utc", "freeze_utc", "cityx_capture_utc", "obs_valid_utc",
                "obs_avail_utc"])
            lx["target_d"] = pd.to_datetime(lx.target).dt.date
            lx = lx[(lx.version == LAMP_VERSION) & (lx.target_d >= lamp0) &
                    (lx.target_d <= through)]
            if (lx.parent_version != LAMP_PARENT).any():
                issues.append("[LAMPX1] parent_version incorrecta")
            if lx.duplicated(["station", "target", "version"]).any():
                issues.append("[LAMPX1] station-target duplicados")
            provenance = {"obs_first", "obs_max", "obs_min", "obs_trend_fph",
                          "lav_slope_fph", "lav_match_utc", "lav_peak_utc",
                          "lav_peak_hour_local", "hours_to_lav_peak"}
            missing_columns = sorted(provenance-set(lx.columns))
            if missing_columns:
                issues.append("[LAMPNOW1] columnas provenance faltantes: " +
                              ", ".join(missing_columns))
            else:
                lx["lav_match_utc"] = pd.to_datetime(lx.lav_match_utc, utc=True)
                lx["lav_peak_utc"] = pd.to_datetime(lx.lav_peak_utc, utc=True)
                obs_lag = (lx.obs_avail_utc-lx.obs_valid_utc).dt.total_seconds()/60
                if not np.allclose(obs_lag, 15):
                    issues.append("[LAMPNOW1] lag ASOS distinto de 15 minutos")
                bad_obs = ((lx.obs_min > lx.obs_first) | (lx.obs_min > lx.obs_latest) |
                           (lx.obs_max < lx.obs_first) | (lx.obs_max < lx.obs_latest))
                if bad_obs.any():
                    issues.append(f"[LAMPNOW1] {int(bad_obs.sum())} filas con extremos ASOS incoherentes")
                peak_hours = (lx.lav_peak_utc-lx.obs_valid_utc).dt.total_seconds()/3600
                if not np.allclose(peak_hours, lx.hours_to_lav_peak, atol=1e-4):
                    issues.append("[LAMPNOW1] hours_to_lav_peak no coincide con timestamps")
                if (lx.hours_to_lav_peak < 0).any():
                    issues.append("[LAMPNOW1] pico LAV anterior a observacion pre-freeze")
            if (lx.lav_avail_utc > lx.freeze_utc).any():
                issues.append("[LAMPX1] runtime LAV disponible despues del freeze")
            lag = (lx.lav_avail_utc-lx.lav_runtime_utc).dt.total_seconds()/3600
            if not np.allclose(lag, LAMP_LAG):
                issues.append(f"[LAMPX1] lag de publicacion distinto de {LAMP_LAG}h")
            if (lx.cityx_capture_utc > lx.freeze_utc).any():
                issues.append("[LAMPX1] parent CITYX posterior al freeze")
            expected_mu = [lamp_prediction(r.station, r.lav_tmax, r.mu_cityx)
                           for r in lx.itertuples(index=False)]
            if not np.allclose(lx.mu_lampx, expected_mu, atol=1e-4):
                issues.append("[LAMPX1] mu no coincide con receta congelada")
            if (lx.now_version != NOW_VERSION).any():
                issues.append("[LAMPNOW1] version incorrecta")
            if (lx.obs_avail_utc > lx.freeze_utc).any():
                issues.append("[LAMPNOW1] observacion disponible despues del freeze")
            expected_now = [now_prediction(r.mu_lampx, r.innovation)
                            for r in lx.itertuples(index=False)]
            if not np.allclose(lx.mu_nowx, expected_now, atol=1e-4):
                issues.append("[LAMPNOW1] mu no coincide con correccion congelada")
            ex = pd.read_csv(EXACT_SELECTOR, parse_dates=["capture_utc"])
            ex["target_d"] = pd.to_datetime(ex.target).dt.date
            expected = set()
            for r in ex.itertuples(index=False):
                cap = r.capture_utc.tz_convert("UTC").tz_localize(None)
                if (r.version == LAMP_PARENT and r.station in LAMP_OFFSETS and
                        lamp0 <= r.target_d <= through and
                        cap <= freeze_utc(r.station, r.target_d)):
                    expected.add((r.station, r.target_d))
            actual = set(zip(lx.station, lx.target_d))
            missing = sorted(expected-actual)
            if missing:
                issues.append(f"[LAMPX1] {len(missing)} CITYX elegibles sin LAMP: " +
                              ", ".join(f"{s}/{d}" for s, d in missing[:8]))
            print(f"[LAMPX1] filas={len(lx)} pares={len(actual)}/{len(expected)} elegibles")
            try:
                verdict = pd.read_csv(LAMP_VERDICT)
                allowed = {"NO_CAPTURES", "NO_RESOLUTIONS", "ACCUMULATING",
                           "WAITING_RESOLUTION", "REJECT_LAMP_AND_NOW",
                           "ADOPT_LAMP_REJECT_NOW", "ADOPT_NOW"}
                if len(verdict) != 1 or verdict.iloc[0].state not in allowed:
                    issues.append("[LAMP gate] estado persistido invalido")
                if len(verdict) == 1 and verdict.iloc[0].lamp_version != LAMP_VERSION:
                    issues.append("[LAMP gate] version persistida incorrecta")
            except FileNotFoundError:
                issues.append(f"[LAMP gate] {LAMP_VERDICT} no existe")
        except FileNotFoundError:
            issues.append(f"[LAMPX1] {LAMP_SHADOW} no existe")

    shadow0 = dt.date.fromisoformat(SHADOW0)
    if through >= shadow0:
        try:
            mc = pd.read_csv(MARKET_CONSENSUS, parse_dates=["capture_utc", "cutoff_utc", "price_utc"])
            mc["target_d"] = pd.to_datetime(mc.target).dt.date
            duplicates = mc.duplicated(["station", "target", "version"]).sum()
            if duplicates:
                issues.append(f"[MKTWX1] {duplicates} picks duplicados")
            bad_capture = mc[mc.capture_utc.dt.tz_convert("UTC").dt.tz_localize(None) > mc.cutoff_utc]
            bad_price = mc[mc.price_utc > mc.cutoff_utc]
            age_h = (mc.cutoff_utc-mc.price_utc).dt.total_seconds()/3600
            bad_age = mc[(age_h < 0) | (age_h > MAX_PRICE_AGE_H)]
            if len(bad_capture):
                issues.append(f"[MKTWX1] {len(bad_capture)} forecasts posteriores al cutoff")
            if len(bad_price):
                issues.append(f"[MKTWX1] {len(bad_price)} precios posteriores al cutoff")
            if len(bad_age):
                issues.append(f"[MKTWX1] {len(bad_age)} precios con edad fuera de [0,{MAX_PRICE_AGE_H}]h")
            if (mc.n_priced < 4).any():
                issues.append(f"[MKTWX1] {(mc.n_priced < 4).sum()} filas con menos de 4 buckets cotizados")

            # Every CITYX snapshot available by the frozen cutoff must eventually
            # yield one consensus row. This catches API/scheduler gaps while the
            # 3-month CLOB history can still be reconstructed.
            ex = pd.read_csv(EXACT_SELECTOR, parse_dates=["capture_utc"])
            ex["target_d"] = pd.to_datetime(ex.target).dt.date
            expected = set()
            for r in ex.itertuples(index=False):
                if r.station not in MKT_STATIONS or not (shadow0 <= r.target_d <= through):
                    continue
                cutoff = freeze_utc(r.station, r.target_d)-dt.timedelta(hours=CUTOFF_HOURS_BEFORE_FREEZE)
                capture = r.capture_utc.tz_convert("UTC").tz_localize(None)
                if capture <= cutoff:
                    expected.add((r.station, r.target_d))
            actual = set(zip(mc.station, mc.target_d))
            missing = sorted(expected-actual)
            if missing:
                issues.append(f"[MKTWX1] {len(missing)} picks elegibles sin captura: " +
                              ", ".join(f"{s}/{d}" for s, d in missing[:8]))
            print(f"[MKTWX1] filas={len(mc)} esperadas={len(expected)} timestamps pre-cutoff OK")
        except FileNotFoundError:
            issues.append(f"[MKTWX1] {MARKET_CONSENSUS} no existe")

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
