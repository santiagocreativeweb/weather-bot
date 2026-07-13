#!/usr/bin/env python3
# scripts/scout_cities.py — Scouting DATA-DRIVEN de ciudades candidatas nuevas (objetivo #5).
# [Creado 2026-07-10.]
#
# QUE HACE (punta a punta, reproducible):
#   1. DESCUBRE todas las ciudades con mercados "highest temperature" en Polymarket (Gamma,
#      tag 104596), abiertas y resueltas en los ultimos ~42 dias, con VOLUMEN por evento.
#   2. Por ciudad extrae la ESTACION DE RESOLUCION leyendo la description del mercado (URL de
#      Weather Underground -> ICAO; weather.gov/wrh/timeseries?site=ICAO para las que resuelve
#      NOAA; Hong Kong resuelve por el HK Observatory, NO un aeropuerto) y la UNIDAD desde los
#      groupItemTitle reales (°F pares par-impar / °C de 1 grado). NO se asume nada por pais
#      (leccion London=EGLC-no-Heathrow; aca aparecieron Denver=KBKF Aurora, Dallas=KDAL Love,
#      Houston=KHOU Hobby, Busan=RKPK Gimhae, Buenos Aires=SAEZ Ezeiza, Moscu=UUWW Vnukovo).
#   3. SIMULACION HONESTA 7/30/60 dias (targets hasta --end): forecasts point-in-time de la
#      Previous Runs API usando SOLO temperature_2m_previous_day1/2 (leads 2/3). La columna
#      temperature_2m a secas es un NOWCAST con avail falso (bug #5) -> PROHIBIDA aqui.
#      Prediccion evaluada = consenso equiponderado lead-2 de gefs/ecmwf/icon (min 2 modelos),
#      cruda y con sesgo rolling walk-forward (ventana 60d, solo dias < target, min 10 puntos).
#      Obs: IEM daily (network por pais/estado); fallback archive de Open-Meteo (proxy debil,
#      MARCADO en la salida). tmax del dia calendario LOCAL (utc_off fijo estandar, convencion
#      del proyecto; MIN_DAY_HOURS=20 como download_openmeteo).
#   4. BASELINE: el MISMO pipeline sobre las 12 actuales -> comparacion manzanas-con-manzanas.
#      Score (= leaderboard.py): hit60*100 - mae60*8 - std60*6 sobre la variante con sesgo.
#   5. RECOMIENDA: ADD si score60 > mediana de las 12 actuales Y volumen no muy por debajo del
#      rango de las 12 (corte blando 0.75x el minimo de las 12 — el numero se reporta siempre).
#
# HIT por bucket (regla FLOOR de WU, °C half-up medido en el proyecto):
#   °F: buckets par-impar [lo,lo+1] (lo=floor si par, sino floor-1). ganador = par de floor(obs).
#       pick = par de floor(mu). Ranking top-2/3: bucket_prob(mu-0.5, sigma, lo, hi) (shift floor).
#   °C: buckets de 1 grado. ganador = floor(obs). pick = floor(mu+0.5) (half-up; floor puro
#       pierde ~6pp, medido). Ranking: bucket_prob(mu, sigma, b, b).
#   sigma = std rolling de los errores propios (mismo walk-forward), piso SIGMA_FLOOR.
#
# CACHE (para no re-bajar): data/scout_m.csv (forecasts), data/scout_obs.csv (obs),
#   data/scout_meta.csv (descubrimiento+volumen). --refresh los ignora. Salida final:
#   data/city_scout.csv (una fila por ciudad x ventana).
# Limite de presupuesto: si hay >15 candidatas se simulan las TOP-15 por volumen (se avisa y
#   las excluidas quedan listadas con su volumen igual).
import argparse, csv, json, math, os, re, sys, time
import datetime as dt
from statistics import mean, median, stdev
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wxbt.market import bucket_prob  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
PREV_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
IEM_DAILY = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
TAG_HIGHEST_TEMP = 104596

D = os.path.join(os.path.dirname(__file__), "..", "data")
F_META = os.path.join(D, "scout_meta.csv")
F_M = os.path.join(D, "scout_m.csv")
F_OBS = os.path.join(D, "scout_obs.csv")
F_OUT = os.path.join(D, "city_scout.csv")

MODELS = {"gefs": "gfs_seamless", "ecmwf": "ecmwf_ifs025", "icon": "icon_seamless"}
LEAD_COLS = {2: "temperature_2m_previous_day1", 3: "temperature_2m_previous_day2"}  # NUNCA lead 1
MIN_DAY_HOURS = 20
SIGMA_FLOOR = {"F": 0.9, "C": 0.5}     # = wxbt/config.py
SIGMA_DEFAULT = {"F": 2.7, "C": 1.5}   # sin historia suficiente (raro: solo primeros dias)
BIAS_WIN, BIAS_MIN_N = 60, 10
WINDOWS = (7, 30, 60)
IEM_MIN_COVER = 0.70                   # <70% de dias con obs IEM -> fallback archive completo
DISCOVER_DAYS = 42                     # ventana de mercados resueltos para volumen (~30+ por ciudad)
VOL_LAST_N = 30                        # promedio sobre los ultimos N eventos resueltos

