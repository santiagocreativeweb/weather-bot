#!/usr/bin/env python3
# scripts/show_live.py — Dashboard de SOLO LECTURA: pronostico actual (m por modelo) vs mercado
# vivo, para hoy..hoy+3. No escribe nada, no accumula, no participa del backtest ni de la
# validacion forward (eso es accumulate_books.py/accumulate_ensemble.py). Es para mirar.
#
# [CORREGIDO 2026-07-08] El bot SI opera el dia del target: lead_h se mide desde avail al PICO de
# tmax (~15:00 local), y "lead 1" = la corrida de la misma manana. El mercado de HOY esta en
# ventana hasta el cierre.
#
# Pronostico: Previous-Runs API con start=end=hoy..hoy+3, columna temperature_2m (corrida MAS
# RECIENTE disponible) -- a diferencia de download_openmeteo.py, aca no reconstruimos un punto
# historico (no hace falta la logica previous_dayN de anti-look-ahead): "ahora" ES el momento real.
import argparse, json, re, sys
import datetime as dt
import requests

PREV_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
GAMMA = "https://gamma-api.polymarket.com"

STATIONS = {  # = download_openmeteo.py / accumulate_ensemble.py. Coords del AEROPUERTO de las reglas.
    "KLGA": (40.7794, -73.8803, -5, "F"), "KORD": (41.9786, -87.9048, -6, "F"),
    "EGLC": (51.5050,  0.0553,  0, "C"),  "LFPB": (48.9694,  2.4414,   1, "C"),
    "RJTT": (35.5533, 139.7811, 9, "C"),  "RKSI": (37.4602, 126.4407,  9, "C"),
    "ZSPD": (31.1434, 121.8052, 8, "C"),  "ZBAA": (40.0801, 116.5846,  8, "C"),
    "RCSS": (25.0694, 121.5521, 8, "C"),  "LEMD": (40.4722,  -3.5609,  1, "C"),
    "EDDM": (48.3538,  11.7861, 1, "C"),  "LIMC": (45.6301,   8.7231,  1, "C"),
    # [2026-07-13] 6 ciudades data-driven (scout + verificacion Gamma/IEM). utc_off ESTANDAR.
    "NZWN": (-41.3272, 174.8053, 12, "C"), "LTAC": (40.1281, 32.9951,  3, "C"),
    "KMIA": (25.7932, -80.2906, -5, "F"),  "WSSS": (1.3502, 103.9944,  8, "C"),
    "WMKK": (2.7456, 101.7099,  8, "C"),   "ZGSZ": (22.6393, 113.8108, 8, "C"),
    # [2026-07-13 tarde] +11 (backtest scout_test12 + verificacion Gamma/IEM 12 ciudades; HK AFUERA:
    # HKO resuelve a 1 DECIMAL, rompe la regla floor). utc_off ESTANDAR (DST via _US/_EU_DST).
    "KSFO": (37.6188, -122.3750, -8, "F"), "KLAX": (33.9425, -118.4081, -8, "F"),
    "KDAL": (32.8471, -96.8518,  -6, "F"), "KATL": (33.6367, -84.4281,  -5, "F"),
    "KHOU": (29.6454, -95.2789,  -6, "F"), "KAUS": (30.1945, -97.6699,  -6, "F"),
    "CYYZ": (43.6772, -79.6306,  -5, "C"), "SBGR": (-23.4356, -46.4731, -3, "C"),
    "SAEZ": (-34.8222, -58.5358, -3, "C"), "MMMX": (19.4363, -99.0721,  -6, "C"),
    "EFHK": (60.3172, 24.9633,    2, "C"),
}
MODELS = {"gefs": "gfs_seamless", "ecmwf": "ecmwf_ifs025", "icon": "icon_seamless"}
MIN_DAY_HOURS = 20

