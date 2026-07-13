#!/usr/bin/env python3
# scripts/capture_nbm.py — Captura FORWARD en vivo del NBM (National Blend of Models, NOAA) para
# KLGA/KORD. [Creado 2026-07-10. Companion de accumulate_books.py / accumulate_ensemble.py.]
#
# POR QUE EXISTE: el NBM es un blend calibrado POR ESTACION (MOS-style, corregido contra el METAR
# que es exactamente el target del mercado). Sus bulletins de texto traen TXN (tmax/tmin de
# periodo 18h, en °F) y XND (desvio estandar de TXN -> sigma directo para s2). NOMADS retiene
# ~2 dias, asi que hay que capturar en vivo cada ciclo; el archivo historico point-in-time vive
# en AWS S3 (ver memoria fuentes-forecast-nbm-mosmix) pero ESTE script es solo el forward.
#
# ANTI-LOOK-AHEAD (invariante #2): capture_utc = instante REAL de la captura (somos nosotros
# bajando el bulletin en vivo), y cycle_utc = init de la corrida. El avail honesto es
# capture_utc: a esa hora seguro ya lo teniamos.
#
# FUENTE: https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.YYYYMMDD/CC/text/
#   blend_nbstx.tCCz (NBS, horas ~6-72) con ciclos CC en {01,07,13,19}; blend_nbetx.tCCz (NBE,
#   dias 3-8) como fallback si al NBS le falta algun target de la ventana. El bulletin es texto
#   de ancho fijo ~30MB con TODAS las estaciones: se procesa en STREAMING (nunca se guarda el
#   archivo entero); el bloque crudo de cada estacion se archiva en data/nbm_raw/ para auditoria.
#
# PARSEO — TRAMPA DE FECHAS (verificado empiricamente 2026-07-10 contra ciclos 07z y 13z):
#   la fila FHR da la hora pronostico de cada columna -> col_utc = cycle_utc + FHR (se valida
#   contra la fila UTC). El TXN en la columna de 00 UTC del dia D+1 es el MAX DIURNO del dia D
#   local US; el de la columna 12 UTC es el MIN nocturno. Regla: convertir el instante de la
#   columna a hora local (offset fijo de STATIONS); hora local >= 12 (~19:00) -> max diurno,
#   target = fecha local; hora local < 12 (~07:00) -> min nocturno, se descarta.
#   Verificacion en vivo: ciclo 07z 2026-07-10 dio KLGA TXN=89 para target HOY (consenso ~87,
#   vivo toco 85 -> plausible). OJO: el ciclo 13z ya NO trae el max de HOY (su ventana quedo
#   atras del inicio del forecast); su primer TXN es el min de manana. Anclar los valores a las
#   posiciones de la fila FHR tambien excluye solo el par CLIMO del formato NBE.
#
# USO: correr tras cada ciclo (~init+70min). append-only; guard anti doble-corrida por
# (cycle_utc, station, target) salvo --force.
#
# Salida data/nbm_forward.csv:
#   capture_utc,cycle_utc,station,target,txn_f,xnd_f,source   (source: nbs|nbe)
import argparse, csv, os, re, sys
import datetime as dt
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from show_live import STATIONS      # metadata unica (lat, lon, off_horas_utc, unit) — no duplicar

NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod"
OUT = "data/nbm_forward.csv"
RAW_DIR = "data/nbm_raw"
LOG = "data/accumulator.log"        # registro append-only de que cada corrida efectivamente pasó
NBM_STATIONS = ["KLGA", "KORD"]     # solo las °F de EEUU: NBM cubre CONUS, no el resto del mundo
CYCLES = (19, 13, 7, 1)             # ciclos con bulletins de texto, del mas nuevo al mas viejo
LEAD_MIN, LEAD_MAX = 0, 2           # targets HOY..HOY+2 (= ventana de entrada de accumulate_books)
HDR_RE = re.compile(r"^ ([A-Z][A-Z0-9]{3,5})\s+NBM V\S* +NB[SE] GUIDANCE\s+"
                    r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{2})(\d{2}) UTC")