# Las 12 actuales (= show_live.STATIONS + check_predictions.NETWORKS). Se corren como BASELINE
# con el mismo pipeline; su volumen sale del mismo descubrimiento.
CURRENT = {
    "nyc": "KLGA", "chicago": "KORD", "london": "EGLC", "paris": "LFPB",
    "tokyo": "RJTT", "seoul": "RKSI", "shanghai": "ZSPD", "madrid": "LEMD",
    "beijing": "ZBAA", "munich": "EDDM", "taipei": "RCSS", "milan": "LIMC",
}
# estacion -> (lat, lon, utc_off_estandar, red IEM o None->archive, sid IEM o None->auto)
# Convencion utc_off = offset ESTANDAR fijo (DST corre el pico 1h; aceptado, ver PROJECT_CONTEXT
# nota DST Madrid/Munich/Milan). Coords = punto de referencia del aeropuerto / HKO.
AIRPORTS = {
    # --- 12 actuales (identicas a show_live.py / check_predictions.py) ---
    "KLGA": (40.7794, -73.8803, -5, "NY_ASOS", "LGA"), "KORD": (41.9786, -87.9048, -6, "IL_ASOS", "ORD"),
    "EGLC": (51.5050, 0.0553, 0, "GB__ASOS", "EGLC"), "LFPB": (48.9694, 2.4414, 1, "FR__ASOS", "LFPB"),
    "RJTT": (35.5533, 139.7811, 9, "JP__ASOS", "RJTT"), "RKSI": (37.4602, 126.4407, 9, "KR__ASOS", "RKSI"),
    "ZSPD": (31.1434, 121.8052, 8, "CN__ASOS", "ZSPD"), "ZBAA": (40.0801, 116.5846, 8, "CN__ASOS", "ZBAA"),
    "RCSS": (25.0694, 121.5521, 8, "TW__ASOS", "RCSS"), "LEMD": (40.4722, -3.5609, 1, "ES__ASOS", "LEMD"),
    "EDDM": (48.3538, 11.7861, 1, "DE__ASOS", "EDDM"), "LIMC": (45.6301, 8.7231, 1, "IT__ASOS", "LIMC"),
    # --- candidatas (estacion LEIDA de las descriptions de Gamma, sondeo 2026-07-10) ---
    "KLAX": (33.9425, -118.4081, -8, "CA_ASOS", "LAX"),   # Los Angeles Intl
    "KSFO": (37.6188, -122.3750, -8, "CA_ASOS", "SFO"),   # San Francisco Intl
    "KSEA": (47.4489, -122.3094, -8, "WA_ASOS", "SEA"),   # Seattle-Tacoma
    "KBKF": (39.7017, -104.7517, -7, "CO_ASOS", "BKF"),   # Denver = BUCKLEY SFB (Aurora), no KDEN
    "KDAL": (32.8471, -96.8518, -6, "TX_ASOS", "DAL"),    # Dallas = LOVE FIELD, no DFW
    "KHOU": (29.6454, -95.2789, -6, "TX_ASOS", "HOU"),    # Houston = HOBBY, no IAH
    "KAUS": (30.1945, -97.6699, -6, "TX_ASOS", "AUS"),    # Austin-Bergstrom
    "KATL": (33.6367, -84.4281, -5, "GA_ASOS", "ATL"),    # Atlanta Hartsfield
    "KMIA": (25.7932, -80.2906, -5, "FL_ASOS", "MIA"),    # Miami Intl
    "CYYZ": (43.6772, -79.6306, -5, "CA_ON_ASOS", "CYYZ"),  # Toronto Pearson (red Ontario, no CA__)
    "MMMX": (19.4363, -99.0721, -6, "MX__ASOS", "MMMX"),  # Mexico City Benito Juarez
    "MPMG": (8.9731, -79.5556, -5, "PA__ASOS", "MPMG"),   # Panama City = Marcos A. Gelabert (Albrook)
    "SAEZ": (-34.8222, -58.5358, -3, "AR__ASOS", "SAEZ"), # Buenos Aires = EZEIZA, no Aeroparque
    "SBGR": (-23.4356, -46.4731, -3, "BR__ASOS", "SBGR"), # Sao Paulo = Guarulhos
    "EHAM": (52.3086, 4.7639, 1, "NL__ASOS", "EHAM"),     # Amsterdam Schiphol
    "EPWA": (52.1657, 20.9671, 1, "PL__ASOS", "EPWA"),    # Warsaw Chopin
    "EFHK": (60.3172, 24.9633, 2, "FI__ASOS", "EFHK"),    # Helsinki Vantaa
    "LTFM": (41.2753, 28.7519, 3, "TR__ASOS", "LTFM"),    # Istanbul Airport (resuelve NOAA)
    "LTAC": (40.1281, 32.9951, 3, "TR__ASOS", "LTAC"),    # Ankara Esenboga
    "UUWW": (55.5915, 37.2615, 3, "RU__ASOS", "UUWW"),    # Moscu = VNUKOVO (resuelve NOAA)
    "LLBG": (32.0114, 34.8867, 2, "IL__ASOS", "LLBG"),    # Tel Aviv Ben Gurion (resuelve NOAA)
    "OEJN": (21.6796, 39.1565, 3, "SA__ASOS", "OEJN"),    # Jeddah King Abdulaziz
    "OPKC": (24.9065, 67.1608, 5, "PK__ASOS", "OPKC"),    # Karachi Jinnah
    "VILK": (26.7606, 80.8893, 5.5, "IN__ASOS", "VILK"),  # Lucknow
    "FACT": (-33.9648, 18.6017, 2, "ZA__ASOS", "FACT"),   # Cape Town Intl
    "HKO":  (22.3020, 114.1740, 8, None, None),           # HONG KONG OBSERVATORY (no METAR/aeropuerto)
    "WSSS": (1.3502, 103.9944, 8, "SG__ASOS", "WSSS"),    # Singapore Changi
    "WMKK": (2.7456, 101.7099, 8, "MY__ASOS", "WMKK"),    # Kuala Lumpur Intl (Sepang)
    "RPLL": (14.5086, 121.0194, 8, "PH__ASOS", "RPLL"),   # Manila Ninoy Aquino
    "RKPK": (35.1795, 128.9382, 9, "KR__ASOS", "RKPK"),   # Busan = GIMHAE
    "NZWN": (-41.3272, 174.8053, 12, "NF__ASOS", "NZWN"), # Wellington (red IEM 'NF__ASOS' = NZ)
    "ZUUU": (30.5785, 103.9471, 8, "CN__ASOS", "ZUUU"),   # Chengdu Shuangliu
    "ZUCK": (29.7192, 106.6417, 8, "CN__ASOS", "ZUCK"),   # Chongqing Jiangbei
    "ZGGG": (23.3924, 113.2988, 8, "CN__ASOS", "ZGGG"),   # Guangzhou Baiyun
    "ZGSZ": (22.6393, 113.8108, 8, "CN__ASOS", "ZGSZ"),   # Shenzhen Bao'an
    "ZSJN": (36.8572, 117.2158, 8, "CN__ASOS", "ZSJN"),   # Jinan Yaoqiang (IEM ralo -> cae a archive)
    "ZSQD": (36.3614, 120.0879, 8, "CN__ASOS", "ZSQD"),   # Qingdao Jiaodong
    "ZHHH": (30.7838, 114.2081, 8, "CN__ASOS", "ZHHH"),   # Wuhan Tianhe
    "ZHCC": (34.5197, 113.8409, 8, "CN__ASOS", "ZHCC"),   # Zhengzhou Xinzheng
}
# descriptions sin URL con ICAO (texto "recorded by the X") -> estacion
NAME_STATION = {"hong kong observatory": "HKO"}