# HORA LOCAL (DST-aware) del PICO de tmax por estacion, MEDIDA de 25-31 dias de METAR (2026-06/07).
# [2026-07-10] Reemplaza el "15:00 para todas" que estaba MAL: los aeropuertos costeros de Asia
# (Seul/Tokio/Shanghai) tienen el maximo a MEDIA MAÑANA por brisa marina, no a la tarde. Con el
# viejo 15:00 el bot creia que faltaban 3h para el pico cuando ya habia pasado -> "A TIEMPO" falso
# y buckets eliminandose "temprano". RCSS/EDDM sin datos IEM -> default por tipo (costero/inland).
PEAK_HOUR = {   # todas MEDIDAS de METAR (IEM); RCSS/EDDM cruzadas ademas con ERA5 (Open-Meteo archive)
    "RKSI": 13.0, "RJTT": 13.0, "ZSPD": 12.0, "ZBAA": 14.5, "RCSS": 12.0,   # Asia (costeros ~12-13h)
    "KLGA": 16.0, "KORD": 15.5,                                              # America
    "EGLC": 15.0, "LFPB": 16.5, "LEMD": 17.0, "EDDM": 15.5, "LIMC": 16.0,    # Europa
    # [2026-07-13] nuevas, MEDIDAS de 30d METAR (workflow de verificacion). WSSS/WMKK tropicales
    # (conveccion de media tarde); KMIA/ZGSZ costeros (~12-13h). NZWN RUIDOSO (bimodal invierno
    # austral, ~1/3 de dias el tmax cae de noche) -> revisar timing antes de operar en serio.
    "KMIA": 13.0, "WSSS": 13.5, "WMKK": 14.0, "ZGSZ": 12.0, "LTAC": 15.0, "NZWN": 12.0,
    # [2026-07-13 tarde] medidas 30d METAR, DST-aware (US +1 vs medicion estandar). Costeros
    # pican temprano (KSFO/KLAX/KHOU); inland ~15-16 (KDAL/KATL/KAUS); MMMX 14 (altura+conveccion);
    # SBGR/SAEZ invierno austral; EFHK/CYYZ alta latitud. Refinar con mas METAR forward.
    "KSFO": 13.5, "KLAX": 13.0, "KDAL": 15.5, "KATL": 15.5, "KHOU": 14.0, "KAUS": 15.5,
    "CYYZ": 15.0, "SBGR": 14.5, "SAEZ": 15.0, "MMMX": 14.0, "EFHK": 16.0,
}
# DST EEUU/Canada (2do dom mar - 1er dom nov): + las 6 US nuevas y Toronto (Canada = reglas US).
_US_DST = {"KLGA", "KORD", "KMIA", "KSFO", "KLAX", "KDAL", "KATL", "KHOU", "KAUS", "CYYZ"}
_EU_DST = {"EGLC", "LFPB", "LEMD", "EDDM", "LIMC", "EFHK"}    # DST UE (+ Helsinki EET->EEST)
# SIN DST: Mexico (abolio 2022), Brasil (2019), Argentina (2009), Turquia, Asia -> MMMX/SBGR/SAEZ
_NZ_DST = {"NZWN"}                                            # DST Nueva Zelanda (HEMISFERIO SUR):
#           ult dom sep -> 1er dom abr (verano austral). En invierno (jun-ago) NO rige -> +12.
# (Asia — Japon/Corea/China/Taiwan/Singapur/Malasia — y Turquia y Shenzhen NO usan DST)


def _nth_sunday(y, m, n):
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)             # primer domingo del mes
    return d + dt.timedelta(days=7 * (n - 1))


def _last_sunday(y, m):
    import calendar
    d = dt.date(y, m, calendar.monthrange(y, m)[1])
    return d - dt.timedelta(days=(d.weekday() - 6) % 7)


def _dst_active(code, date):
    y = date.year
    if code in _US_DST:
        return _nth_sunday(y, 3, 2) <= date < _nth_sunday(y, 11, 1)
    if code in _EU_DST:
        return _last_sunday(y, 3) <= date < _last_sunday(y, 10)
    if code in _NZ_DST:   # hemisferio sur: verano austral cruza el ano nuevo
        return date >= _last_sunday(y, 9) or date < _nth_sunday(y, 4, 1)
    return False


