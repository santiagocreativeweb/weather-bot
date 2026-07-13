#!/usr/bin/env python3
# scripts/accumulate_mosmix.py — Capturador FORWARD del parametro TX de DWD MOSMIX_L (MOS por
# estacion). [Creado 2026-07-10. Plan paralelo, no bloquea nada. Companion de accumulate_books.py /
# accumulate_ensemble.py / capture_mosmix.py — misma filosofia: append-only, guard anti
# doble-corrida, log_run a data/accumulator.log, fail-loud sin escrituras parciales.]
#
# POR QUE EXISTE: MOSMIX no tiene archivo point-in-time gratis (solo el LATEST vigente), asi que
# la unica forma de tener MOSMIX honesto (invariante #2: avail_utc = instante REAL de descarga)
# es capturarlo forward. capture_mosmix.py ya guarda el tmax DERIVADO (max de la serie horaria
# TTT por dia local) en data/mosmix_forward.csv; ESTE script guarda el parametro TX NATIVO del
# MOS (el maximo de la curva continua sobre ventanas de 12h, no el max de muestras horarias).
# Son dos predictores distintos: TX nativo corre tipicamente +0.4..+2.0C ARRIBA del max horario
# (la curva continua pica entre muestras). En ~90 dias se comparan ambos contra el consenso.
#
# SALIDA data/mosmix_tx_forward.csv (avail_utc,run_utc,station,target,tx_c).
#   NO data/mosmix_forward.csv: ese archivo YA EXISTE con otro esquema (7 columnas, de
#   capture_mosmix.py) y esta prohibido tocarlo; mezclar esquemas corromperia el CSV.
#   tx_c en CELSIUS 1 decimal para TODAS las estaciones (tambien KLGA/KORD): la conversion a
#   grados F se hace en evaluacion, no en captura.
#
# SEMANTICA TX (verificada EMPIRICAMENTE el 2026-07-10 contra el KML real, corrida 21Z):
#   TX viene en Kelvin, solo en ALGUNOS ForecastTimeSteps, y las horas UTC varian POR ESTACION
#   (no es el 06/18 UTC canonico para todas):
#     EGLC/LFPB/LEMD/EDDM: 06 y 18 UTC   | LIMC: solo 18 UTC | RJTT: solo 12 UTC
#     RKSI: 06 y 12 UTC                  | ZBAA/ZSPD: 00/06/12/18 UTC (ventanas SOLAPADAS)
#     KLGA/KORD: el elemento TX existe pero TODOS los valores son '-' (DWD no publica TX para
#     estaciones US) -> warning y 0 filas para ellas; su tmax MOSMIX ya sale del TTT horario
#     via capture_mosmix.py.
#   Cada valor TX en el timestep t cubre la ventana de las 12h PREVIAS: (t-12h, t].
#
# MAPEO TX -> DIA LOCAL (la trampa central): un valor TX se asigna al dia calendario LOCAL D de
# la estacion si el pico de tarde (D 15:00 local) cae dentro de la ventana (t-12h, t] en hora
# local. Esta regla absorbe sola la heterogeneidad de horas UTC (p.ej. RJTT 12Z -> ventana local
# (09:00,21:00] que contiene las 15:00; el 06Z de Europa -> ventana nocturna local que no
# contiene 15:00 de ningun dia y se descarta). En ZBAA/ZSPD/RKSI dos ventanas solapadas cubren
# el mismo pico -> se toma el MAX de los candidatos (el max diario domina cualquier ventana de
# 12h que contenga el pico; las horas 00-02 del dia siguiente que arrastra la ventana 18Z no
# producen maximos de tarde).
#
# VALIDACION INTERNA (obligatoria, corre en cada captura): para cada (estacion, dia local) con
# >=18 horas de TTT, el TX asignado debe caer en [maxTTT-1.0, maxTTT+3.0] C. La banda es
# ASIMETRICA a proposito: el enunciado canonico "+-1C" fallaba empiricamente porque TX (max de
# curva continua) excede el max de muestras horarias en +0.4..+2.0C de forma sistematica
# (verificado 2026-07-10 en EGLC/RJTT/ZBAA); un mapeo corrido un dia, en cambio, da diffs de
# signo aleatorio y magnitud mayor. Si la banda falla: warning fuerte (la fila SE ESCRIBE igual
# porque el TX capturado es dato honesto, pero queda contado en el log para investigar).
#
# OFFSETS: fijos de JULIO (verano). Validos hasta fin de octubre 2026 (cambio DST europeo
# 2026-10-25, US 2026-11-01; Asia no tiene DST). Si la captura sigue pasado octubre, actualizar.
#
# USO: python scripts/accumulate_mosmix.py --date YYYY-MM-DD   (varias veces por dia; el guard
# por (run_utc, station, target) hace las corridas idempotentes: re-capturar la misma corrida
# DWD no agrega nada. Sin --force a proposito, igual que capture_mosmix.py: duplicar filas de
# la misma corrida real nunca sirve, sesgaria cualquier promedio posterior.)
import argparse, csv, io, os, sys, time, zipfile
import datetime as dt
import xml.etree.ElementTree as ET
import requests

