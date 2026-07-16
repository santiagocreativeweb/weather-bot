#!/usr/bin/env python3
# scripts/download_openmeteo.py — v3: forecasts POINT-IN-TIME desde Previous Runs API.
# [Reescrito 2026-07-07 tras diagnostico EN VIVO. La v2 asumia archivo de ensembles; no existe.]
#
# HALLAZGO QUE OBLIGA EL REDISEÑO (verificado en vivo, no inferido):
#   * ensemble-api.open-meteo.com NO tiene archivo historico: los miembros vienen None para
#     todo lo anterior a ~3 dias (solo sirve forward). Un rango >ventana -> 400.
#   * previous-runs-api.open-meteo.com/v1/forecast SI da pronostico DETERMINISTICO point-in-time
#     multi-año via temperature_2m_previous_dayN, y es anti-look-ahead POR CONSTRUCCION:
#     previous_dayN = la corrida de N dias atras (mas vieja => mas error). Confirmado empirico:
#     rmse(lead1) < rmse(lead2)  [KLGA 1.91F -> 3.09F sobre 93 dias].
#
# QUE ESCRIBE CADA COLUMNA:
#   m  = tmax diaria pronosticada point-in-time por (estacion, modelo, lead). En la MISMA unidad
#        que obs.csv (F para K*, C para el resto) para que EMOS opere sobre anomalias homogeneas.
#   s2 = varianza de residuos (m - obs) por (estacion, modelo, lead), VENTANA EXPANDIENTE:
#        cada target usa SOLO residuos de targets ANTERIORES -> tampoco mira el futuro.
#        [ASUNCION] s2 es spread MODELADO desde error historico, NO el spread real entre miembros
#        de un ensemble. Se documenta explicito (decision de arquitectura 2026-07-07).
#        Por que por-modelo y no un s2 unico: mixture_mean_var (calibration.py) suma sola
#        var-dentro-de-modelo + var-ENTRE-modelos (gefs/ecmwf/icon discrepan dia a dia) => la
#        sharpness varia por dia aunque el s2 base sea historico por lead. Un s2 combinado
#        perderia esa señal instance-specific.
#
# PLAN PARALELO (no bloquea el backtest): scripts/accumulate_ensemble.py junta el ensemble REAL
# forward desde hoy; en ~90 dias valida si s2 modelado ~ spread real y se recalibra si no.
#
# DISCIPLINA fail-loud (heredada): si una llamada falla, si un dia no tiene horas suficientes para
# un tmax honesto, o si salen 0 filas -> CORTAR con error claro. Nunca escribir basura silenciosa.
# s2 > 0 SIEMPRE por construccion (piso fisico SIGMA_FLOOR^2) -> checks.py no encuentra NaN/<=0.
#
# Contrato de salida data/forecasts.csv: station,target,model,init,avail,lead_h,m,s2
import argparse, csv, sys, time
import datetime as dt
import requests

PREV_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"

