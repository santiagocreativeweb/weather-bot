#!/usr/bin/env python3
# scripts/capture_cwa.py — Capturador FORWARD de CWA Taiwan (F-D0047-063) para RCSS (Taipei).
# [Creado 2026-07-10. Cierra la 12va estacion: NBM cubre KLGA/KORD, MOSMIX 9-11, CWA cubre RCSS.]
#
# POR QUE EXISTE: RCSS es la unica estacion sin fuente calibrada (MOSMIX solo tiene RCTP Taoyuan,
# otro microclima). La CWA publica el pronostico OFICIAL editado por forecasters (tipo NBM/NDFD)
# por distrito; el aeropuerto Songshan esta EN el distrito de Songshan (Geocode 63000010, punto
# representativo a ~2.5km de la pista, mismo llano del Taipei Basin). Sin archivo historico (CWA
# lo confirmo oficialmente) -> captura forward-only, igual que MOSMIX.
#
# FUENTE: mirror anonimo en AWS Open Data (sin API key, cert TLS sano — el cert de
# opendata.cwa.gov.tw rompe urllib de Python 3.14):
#   https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Forecast/F-D0047-063.json
# 4 corridas/dia (05:30/11:30/17:30/23:30 hora Taiwan, publicadas con ~45min de lag). El campo
# `Sent` del JSON es el instante de emision (+08:00) = id de la corrida; capture_utc = instante
# real de la descarga (invariante #2 anti-look-ahead: NUNCA usar la hora nominal de la corrida).
#
# SEMANTICA: el elemento 最高溫度 (MaxTemperature) viene en periodos de 12h. Solo tomamos los
# periodos DIA (06:00->18:00 local Taiwan), que contienen el pico ~15:00 local del mercado; los
# periodos noche (18->06) son el max vespertino/nocturno y se descartan. target = fecha LOCAL del
# periodo dia. LIMITACION conocida: MaxTemperature viene en grados C ENTEROS (menos sharpness que
# un MOS con decimales) — util como ancla/predictor extra, no reemplaza al EMOS.
# TRAMPA de esquema: en el mirror S3/fileapi ElementValue es un dict; en la datastore API es una
# lista de dicts. El parser tolera ambos.
#
# USO: python scripts/capture_cwa.py --date YYYY-MM-DD   (varias veces por dia; idempotente)
# Salida data/cwa_forward.csv: capture_utc,sent_utc,station,target,tmax_c
import argparse, csv, os, sys
import datetime as dt
import requests

URL = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Forecast/F-D0047-063.json"
GEOCODE = "63000010"          # 松山區 Songshan District (contiene el aeropuerto RCSS)
ELEMENT = "最高溫度"   # 最高溫度 (MaxTemperature) — clave en chino en el feed
STATION = "RCSS"
OUT = "data/cwa_forward.csv"
LOG = "data/accumulator.log"  # registro append-only compartido con accumulate_*/capture_*
HORIZON_DAYS = 3              # targets hoy..hoy+3 (= ventana del resto del sistema)
TMAX_SANE = (5.0, 45.0)       # rango fisico plausible para Taipei en C (fail-loud fuera de esto)


def log_run(script, snapshot, status, detail):
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def element_value(ev, key):
    """ElementValue del feed CWA: dict en S3/fileapi, lista de dicts en la datastore API."""
    if isinstance(ev, list):
        ev = ev[0] if ev else {}
    return ev.get(key)


def fetch():
    """-> (sent_utc datetime, [(target_date_local, tmax_c float), ...]) solo periodos DIA."""
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    d = r.json()["cwaopendata"]
    sent = dt.datetime.fromisoformat(d["Sent"]).astimezone(dt.timezone.utc)
    locs = d["Dataset"]["Locations"]["Location"]
    loc = next((l for l in locs if str(l.get("Geocode")) == GEOCODE), None)
    if loc is None:
        raise ValueError(f"Geocode {GEOCODE} (Songshan) no esta en el feed ({len(locs)} distritos)")
    el = next((e for e in loc["WeatherElement"] if e.get("ElementName") == ELEMENT), None)
    if el is None:
        names = [e.get("ElementName", "?") for e in loc["WeatherElement"]]
        raise ValueError(f"elemento MaxTemperature ausente (hay {len(names)} elementos)")
    out = []
    for t in el["Time"]:
        st = dt.datetime.fromisoformat(t["StartTime"])   # +08:00 local Taiwan
        en = dt.datetime.fromisoformat(t["EndTime"])
        # periodo DIA = arranca 06:00 local y termina 18:00 del MISMO dia local (cubre pico 15:00)
        if not (st.hour == 6 and en.hour == 18 and st.date() == en.date()):
            continue
        v = element_value(t.get("ElementValue", {}), "MaxTemperature")
        if v in (None, "", "-"):
            continue
        tmax = float(v)
        if not (TMAX_SANE[0] <= tmax <= TMAX_SANE[1]):
            raise ValueError(f"tmax fuera de rango sano: {tmax}C target {st.date()}")
        out.append((st.date(), tmax))
    if not out:
        raise ValueError("feed sin periodos dia con MaxTemperature")
    return sent, out


def main(a):
    today = dt.date.fromisoformat(a.date)   # exigido explicito: reproducibilidad (= capture_*)
    # guard anti doble-corrida por (sent_utc, station): el script corre varias veces al dia y
    # solo agrega cuando la CWA emitio una corrida nueva. Sin --force a proposito.
    seen = set()
    if os.path.exists(OUT) and os.path.getsize(OUT) > 0:
        with open(OUT) as f:
            for r in csv.DictReader(f):
                seen.add((r["sent_utc"], r["station"]))
    cap = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        sent, periods = fetch()
    except Exception as e:
        print(f"[FAIL] CWA F-D0047-063: {e}", file=sys.stderr)
        log_run("cwa", a.date, "FAIL", str(e))
        sys.exit(1)
    sent_iso = sent.isoformat(timespec="seconds")
    if (sent_iso, STATION) in seen:
        print(f"[SKIP] corrida CWA {sent_iso} ya capturada (no re-agrego).")
        log_run("cwa", a.date, "SKIP", f"sent={sent_iso} ya capturado")
        sys.exit(1)   # exit 1 benigno para el scheduler, igual que capture_nbm
    rows = [[cap, sent_iso, STATION, tgt.isoformat(), f"{tmax:.1f}"]
            for tgt, tmax in periods
            if today <= tgt <= today + dt.timedelta(days=HORIZON_DAYS)]
    if not rows:
        print("[WARN] corrida nueva pero sin targets en ventana hoy..+3 (feed corrido de fecha?).")
        log_run("cwa", a.date, "WARN", f"sent={sent_iso} 0 targets en ventana")
        return
    new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["capture_utc", "sent_utc", "station", "target", "tmax_c"])
        w.writerows(rows)
    print(f"+{len(rows)} filas a {OUT} (corrida {sent_iso}, targets "
          f"{rows[0][3]}..{rows[-1][3]}).")
    log_run("cwa", a.date, "OK", f"rows={len(rows)} sent={sent_iso}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Captura forward del pronostico CWA (distrito Songshan) para RCSS/Taipei.")
    ap.add_argument("--date", required=True, help="fecha 'hoy' YYYY-MM-DD (targets hoy..hoy+3)")
    main(ap.parse_args())
