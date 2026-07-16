#!/usr/bin/env python3
# scripts/accumulate_predictions.py — Guarda NUESTRA prediccion FORWARD (la del motor CALIBRADO)
# dia a dia, para despues compararla contra el bucket ganador real (check_predictions.py).
# [Creado 2026-07-08. Companion de accumulate_books/ensemble; mismo patron: --date, append-only,
#  guard anti doble-corrida, log a accumulator.log.]
#
# QUE GUARDA y POR QUE lo CALIBRADO (no el consenso crudo del dashboard): el edge se decide con
# la prediccion CALIBRADA (EMOS sobre ANOMALIAS respecto a climatologia -> mu,sigma), NO con el
# promedio crudo de gefs/ecmwf/icon. Toda la sesion mostro que el crudo sobrestima ~5x. Guardamos
# AMBOS (mu_cal/sigma_cal y mu_raw/sigma_raw) para medir cuanto aporta la calibracion.
#
# ANTI-LOOK-AHEAD: los params EMOS se entrenan con el historico <= ultima obs (jun-2026); predecir
# julio-2026 forward con esos params NO mira el futuro. m viene de la corrida MAS RECIENTE
# (Previous-Runs). s2 = ultimo s2 MODELADO por (estacion,modelo,lead) de forecasts.csv (la mejor
# estimacion acumulada que el sistema tiene; el s2 modelado es varianza lenta de residuos por lead).
#
# SEMANTICA DE LEADS (corregida 2026-07-08 — la v1 tenia el mapeo corrido en 1): en TODO el sistema
# lead_h se mide desde `avail` del forecast hasta el PICO de tmax (~15:00 local del target), y
# _lead_day bucketea lead_h<=24 -> 1, <=48 -> 2, else 3. O sea: "lead 1" = corrida de la MISMA
# manana del target (el bot OPERA el dia del target hasta el cierre), "lead 2" = corrida del dia
# anterior, "lead 3" = de 2 dias antes. Por eso este snapshot cubre targets HOY..HOY+2 (no +3:
# no hay 4ta columna de Previous Runs, el sistema nunca entreno ese lead) y el s2 se indexa por
# el _lead_day REAL de cada (modelo, target) calculado con la misma formula del downloader.
#
# Salida data/predictions_forward.csv:
#   snapshot_date,station,target,lead_day,lead_h,unit,n_models,mu_cal,sigma_cal,mu_raw,sigma_raw
import argparse, csv, os, sys
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from wxbt import config as C                                    # noqa: E402
from wxbt.engine import fit_all, _lead_day                      # noqa: E402
from wxbt.calibration import predict, predict_raw               # noqa: E402
from wxbt.engine import clim_val                                # noqa: E402
from show_live import STATIONS, fetch_forecast, peak_utc        # noqa: E402  (m por modelo, Previous-Runs)

OUT = "data/predictions_forward.csv"
LOG = "data/accumulator.log"
# sesgo rolling-60d por estacion (calibrador V2; lo escribe scripts/calib_lab.py)
try:
    import json as _json
    _b = _json.load(open("data/station_bias.json", encoding="utf-8"))
    STATION_BIAS = _b.get("bias", {})
    print(f"[calibrador V2] sesgo rolling aplicado (asof {_b.get('asof')})", file=sys.stderr)
except Exception:
    STATION_BIAS = {}
FC_HIST = "data/forecasts.csv"
OBS_HIST = "data/obs.csv"
TARGET_MIN_D, TARGET_MAX_D = 0, 2   # targets HOY..HOY+2 (ver nota de semantica de leads arriba)
MODEL_LAG_H = {"gefs": 5.0, "ecmwf": 7.0, "icon": 7.0}   # = download_openmeteo.py MODELS lag_h


def log_run(script, snapshot, status, detail):
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def latest_s2(fc):
    """Ultimo s2 MODELADO por (station, model, lead_day) -> {(st,model,ld): s2}. El s2 del sistema
    es varianza de residuos con ventana expandiente; el ultimo valor es la estimacion vigente."""
    fc = fc.copy()
    fc["ld"] = fc["lead_h"].map(_lead_day)
    fc = fc.sort_values("avail")
    out = {}
    for r in fc.itertuples():
        out[(r.station, r.model, r.ld)] = r.s2
    return out


