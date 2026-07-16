#!/usr/bin/env python3
# scripts/accumulate_books.py — Junta el BOOK real (spread + profundidad) FORWARD, un dia a la vez.
# [Creado 2026-07-08. Plan paralelo, NO bloquea nada. Companion de accumulate_ensemble.py.]
#
# POR QUE EXISTE: el backtest asume half-spread FIJO hs=0.02 y no modela impacto (prices-history no
# trae el book). La envolvente parametrica mostro que el edge muere en half-spread ~6-7c; un spot-check
# de 1 mercado hoy dio ~1-2c efectivo en buckets liquidos. Pero: N=1 y liquidez de HOY (2026), no la
# de 2025 donde vive el edge mono-KLGA. El book de 2025 NO se puede reconstruir. Este script guarda,
# desde HOY hacia adelante, el book real en la VENTANA DE ENTRADA (D+1..D+3 antes del cierre) para
# que en ~90 dias haya muestra y se responda empiricamente:
#   "el half-spread EFECTIVO real (impacto incl., orden $40) se queda <= break-even (~6c)?"
# Si si -> el [ASUNCION] hs pasa a [VERIFICADO]. Si no -> re-correr la envolvente con el hs real.
#
# DISENO CLAVE: NO intenta capturar todos los leads en una corrida. La liquidez cambia fuerte con el
# tiempo-a-cierre, asi que cada mercado se snapshotea en varias corridas diarias sucesivas a medida
# que se acerca al cierre; la cadencia diaria arma el corte transversal por lead. Cada corrida solo
# captura los mercados VIVOS que hoy caen en la ventana, y guarda hours_to_close para estratificar.
#
# USO: correr UNA vez por dia (cron/scheduler). append-only; se niega a duplicar el mismo snapshot_date
# (guard anti doble-corrida) salvo --force. Ponderar a KLGA/NYC en el ANALISIS, no en la coleccion.
#
# Salida data/books_forward.csv:
#   snapshot_date,snapshot_ts,city,station,target,lead_day,hours_to_close,bucket_lo,bucket_hi,
#   mid,best_bid,best_ask,hs_top,hs_eff_40,filled_usd,depth_bestask_sh
#   (hs_top=half-spread top-of-book; hs_eff_40=half-spread EFECTIVO caminando asks por orden de $40)
import argparse, csv, json, os, re, sys, time
import datetime as dt
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
OUT = "data/books_forward.csv"
LOG = "data/accumulator.log"           # registro append-only de que cada corrida efectivamente pasó
ORDER_USD = 40.0            # = PER_MARKET_CAP_USD HOY: el tamano que golpearia el book. hs_eff_40 es
                           # DERIVADO de este supuesto; si cambia el cap, re-caminar `book_json` crudo.
TOP_LEVELS = 20            # niveles de bid/ask (con size) guardados crudos en `book_json`. El book de
                           # HOY no se archiva en ningun otro lado -> guardarlo crudo permite re-derivar
                           # hs_eff para CUALQUIER tamano futuro. ~KB/snapshot, costo trivial.
ENTRY_LEAD_MIN, ENTRY_LEAD_MAX = 0, 2   # targets HOY..HOY+2. [CORREGIDO 2026-07-08: la v1 usaba 1..3;
                                        # pero "lead 1" del sistema = corrida de la MISMA manana del
                                        # target (lead_h se mide al pico ~15:00 local) -> el bot SI
                                        # opera el dia del target, y HOY+3 no tiene forecast (no hay
                                        # 4ta columna de Previous Runs). El book del dia-del-target
                                        # es donde mas se opera; habia que muestrearlo.
MID_LO, MID_HI = 0.05, 0.95             # solo buckets no-degenerados (donde puede haber edge real)


def log_run(script, snapshot, status, detail):
    """Una linea por corrida a data/accumulator.log (sobrevive reinicios). Distingue '90 dias de
    data' de '60 dias con 30 huecos silenciosos': sin esto no se sabe si un dia corrio."""
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", newline="") as f:
        f.write(f"{ts} | {script} | {snapshot} | {status} | {detail}\n")

