#!/usr/bin/env python3
# scripts/download_polymarket.py — v2: enumera mercados de temperatura y baja precios historicos.
# [Reescrito 2026-07-07 tras diagnostico EN VIVO. La v1 filtraba /markets?closed=true por substring
#  'highest temperature' y matcheaba 0: ese endpoint ordena por mas ANTIGUOS (mercados 2020) y las
#  questions de clima no contienen esa frase exacta. Verificado en vivo.]
#
# COMO SE DESCUBREN LOS MERCADOS (verificado):
#   * Los mercados de tmax diaria son EVENTOS recurrentes agrupados por serie por ciudad
#     (seriesSlug 'nyc-daily-weather', etc.) y etiquetados con tag 'highest-temperature' (id 104596).
#   * Enumerar: GET /events?tag_id=104596&closed=true paginando por offset -> todas las ciudades.
#   * Cada evento trae markets[] = buckets; groupItemTitle = '66-67°F' / '15°C' / '65°F or below';
#     clobTokenIds[0] = token YES; volume por market (saltar 0 -> sin historia -> ahorra llamadas).
#   * Precios historicos: CLOB /prices-history CON startTs/endTs EXPLICITOS (interval=max da []).
#
# Contratos de salida:
#   data/markets.csv = station,target,bucket,lo,hi,open_t,close_t   (lo/hi vacio = cola abierta)
#   data/prices.csv  = t,station,target,bucket,lo,hi,mid,hs         (mid=p_yes; hs=half-spread)
# [ASUNCION] hs=0.02 fijo: prices-history no trae el book, no hay half-spread historico real.
# [VERIFICAR-VIVO] mapeo ciudad->estacion (coords/rules exactas del mercado antes de dinero real).
import argparse, csv, json, re, sys, time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TAG_HIGHEST_TEMP = 104596

# token de ciudad en el slug (highest-temperature-in-{token}-on-...) -> estacion del motor.
CITY_STATION = {"nyc": "KLGA", "chicago": "KORD", "london": "EGLC",
                "paris": "LFPB", "tokyo": "RJTT", "seoul": "RKSI",
                "shanghai": "ZSPD", "madrid": "LEMD", "beijing": "ZBAA",
                "munich": "EDDM", "taipei": "RCSS", "milan": "LIMC"}
# series diarias por ciudad. Enumerar por serie (no por tag global) recupera TODA la historia:
# los mercados de 2025 estan tageados distinto (tag 84 'weather', no 104596 'highest-temperature',
# que es reciente) -> el tag global se los perdia. NYC/London llegan a ene-2025. [VERIFICAR-VIVO:
# los series_id son estables pero confirmar si Polymarket agrega/renombra series.]
CITY_SERIES = {"nyc": 10005, "chicago": 10726, "london": 10006,
               "paris": 11168, "tokyo": 10740, "seoul": 10742,
               "shanghai": 10741, "madrid": 11345, "beijing": 11363,
               "munich": 11272, "taipei": 11346, "milan": 11343}
HALF_SPREAD_ASSUMED = 0.02
CITY_RE = re.compile(r"highest-temperature-in-([a-z]+)-on-")
ARGS_MARKETS_ONLY = False   # lo setea el bloque __main__; default seguro si se importa el modulo


def parse_bucket(title):
    """groupItemTitle -> (lo, hi). Cola baja: (None, hi). Cola alta: (lo, None). Rango: (lo, hi).
    Unico valor: (v, v). Devuelve None si no parsea (se salta, se avisa)."""
    t = (title or "").strip()
    nums = [int(x) for x in re.findall(r"\d+", t)]
    low = re.search(r"or (below|lower|less)", t, re.I)
    high = re.search(r"or (above|higher|more|greater)", t, re.I)
    if not nums:
        return None
    if low:
        return (None, nums[0])
    if high:
        return (nums[0], None)
    if len(nums) >= 2 and re.search(r"\d+\s*[-–]\s*\d+", t):
        return (nums[0], nums[1])
    return (nums[0], nums[0])