def local_offset(code, date):
    """Offset UTC REAL de la estacion en `date` = base estandar de STATIONS + 1h si rige DST. El
    offset de STATIONS es ESTANDAR; en verano America/Europa corren +1 (Asia no usa DST)."""
    return STATIONS[code][2] + (1 if _dst_active(code, date) else 0)


def peak_utc(code, d):
    """Instante UTC del PICO de tmax del dia LOCAL `d` para la estacion: hora-local-del-pico
    (PEAK_HOUR, DST-aware) convertida a UTC. Fuente unica de verdad para deadline/lead/estado."""
    return dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=PEAK_HOUR[code] - local_offset(code, d))

CITY_SERIES = {"nyc": 10005, "chicago": 10726, "london": 10006,
               "paris": 11168, "tokyo": 10740, "seoul": 10742,
               "shanghai": 10741, "madrid": 11345, "beijing": 11363,
               "munich": 11272, "taipei": 11346, "milan": 11343,
               # [2026-07-13] series verificadas via Gamma (tag 104596)
               "wellington": 10902, "ankara": 10900, "miami": 10728,
               "singapore": 11314, "kuala-lumpur": 11510, "shenzhen": 11366,
               # [2026-07-13 tarde] +11 (HK afuera). series de Gamma.
               "san-francisco": 11371, "los-angeles": 11370, "dallas": 10727,
               "atlanta": 10739, "houston": 11369, "austin": 11367, "toronto": 10743,
               "sao-paulo": 11169, "buenos-aires": 10744, "mexico-city": 11428, "helsinki": 11508}
CITY_STATION = {"nyc": "KLGA", "chicago": "KORD", "london": "EGLC",
                "paris": "LFPB", "tokyo": "RJTT", "seoul": "RKSI",
                "shanghai": "ZSPD", "madrid": "LEMD", "beijing": "ZBAA",
                "munich": "EDDM", "taipei": "RCSS", "milan": "LIMC",
                "wellington": "NZWN", "ankara": "LTAC", "miami": "KMIA",
                "singapore": "WSSS", "kuala-lumpur": "WMKK", "shenzhen": "ZGSZ",
                "san-francisco": "KSFO", "los-angeles": "KLAX", "dallas": "KDAL",
                "atlanta": "KATL", "houston": "KHOU", "austin": "KAUS", "toronto": "CYYZ",
                "sao-paulo": "SBGR", "buenos-aires": "SAEZ", "mexico-city": "MMMX",
                "helsinki": "EFHK"}
CITY_RE = re.compile(r"highest-temperature-in-([a-z-]+?)-on-")   # [a-z-] + non-greedy: 'kuala-lumpur'


def parse_bucket(title):
    """groupItemTitle -> (lo, hi) numerico. = download_polymarket.py/accumulate_books.py."""
    t = (title or "").strip()
    nums = [int(x) for x in re.findall(r"\d+", t)]
    if not nums:
        return None, None
    if re.search(r"or (below|lower|less)", t, re.I):
        return None, nums[0]
    if re.search(r"or (above|higher|more|greater)", t, re.I):
        return nums[0], None
    if len(nums) >= 2 and re.search(r"\d+\s*[-–]\s*\d+", t):
        return nums[0], nums[1]
    return nums[0], nums[0]


def daily_tmax(times, vals, off):
    buck = {}
    for t, v in zip(times, vals):
        if v is None:
            continue
        u = dt.datetime.fromisoformat(t) + dt.timedelta(hours=off)
        buck.setdefault(u.date(), []).append(float(v))
    return {d: max(vs) for d, vs in buck.items() if len(vs) >= MIN_DAY_HOURS}