def main(a):
    today = dt.date.fromisoformat(a.date)
    if os.path.exists(OUT) and not a.force:
        with open(OUT) as f:
            if any(row.startswith(a.date + ",") for row in f):
                print(f"[ABORT] ya hay filas para snapshot {a.date} en {OUT}. --force para re-agregar.",
                      file=sys.stderr)
                log_run("predictions", a.date, "SKIP", "snapshot ya existe")
                sys.exit(1)

    # 1) entrenar params EMOS+clim sobre el historico (<= ultima obs) -> anti-look-ahead forward.
    # [FIX 2026-07-10 auditoria] fit SIN las filas lead-1: son nowcast con avail falso (bug #5,
    # PROJECT_CONTEXT §5) y el lab que valido V2 (calib_lab) las excluye (fc.lead_h > 24) — la
    # produccion tiene que entrenar con el MISMO filtro que la config validada. El s2map (abajo)
    # SI conserva ld=1: en vivo la corrida de la misma manana es legitima y necesita su s2.
    fc = pd.read_csv(FC_HIST, parse_dates=["init", "avail", "target"])
    fc["target"] = fc["target"].dt.date
    obs = pd.read_csv(OBS_HIST, parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    params = fit_all(fc[fc.lead_h > 24], obs, sorted(obs.date.unique()))
    if not params:
        print("[ABORT] fit_all no devolvio params (historico insuficiente?).", file=sys.stderr)
        log_run("predictions", a.date, "WARN", "fit_all vacio"); return
    s2map = latest_s2(fc)

    # 2) m vigente por (estacion, target, modelo) de la corrida mas reciente (Previous-Runs)
    fcast = fetch_forecast(today, TARGET_MAX_D)   # {station:{date:{model:m}}}
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    rows = []
    for code, (lat, lon, off, unit) in STATIONS.items():
        pars = params.get(code)
        if pars is None:
            print(f"[WARN] {code}: sin params calibrados -> salto", file=sys.stderr); continue
        for d, models_m in sorted(fcast.get(code, {}).items()):
            if not (TARGET_MIN_D <= (d - today).days <= TARGET_MAX_D):
                continue
            # lead_h REAL de la decision: de AHORA al PICO por estacion (PEAK_HOUR, DST-aware; los
            # costeros de Asia pican a media manana, no 15:00). Si el pico ya paso (o falta demasiado),
            # el bot no formaria esta prediccion.
            peak = peak_utc(code, d)
            lead_h_now = (peak - now).total_seconds() / 3600.0
            if not (1.0 < lead_h_now <= 78.0):
                continue
            # armar per_model {model:(m,s2)}: el s2 se indexa por el _lead_day de la corrida que
            # ESTAMOS usando (la mas reciente de cada modelo), no por dias-calendario al target.
            pm = {}
            for model, m in models_m.items():
                # la corrida mas reciente usable: la de hoy si su avail ya paso, si no la de ayer
                run_day = today if (dt.datetime.combine(today, dt.time())
                                    + dt.timedelta(hours=MODEL_LAG_H[model])) <= now \
                    else today - dt.timedelta(days=1)
                avail = dt.datetime.combine(run_day, dt.time()) + dt.timedelta(hours=MODEL_LAG_H[model])
                lh = (peak - avail).total_seconds() / 3600.0
                if not (1.0 < lh <= 78.0):
                    continue
                ldb = _lead_day(lh)
                s2 = s2map.get((code, model, ldb))
                if s2 is None:
                    continue
                pm[model] = (m, s2)
            if len(pm) < C.MIN_MODELS_ENTRY:
                continue
            # calibrado: clim -> anomalia -> predict -> +clim (identico al engine; ld = dias al
            # cierre como float, igual que ld_dec en la entrada del backtest)
            c = clim_val(pars["clim"], d)
            pm_a = {k: (m - c, s2) for k, (m, s2) in pm.items()}
            pr = predict(pars["emos"], pm_a, ld=lead_h_now / 24.0)
            if pr is None:
                continue
            mu_a, sigma_cal = pr
            mu_cal = c + mu_a
            # CALIBRADOR V2 (adoptado 2026-07-09 por el lab, walk-forward 60d: hit 39%->43%,
            # MAE 1.11->1.03): correccion de sesgo ROLLING por estacion (media de pred-real de
            # los ultimos 60 dias). data/station_bias.json lo refresca calib_lab.py (semanal).
            mu_cal -= STATION_BIAS.get(code, 0.0)
            # crudo: mezcla equiponderada sin calibrar (baseline para medir el aporte de EMOS)
            raw = predict_raw(pm, C.SIGMA_FLOOR[unit])
            mu_raw, sigma_raw = raw if raw else (float("nan"), float("nan"))
            rows.append([today.isoformat(), code, d.isoformat(), _lead_day(lead_h_now),
                         round(lead_h_now, 1), unit, len(pm),
                         round(mu_cal, 2), round(sigma_cal, 2), round(mu_raw, 2), round(sigma_raw, 2)])

    if not rows:
        print("[WARN] 0 predicciones (sin forecast/params en ventana).", file=sys.stderr)
        log_run("predictions", a.date, "WARN", "0 filas"); return
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["snapshot_date", "station", "target", "lead_day", "lead_h", "unit", "n_models",
                        "mu_cal", "sigma_cal", "mu_raw", "sigma_raw"])
        w.writerows(rows)
    print(f"+{len(rows)} predicciones a {OUT} (snapshot {today}). "
          f"Chequear ganadores con check_predictions.py cuando resuelvan.")
    log_run("predictions", a.date, "OK", f"rows={len(rows)} stations={len({r[1] for r in rows})}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Snapshot diario de la prediccion calibrada del motor (forward).")
    ap.add_argument("--date", required=True, help="fecha del snapshot YYYY-MM-DD (hoy)")
    ap.add_argument("--force", action="store_true", help="permitir re-agregar el mismo snapshot_date")
    main(ap.parse_args())