def enum_events(closed_only, start_date, end_date, max_events):
    """Eventos de tmax de MIS ciudades en [start,end]. Enumera por SERIE por ciudad (no por tag
    global) -> captura toda la historia, incluida 2025 (tageada distinto). Pagina cada serie hasta
    vaciarla (una sola ciudad ~<550 eventos/1.5años, muy debajo del offset-cap de Gamma). Filtra
    fecha client-side por endDate."""
    out = []
    for city, sid in CITY_SERIES.items():
        offset = 0
        while len(out) < max_events:
            params = {"series_id": sid, "limit": 100, "offset": offset}
            if closed_only:
                params["closed"] = "true"
            r = requests.get(f"{GAMMA}/events", params=params, timeout=60)
            if r.status_code != 200:
                print(f"[WARN] /events {r.status_code} serie {sid} ({city}) offset {offset}; corto.", file=sys.stderr)
                break
            batch = r.json()
            if not batch:
                break
            for e in batch:
                ed = (e.get("endDate") or "")[:10]
                m = CITY_RE.search(e.get("slug") or "")
                if m and m.group(1) in CITY_STATION and ed and start_date <= ed <= end_date:
                    out.append(e)
            offset += 100
            time.sleep(0.15)
    return out


def price_history(token, start_ts, end_ts, retries=3):
    """1 llamada a CLOB /prices-history. Reintenta con backoff en 429 (rate limit) por la concurrencia."""
    for i in range(retries):
        try:
            r = requests.get(f"{CLOB}/prices-history",
                             params={"market": token, "startTs": start_ts, "endTs": end_ts, "fidelity": 60},
                             timeout=60)
        except requests.RequestException:
            time.sleep(0.5 * (i + 1)); continue
        if r.status_code == 429:
            time.sleep(0.8 * (i + 1)); continue
        if r.status_code != 200:
            return []
        return r.json().get("history", [])
    return []