CITY_SERIES = {"nyc": 10005, "chicago": 10726, "london": 10006,
               "paris": 11168, "tokyo": 10740, "seoul": 10742,
               "shanghai": 10741, "madrid": 11345, "beijing": 11363,
               "munich": 11272, "taipei": 11346, "milan": 11343}
CITY_STATION = {"nyc": "KLGA", "chicago": "KORD", "london": "EGLC",
                "paris": "LFPB", "tokyo": "RJTT", "seoul": "RKSI",
                "shanghai": "ZSPD", "madrid": "LEMD", "beijing": "ZBAA",
                "munich": "EDDM", "taipei": "RCSS", "milan": "LIMC"}
CITY_RE = re.compile(r"highest-temperature-in-([a-z]+)-on-([a-z]+)-(\d+)-(\d+)")
MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"], 1)}


def parse_bucket(title):
    """groupItemTitle -> (lo, hi). Cola baja (None,hi); cola alta (lo,None); rango (lo,hi)."""
    t = (title or "").strip()
    nums = [int(x) for x in re.findall(r"\d+", t)]
    if not nums:
        return None
    if re.search(r"or (below|lower|less)", t, re.I):
        return (None, nums[0])
    if re.search(r"or (above|higher|more|greater)", t, re.I):
        return (nums[0], None)
    if len(nums) >= 2 and re.search(r"\d+\s*[-–]\s*\d+", t):
        return (nums[0], nums[1])
    return (nums[0], nums[0])


def slug_target(slug):
    """highest-temperature-in-nyc-on-july-9-2026 -> (city, date). None si no parsea."""
    m = CITY_RE.search(slug or "")
    if not m:
        return None
    city, mon, day, year = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
    if mon not in MONTHS:
        return None
    try:
        return city, dt.date(year, MONTHS[mon], day)
    except ValueError:
        return None


def live_events(city, sid):
    """Eventos VIVOS (no cerrados) de una ciudad. Una pagina basta: pocos abiertos a la vez."""
    try:
        r = requests.get(f"{GAMMA}/events",
                         params={"series_id": sid, "closed": "false", "limit": 100}, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] /events serie {sid} ({city}): {e}", file=sys.stderr)
        return []


def book(token):
    """CLOB /book -> (best_bid, best_ask, asks_ordenados). None si vacio/iliquido."""
    try:
        b = requests.get(f"{CLOB}/book", params={"token_id": token}, timeout=20).json()
    except Exception:
        return None
    bids, asks = b.get("bids", []), b.get("asks", [])
    if not bids or not asks:
        return None
    bb = max(float(x["price"]) for x in bids)
    ba = min(float(x["price"]) for x in asks)
    asks_s = sorted(asks, key=lambda a: float(a["price"]))       # asks ascendente (mejor = mas barato)
    bids_s = sorted(bids, key=lambda a: -float(a["price"]))      # bids descendente (mejor = mas caro)
    return bb, ba, asks_s, bids_s


def raw_levels(side, n):
    """Top-n niveles [precio, size] para archivar el book crudo (re-caminable a cualquier tamano)."""
    return [[round(float(x["price"]), 4), round(float(x["size"]), 1)] for x in side[:n]]


def eff_halfspread(mid, asks_s, budget):
    """Camina los asks gastando `budget` USDC -> (half-spread efectivo, USD llenado, shares)."""
    spent, shares = 0.0, 0.0
    for a in asks_s:
        p, sz = float(a["price"]), float(a["size"])
        take = min(p * sz, budget - spent)
        if take <= 0:
            break
        shares += take / p
        spent += take
        if spent >= budget - 1e-6:
            break
    if shares <= 0:
        return None, 0.0, 0.0
    return spent / shares - mid, spent, shares