CITY_RE = re.compile(r"highest-temperature-in-(.+)-on-([a-z]+)-(\d+)-(\d+)$")
WU_RE = re.compile(r"wunderground\.com/history/daily/([^\s\"'<>)]+)")
NOAA_RE = re.compile(r"weather\.gov/wrh/timeseries\?site=([A-Za-z0-9]{4})")
NAME_RE = re.compile(r"recorded (?:at|by)(?: NOAA at)? the (.{4,70}?)(?: Station)? in degrees")

SESSION = requests.Session()


def get(url, params, timeout=90, retries=3):
    """GET con reintentos/backoff (429/5xx/red) y sleep 0.3s entre llamadas."""
    last = None
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            time.sleep(0.3)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(0.8 * (i + 1)); continue
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"GET {url} fallo tras {retries} intentos: {last}")


# ------------------------------------------------------------------ descubrimiento (Gamma)
def discover(today):
    """{city: {'events': [(target, closed, vol)], 'desc': str, 'unit': F/C, 'name': str}}
    Enumera tag 104596: resueltos en los ultimos DISCOVER_DAYS (chunks de 15d por el offset-cap)
    + abiertos. El volumen es por EVENTO (= suma de sus buckets, campo volume de Gamma)."""
    t0 = today - dt.timedelta(days=DISCOVER_DAYS)
    chunks, a = [], t0
    while a <= today:
        b = min(a + dt.timedelta(days=14), today)
        chunks.append((a.isoformat(), b.isoformat())); a = b + dt.timedelta(days=1)
    raw = []
    for a, b in chunks:
        off = 0
        while True:
            r = get(GAMMA + "/events", {"tag_id": TAG_HIGHEST_TEMP, "closed": "true",
                                        "end_date_min": a, "end_date_max": b,
                                        "limit": 100, "offset": off})
            batch = r.json() if r.status_code == 200 else []
            raw += [(e, True) for e in batch]
            if len(batch) < 100:
                break
            off += 100
    off = 0
    while True:
        r = get(GAMMA + "/events", {"tag_id": TAG_HIGHEST_TEMP, "closed": "false",
                                    "limit": 100, "offset": off})
        batch = r.json() if r.status_code == 200 else []
        raw += [(e, False) for e in batch]
        if len(batch) < 100:
            break
        off += 100
    cities = {}
    for e, closed in raw:
        m = CITY_RE.match(e.get("slug") or "")
        if not m:
            continue
        city = m.group(1)
        target = (e.get("endDate") or "")[:10]
        if not target:
            continue
        vol = e.get("volume")
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            vol = sum(float(mk.get("volumeNum") or mk.get("volume") or 0) for mk in e.get("markets", []))
        c = cities.setdefault(city, {"events": [], "desc": "", "titles": "", "d_target": ""})
        c["events"].append((target, closed, vol))
        mks = e.get("markets") or []
        if mks and target > c["d_target"]:                # description/unidad del evento mas nuevo
            c["d_target"] = target
            c["desc"] = " ".join((mk.get("description") or "") for mk in mks[:1])
            c["titles"] = " ".join((mk.get("groupItemTitle") or "") for mk in mks)
    return cities