BASE = ("https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/"
        "single_stations/{id}/kml/MOSMIX_L_LATEST_{id}.kmz")
# IDs MOSMIX verificados a mano. RCSS (Taipei) NO existe en MOSMIX — excluida; quedan 11/12.
MOSMIX_IDS = {"KLGA": "72503", "KORD": "72530", "EGLC": "P0478", "LFPB": "07150",
              "LEMD": "08221", "EDDM": "10870", "LIMC": "16066", "RJTT": "47671",
              "RKSI": "47113", "ZBAA": "54511", "ZSPD": "58362"}
# offset UTC de JULIO por estacion (ver nota DST arriba)
UTC_OFF = {"KLGA": -4, "KORD": -5, "EGLC": 1, "LFPB": 2, "LEMD": 2, "EDDM": 2,
           "LIMC": 2, "RJTT": 9, "RKSI": 9, "ZBAA": 8, "ZSPD": 8}
OUT = "data/mosmix_tx_forward.csv"
LOG = "data/accumulator.log"    # registro append-only compartido con accumulate_*/capture_*
HORIZON_DAYS = 3     # targets hoy..hoy+3 (= ventana del resto del sistema)
PEAK_HOUR = 15       # pico de tarde local que ancla el mapeo ventana->dia
TX_WINDOW_H = 12     # cada TX cubre las 12h previas a su timestep (convencion DWD)
VAL_MIN_HOURS = 18   # horas de TTT minimas del dia local para que la validacion sea juzgable
VAL_LO, VAL_HI = -1.0, 3.0   # banda tx - maxTTT aceptada (asimetrica, ver header)


def log_run(script, snapshot, status, detail):
    """Una linea por corrida a data/accumulator.log (sobrevive reinicios). Distingue '90 dias de
    data' de '60 dias con 30 huecos silenciosos': sin esto no se sabe si un dia corrio."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def parse_kmz(content):
    """KMZ (zip con un KML adentro) -> (issue dt UTC, [timesteps UTC], [TX K|None], [TTT K|None]).
    Parse namespace-agnostico (matchea el nombre LOCAL del tag/atributo) por si DWD versiona el
    xsd de la extension dwd:. Valores faltantes vienen como '-' en el KML."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not kmls:
            raise ValueError("KMZ sin KML adentro")
        root = ET.fromstring(z.read(kmls[0]))
    issue, steps, series = None, [], {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "IssueTime" and el.text:
            issue = dt.datetime.fromisoformat(el.text.strip().replace("Z", "+00:00"))
        elif tag == "TimeStep" and el.text:
            steps.append(dt.datetime.fromisoformat(el.text.strip().replace("Z", "+00:00")))
        elif tag == "Forecast":
            name = next((v for k, v in el.attrib.items()
                         if k.rsplit("}", 1)[-1] == "elementName"), None)
            if name in ("TX", "TTT"):
                raw = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "value"), "") or ""
                series[name] = [None if v == "-" else float(v) for v in raw.split()]
    tx, ttt = series.get("TX"), series.get("TTT")
    if issue is None or not steps or ttt is None:
        raise ValueError(f"KML incompleto (issue={issue is not None} "
                         f"steps={len(steps)} ttt={ttt is not None})")
    for nm, s in (("TX", tx), ("TTT", ttt)):
        if s is not None and len(s) != len(steps):
            raise ValueError(f"{nm} ({len(s)}) no calza con ForecastTimeSteps ({len(steps)})")
    return issue, steps, tx or [None] * len(steps), ttt


def tx_by_local_day(steps, tx, off):
    """{dia LOCAL: tx Kelvin}. Regla: TX@t cubre (t-12h, t]; se asigna al dia D si D 15:00 local
    cae en la ventana (en local). Ventanas solapadas (ZBAA/ZSPD/RKSI) -> max de candidatos."""
    days = {}
    for t, v in zip(steps, tx):
        if v is None:
            continue
        lo = t - dt.timedelta(hours=TX_WINDOW_H) + dt.timedelta(hours=off)
        hi = t + dt.timedelta(hours=off)
        for d in {lo.date(), hi.date()}:           # el pico solo puede caer en uno de los dos
            peak = dt.datetime.combine(d, dt.time(PEAK_HOUR, 0), tzinfo=lo.tzinfo)
            if lo < peak <= hi:
                days[d] = max(v, days.get(d, v))
    return days


