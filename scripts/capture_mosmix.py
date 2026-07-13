#!/usr/bin/env python3
# scripts/capture_mosmix.py — Capturador FORWARD de DWD MOSMIX_L (MOS por estacion, gratis).
# [Creado 2026-07-10. Plan paralelo, no bloquea nada. Companion de accumulate_books.py /
#  accumulate_ensemble.py — misma filosofia: append-only, guard anti doble-corrida, log_run.]
#
# POR QUE EXISTE: MOSMIX no tiene archivo point-in-time gratis (solo el LATEST vigente), asi que
# la UNICA forma de tener MOSMIX honesto (invariante #2: avail = instante real de publicacion) es
# capturarlo FORWARD desde hoy. En ~90 dias habra muestra para comparar MOS-por-estacion contra
# el consenso Open-Meteo del bot. 4 ciclos/dia (03/09/15/21 UTC): correr el script varias veces
# al dia; el guard por (issue_utc, station) saltea en silencio las corridas ya capturadas.
#
# SEMANTICA CRITICA: NO usamos los campos TX del KML (ventanas 12h en UTC que NO calzan con el
# dia calendario LOCAL de Asia — y el mercado resuelve el max del dia calendario local, pico
# ~15:00 local). Calculamos nosotros: tmax/tmin = max/min de la serie horaria TTT (KELVIN) sobre
# las horas cuyo instante LOCAL (offset de STATIONS) cae en el dia target. Solo se emite el
# target si hay >=18 horas del dia local cubiertas (si no, el extremo esta sesgado).
#
# USO: python scripts/capture_mosmix.py --date YYYY-MM-DD   (varias veces por dia; idempotente)
# Salida data/mosmix_forward.csv: capture_utc,issue_utc,station,target,tmax_pred,tmin_pred,n_horas
import argparse, csv, io, os, sys, time, zipfile
import datetime as dt
import xml.etree.ElementTree as ET
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from show_live import STATIONS   # {code: (lat, lon, off_horas_utc, unit)} — no duplicar metadata

BASE = ("https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/"
        "single_stations/{id}/kml/MOSMIX_L_LATEST_{id}.kmz")
# IDs MOSMIX verificados a mano. RCSS (Taipei) NO existe en MOSMIX — excluida; quedan 11/12.
MOSMIX_IDS = {"KLGA": "72503", "KORD": "72530", "EGLC": "P0478", "LFPB": "07150",
              "LEMD": "08221", "EDDM": "10870", "LIMC": "16066", "RJTT": "47671",
              "RKSI": "47113", "ZBAA": "54511", "ZSPD": "58362"}
OUT = "data/mosmix_forward.csv"
LOG = "data/accumulator.log"    # registro append-only compartido con accumulate_*.py
MIN_DAY_HOURS = 18   # no emitir un target con el dia local a medio cubrir (tmax/tmin sesgados)
HORIZON_DAYS = 3     # targets hoy..hoy+3 (= ventana del resto del sistema)


def log_run(script, snapshot, status, detail):
    """Una linea por corrida a data/accumulator.log (sobrevive reinicios). Distingue '90 dias de
    data' de '60 dias con 30 huecos silenciosos': sin esto no se sabe si un dia corrio."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def parse_kmz(content):
    """KMZ (zip con un KML adentro) -> (issue datetime UTC, [timesteps UTC], [TTT Kelvin|None]).
    Parse namespace-agnostico (matchea el nombre LOCAL del tag/atributo) por si DWD versiona el
    xsd de la extension dwd:. Valores faltantes vienen como '-' en el KML."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not kmls:
            raise ValueError("KMZ sin KML adentro")
        root = ET.fromstring(z.read(kmls[0]))
    issue, steps, ttt = None, [], None
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "IssueTime" and el.text:
            issue = dt.datetime.fromisoformat(el.text.strip().replace("Z", "+00:00"))
        elif tag == "TimeStep" and el.text:
            steps.append(dt.datetime.fromisoformat(el.text.strip().replace("Z", "+00:00")))
        elif tag == "Forecast":
            name = next((v for k, v in el.attrib.items()
                         if k.rsplit("}", 1)[-1] == "elementName"), None)
            if name == "TTT":
                raw = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "value"), "") or ""
                ttt = [None if v == "-" else float(v) for v in raw.split()]
    if issue is None or not steps or ttt is None:
        raise ValueError(f"KML incompleto (issue={issue is not None} "
                         f"steps={len(steps)} ttt={ttt is not None})")
    if len(ttt) != len(steps):
        raise ValueError(f"TTT ({len(ttt)}) no calza con ForecastTimeSteps ({len(steps)})")
    return issue, steps, ttt