def resolve_station(city, info):
    """(station, unit, station_name, how). Estacion desde la description (URL WU -> ICAO;
    weather.gov site=ICAO; tabla por nombre). Unidad desde los groupItemTitle REALES."""
    desc, titles = info["desc"], info["titles"]
    unit = "F" if "F" in titles else ("C" if "C" in titles else "?")
    nm = NAME_RE.search(desc)
    name = (nm.group(1) if nm else "").encode("ascii", "replace").decode()
    icao, how = None, ""
    m = WU_RE.search(desc)
    if m:
        segs = [s.strip(".,;\"'") for s in m.group(1).split("/") if s]
        for s in segs:                                    # el ICAO es el segmento en MAYUSCULAS
            if re.fullmatch(r"[A-Z][A-Za-z0-9]{2,3}", s):
                icao, how = s, "wunderground-url"
    if not icao:
        m = NOAA_RE.search(desc)
        if m:
            icao, how = m.group(1).upper(), "weather.gov-url"
    if not icao:
        for k, v in NAME_STATION.items():
            if k in desc.lower():
                icao, how = v, "tabla-nombre"
    return icao, unit, name, how


# ------------------------------------------------------------------ forecasts (Previous Runs)
def daily_tmax(times, vals, off):
    """tmax por dia calendario LOCAL (utc_off fijo). Descarta dias con <MIN_DAY_HOURS horas.
    = show_live.py / download_openmeteo.py."""
    buck = {}
    for t, v in zip(times, vals):
        if v is None:
            continue
        u = dt.datetime.fromisoformat(t) + dt.timedelta(hours=off)
        buck.setdefault(u.date(), []).append(float(v))
    return {d: max(vs) for d, vs in buck.items() if len(vs) >= MIN_DAY_HOURS}