# code -> (lat, lon, utc_off, unit)  [VERIFICAR-VIVO: coords/unidad EXACTAS segun rules del mercado]
STATIONS = {
    "KLGA": (40.7794, -73.8803, -5, "F"), "KORD": (41.9786, -87.9048, -6, "F"),
    "EGLC": (51.5050,  0.0553,  0, "C"),  "LFPB": (48.9694,  2.4414,   1, "C"),
    "RJTT": (35.5533, 139.7811, 9, "C"),  "RKSI": (37.4602, 126.4407,  9, "C"),
    "ZSPD": (31.1434, 121.8052, 8, "C"),  "ZBAA": (40.0801, 116.5846,  8, "C"),
    "RCSS": (25.0694, 121.5521, 8, "C"),  "LEMD": (40.4722,  -3.5609,  1, "C"),
    "EDDM": (48.3538,  11.7861, 1, "C"),  "LIMC": (45.6301,   8.7231,  1, "C"),
    # [2026-07-13] 6 nuevas (= show_live.STATIONS). Nota: onboard_cities.py baja SU historia
    # append-only; este dict las cubre por si algun dia se reconstruye forecasts.csv entero.
    "NZWN": (-41.3272, 174.8053, 12, "C"), "LTAC": (40.1281, 32.9951,  3, "C"),
    "KMIA": (25.7932, -80.2906, -5, "F"),  "WSSS": (1.3502, 103.9944,  8, "C"),
    "WMKK": (2.7456, 101.7099,  8, "C"),   "ZGSZ": (22.6393, 113.8108, 8, "C"),
    "KSFO": (37.6188, -122.3750, -8, "F"), "KLAX": (33.9425, -118.4081, -8, "F"),
    "KDAL": (32.8471, -96.8518,  -6, "F"), "KATL": (33.6367, -84.4281,  -5, "F"),
    "KHOU": (29.6454, -95.2789,  -6, "F"), "KAUS": (30.1945, -97.6699,  -6, "F"),
    "CYYZ": (43.6772, -79.6306,  -5, "C"), "SBGR": (-23.4356, -46.4731, -3, "C"),
    "SAEZ": (-34.8222, -58.5358, -3, "C"), "MMMX": (19.4363, -99.0721,  -6, "C"),
    "EFHK": (60.3172, 24.9633,    2, "C"),
}
# model interno -> id Open-Meteo determinístico valido en previous-runs. [VERIFICADO en vivo:
# gfs025 devuelve 0 en este endpoint; gfs_seamless si.]  lag_h = disponibilidad tras init (config).
MODELS = {"gefs": ("gfs_seamless", 5.0), "ecmwf": ("ecmwf_ifs025", 7.0), "icon": ("icon_seamless", 7.0)}
SIGMA_FLOOR = {"F": 0.9, "C": 0.5}   # = wxbt/config.py SIGMA_FLOOR (piso de sigma por unidad)
# lead operado -> columna Previous Runs. base=corrida mas reciente (lead corto ~mismo dia),
# previous_dayN = corrida N dias mas vieja. Mapeo a _lead_day del motor (<=24->1, <=48->2, else 3).
LEAD_COL = {1: "temperature_2m", 2: "temperature_2m_previous_day1", 3: "temperature_2m_previous_day2"}
MIN_DAY_HOURS = 20   # exigir casi el dia completo para un tmax honesto (no subestimar el pico)
MIN_S2_WARMUP = 20   # hasta juntar esta N de residuos, no confiar en la var muestral: pisar con floor^2


def fetch(lat, lon, unit, om):
    """UNA request por (estacion, modelo): models=om para pronostico POR MODELO real
    (mixture_mean_var necesita m_i distinto por modelo para captar el disagreement)."""
    p = dict(latitude=lat, longitude=lon, models=om,
             hourly=",".join(["temperature_2m", "temperature_2m_previous_day1", "temperature_2m_previous_day2"]),
             start_date=ARGS.start, end_date=ARGS.end, timezone="UTC",
             temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
    r = requests.get(PREV_RUNS, params=p, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]} "
                           f"(rango fuera del archivo? probar --start mas reciente)")
    return r.json()["hourly"]


def daily_tmax(times, vals, off):
    """Tmax por dia LOCAL. Descarta dias con < MIN_DAY_HOURS horas (tmax poco fiable)."""
    buck = {}   # date -> list de valores horarios
    for t, v in zip(times, vals):
        if v is None:
            continue
        u = dt.datetime.fromisoformat(t) + dt.timedelta(hours=off)
        buck.setdefault(u.date(), []).append(float(v))
    return {d: max(vs) for d, vs in buck.items() if len(vs) >= MIN_DAY_HOURS}


def load_obs(station):
    """obs reales ya bajadas -> {date: tmax_int} para calcular residuos (m - obs)."""
    out = {}
    try:
        with open(ARGS.obs, newline="") as f:
            for row in csv.DictReader(f):
                if row["station"] == station:
                    out[dt.date.fromisoformat(row["date"])] = float(row["tmax_int"])
    except FileNotFoundError:
        raise RuntimeError(f"no encuentro {ARGS.obs} -- correr download_iem_obs.py primero "
                           f"(s2 se modela desde residuos m-obs, necesita obs).")
    return out