def k_to_unit(kelvin, unit):
    """Kelvin -> unidad de la estacion (F si el codigo empieza con K, C el resto)."""
    c = kelvin - 273.15
    return c * 9 / 5 + 32 if unit == "F" else c


def day_extremes(steps, ttt, off):
    """{fecha LOCAL: (tmax K, tmin K, n_horas)} agrupando la serie horaria por dia calendario
    local de la estacion. El >=18h se chequea en el caller (aca solo se agrupa)."""
    per_day = {}
    for t, v in zip(steps, ttt):
        if v is None:
            continue
        per_day.setdefault((t + dt.timedelta(hours=off)).date(), []).append(v)
    return {d: (max(vs), min(vs), len(vs)) for d, vs in per_day.items()}


def main(a):
    today = dt.date.fromisoformat(a.date)   # exigido explicito: reproducibilidad (= accumulate_*)
    # guard anti doble-corrida: clave = (issue_utc, station). A diferencia de accumulate_books
    # (guard por snapshot_date, 1 corrida/dia), aca el script corre VARIAS veces al dia y solo
    # debe agregar cuando DWD publico un ciclo nuevo. Sin --force: duplicar la misma corrida
    # real nunca sirve (sesgaria cualquier promedio posterior).
    seen = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                seen.add((r["issue_utc"], r["station"]))
    rows, skipped, errs = [], 0, 0
    for code, mid in MOSMIX_IDS.items():
        off, unit = STATIONS[code][2], STATIONS[code][3]
        # capture_utc = instante REAL de la descarga (por estacion, no por corrida del script)
        cap = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        try:
            r = requests.get(BASE.format(id=mid), timeout=60)
            r.raise_for_status()
            issue, steps, ttt = parse_kmz(r.content)
        except Exception as e:
            print(f"[WARN] {code} (MOSMIX {mid}): {e}", file=sys.stderr)
            errs += 1
            continue
        time.sleep(0.2)   # cortesia con opendata.dwd.de (~15KB c/u, 11 estaciones)
        issue_iso = issue.isoformat(timespec="seconds")
        if (issue_iso, code) in seen:
            skipped += 1   # esa corrida ya esta capturada -> saltear en silencio (es lo esperado)
            continue
        ext = day_extremes(steps, ttt, off)
        for lead in range(HORIZON_DAYS + 1):
            target = today + dt.timedelta(days=lead)
            if target not in ext:
                continue
            tmax_k, tmin_k, n = ext[target]
            if n < MIN_DAY_HOURS:
                continue   # dia local a medio cubrir (p.ej. HOY en Asia con ciclo tardio)
            rows.append([cap, issue_iso, code, target.isoformat(),
                         round(k_to_unit(tmax_k, unit), 1), round(k_to_unit(tmin_k, unit), 1), n])
    if rows:
        # (hardening 2026-07-10, mismo fix que capture_nbm) CSV de 0 bytes -> re-escribir header
        new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
        with open(OUT, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["capture_utc", "issue_utc", "station", "target",
                            "tmax_pred", "tmin_pred", "n_horas"])
            w.writerows(rows)
    st_new = len({r[2] for r in rows})
    status = "OK" if rows else ("SKIP" if skipped and not errs else "WARN")
    print(f"+{len(rows)} filas a {OUT} ({st_new} estaciones con corrida nueva, "
          f"{skipped} ya capturadas, {errs} errores).")
    log_run("mosmix", a.date, status,
            f"rows={len(rows)} stations_new={st_new} skipped={skipped} errores={errs}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Captura forward de MOSMIX_L (tmax/tmin del dia calendario LOCAL por estacion).")
    ap.add_argument("--date", required=True, help="fecha 'hoy' YYYY-MM-DD (target hoy..hoy+3)")
    main(ap.parse_args())