def main(a):
    mk_rows, px_rows = [], []
    events = enum_events(not a.include_open, a.start, a.end, a.max_events)
    print(f"eventos de temperatura en [{a.start},{a.end}] para {sorted(set(CITY_STATION.values()))}: {len(events)}",
          file=sys.stderr)
    seen_cities = set()
    tasks = []   # (token, start_ts, end_ts, st, target, lo, hi) para bajar precios concurrente
    for e in events:
        city = CITY_RE.search(e["slug"]).group(1)
        st = CITY_STATION[city]
        seen_cities.add(city)
        target = (e.get("endDate") or "")[:10]      # dia del tmax (= dia de resolucion)
        try:
            open_dt = _to_naive_utc(e.get("startDate") or e.get("createdAt"))
            close_dt = _to_naive_utc(e.get("endDate"))
            start_ts = int(open_dt.replace(tzinfo=dt.timezone.utc).timestamp())
            end_ts = int(close_dt.replace(tzinfo=dt.timezone.utc).timestamp()) + 6 * 3600
        except Exception:
            continue
        # naive-UTC en TODAS las columnas de tiempo: el motor mezcla close_t (markets) con t (prices)
        # y forecasts (init/avail), y pandas no resta tz-aware con tz-naive. Todo naive-UTC consistente.
        open_t = open_dt.isoformat()
        close_t = close_dt.isoformat()
        for mk in e.get("markets", []):
            b = parse_bucket(mk.get("groupItemTitle"))
            if b is None:
                print(f"[WARN] bucket no parseado: {mk.get('groupItemTitle')!r} ({e['slug']})", file=sys.stderr)
                continue
            lo, hi = b
            # resolucion REAL del mercado (settlement del oraculo WU) desde outcomePrices=[p_yes,p_no]:
            # ["1","0"] = este bucket GANO, ["0","1"] = perdio. Es el ground-truth de PAGO (lo que
            # cobras en vivo), disponible para ~100% de los resueltos (no depende de que el precio
            # converja en la data). '' si aun no resolvio. Ver engine.resolve="market".
            resolved = ""
            op = mk.get("outcomePrices")
            if isinstance(op, str):
                try: op = json.loads(op)
                except Exception: op = None
            if isinstance(op, list) and len(op) == 2:
                try: resolved = 1 if float(op[0]) > 0.5 else (0 if float(op[0]) < 0.5 else "")
                except Exception: pass
            mk_rows.append([st, target, _bucket_id(lo, hi), _blank(lo), _blank(hi), open_t, close_t, resolved])
            if ARGS_MARKETS_ONLY or float(mk.get("volume") or 0) <= 0:   # sin prices o sin volumen
                continue
            toks = mk.get("clobTokenIds")
            if isinstance(toks, str):
                toks = json.loads(toks or "[]")
            if toks:
                tasks.append((toks[0], start_ts, end_ts, st, target, lo, hi))   # toks[0] = YES

    # precios en paralelo (CLOB /prices-history es 1 llamada por token; concurrencia moderada).
    # --markets-only salta esto: regenera solo markets.csv (p.ej. para agregar la columna resolved)
    # sin re-bajar cientos de miles de precios.
    n_price_calls = 0
    if not ARGS_MARKETS_ONLY:
        def fetch(task):
            token, s_ts, e_ts, st, target, lo, hi = task
            return task, price_history(token, s_ts, e_ts)
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for fut in as_completed([ex.submit(fetch, t) for t in tasks]):
                (token, s_ts, e_ts, st, target, lo, hi), hist = fut.result()
                for pt in hist:
                    t_iso = dt.datetime.fromtimestamp(pt["t"], dt.timezone.utc).replace(tzinfo=None).isoformat()
                    px_rows.append([t_iso, st, target, _bucket_id(lo, hi), _blank(lo), _blank(hi),
                                    round(float(pt["p"]), 4), HALF_SPREAD_ASSUMED])
        n_price_calls = len(tasks)
    if not mk_rows:
        print("[ABORT] 0 mercados -- revisar rango/ciudades. NO escribo CSVs vacios.", file=sys.stderr)
        sys.exit(1)
    _write("data/markets.csv", ["station", "target", "bucket", "lo", "hi", "open_t", "close_t", "resolved"], mk_rows)
    if not ARGS_MARKETS_ONLY:
        _write("data/prices.csv", ["t", "station", "target", "bucket", "lo", "hi", "mid", "hs"], px_rows)
    print(f"markets: {len(mk_rows)} filas  prices: {len(px_rows)} filas  ({n_price_calls} llamadas de precio)")
    print(f"ciudades con data: {sorted(seen_cities)}")
    print("PENDIENTE [VERIFICAR-VIVO]: confirmar coords/rules exactas por ciudad y hs real (aqui hs=0.02).")


def _to_naive_utc(iso):
    """ISO (con 'Z' u offset) -> datetime naive en UTC. Uniforma tz para el motor."""
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _bucket_id(lo, hi):
    """id entero estable del bucket para la columna 'bucket' (el motor lo castea a int).
    Usa el borde definido: cola baja->hi, cola alta/rango/single->lo."""
    return int(hi if lo is None else lo)


def _blank(v):
    return "" if v is None else int(v)


def _write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01", help="target/endDate min (YYYY-MM-DD)")
    ap.add_argument("--end", default="2026-07-01", help="target/endDate max (YYYY-MM-DD)")
    ap.add_argument("--max-events", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=8, help="concurrencia de bajada de precios")
    ap.add_argument("--include-open", action="store_true", help="incluir mercados aun abiertos")
    ap.add_argument("--markets-only", action="store_true", help="regenerar solo markets.csv (con resolved), sin bajar precios")
    _a = ap.parse_args()
    ARGS_MARKETS_ONLY = _a.markets_only
    main(_a)