def ttt_day_max(steps, ttt, off):
    """{dia LOCAL: (max TTT Kelvin, n_horas)} — solo para la validacion interna del mapeo."""
    per_day = {}
    for t, v in zip(steps, ttt):
        if v is None:
            continue
        per_day.setdefault((t + dt.timedelta(hours=off)).date(), []).append(v)
    return {d: (max(vs), len(vs)) for d, vs in per_day.items()}


def main(a):
    today = dt.date.fromisoformat(a.date)   # exigido explicito: reproducibilidad (= accumulate_*)
    # guard anti doble-corrida / idempotencia: clave = (run_utc, station, target). El script
    # corre varias veces al dia y solo debe agregar cuando DWD publico un ciclo nuevo.
    seen = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                seen.add((r["run_utc"], r["station"], r["target"]))
    rows, skipped, errs, val_fail = [], 0, 0, 0
    for code, mid in MOSMIX_IDS.items():
        off = UTC_OFF[code]
        # avail_utc = instante REAL de la descarga (invariante #2; por estacion, no por corrida)
        avail = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        try:
            r = requests.get(BASE.format(id=mid), timeout=60)
            r.raise_for_status()
            issue, steps, tx, ttt = parse_kmz(r.content)
        except Exception as e:
            print(f"[WARN] {code} (MOSMIX {mid}): {e}", file=sys.stderr)
            errs += 1
            continue        # fail-loud por estacion, no global: las demas siguen
        time.sleep(0.2)     # cortesia con opendata.dwd.de (~15KB c/u, 11 estaciones)
        run_iso = issue.isoformat(timespec="seconds")
        tx_days = tx_by_local_day(steps, tx, off)
        if not tx_days:
            # KLGA/KORD esperado: DWD no publica TX para estaciones US (todos '-')
            print(f"[WARN] {code}: sin valores TX en la corrida {run_iso} "
                  f"(esperado en KLGA/KORD; su tmax sale de TTT via capture_mosmix.py)",
                  file=sys.stderr)
            continue
        vmax = ttt_day_max(steps, ttt, off)
        for lead in range(HORIZON_DAYS + 1):
            target = today + dt.timedelta(days=lead)
            if target not in tx_days:
                continue    # p.ej. HOY en corridas tardias: la ventana del pico ya paso
            if (run_iso, code, target.isoformat()) in seen:
                skipped += 1   # esa corrida ya esta capturada -> saltear (es lo esperado)
                continue
            tx_c = tx_days[target] - 273.15
            # validacion interna del mapeo (ver banda asimetrica en el header)
            if target in vmax and vmax[target][1] >= VAL_MIN_HOURS:
                diff = tx_c - (vmax[target][0] - 273.15)
                if not (VAL_LO <= diff <= VAL_HI):
                    val_fail += 1
                    print(f"[WARN] {code} {target}: TX={tx_c:.1f}C vs maxTTT="
                          f"{vmax[target][0] - 273.15:.1f}C (diff {diff:+.1f}C fuera de banda "
                          f"[{VAL_LO},{VAL_HI}]) -- posible mapeo corrido un dia, investigar",
                          file=sys.stderr)
            rows.append([avail, run_iso, code, target.isoformat(), round(tx_c, 1)])
    if rows:    # escritura unica al final: nunca escrituras parciales
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["avail_utc", "run_utc", "station", "target", "tx_c"])
            w.writerows(rows)
    st_new = len({r[2] for r in rows})
    status = "OK" if rows else ("SKIP" if skipped and not errs else "WARN")
    print(f"+{len(rows)} filas a {OUT} ({st_new} estaciones con corrida nueva, "
          f"{skipped} (run,station,target) ya capturados, {errs} errores, "
          f"{val_fail} fallas de validacion TXvsTTT).")
    log_run("mosmix_tx", a.date, status,
            f"rows={len(rows)} stations_new={st_new} skipped={skipped} "
            f"errores={errs} val_fail={val_fail}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Captura forward del TX nativo de MOSMIX_L mapeado al dia calendario LOCAL.")
    ap.add_argument("--date", required=True, help="fecha 'hoy' YYYY-MM-DD (targets hoy..hoy+3)")
    main(ap.parse_args())
