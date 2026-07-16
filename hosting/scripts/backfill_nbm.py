#!/usr/bin/env python3
# scripts/backfill_nbm.py — Backfill POINT-IN-TIME del NBM (texto NBS) para KLGA/KORD desde el
# archivo publico de AWS. [Creado 2026-07-10.]
#
# POR QUE EXISTE: es la UNICA fuente nueva con archivo historico point-in-time HONESTO (memoria
# fuentes-forecast-nbm-mosmix). El header HTTP Last-Modified de S3 es el instante REAL en que esa
# corrida quedo publicada (~init+70min) -> lo guardamos como avail_utc y cumplimos el INVARIANTE
# anti-look-ahead #2 (CLAUDE.md). Con esto se puede correr el A/B historico NBM vs calibrador V2
# sobre la misma ventana del lab (data/backfill_check.csv) y decidir si integrar NBM al motor.
#
# QUE BAJA por cada dia target D (ventana del lab):
#   - ciclo 13z del dia D-1  -> lead 2 (la corrida operativa contra la que esta medido el bot)
#   - ciclo 01z del dia D    -> lead 1 HONESTO (hoy no hay NINGUNA fuente lead-1 limpia por bug #5)
#
# FORMATO NBS (verificado empiricamente contra blend.20260509/13):
#   bloque de estacion arranca con ' KLGA    NBM V5.0 NBS GUIDANCE    5/09/2026  1300 UTC'
#   filas fijas de 3 chars por columna; FHR da la hora de pronostico de cada columna.
#   TXN vive SOLO en las columnas 00 y 12 UTC: la columna 00 UTC del dia D+1 es el MAX diurno
#   del dia D (°F); la columna 12 UTC es el min nocturno (la descartamos). XND = desvio (°F).
#   TRAMPA DE FECHAS: no confiar en la fila DT; derivamos la fecha de cada columna como
#   init + FHR horas y nos quedamos con valid.hour==0 y valid.date()-1dia == target.
#
# USO: python scripts/backfill_nbm.py --start 2026-05-10 --end 2026-07-08 [--only-lead 2]
# Append-only a data/nbm_backfill.csv con guard anti doble-corrida: si (cycle_utc,station,target)
# ya esta en el CSV no se re-baja (permite reanudar tras timeout/corte). Streaming: extrae solo
# los bloques KLGA/KORD y corta la descarga apenas los tiene (no acumula los ~30MB en disco).
import argparse, csv, os, re, sys
import datetime as dt
from email.utils import parsedate_to_datetime
import requests

BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
OUT = "data/nbm_backfill.csv"
LOG = "data/accumulator.log"          # mismo registro append-only que accumulate_books.py
HEADER = ["avail_utc", "cycle_utc", "station", "target", "lead", "txn_f", "xnd_f"]
STATIONS = ["KLGA", "KORD"]           # unicas con mercado °F y archivo NBM (memoria de fuentes)
# (lead, ciclo, offset de dias del ciclo respecto al target): 13z de D-1 y 01z de D
CYCLES = {2: (13, -1), 1: (1, 0)}