def log_run(script, snapshot, status, detail):
    """Una linea por corrida a data/accumulator.log (sobrevive reinicios). Distingue '90 dias de
    data' de '60 dias con 30 huecos silenciosos': sin esto no se sabe si un dia corrio."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def fetch_blocks(url, stations):
    """Baja el bulletin en STREAMING y devuelve {station: [lineas del bloque]}. None si 404.
    Nunca materializa los ~30MB: corta la conexion apenas cierra el ultimo bloque buscado."""
    r = requests.get(url, stream=True, timeout=(30, 120))
    if r.status_code in (403, 404):     # NOMADS devuelve 403 (no 404) para rutas aun no publicadas
        r.close()
        return None
    r.raise_for_status()
    want, blocks, cur = set(stations), {}, None
    try:
        # chunk_size default (512B) hace la descarga ~90KB/s; 1MB la vuelve cuestion de segundos
        for raw in r.iter_lines(chunk_size=1 << 20):
            line = raw.decode("latin-1")
            m = HDR_RE.match(line)
            if m:                                   # header de estacion -> cierra el bloque previo
                cur = m.group(1) if m.group(1) in want else None
                if cur is None and len(blocks) == len(want):
                    break                           # ya tenemos todos los bloques: descartar resto
            if cur is not None:
                blocks.setdefault(cur, []).append(line)
    finally:
        r.close()
    return blocks


def parse_block(lines, off):
    """Bloque crudo -> (cycle_dt, [(target_local, txn, xnd), ...]) SOLO maximos diurnos.
    Ancla cada valor a la posicion de su token en la fila FHR (ancho fijo, right-aligned):
    funciona igual para NBS y NBE, y en NBE deja afuera el par CLIMO (sin FHR debajo)."""
    m = HDR_RE.match(lines[0])
    if not m:
        raise ValueError(f"header NBM no reconocido: {lines[0]!r}")
    mon, day, year, hh, mm = (int(m.group(i)) for i in range(2, 7))
    cycle_dt = dt.datetime(year, mon, day, hh, mm, tzinfo=dt.timezone.utc)
    rows = {}
    for ln in lines[1:]:
        lm = re.match(r"^ (FHR|UTC|TXN|XND)\b", ln)
        if lm:
            rows[lm.group(1)] = ln
    if "FHR" not in rows or "TXN" not in rows:
        raise ValueError(f"bloque sin filas FHR/TXN (formato cambio?): {sorted(rows)}")

    def col(line, end):                             # campo de 3 chars right-aligned en `end`
        tok = line.ljust(end)[end - 3:end].strip()
        return int(tok) if tok not in ("", "-") else None

    out = []
    for t in re.finditer(r"-?\d+", rows["FHR"]):
        fhr, e = int(t.group()), t.end()
        col_dt = cycle_dt + dt.timedelta(hours=fhr)
        utc_h = col(rows.get("UTC", ""), e)
        if utc_h is not None and utc_h % 24 != col_dt.hour:  # sanity: FHR y fila UTC deben coincidir
            raise ValueError(f"desalineado: FHR {fhr} -> {col_dt.hour:02d}Z pero fila UTC dice {utc_h}")
        txn = col(rows["TXN"], e)
        if txn is None or txn <= -99 or txn >= 130:          # blanco o centinela de missing
            continue
        loc = col_dt + dt.timedelta(hours=off)
        if loc.hour < 12:                                    # ~07:00 local = MIN nocturno, no sirve
            continue
        out.append((loc.date(), txn, col(rows.get("XND", ""), e)))
    return cycle_dt, out


def capture_cycle(cyc_date, cc, today):
    """Baja NBS del ciclo (y NBE solo si falta algun target de la ventana), archiva los bloques
    crudos y devuelve (cycle_dt, filas [station, target, txn, xnd, source])."""
    os.makedirs(RAW_DIR, exist_ok=True)
    tag = f"{cyc_date:%Y%m%d}_t{cc:02d}z"
    filas, cycle_dt, faltan = [], None, set()
    for src, prod in (("nbs", "nbstx"), ("nbe", "nbetx")):
        if src == "nbe" and not faltan:
            break                                   # NBS ya cubrio toda la ventana: no bajar NBE
        url = f"{NOMADS}/blend.{cyc_date:%Y%m%d}/{cc:02d}/text/blend_{prod}.t{cc:02d}z"
        blocks = fetch_blocks(url, NBM_STATIONS)
        if blocks is None:
            if src == "nbs":
                return None, []                     # ciclo inexistente/todavia no publicado
            print(f"[WARN] sin NBE para {tag}; targets faltantes quedan sin fila: "
                  f"{sorted(str(x[1]) for x in faltan)}", file=sys.stderr)
            break
        for st in NBM_STATIONS:
            if st not in blocks:
                print(f"[WARN] {st} no aparecio en blend_{prod} {tag}", file=sys.stderr)
                continue
            suf = "" if src == "nbs" else "_nbe"
            with open(os.path.join(RAW_DIR, f"{st}_{tag}{suf}.txt"), "w", newline="") as f:
                f.write("\n".join(blocks[st]) + "\n")           # bloque crudo para auditoria (pisa)
            off = STATIONS[st][2]
            cyc, vals = parse_block(blocks[st], off)
            cycle_dt = cycle_dt or cyc
            ya = {(s, t) for s, t, *_ in filas}
            for target, txn, xnd in vals:
                lead = (target - today).days
                if LEAD_MIN <= lead <= LEAD_MAX and (st, target) not in ya:
                    filas.append([st, target, txn, xnd, src])
        if src == "nbs":
            tiene = {(s, t) for s, t, *_ in filas}
            faltan = {(s, today + dt.timedelta(days=l))
                      for s in NBM_STATIONS for l in range(LEAD_MIN, LEAD_MAX + 1)} - tiene
    return cycle_dt, filas


def main(a):
    today = dt.date.fromisoformat(a.date)   # exigido explicito: reproducibilidad + Date.now no confiable
    # elegir corrida: la mas reciente disponible de --date (o el dia anterior si aun no hay ninguna);
    # --cycle fuerza el ciclo CC de --date (util para cron deterministico y para backfill del dia).
    if a.cycle is not None:
        candidatos = [(today, a.cycle)]
    else:
        candidatos = [(d, cc) for d in (today, today - dt.timedelta(days=1)) for cc in CYCLES]
    cycle_dt, filas = None, []
    for cyc_date, cc in candidatos:
        try:
            cycle_dt, filas = capture_cycle(cyc_date, cc, today)
        except Exception as e:
            print(f"[FAIL] {cyc_date:%Y%m%d} t{cc:02d}z: {e}", file=sys.stderr)
            log_run("nbm", a.date, "FAIL", f"cycle={cyc_date:%Y%m%d}t{cc:02d}z err={e}")
            sys.exit(1)
        if cycle_dt is not None:
            break
        print(f"[INFO] blend.{cyc_date:%Y%m%d} t{cc:02d}z todavia no esta; pruebo anterior",
              file=sys.stderr)
    if cycle_dt is None:
        print("[WARN] ningun ciclo NBM disponible en NOMADS (raro: retiene ~2 dias). No agrego nada.",
              file=sys.stderr)
        log_run("nbm", a.date, "WARN", "sin ciclo disponible en NOMADS")
        return
    cyc_iso = cycle_dt.isoformat(timespec="minutes")
    if not filas:
        print(f"[WARN] ciclo {cyc_iso} sin TXN en ventana hoy..D+{LEAD_MAX}. No agrego nada.",
              file=sys.stderr)
        log_run("nbm", a.date, "WARN", f"cycle={cyc_iso} 0 filas en ventana")
        return

    # guard anti doble-corrida por (cycle_utc, station, target): re-correr el mismo ciclo no duplica
    vistos = set()
    if os.path.exists(OUT):
        with open(OUT, newline="") as f:
            vistos = {(r["cycle_utc"], r["station"], r["target"]) for r in csv.DictReader(f)}
    nuevas = [r for r in filas if a.force or (cyc_iso, r[0], r[1].isoformat()) not in vistos]
    dups = len(filas) - len(nuevas)
    if not nuevas:
        print(f"[ABORT] las {dups} filas del ciclo {cyc_iso} ya estan en {OUT}. --force para re-agregar.",
              file=sys.stderr)
        log_run("nbm", a.date, "SKIP", f"cycle={cyc_iso} ya capturado (no re-agregue)")
        sys.exit(1)

    now = dt.datetime.now(dt.timezone.utc)
    # 0 bytes cuenta como nuevo: si no, un CSV vaciado externamente quedaria sin header y el
    # guard del proximo run reventaria con KeyError (verificado adversarialmente 2026-07-10)
    new = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["capture_utc", "cycle_utc", "station", "target", "txn_f", "xnd_f", "source"])
        for st, target, txn, xnd, src in sorted(nuevas, key=lambda r: (r[0], r[1])):
            w.writerow([now.isoformat(timespec="minutes"), cyc_iso, st, target.isoformat(),
                        txn, "" if xnd is None else xnd, src])
    resumen = "; ".join(f"{st} {t.isoformat()}={txn}F(sd={xnd})"
                        for st, t, txn, xnd, _ in sorted(nuevas, key=lambda r: (r[0], r[1])))
    print(f"+{len(nuevas)} filas a {OUT} (ciclo {cyc_iso}, {dups} dups salteadas). {resumen}")
    log_run("nbm", a.date, "OK", f"cycle={cyc_iso} rows={len(nuevas)} dups={dups}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Captura forward del NBM (TXN/XND) para KLGA/KORD.")
    ap.add_argument("--date", required=True, help="fecha 'hoy' YYYY-MM-DD (define la ventana de targets)")
    ap.add_argument("--cycle", type=int, choices=[1, 7, 13, 19], default=None,
                    help="forzar ciclo CC de --date (default: el mas reciente disponible)")
    ap.add_argument("--force", action="store_true", help="re-agregar aunque (ciclo,station,target) exista")
    main(ap.parse_args())