def expanding_s2(dates_sorted, resid, floor2):
    """s2[d] = varianza muestral de residuos de targets ESTRICTAMENTE anteriores a d (anti-look-ahead).
    Warmup: con < MIN_S2_WARMUP residuos previos, pisar con floor^2 (no confiar en n chico).
    Devuelve s2 para TODO d con forecast (aunque no tenga obs: usa la historia acumulada)."""
    s2, hist = {}, []
    for d in dates_sorted:
        if len(hist) >= 2:
            mean = sum(hist) / len(hist)
            var = sum((r - mean) ** 2 for r in hist) / (len(hist) - 1)
        else:
            var = floor2
        if len(hist) < MIN_S2_WARMUP:
            var = max(var, floor2)
        s2[d] = max(var, floor2)          # > 0 SIEMPRE y nunca por debajo del piso fisico
        if d in resid:                    # sumar el residuo de HOY recien despues (no para si mismo)
            hist.append(resid[d])
    return s2


def main():
    rows = []
    for code, (lat, lon, off, unit) in STATIONS.items():
        obs = load_obs(code)
        floor2 = SIGMA_FLOOR[unit] ** 2
        for model, (om, lag_h) in MODELS.items():
            try:
                h = fetch(lat, lon, unit, om)
            except Exception as e:
                print(f"[ABORT] {code} {model}: {e}", file=sys.stderr); sys.exit(1)
            times = h["time"]
            for lead, col in LEAD_COL.items():
                if col not in h:
                    print(f"[ABORT] {code} {model}: falta columna {col} en la respuesta "
                          f"(cambio la API?)", file=sys.stderr); sys.exit(1)
                dmax = daily_tmax(times, h[col], off)   # {date: m} para este (estacion, modelo, lead)
                resid = {d: dmax[d] - obs[d] for d in dmax if d in obs}
                s2map = expanding_s2(sorted(dmax), resid, floor2)
                for d, m in dmax.items():
                    init = dt.datetime.combine(d, dt.time()) - dt.timedelta(days=lead - 1)
                    avail = init + dt.timedelta(hours=lag_h)
                    # lead_h = horas de avail al PICO de tmax (~15:00 local), no a la medianoche:
                    # es el horizonte real del pronostico y bucketea limpio en _lead_day sin que el
                    # ancho de huso (24-off) desplace el lead. En estaciones al este (Tokio/Seul) la
                    # corrida 00Z del mismo dia queda con lead<=1h -> se descarta sola (correcto: no
                    # estaba disponible con antelacion util antes del pico).
                    peak = dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=15 - off)
                    lead_h = (peak - avail).total_seconds() / 3600.0
                    if not (1.0 < lead_h <= 78.0):
                        continue
                    rows.append([code, d.isoformat(), model, init.isoformat(), avail.isoformat(),
                                 round(lead_h, 1), round(m, 2), round(s2map[d], 3)])
            time.sleep(0.3)
    if not rows:
        print("[ABORT] cero filas -- no escribo CSV vacio que rompa rio abajo.", file=sys.stderr)
        sys.exit(1)
    with open(ARGS.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "target", "model", "init", "avail", "lead_h", "m", "s2"])
        w.writerows(rows)
    print(f"escrito {ARGS.out}: {len(rows)} filas (point-in-time, s2 modelado por (estacion,modelo,lead))")
    print("s2 = varianza de residuos ventana-expandiente [ASUNCION: NO es spread de ensemble real].")
    print("Validacion forward pendiente: correr accumulate_ensemble.py a diario ~90 dias.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--obs", default="data/obs.csv")
    ap.add_argument("--out", default="data/forecasts.csv")
    ARGS = ap.parse_args()
    main()