def log_run(script, snapshot, status, detail):
    """Una linea por corrida a data/accumulator.log (= accumulate_books.py). Sin esto no se
    distingue 'ventana completa' de 'ventana con huecos silenciosos'."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")


def existing_keys():
    """(cycle_utc, station, target) ya presentes -> guard anti doble-corrida / reanudacion."""
    keys = set()
    if os.path.exists(OUT):
        with open(OUT, newline="") as f:
            for row in csv.DictReader(f):
                keys.add((row["cycle_utc"], row["station"], row["target"]))
    return keys


def fetch_cycle(day, cc, need):
    """Baja en streaming el NBS de blend.{day}/{cc}z y extrae SOLO los bloques de `need`.
    Devuelve (avail_utc_iso, {station: [lineas]}) o None si el archivo no existe (404)."""
    url = f"{BASE}/blend.{day:%Y%m%d}/{cc:02d}/text/blend_nbstx.t{cc:02d}z"
    r = requests.get(url, stream=True, timeout=300)
    if r.status_code in (403, 404):    # S3 sin ListBucket devuelve 403 para keys ausentes
        return None
    r.raise_for_status()
    # Last-Modified de S3 = instante REAL de publicacion de la corrida (INVARIANTE #2): es el
    # unico avail admisible; NUNCA usar la hora del ciclo como avail.
    avail = parsedate_to_datetime(r.headers["Last-Modified"]).astimezone(dt.timezone.utc)
    heads = tuple(f" {s} " for s in need)
    blocks, cur = {}, None
    try:
        # [FIX 2026-07-10 auditoria] chunk_size default de iter_lines = 512 bytes -> ~90KB/s
        # sobre S3 (el bulletin de 30MB tardaba >5min y la corrida moria por timeout).
        # Con chunks de 1MB el mismo archivo se recorre en segundos.
        for raw in r.iter_lines(chunk_size=1 << 20):
            line = raw.decode("ascii", "replace")
            if "NBS GUIDANCE" in line:
                st = line.split()[0] if line.strip() else ""
                if line.startswith(heads) and st in need:
                    cur = st
                    blocks[cur] = [line]
                else:
                    if len(blocks) == len(need):
                        break          # ya tenemos todos los bloques: cortar la descarga
                    cur = None
            elif cur is not None:
                if line.strip() == "":
                    cur = None
                    if len(blocks) == len(need):
                        break
                else:
                    blocks[cur].append(line)
    finally:
        r.close()
    return avail.isoformat(timespec="seconds"), blocks


def parse_txn(lines, init_utc, target):
    """(txn_f, xnd_f) del MAX diurno del dia `target` dentro de un bloque NBS, o None.
    Columna correcta = FHR tal que init+FHR cae 00 UTC del dia target+1 (verificado: mapear
    +/-1 dia dispara el MAE contra obs de ~2°F a >4°F)."""
    def row(tag):
        return next((l for l in lines if l.startswith(f" {tag} ")), None)
    fhr, txn, xnd = row("FHR"), row("TXN"), row("XND")
    if not (fhr and txn):
        return None
    for m in re.finditer(r"\d+", fhr):
        valid = init_utc + dt.timedelta(hours=int(m.group()))
        if valid.hour == 0 and valid.date() - dt.timedelta(days=1) == target:
            e = m.end()                      # campos fijos de 3 chars, alineados a derecha
            t = txn[e - 3:e].strip() if len(txn) >= e - 2 else ""
            if not t:
                return None
            x = xnd[e - 3:e].strip() if xnd and len(xnd) >= e - 2 else ""
            return int(t), (int(x) if x else "")
    return None


def main(a):
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    leads = [int(a.only_lead)] if a.only_lead else sorted(CYCLES, reverse=True)  # 2 primero: es la señal operativa
    done = existing_keys()
    # [FIX 2026-07-10 auditoria] un CSV vacio (corrida previa muerta antes del flush del header)
    # debe tratarse como archivo nuevo, si no queda para siempre sin header.
    new_file = not os.path.exists(OUT) or os.path.getsize(OUT) == 0
    added = skipped = missing = 0
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        d = start
        while d <= end:
            for lead in leads:
                cc, off = CYCLES[lead]
                cyc_day = d + dt.timedelta(days=off)
                init = dt.datetime(cyc_day.year, cyc_day.month, cyc_day.day, cc,
                                   tzinfo=dt.timezone.utc)
                cyc_iso = init.isoformat(timespec="seconds")
                need = [s for s in STATIONS if (cyc_iso, s, d.isoformat()) not in done]
                if not need:
                    skipped += 1
                    continue
                try:
                    got = fetch_cycle(cyc_day, cc, set(need))
                except Exception as e:
                    print(f"[WARN] {cyc_day} t{cc:02d}z: {e} (reanudable, sigo)", file=sys.stderr)
                    missing += 1
                    continue
                if got is None:
                    print(f"[WARN] {cyc_day} t{cc:02d}z: 404 (corrida ausente del archivo)",
                          file=sys.stderr)
                    missing += 1
                    continue
                avail, blocks = got
                for s in need:
                    if s not in blocks:
                        print(f"[WARN] {cyc_day} t{cc:02d}z: sin bloque {s}", file=sys.stderr)
                        continue
                    p = parse_txn(blocks[s], init, d)
                    if p is None:
                        print(f"[WARN] {cyc_day} t{cc:02d}z {s}: sin TXN para target {d}",
                              file=sys.stderr)
                        continue
                    w.writerow([avail, cyc_iso, s, d.isoformat(), lead, p[0], p[1]])
                    done.add((cyc_iso, s, d.isoformat()))
                    added += 1
                f.flush()                    # progreso incremental: reanudable tras corte
            d += dt.timedelta(days=1)
    print(f"+{added} filas a {OUT} ({start}..{end}, leads={leads}); "
          f"skip(ya presentes)={skipped} ciclos, huecos/errores={missing}.")
    log_run("nbm_backfill", f"{start}..{end}", "OK",
            f"rows={added} skip={skipped} missing={missing} leads={leads}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Backfill point-in-time del NBM (NBS text, AWS) para KLGA/KORD.")
    ap.add_argument("--start", required=True, help="primer dia TARGET YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="ultimo dia TARGET YYYY-MM-DD (inclusive)")
    ap.add_argument("--only-lead", choices=["1", "2"], default=None,
                    help="bajar solo ese lead (default: ambos, lead 2 primero)")
    main(ap.parse_args())