def fetch_forecasts(station, lat, lon, off, unit, start, end):
    """{model: {lead: {date: tmax}}} SOLO leads 2/3 (previous_day1/2). La columna temperature_2m
    a secas es NOWCAST con avail falso (bug #5) y NO se pide."""
    out = {}
    for model, om in MODELS.items():
        p = dict(latitude=lat, longitude=lon, models=om,
                 hourly=",".join(LEAD_COLS.values()),
                 start_date=start.isoformat(), end_date=end.isoformat(), timezone="UTC",
                 temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
        try:
            r = get(PREV_RUNS, p)
            if r.status_code != 200:
                print(f"  [WARN] {station} {model}: HTTP {r.status_code}", file=sys.stderr); continue
            h = r.json()["hourly"]
        except Exception as e:
            print(f"  [WARN] {station} {model}: {e}", file=sys.stderr); continue
        out[model] = {ld: daily_tmax(h["time"], h[col], off) for ld, col in LEAD_COLS.items()
                      if col in h}
    return out


# ------------------------------------------------------------------ obs (IEM -> archive)
def fetch_obs_iem(network, sid, start, end, unit):
    """{date: tmax en unidad del MERCADO} desde IEM daily (1 llamada por rango completo)."""
    if not network or not sid:
        return {}
    p = dict(network=network, stations=sid, var="max_temp_f",
             year1=start.year, month1=start.month, day1=start.day,
             year2=end.year, month2=end.month, day2=end.day, format="csv")
    try:
        r = get(IEM_DAILY, p)
        if r.status_code != 200:
            return {}
    except Exception:
        return {}
    lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
    if len(lines) < 2:
        return {}
    hdr = lines[0].split(",")
    out = {}
    for ln in lines[1:]:
        row = dict(zip(hdr, ln.split(",")))
        v = row.get("max_temp_f")
        if not v or v in ("None", "M"):
            continue
        try:
            tf = float(v)
            d = dt.date.fromisoformat(row["day"])
        except Exception:
            continue
        out[d] = tf if unit == "F" else (tf - 32.0) * 5.0 / 9.0
    return out


def fetch_obs_archive(lat, lon, start, end, unit):
    """Fallback: archive de Open-Meteo (reanalisis en coords). PROXY mas debil que la obs de la
    estacion -> quien lo use queda MARCADO obs_src=archive en la salida."""
    p = dict(latitude=lat, longitude=lon, start_date=start.isoformat(), end_date=end.isoformat(),
             daily="temperature_2m_max", timezone="auto",
             temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
    try:
        r = get(ARCHIVE, p)
        if r.status_code != 200:
            return {}
        dly = r.json().get("daily", {})
    except Exception:
        return {}
    out = {}
    for t, v in zip(dly.get("time", []), dly.get("temperature_2m_max", [])):
        if v is None:
            continue
        out[dt.date.fromisoformat(t)] = float(v)
    return out


# ------------------------------------------------------------------ simulacion walk-forward
def pair_lo(t):
    """lado bajo del bucket °F par-impar que contiene el entero t (84-85, 86-87, ...)."""
    t = int(t)
    return t if t % 2 == 0 else t - 1


def outcomes(mu, sigma, obs, unit):
    """hit/top2/top3/ae/e de UNA prediccion vs la obs, bajo la regla FLOOR de WU."""
    if unit == "F":
        win, pick = pair_lo(math.floor(obs)), pair_lo(math.floor(mu))
        ladder = [pick + k for k in range(-16, 18, 2)]
        ranked = sorted(ladder, key=lambda lo: -bucket_prob(mu - 0.5, sigma, lo, lo + 1))
    else:
        win, pick = int(math.floor(obs)), int(math.floor(mu + 0.5))
        base = int(math.floor(mu))
        ladder = list(range(base - 8, base + 9))
        ranked = sorted(ladder, key=lambda b: -bucket_prob(mu, sigma, b, b))
    e = mu - obs
    return dict(hit=int(pick == win), top2=int(win in ranked[:2]), top3=int(win in ranked[:3]),
                ae=abs(e), e=e)


def simulate(unit, fc_by_model, obs, end, eval_days=60):
    """Registros por dia evaluable en [end-59, end]: consenso lead-2 (min 2 modelos), variante
    cruda y con sesgo rolling (ventana BIAS_WIN, dias < target, min BIAS_MIN_N). sigma = std
    rolling de los errores propios (piso SIGMA_FLOOR)."""
    eval_start = end - dt.timedelta(days=eval_days - 1)
    alld = set()
    for m in fc_by_model:
        alld |= set(fc_by_model[m].get(2, {}))
    mus = {}
    for d in alld:
        vals = [fc_by_model[m][2][d] for m in fc_by_model if d in fc_by_model[m].get(2, {})]
        if len(vals) >= 2:
            mus[d] = sum(vals) / len(vals)
    errs = sorted((d, mus[d] - obs[d]) for d in mus if d in obs)
    recs = []
    for d in sorted(mus):
        if not (eval_start <= d <= end) or d not in obs:
            continue
        lo = d - dt.timedelta(days=BIAS_WIN)
        hist = [e for (dd, e) in errs if lo <= dd < d]
        if len(hist) >= BIAS_MIN_N:
            bias, sigma = mean(hist), max(stdev(hist), SIGMA_FLOOR[unit])
        else:
            bias, sigma = 0.0, SIGMA_DEFAULT[unit]
        recs.append(dict(date=d, n_hist=len(hist),
                         raw=outcomes(mus[d], sigma, obs[d], unit),
                         cor=outcomes(mus[d] - bias, sigma, obs[d], unit)))
    return recs


def window_metrics(recs, end, w):
    """Metricas de una ventana que termina en end (inclusive): n, hit, top2/3, MAE, sesgo, std."""
    lo = end - dt.timedelta(days=w - 1)
    rs = [r for r in recs if lo <= r["date"] <= end]
    out = dict(window=w, n=len(rs))
    for var in ("raw", "cor"):
        if not rs:
            for k in ("hit", "top2", "top3", "mae", "bias", "std"):
                out[f"{k}_{var}"] = None
            continue
        es = [r[var]["e"] for r in rs]
        out[f"hit_{var}"] = mean(r[var]["hit"] for r in rs)
        out[f"top2_{var}"] = mean(r[var]["top2"] for r in rs)
        out[f"top3_{var}"] = mean(r[var]["top3"] for r in rs)
        out[f"mae_{var}"] = mean(r[var]["ae"] for r in rs)
        out[f"bias_{var}"] = mean(es)
        out[f"std_{var}"] = stdev(es) if len(es) >= 2 else None
    if out.get("hit_cor") is not None and out.get("std_cor") is not None:
        out["score"] = out["hit_cor"] * 100 - out["mae_cor"] * 8 - out["std_cor"] * 6
    else:
        out["score"] = None
    return out


# ------------------------------------------------------------------ cache helpers
def load_cache_m():
    out = {}
    if not os.path.exists(F_M):
        return out
    with open(F_M, newline="") as f:
        for row in csv.DictReader(f):
            st = row["station"]
            d = dt.date.fromisoformat(row["target"])
            out.setdefault(st, {}).setdefault(row["model"], {}).setdefault(int(row["lead"]), {})[d] = float(row["m"])
    return out


def save_cache_m(cache):
    with open(F_M, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "target", "model", "lead", "m"])
        for st in sorted(cache):
            for model in sorted(cache[st]):
                for lead in sorted(cache[st][model]):
                    for d in sorted(cache[st][model][lead]):
                        w.writerow([st, d.isoformat(), model, lead, round(cache[st][model][lead][d], 2)])


def load_cache_obs():
    out, src = {}, {}
    if not os.path.exists(F_OBS):
        return out, src
    with open(F_OBS, newline="") as f:
        for row in csv.DictReader(f):
            st = row["station"]
            out.setdefault(st, {})[dt.date.fromisoformat(row["date"])] = float(row["obs"])
            src[st] = row["src"]
    return out, src


def save_cache_obs(cache, src):
    with open(F_OBS, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["station", "date", "obs", "src"])
        for st in sorted(cache):
            for d in sorted(cache[st]):
                w.writerow([st, d.isoformat(), round(cache[st][d], 2), src.get(st, "?")])


def covered(dmap, start, end):
    """el cache cubre el rango pedido? (algun dato cerca de ambos extremos)"""
    if not dmap:
        return False
    ds = sorted(dmap)
    return ds[0] <= start + dt.timedelta(days=3) and ds[-1] >= end - dt.timedelta(days=2)


# ------------------------------------------------------------------ main
def fmt(v, spec="%.2f"):
    return "" if v is None else (spec % v)


def main(a):
    t_run = time.time()
    end = dt.date.fromisoformat(a.end)
    today = dt.date.today()
    fetch_start = end - dt.timedelta(days=125)   # 60d de eval + 60d de warmup del sesgo + margen

    # ---- 1) descubrimiento + volumen (cache scout_meta.csv) ----
    meta = {}
    if os.path.exists(F_META) and not a.refresh:
        with open(F_META, newline="") as f:
            for row in csv.DictReader(f):
                row["vol30"] = float(row["vol30"]) if row["vol30"] else 0.0
                row["vol_n"] = int(row["vol_n"] or 0)
                meta[row["city"]] = row
        print(f"[cache] scout_meta.csv: {len(meta)} ciudades (usar --refresh para re-descubrir)")
    if not meta:
        print("descubriendo ciudades en Gamma (tag 104596, abiertos + resueltos "
              f"{DISCOVER_DAYS}d)...")
        cities = discover(today)
        for city, info in sorted(cities.items()):
            icao, unit, name, how = resolve_station(city, info)
            resolved = sorted([ev for ev in info["events"] if ev[1]])
            vol_evs = resolved[-VOL_LAST_N:] if resolved else sorted(info["events"])
            vol30 = mean(v for _, _, v in vol_evs) if vol_evs else 0.0
            meta[city] = dict(
                city=city, station=icao or "", unit=unit, station_name=name, station_how=how,
                is_current=int(city in CURRENT), vol30=vol30, vol_n=len(vol_evs),
                mkt_from=(resolved[0][0] if resolved else ""), mkt_to=(resolved[-1][0] if resolved else ""))
            if city in CURRENT and icao and icao != CURRENT[city]:
                print(f"  [ALERTA] {city}: la description dice {icao} pero el motor usa "
                      f"{CURRENT[city]} -- REVISAR REGLAS", file=sys.stderr)
        with open(F_META, "w", newline="") as f:
            cols = ["city", "station", "unit", "station_name", "station_how", "is_current",
                    "vol30", "vol_n", "mkt_from", "mkt_to"]
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for c in sorted(meta):
                w.writerow({k: meta[c].get(k, "") for k in cols})
        print(f"  {len(meta)} ciudades con mercados (abiertos o resueltos <{DISCOVER_DAYS}d)")

    # ---- 2) separar candidatas vs actuales; priorizar top-N por volumen ----
    cands = sorted((m for c, m in meta.items() if not int(m["is_current"])),
                   key=lambda m: -m["vol30"])
    curr = [m for c, m in meta.items() if int(m["is_current"])]
    cut = []
    if len(cands) > a.max_candidates:
        cut = cands[a.max_candidates:]
        cands = cands[:a.max_candidates]
        print(f"[PRESUPUESTO] {len(cands) + len(cut)} candidatas > {a.max_candidates}: simulo las "
              f"TOP-{a.max_candidates} por volumen. Excluidas (city vol30):")
        for m in cut:
            print(f"    - {m['city']:<16} ${m['vol30']:>10,.0f}/dia  (estacion {m['station'] or '?'})")

    runs = cands + curr
    for m in runs:
        st = m["station"]
        if st in AIRPORTS:
            lat, lon, off, net, sid = AIRPORTS[st]
            m.update(lat=lat, lon=lon, utc_off=off, net=net, sid=sid, coord_src="tabla")
        elif st:
            # estacion nueva no catalogada: geocodificar el nombre (proxy) y avisar fuerte
            q = m["station_name"] or m["city"].replace("-", " ")
            try:
                r = get(GEOCODE, {"name": q, "count": 1})
                g = (r.json().get("results") or [{}])[0]
                m.update(lat=g.get("latitude"), lon=g.get("longitude"),
                         utc_off=round((g.get("longitude") or 0) / 15 * 2) / 2, net=None, sid=None,
                         coord_src="geocode")
                print(f"  [WARN] {m['city']}: estacion {st} no catalogada -> geocode "
                      f"'{q}' ({m['lat']},{m['lon']}) — validar a mano", file=sys.stderr)
            except Exception:
                m.update(lat=None, lon=None, utc_off=0, net=None, sid=None, coord_src="none")
        else:
            m.update(lat=None, lon=None, utc_off=0, net=None, sid=None, coord_src="none")
            print(f"  [WARN] {m['city']}: sin estacion identificable en la description", file=sys.stderr)

    # ---- 3) datos: forecasts (cache scout_m) + obs (cache scout_obs) ----
    cache_m = {} if a.refresh else load_cache_m()
    cache_o, cache_osrc = ({}, {}) if a.refresh else load_cache_obs()
    for i, m in enumerate(runs, 1):
        st, city = m["station"], m["city"]
        if not st or m["lat"] is None:
            m["recs"] = []
            continue
        have = cache_m.get(st, {})
        need_m = not (have and all(covered(have.get(mod, {}).get(2, {}), fetch_start, end)
                                   for mod in MODELS if mod in have) and len(have) >= 2)
        print(f"[{i}/{len(runs)}] {city} ({st}, {m['unit']})"
              f"{' [cache-m]' if not need_m else ''}", flush=True)
        if need_m:
            fc = fetch_forecasts(st, m["lat"], m["lon"], m["utc_off"], m["unit"], fetch_start, end)
            if fc:
                cache_m[st] = fc
        fc = cache_m.get(st, {})
        if len([1 for mod in fc if fc[mod].get(2)]) < 2:
            print(f"  [WARN] {city}: <2 modelos con lead-2 -- sin simulacion", file=sys.stderr)
            m["recs"] = []
            m["obs_src"] = ""
            continue
        # obs
        obs = cache_o.get(st, {})
        if not covered(obs, fetch_start, end):
            expected = (end - fetch_start).days + 1
            obs = fetch_obs_iem(m["net"], m["sid"], fetch_start, end, m["unit"])
            src = "iem"
            if len(obs) < IEM_MIN_COVER * expected:
                obs2 = fetch_obs_archive(m["lat"], m["lon"], fetch_start, end, m["unit"])
                if len(obs2) > len(obs):
                    obs, src = obs2, "archive"
            cache_o[st], cache_osrc[st] = obs, src
        m["obs_src"] = cache_osrc.get(st, "?")
        m["recs"] = simulate(m["unit"], fc, obs, end)

    save_cache_m(cache_m)
    save_cache_obs(cache_o, cache_osrc)

    # ---- 4) metricas por ventana + score ----
    rows = []
    for m in runs:
        m["mets"] = {w: window_metrics(m.get("recs", []), end, w) for w in WINDOWS}
        for w in WINDOWS:
            mt = m["mets"][w]
            rows.append(dict(
                city=m["city"], station=m["station"], unit=m["unit"],
                is_current=int(m["is_current"]), window=w, n=mt["n"],
                hit_raw=fmt(mt["hit_raw"], "%.3f"), top2_raw=fmt(mt["top2_raw"], "%.3f"),
                top3_raw=fmt(mt["top3_raw"], "%.3f"), mae_raw=fmt(mt["mae_raw"]),
                bias_raw=fmt(mt["bias_raw"]), std_raw=fmt(mt["std_raw"]),
                hit_bias=fmt(mt["hit_cor"], "%.3f"), top2_bias=fmt(mt["top2_cor"], "%.3f"),
                top3_bias=fmt(mt["top3_cor"], "%.3f"), mae_bias=fmt(mt["mae_cor"]),
                bias_bias=fmt(mt["bias_cor"]), std_bias=fmt(mt["std_cor"]),
                score=fmt(mt["score"], "%.1f"),
                vol30_usd=round(m["vol30"], 0), vol_n_markets=m["vol_n"],
                mkt_from=m.get("mkt_from", ""), mkt_to=m.get("mkt_to", ""),
                obs_src=m.get("obs_src", ""), coord_src=m.get("coord_src", ""),
                lat=m.get("lat"), lon=m.get("lon"), utc_off=m.get("utc_off"),
                iem_net=m.get("net") or "", station_name=m.get("station_name", ""),
                station_how=m.get("station_how", "")))
    with open(F_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # ---- 5) leaderboard + recomendacion ----
    def s60(m):
        return m["mets"][60]["score"]
    cur_scores = sorted(s60(m) for m in curr if s60(m) is not None)
    cur_vols = sorted(m["vol30"] for m in curr)
    med12 = median(cur_scores) if cur_scores else float("nan")
    vol_soft = 0.75 * cur_vols[0] if cur_vols else 0.0
    print("\n=== SCOUT WXBT: leaderboard w60 (variante con sesgo rolling), end=%s ===" % end)
    print("mediana score 12 actuales: %.1f | volumen 12 actuales: min $%.0f / med $%.0f / max $%.0f (por dia-mercado)"
          % (med12, cur_vols[0], median(cur_vols), cur_vols[-1]))
    print("corte blando de volumen para ADD: >= $%.0f (0.75x min de las 12)\n" % vol_soft)
    hdr = "%-4s %-16s %-5s %-2s %2s %4s %5s %5s %5s %6s %6s %6s %7s %10s %s"
    print(hdr % ("rank", "city", "stn", "u", "C", "n60", "hit%", "top2%", "top3%",
                 "MAE", "std", "score", "rec", "vol30$", "flags"))
    ranked = sorted(runs, key=lambda m: (s60(m) is None, -(s60(m) or 0)))
    for i, m in enumerate(ranked, 1):
        mt = m["mets"][60]
        flags = []
        if m.get("obs_src") == "archive":
            flags.append("obs-proxy")
        if m["vol_n"] < 15:
            flags.append("mercado-joven")
        if mt["n"] and mt["n"] < 45:
            flags.append("pocos-dias-sim")
        if m.get("coord_src") == "geocode":
            flags.append("coords-geocode")
        rec = ""
        if not int(m["is_current"]):
            if mt["score"] is None:
                rec = "NO(sin-datos)"
            elif mt["score"] <= med12:
                rec = "NO(score)"
            elif m["vol30"] < vol_soft:
                rec = "NO(volumen)"
            else:
                rec = "ADD"
        print(hdr % (i, m["city"][:16], m["station"][:5], m["unit"],
                     "*" if int(m["is_current"]) else " ", mt["n"],
                     fmt((mt["hit_cor"] or 0) * 100, "%.0f") if mt["hit_cor"] is not None else "-",
                     fmt((mt["top2_cor"] or 0) * 100, "%.0f") if mt["top2_cor"] is not None else "-",
                     fmt((mt["top3_cor"] or 0) * 100, "%.0f") if mt["top3_cor"] is not None else "-",
                     fmt(mt["mae_cor"]), fmt(mt["std_cor"]), fmt(mt["score"], "%.1f"),
                     rec or ("(actual)"), format(m["vol30"], ",.0f"), ",".join(flags)))
    print("\nC='*' = ciudad actual (baseline). score = hit60*100 - mae60*8 - std60*6 (con sesgo).")
    print("MAE/std en la unidad del MERCADO (F o C) -- OJO al comparar F vs C (1F ~ 0.56C).")
    print(f"salida completa: {os.path.abspath(F_OUT)}  (ventanas 7/30/60, cruda y con sesgo)")
    print("runtime: %.1f min" % ((time.time() - t_run) / 60))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scouting data-driven de ciudades candidatas (Polymarket tmax).")
    ap.add_argument("--end", default="2026-07-08", help="ultimo target de las ventanas 7/30/60")
    ap.add_argument("--max-candidates", type=int, default=15, help="tope de candidatas a simular (por volumen)")
    ap.add_argument("--refresh", action="store_true", help="ignorar caches scout_*.csv")
    main(ap.parse_args())