def main(a):
    today = dt.date.fromisoformat(a.date)   # exigido explicito: reproducibilidad + Date.now no confiable
    if os.path.exists(OUT) and not a.force:
        with open(OUT) as f:
            if any(row.startswith(a.date + ",") for row in f):
                print(f"[ABORT] ya hay filas para snapshot {a.date} en {OUT}. --force para re-agregar.",
                      file=sys.stderr)
                log_run("books", a.date, "SKIP", "snapshot ya existe (no re-corri)")
                sys.exit(1)
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for city, sid in CITY_SERIES.items():
        station = CITY_STATION[city]
        for e in live_events(city, sid):
            st = slug_target(e.get("slug") or "")
            if not st:
                continue
            _, target = st
            lead = (target - today).days
            if not (ENTRY_LEAD_MIN <= lead <= ENTRY_LEAD_MAX):
                continue
            ed = e.get("endDate")
            htc = ((dt.datetime.fromisoformat(ed.replace("Z", "+00:00")) - now).total_seconds() / 3600
                   if ed else "")
            for m in e.get("markets", []):
                toks = m.get("clobTokenIds")
                if isinstance(toks, str):
                    toks = json.loads(toks or "[]")
                if not toks:
                    continue
                bk = book(toks[0])
                if not bk:
                    continue
                bb, ba, asks_s, bids_s = bk
                mid = (bb + ba) / 2
                if not (MID_LO < mid < MID_HI):
                    continue
                buck = parse_bucket(m.get("groupItemTitle"))
                if buck is None:
                    continue
                lo, hi = buck
                hs_eff, filled, _ = eff_halfspread(mid, asks_s, ORDER_USD)
                depth_ask = sum(float(x["size"]) for x in asks_s if abs(float(x["price"]) - ba) < 1e-9)
                # book crudo (top-N ambos lados): re-caminable a cualquier tamano si cambia el cap.
                bjson = json.dumps({"a": raw_levels(asks_s, TOP_LEVELS), "b": raw_levels(bids_s, TOP_LEVELS)},
                                   separators=(",", ":"))
                rows.append([today.isoformat(), now.isoformat(timespec="minutes"), city, station,
                             target.isoformat(), lead, round(htc, 1) if htc != "" else "",
                             "" if lo is None else lo, "" if hi is None else hi,
                             round(mid, 3), round(bb, 3), round(ba, 3), round((ba - bb) / 2, 3),
                             round(hs_eff, 3) if hs_eff is not None else "",
                             round(filled, 0), round(depth_ask, 0), bjson])
            time.sleep(0.1)
    if not rows:
        print("[WARN] 0 filas (ningun mercado vivo en ventana D+1..D+3, o API caida). No agrego nada.",
              file=sys.stderr)
        log_run("books", a.date, "WARN", "0 filas (sin mercados en ventana o API caida)")
        return
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["snapshot_date", "snapshot_ts", "city", "station", "target", "lead_day",
                        "hours_to_close", "bucket_lo", "bucket_hi", "mid", "best_bid", "best_ask",
                        "hs_top", "hs_eff_40", "filled_usd", "depth_bestask_sh", "book_json"])
        w.writerows(rows)
    liq = [r for r in rows if r[13] != ""]
    med = sorted(r[13] for r in liq)[len(liq) // 2] if liq else float("nan")
    cities = len({r[2] for r in rows})
    print(f"+{len(rows)} filas a {OUT} (snapshot {today}). hs_eff_40 mediano={med:.3f} "
          f"(break-even ~0.06). Correr a diario; validar en ~90 dias.")
    log_run("books", a.date, "OK", f"rows={len(rows)} cities={cities} hs_eff_med={med:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Snapshot diario del book real en la ventana de entrada.")
    ap.add_argument("--date", required=True, help="fecha del snapshot YYYY-MM-DD (hoy)")
    ap.add_argument("--force", action="store_true", help="permitir re-agregar el mismo snapshot_date")
    main(ap.parse_args())