def fetch_forecast(today, horizon_days):
    """{station: {target_date: {model: tmax_pronosticado}}} usando la corrida MAS RECIENTE."""
    end = today + dt.timedelta(days=horizon_days)
    out = {}
    for code, (lat, lon, off, unit) in STATIONS.items():
        out[code] = {}
        for model, om in MODELS.items():
            p = dict(latitude=lat, longitude=lon, models=om, hourly="temperature_2m",
                     start_date=today.isoformat(), end_date=end.isoformat(), timezone="UTC",
                     temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
            try:
                r = requests.get(PREV_RUNS, params=p, timeout=60); r.raise_for_status()
                h = r.json()["hourly"]
            except Exception as e:
                print(f"[WARN] {code} {model}: {e}", file=sys.stderr); continue
            for d, m in daily_tmax(h["time"], h["temperature_2m"], off).items():
                if today <= d <= end:
                    out[code].setdefault(d, {})[model] = round(m, 1)
    return out


def fetch_market(today, horizon_days):
    """{station: {target_date: [(bucket_label, lo, hi, mid), ...]}} de mercados VIVOS en Polymarket.
    lo/hi numericos (uno puede ser None en cola abierta) -- para posicionar el bucket en un eje."""
    end = today + dt.timedelta(days=horizon_days)
    out = {}
    for city, sid in CITY_SERIES.items():
        station = CITY_STATION[city]
        try:
            r = requests.get(f"{GAMMA}/events",
                             params={"series_id": sid, "closed": "false", "limit": 100}, timeout=60)
            r.raise_for_status()
            evs = r.json()
        except Exception as e:
            print(f"[WARN] mercado {city}: {e}", file=sys.stderr); continue
        for e in evs:
            m = CITY_RE.search(e.get("slug") or "")
            if not m:
                continue
            ed = (e.get("endDate") or "")[:10]
            if not ed:
                continue
            close = dt.date.fromisoformat(ed)
            if not (today <= close <= end):
                continue
            buckets = []
            for mk in e.get("markets", []):
                mid = mk.get("lastTradePrice") or mk.get("outcomePrices")
                # bestPrice/lastTradePrice pueden faltar; usar outcomePrices YES o bestAsk/Bid si esta
                try:
                    p = float(json.loads(mid)[0]) if isinstance(mid, str) else float(mid)
                except Exception:
                    p = None
                lo, hi = parse_bucket(mk.get("groupItemTitle"))
                buckets.append((mk.get("groupItemTitle"), lo, hi, p))
            out.setdefault(station, {})[close] = buckets
    return out


def main(a):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    print(f"=== pronostico vs mercado, desde {today} (HOY esta en ventana hasta el cierre) ===\n")
    fc = fetch_forecast(today, a.horizon)
    mk = fetch_market(today, a.horizon)
    for code in STATIONS:
        print(f"--- {code} ---")
        dates = sorted(set(fc.get(code, {})) | set(mk.get(code, {})))
        for d in dates:
            lead = (d - today).days
            tag = " [HOY -- en ventana hasta el cierre]" if lead == 0 else f" [D+{lead}]"
            models = fc.get(code, {}).get(d, {})
            if models:
                vals = list(models.values())
                consenso = sum(vals) / len(vals)
                spread = max(vals) - min(vals) if len(vals) > 1 else 0.0
                print(f"  {d}{tag}: modelo consenso={consenso:.1f}  "
                      f"(gefs={models.get('gefs','-')} ecmwf={models.get('ecmwf','-')} "
                      f"icon={models.get('icon','-')}, spread={spread:.1f})")
            else:
                print(f"  {d}{tag}: [sin pronostico -- fuera de horizonte del modelo]")
            bkts = mk.get(code, {}).get(d)
            if bkts:
                # ordenar por precio desc, mostrar los que tengan precio (mercado liquido = mid claro)
                priced = sorted([(t, p) for t, _, _, p in bkts if p is not None], key=lambda x: -x[1])
                if priced:
                    top = ", ".join(f"{t}={p:.2f}" for t, p in priced[:4])
                    print(f"            mercado (top precios): {top}")
                else:
                    print(f"            mercado: {len(bkts)} buckets, sin precio disponible")
            else:
                print(f"            mercado: sin evento vivo para esa fecha")
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Dashboard de solo lectura: pronostico actual vs mercado vivo.")
    ap.add_argument("--date", default=None, help="fecha 'hoy' YYYY-MM-DD (default: hoy real)")
    ap.add_argument("--horizon", type=int, default=3, help="dias hacia adelante a mostrar (default 3)")
    main(ap.parse_args())
