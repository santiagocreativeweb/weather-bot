#!/usr/bin/env python3
# scripts/dashboard.py — v5 PLATAFORMA: dashboard de SOLO LECTURA estilo terminal de trading.
# [Redisenado 2026-07-08 con los 11 requerimientos de Santiago:]
#   1. Sin "D+1": fechas reales (09/07/2026).  2. Barra de FILTROS combinables (continente, pais,
#   ciudad, estado, confianza, recomendados, prob alta) sticky.  3. DATE PICKER: cualquier fecha —
#   los dias pasados salen del backfill walk-forward embebido (finalizados con resultado).
#   4./5. Mercados terminados = FINALIZADO (badge verde) con ganador, pronostico, acierto y pwin —
#   nunca "sin mercado vivo" si el mercado existio (se re-fetchea por slug si ya cerro).
#   6. Accordion por card: ventanas de entrada sugeridas (1-3, derivadas de las corridas que llegan
#   antes del deadline) + "Pronostico bloqueado a las HH:MM (UTC-3)".
#   7./8. TODO en UTC-3 (Argentina) + reloj en vivo con segundos.
#   9. Titulo de la card -> link al mercado de Polymarket (slug del evento).
#   10. Fila WU: obs EN VIVO de IEM (misma estacion fisica) + badge-link a la pagina de WU de ese
#   dia. WU no tiene API publica: IEM es el proxy honesto; el link abre la fuente oficial.
#
# HONESTIDAD (invariable): Δ¢ = p_bot − precio es edge BRUTO (sin fees/spread/shrink) — NO señal.
# "Ventanas sugeridas" = llegadas de corridas de modelos ANTES del deadline (microestructura
# medida), no un optimizador magico. El bot predice el MAX; el min mostrado es consenso crudo.
import json as _json
import math, os, re, sys, time
import datetime as dt
import requests

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from show_live import (STATIONS, MODELS, MIN_DAY_HOURS, PREV_RUNS, GAMMA,   # noqa: E402
                       CITY_SERIES, CITY_STATION, parse_bucket, daily_tmax,
                       peak_utc, local_offset, PEAK_HOUR)
from wxbt.market import bucket_prob                                          # noqa: E402
from wxbt.engine import fit_all, clim_val, _lead_day                          # noqa: E402
from wxbt.calibration import predict                                          # noqa: E402
import pandas as _pd                                                          # noqa: E402
from check_predictions import NETWORKS                                        # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "live_dashboard.html")
TIMING_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "timing_analysis.json")
BACKFILL_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "backfill_check.csv")
ART = dt.timezone(dt.timedelta(hours=-3))          # 7. TODO el sistema en UTC-3
BUCKET_WIDTH = {"F": 2, "C": 1}
NO_PRICE_MIN, NO_PBOT_MAX = 0.10, 0.03
RECO_EDGE_MIN = 10.0        # filtro "recomendados": top-1 del bot con Δ¢ >= +10 (edge BRUTO)
PMAX_HI = 0.40              # filtro "prob alta": p_bot max >= 0.40
# [Bloque A 2026-07-10] Momento en que el bot deja de recalibrar (congela la prediccion).
# Antes = pico −1.5h; Santiago pidio retrasarlo ~45min -> pico −0.75h (mas tiempo pre-bloqueo).
FREEZE_LEAD_H = 0.75      # (legacy, ya no gobierna el freeze — ver freeze_utc)
FREEZE_LOCAL_H = 4.5      # [2026-07-13] bloqueo a las 04:30 HORA LOCAL del target (pedido Santiago:
#                           "fijar a media madrugada 4-5am local" = cuando abre los trades)
# Resolucion de mercado (objetivo #1): NO marcar FINALIZADO hasta que Gamma valide. Antes de eso,
# si el payout ya llego a >=99.5% Y el pico ya paso -> estado "pendiente de revision" (no finalizado).
MKT_RESOLVED_MIN = 0.995
FC_TTL, OBS_TTL, RESOLVED_TTL = 900, 600, 100000
DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
AUDIT_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "forecast_audit.json")
FC_HIST = os.path.join(os.path.dirname(__file__), "..", "data", "forecasts.csv")
OBS_HIST = os.path.join(os.path.dirname(__file__), "..", "data", "obs.csv")
AUDIT_MIN_DELTA = 0.1      # solo registrar cambios del max predicho >= 0.1 grados (ignora ruido)
_FROZE = {"dirty": False}  # flag: card_html capturo un freeze nuevo -> generate_once debe guardar
PARAMS_TTL = 3600          # los params EMOS/clim no cambian durante el dia
_CACHE = {"fc": (0.0, None), "obs": (0.0, None), "slug": {},
          "params": (0.0, None), "s2": (0.0, None)}

# estacion -> (continente, pais, ciudad, slug-ciudad-polymarket, path WU)
STATION_META = {
    "KLGA": ("America", "EEUU", "Nueva York", "nyc", "us/ny/new-york-city/KLGA"),
    "KORD": ("America", "EEUU", "Chicago", "chicago", "us/il/chicago/KORD"),
    "EGLC": ("Europa", "Reino Unido", "Londres", "london", "gb/london/EGLC"),
    "LFPB": ("Europa", "Francia", "Paris", "paris", "fr/bonneuil-en-france/LFPB"),
    "LEMD": ("Europa", "España", "Madrid", "madrid", "es/madrid/LEMD"),
    "EDDM": ("Europa", "Alemania", "Munich", "munich", "de/munich/EDDM"),
    "LIMC": ("Europa", "Italia", "Milan", "milan", "it/milan/LIMC"),
    "RJTT": ("Asia", "Japon", "Tokio", "tokyo", "jp/tokyo/RJTT"),
    "RKSI": ("Asia", "Corea del Sur", "Seul", "seoul", "kr/incheon/RKSI"),
    "ZSPD": ("Asia", "China", "Shanghai", "shanghai", "cn/shanghai/ZSPD"),
    "ZBAA": ("Asia", "China", "Beijing", "beijing", "cn/beijing/ZBAA"),
    "RCSS": ("Asia", "Taiwan", "Taipei", "taipei", "tw/taipei/RCSS"),
    # [2026-07-13] 6 ciudades nuevas (verificadas Gamma/IEM). continente = grupo sinoptico de display.
    "NZWN": ("Oceania", "Nueva Zelanda", "Wellington", "wellington", "nz/wellington/NZWN"),
    "LTAC": ("Europa", "Turquia", "Ankara", "ankara", "tr/cubuk/LTAC"),
    "KMIA": ("America", "EEUU", "Miami", "miami", "us/fl/miami/KMIA"),
    "WSSS": ("Asia", "Singapur", "Singapur", "singapore", "sg/singapore/WSSS"),
    "WMKK": ("Asia", "Malasia", "Kuala Lumpur", "kuala-lumpur", "my/kuala-lumpur/WMKK"),
    "ZGSZ": ("Asia", "China", "Shenzhen", "shenzhen", "cn/shenzhen/ZGSZ"),
    # [2026-07-13 tarde] +11 (HK afuera). Sudamerica agrupada en "America" para el display.
    "KSFO": ("America", "EEUU", "San Francisco", "san-francisco", "us/ca/san-francisco/KSFO"),
    "KLAX": ("America", "EEUU", "Los Angeles", "los-angeles", "us/ca/los-angeles/KLAX"),
    "KDAL": ("America", "EEUU", "Dallas", "dallas", "us/tx/dallas/KDAL"),
    "KATL": ("America", "EEUU", "Atlanta", "atlanta", "us/ga/atlanta/KATL"),
    "KHOU": ("America", "EEUU", "Houston", "houston", "us/tx/houston/KHOU"),
    "KAUS": ("America", "EEUU", "Austin", "austin", "us/tx/austin/KAUS"),
    "CYYZ": ("America", "Canada", "Toronto", "toronto", "ca/on/toronto/CYYZ"),
    "SBGR": ("America", "Brasil", "Sao Paulo", "sao-paulo", "br/guarulhos/SBGR"),
    "SAEZ": ("America", "Argentina", "Buenos Aires", "buenos-aires", "ar/ezeiza/SAEZ"),
    "MMMX": ("America", "Mexico", "Ciudad de Mexico", "mexico-city", "mx/mexico-city/MMMX"),
    "EFHK": ("Europa", "Finlandia", "Helsinki", "helsinki", "fi/vantaa/EFHK"),
}
STATION_NAME = {k: f"{v[2]}" for k, v in STATION_META.items()}
MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]


def ddmmyyyy(d):
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def fecha_es(d):
    return f"{DIAS[d.weekday()]} {ddmmyyyy(d)}"


def pm_slug(code, d):
    city = STATION_META[code][3]
    return f"highest-temperature-in-{city}-on-{MONTHS_EN[d.month-1]}-{d.day}-{d.year}"


def wu_url(code, d):
    return f"https://www.wunderground.com/history/daily/{STATION_META[code][4]}/date/{d.year}-{d.month}-{d.day}"


def to_art(t_utc):
    return t_utc.replace(tzinfo=dt.timezone.utc).astimezone(ART)


def freeze_utc(code, d):
    """Instante (naive-UTC) en que el pronostico queda FIJADO. [CAMBIO 2026-07-13, pedido Santiago]
    Antes: pico local - FREEZE_LEAD_H (media tarde). Ahora: FREEZE_LOCAL_H hora LOCAL de la
    madrugada del target (04:30) = la hora a la que Santiago abre los trades. Asi el pick fijado
    (que miden stats/leaderboard/timeline) ES el pick operado, y coincide con la ventana de entrada
    temprana validada por lab_entry_timing (precio blando + books mas baratos)."""
    return dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=FREEZE_LOCAL_H - local_offset(code, d))


def entry_windows(code, d):
    """Ventanas de entrada sugeridas (1-3) + deadline, TODO en ART.
    Derivacion honesta: las corridas 00/06/12/18Z llegan ~init+6h; solo sirven las que llegan
    ANTES del deadline (04:30 local del target, cuando el bot ya no recalibra).
    Se sugieren las ULTIMAS hasta-3 llegadas pre-deadline (mas cerca del evento = mejor forecast)."""
    ddl = freeze_utc(code, d)
    wins = []
    for day in (d - dt.timedelta(days=1), d):
        for initz in (0, 6, 12, 18):
            avail = dt.datetime.combine(day, dt.time(initz)) + dt.timedelta(hours=6)
            if avail < ddl:
                wins.append(avail)
    wins = sorted(wins)[-3:]
    out = []
    for w in wins:
        a = to_art(w)
        tag = f"{a.strftime('%H:%M')}" + ("" if a.date() == to_art(ddl).date() else f" ({ddmmyyyy(a.date())})")
        out.append(tag)
    return out, to_art(ddl).strftime("%H:%M")


def daily_tmin(times, vals, off):
    buck = {}
    for t, v in zip(times, vals):
        if v is None:
            continue
        u = dt.datetime.fromisoformat(t) + dt.timedelta(hours=off)
        buck.setdefault(u.date(), []).append(float(v))
    return {d: min(vs) for d, vs in buck.items() if len(vs) >= MIN_DAY_HOURS}


def fetch_forecast_minmax(today, horizon_days):
    end = today + dt.timedelta(days=horizon_days)
    start = today - dt.timedelta(days=1)   # cubrir el dia LOCAL de Asia (empieza la tarde UTC previa)
    out = {}
    for code, (lat, lon, off, unit) in STATIONS.items():
        out[code] = {}
        for model, om in MODELS.items():
            p = dict(latitude=lat, longitude=lon, models=om, hourly="temperature_2m",
                     start_date=start.isoformat(), end_date=end.isoformat(), timezone="UTC",
                     temperature_unit=("fahrenheit" if unit == "F" else "celsius"))
            h = None
            for _try in range(2):
                try:
                    r = requests.get(PREV_RUNS, params=p, timeout=60); r.raise_for_status()
                    h = r.json()["hourly"]; break
                except Exception as e:
                    if _try:
                        print(f"[WARN] {code} {model}: {e}", file=sys.stderr)
            if h is None:
                continue
            mx = daily_tmax(h["time"], h["temperature_2m"], off)
            mn = daily_tmin(h["time"], h["temperature_2m"], off)
            for d in mx:
                if start <= d <= end:
                    slot = out[code].setdefault(d, {"max": {}, "min": {}})
                    slot["max"][model] = round(mx[d], 1)
                    if d in mn:
                        slot["min"][model] = round(mn[d], 1)
    return out


def fetch_market_full(today, horizon_days):
    """{station: {date: {"buckets":[(lab,lo,hi,mid)], "close_utc": dt|None, "winner": lab|None}}}
    Eventos VIVOS por serie + re-fetch POR SLUG de los que ya cerraron (punto 4: un mercado de hoy
    que termino debe mostrarse FINALIZADO, no 'sin mercado vivo')."""
    end = today + dt.timedelta(days=horizon_days)
    out = {}
    live_found = set()
    for city, sid in CITY_SERIES.items():
        code = CITY_STATION[city]
        try:
            r = requests.get(f"{GAMMA}/events",
                             params={"series_id": sid, "closed": "false", "limit": 100}, timeout=60)
            r.raise_for_status()
            evs = r.json()
        except Exception as e:
            print(f"[WARN] mercado {city}: {e}", file=sys.stderr); continue
        for e in evs:
            ed = (e.get("endDate") or "")
            if not ed:
                continue
            close = dt.date.fromisoformat(ed[:10])
            if not (today <= close <= end):
                continue
            out.setdefault(code, {})[close] = _parse_event(e)
            live_found.add((code, close))
    # los que faltan (mercado ya cerrado/resuelto hoy, o AYER aun no volcado al backfill)
    # -> por slug, con cache (resuelto no cambia)
    for code in STATIONS:
        for n in range(-1, horizon_days + 1):
            d = today + dt.timedelta(days=n)
            if (code, d) in live_found:
                continue
            slug = pm_slug(code, d)
            now = time.monotonic()
            ts, cached = _CACHE["slug"].get(slug, (0.0, None))
            if cached is not None and now - ts < RESOLVED_TTL:
                if cached != "none":
                    out.setdefault(code, {})[d] = cached
                continue
            try:
                r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30)
                evs = r.json() if r.status_code == 200 else []
            except Exception:
                evs = []
            if evs:
                parsed = _parse_event(evs[0])
                out.setdefault(code, {})[d] = parsed
                # resuelto no cambia -> cache largo; sin resolver, corto (puede resolver pronto)
                ttl_key = now if parsed.get("winner") else now - RESOLVED_TTL + 300
                _CACHE["slug"][slug] = (ttl_key, parsed)
            else:
                # mercado aun no listado: reintentar en ~5 min, no en 27h
                _CACHE["slug"][slug] = (now - RESOLVED_TTL + 300, "none")
    return out


def _mkt_price(mk):
    """Precio del bucket que MEJOR refleja el orderbook VIVO. Prioridad (2026-07-10, pedido de
    Santiago: 'los precios de las cards no coinciden con el mercado'):
      1) MID del libro = (bestBid+bestAsk)/2  -> lo que realmente ves para operar AHORA
      2) lastTradePrice -> ultima operacion (se atrasa si no hubo trades recientes)
      3) outcomePrices[0] -> precio de resolucion/indicativo de Gamma
    El lastTradePrice como fuente principal (version vieja) era la causa del desfasaje."""
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    bid, ask = num(mk.get("bestBid")), num(mk.get("bestAsk"))
    if bid is not None and ask is not None and 0 <= bid <= ask <= 1:
        return round((bid + ask) / 2, 4)
    p = num(mk.get("lastTradePrice"))
    if p is not None:
        return p
    op = mk.get("outcomePrices")
    try:
        return float(_json.loads(op)[0]) if isinstance(op, str) else num(op)
    except Exception:
        return None


def _parse_event(e):
    buckets, winner = [], None
    for mk in e.get("markets", []):
        p = _mkt_price(mk)
        lo, hi = parse_bucket(mk.get("groupItemTitle"))
        buckets.append((mk.get("groupItemTitle"), lo, hi, p))
        op = mk.get("outcomePrices")
        try:
            yes = float(_json.loads(op)[0]) if isinstance(op, str) else None
        except Exception:
            yes = None
        # [FIX 2026-07-12, pedido Santiago] ganador SOLO con resolucion REAL (UMA/closed): un bucket
        # cotizando >=0.99 EN CURSO no es ganador todavia (inflaba stats y marcaba FINALIZADO antes
        # de tiempo). El caso "de-facto decidido" lo cubre el estado 'pendrev', no este winner.
        resolved = (bool(mk.get("closed")) or bool(e.get("closed"))
                    or str(mk.get("umaResolutionStatus") or "").lower() == "resolved")
        if yes is not None and yes >= 0.99 and resolved:
            winner = mk.get("groupItemTitle")
    close_utc = None
    try:
        close_utc = dt.datetime.fromisoformat((e.get("endDate") or "").replace("Z", "+00:00"))
    except Exception:
        pass
    return {"buckets": buckets, "close_utc": close_utc, "winner": winner,
            "closed": bool(e.get("closed"))}


def _fresh_metar_extremes(code, today, unit):
    """[2026-07-11] MAX/MIN por dia LOCAL desde el METAR HORARIO crudo (asos.py) — mas FRESCO que el
    resumen diario de IEM (que puede atrasarse ~1h). El max del dia solo sube; tomamos el max de los
    METAR ya publicados. Dia local DST-aware (local_offset), consistente con la resolucion WU."""
    st = code.lstrip("K") if code.startswith("K") else code
    d0 = today - dt.timedelta(days=1)
    p = dict(station=st, network=NETWORKS[code], data="tmpf", tz="UTC",
             format="onlycomma", missing="M",
             year1=d0.year, month1=d0.month, day1=d0.day, hour1=0, minute1=0,
             year2=today.year, month2=today.month, day2=today.day, hour2=23, minute2=59)
    r = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params=p, timeout=25)
    out = {}
    for line in r.text.splitlines():
        parts = line.split(",")
        if len(parts) < 3 or parts[0] == "station":
            continue
        ts, tmpf = parts[1].strip(), parts[2].strip()
        if tmpf in ("M", "", "None"):
            continue
        try:
            t_utc = dt.datetime.fromisoformat(ts.replace(" ", "T"))
        except ValueError:
            continue
        v = float(tmpf)
        v = v if unit == "F" else (v - 32) * 5 / 9
        ld = (t_utc + dt.timedelta(hours=local_offset(code, t_utc.date()))).date()
        mx, mn = out.get(ld, (None, None))
        out[ld] = (v if mx is None else max(mx, v), v if mn is None else min(mn, v))
    return out


def fetch_obs_live(today):
    """{(station, date): {max, min}} para AYER..HOY+1 (Argentina) — asi el dia LOCAL en curso de
    Asia (que en fecha AR es today+1) tambien trae su obs parcial. Base = IEM daily; TOP-UP con el
    METAR horario mas reciente (mas fresco). El max del dia solo sube -> se mergea con max()."""
    out = {}
    d0, d1 = today - dt.timedelta(days=1), today + dt.timedelta(days=1)
    for code, (lat, lon, off, unit) in STATIONS.items():
        if unit == "F":
            continue  # daily.py corrupts °F buckets; the raw-ASOS pass below is authoritative.
        try:
            p = dict(network=NETWORKS[code],
                     stations=code.lstrip("K") if code.startswith("K") else code,
                     var="max_temp_f,min_temp_f",
                     year1=d0.year, month1=d0.month, day1=d0.day,
                     year2=d1.year, month2=d1.month, day2=d1.day, format="csv")
            r = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py",
                             params=p, timeout=30)
            lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
            if len(lines) < 2:
                continue
            hdr = lines[0].split(",")
            def conv(v):
                if not v or v in ("None", "M"):
                    return None
                tf = float(v)
                return tf if unit == "F" else (tf - 32) * 5 / 9
            for l in lines[1:]:
                row = dict(zip(hdr, l.split(",")))
                mx, mn = conv(row.get("max_temp_f")), conv(row.get("min_temp_f"))
                if mx is not None or mn is not None:
                    out[(code, dt.date.fromisoformat(row["day"]))] = {"max": mx, "min": mn}
        except Exception as e:
            print(f"[WARN] obs-live {code}: {e}", file=sys.stderr)
    # TOP-UP fresco: mergea el MAX/MIN del METAR horario mas reciente (asos.py) por si el resumen
    # diario de IEM va atrasado. max del dia solo sube -> max(); min -> min(). Si falla, queda el daily.
    for code, (lat, lon, off, unit) in STATIONS.items():
        try:
            for d, (mx, mn) in _fresh_metar_extremes(code, today, unit).items():
                cur = out.get((code, d), {})
                # IEM daily is not WU-compatible for °F; raw hourly ASOS is authoritative there.
                nmx = (mx if unit == "F" and mx is not None else
                       max([v for v in (cur.get("max"), mx) if v is not None], default=None))
                nmn = min([v for v in (cur.get("min"), mn) if v is not None], default=None)
                out[(code, d)] = {"max": nmx, "min": nmn}
        except Exception as e:
            print(f"[WARN] obs-fresh {code}: {e}", file=sys.stderr)
    return out


# ---------------- TIMELINE 24h por card (slider 30 min, pedido Santiago 2026-07-11) ----------------
CLOB = "https://clob.polymarket.com"
TL_TTL = 900   # cache 15 min por (station, date): 1 slider abierto = ~10 fetches a prices-history


def build_timeline(code, d):
    """Serie de 24h en pasos de 30 min para el modal: precios por bucket (prices-history del CLOB
    por token YES) + mu del bot (revisiones de forecast_audit.json, funcion escalonada). Tiempos en
    epoch UTC; el JS los muestra en UTC-3."""
    now = time.monotonic()
    tl = _CACHE.setdefault("tl", {})
    hit = tl.get((code, d.isoformat()))
    if hit and now - hit[0] < TL_TTL:
        return hit[1]
    slug = pm_slug(code, d)
    r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=30)
    evs = r.json() if r.status_code == 200 else []
    if not evs:
        return {"ok": False, "msg": "mercado no encontrado en Gamma"}
    now_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    # [FIX 2026-07-13, pedido Santiago] la ventana de 24h se ancla al ULTIMO precio REAL del
    # mercado, NO a "ahora": en un mercado PASADO/resuelto no hay trades en las ultimas 24h -> antes
    # la columna de precios salia toda vacia. Ahora: 1) fetch AMPLIO (5 dias) de cada token; 2)
    # end_ts = min(ahora, ultimo trade del mercado); 3) grilla de 24h que TERMINA ahi -> se ve el
    # movimiento de precios de las 24h ANTES de resolver.
    wide_start = now_ts - 5 * 24 * 3600
    labels, prices, bmeta, raw_hist = [], {}, {}, {}
    unit = STATIONS[code][3]
    last_trade = 0
    for mk in evs[0].get("markets", []):
        lab = mk.get("groupItemTitle")
        try:
            tok = _json.loads(mk.get("clobTokenIds") or "[]")[0]   # token YES
        except Exception:
            continue
        if not lab or not tok:
            continue
        bmeta[lab] = parse_bucket(lab)   # (lo, hi) para rankear top-2/top-3 en cada paso
        try:
            h = requests.get(f"{CLOB}/prices-history",
                             params={"market": tok, "startTs": wide_start, "endTs": now_ts,
                                     "fidelity": 30}, timeout=20).json().get("history", [])
        except Exception:
            h = []
        raw_hist[lab] = h
        labels.append(lab)
        if h:
            last_trade = max(last_trade, int(h[-1]["t"]))
    # ancla: ultimo trade (mercado cerrado) o ahora (mercado vivo), redondeado a 30 min
    end_ts = (min(now_ts, last_trade) if last_trade else now_ts) // 1800 * 1800
    times = [end_ts - (48 - i) * 1800 for i in range(49)]          # 24h que terminan en el ancla
    for lab in labels:
        h = raw_hist.get(lab, [])
        series, j, last = [], 0, None
        for t in times:
            while j < len(h) and h[j]["t"] <= t:
                last = h[j]["p"]; j += 1
            series.append(round(last, 3) if last is not None else None)
        prices[lab] = series
    # mu del bot: funcion escalonada de las revisiones del audit ("dd/mm HH:MM" ART; "snapshot"=inicio)
    key = f"{code}|{d.isoformat()}"
    hist = (load_audit().get(key) or {}).get("hist", [])
    revs = []
    for ts_s, mu in hist:
        if ts_s == "snapshot":
            revs.append((times[0] - 1, float(mu)))
            continue
        try:
            dd, rest = ts_s.split(" ")
            day, mon = dd.split("/")
            hh, mm = rest.split(":")
            t_art = dt.datetime(d.year, int(mon), int(day), int(hh), int(mm))
            revs.append((int((t_art + dt.timedelta(hours=3)).replace(tzinfo=dt.timezone.utc).timestamp()), float(mu)))
        except Exception:
            continue
    revs.sort()
    # sigma representativa para rankear top-2/top-3 en cada paso (el ranking es robusto a sigma).
    sig = (load_preds(dt.date.today()).get((code, d), (None, None))[1]) or (2.6 if unit == "F" else 1.5)
    # [2026-07-13, pedido Santiago] instante del BLOQUEO visible en el timeline + clamp: despues
    # del freeze el mu mostrado queda CLAVADO en el valor congelado (el audit ya no graba revisiones
    # post-freeze, esto es cinturon-y-tiradores + verificabilidad visual de que nada se mueve).
    frz_ts = int(freeze_utc(code, d).replace(tzinfo=dt.timezone.utc).timestamp())
    froze_mu = ((load_audit().get(key) or {}).get("froze") or {}).get("mu")
    mu_series, pick, ranks = [], [], []
    for t in times:
        cur = None
        for rt, mv in revs:
            if rt <= t:
                cur = mv
        if t >= frz_ts and froze_mu is not None:
            cur = froze_mu
        mu_series.append(round(cur, 1) if cur is not None else None)
        if cur is None:
            pick.append("—"); ranks.append([]); continue
        fb = int(math.floor(cur))
        # PICK = el bucket del mercado que contiene floor(mu) (WU florea). Es SIEMPRE el top-1.
        pick_lab = None
        for lab, (lo2, hi2) in bmeta.items():
            if (lo2 is None or fb >= lo2) and (hi2 is None or fb <= hi2):
                pick_lab = lab; break
        if pick_lab is None:                    # fallback a la etiqueta calculada
            pick_lab = f"{fb if unit!='F' else (fb if fb%2==0 else fb-1)}" + ("°C" if unit != "F" else f"-{(fb if fb%2==0 else fb-1)+1}°F")
        pick.append(pick_lab)
        # top-2/top-3 = pick + los vecinos MAS probables (floor-consistente), pick siempre primero
        pb = {lab: pbot_floor(cur, sig, lo2, hi2) for lab, (lo2, hi2) in bmeta.items()}
        rest = [l for l, _ in sorted(pb.items(), key=lambda kv: -kv[1]) if l != pick_lab]
        ranks.append(([pick_lab] + rest)[:3])
    out = {"ok": True, "times": times, "labels": labels, "prices": prices,
           "mu": mu_series, "pick": pick, "ranks": ranks, "unit": "°F" if unit == "F" else "°C",
           "city": STATION_META[code][2], "frz": frz_ts}
    tl[(code, d.isoformat())] = (now, out)
    return out


def load_preds(today):
    path = os.path.join(os.path.dirname(__file__), "..", "data", "predictions_forward.csv")
    out = {}
    if not os.path.exists(path):
        return out
    try:
        import csv as _csv
        with open(path, newline="") as f:
            for r in _csv.DictReader(f):
                out[(r["station"], dt.date.fromisoformat(r["target"]))] = \
                    (float(r["mu_cal"]), float(r["sigma_cal"]))
    except Exception as e:
        print(f"[WARN] predictions_forward.csv: {e}", file=sys.stderr)
    return out


def load_history():
    """Backfill lead-2 -> cards FINALIZADAS de dias pasados para el date-picker (punto 3)."""
    hist = {}
    if not os.path.exists(BACKFILL_CSV):
        return hist
    try:
        import csv as _csv
        with open(BACKFILL_CSV, newline="") as f:
            for r in _csv.DictReader(f):
                if r["lead"] != "2":
                    continue
                d = dt.date.fromisoformat(r["target"])
                hist[(r["station"], d)] = dict(
                    mu=float(r["mu_cal"]), sigma=float(r["sigma_cal"]),
                    mu_raw=float(r["mu_raw"]),
                    max_real=(float(r["max_real"]) if r["max_real"] else None),
                    win=(r["win_mkt"] or None),
                    hit=(int(float(r["hit_cal"])) if r["hit_cal"] else None),
                    pwin=(float(r["pwin_cal"]) if r["pwin_cal"] else None))
    except Exception as e:
        print(f"[WARN] backfill_check.csv: {e}", file=sys.stderr)
    return hist


def _load_emos():
    """params EMOS+clim por estacion (fit_all sobre el historico), cacheado 1h."""
    now = time.monotonic()
    ts, p = _CACHE["params"]
    if p is not None and now - ts < PARAMS_TTL:
        return p
    try:
        fc = _pd.read_csv(FC_HIST, parse_dates=["target"]); fc["target"] = fc["target"].dt.date
        obs = _pd.read_csv(OBS_HIST, parse_dates=["date"]); obs["date"] = obs["date"].dt.date
        # [FIX 2026-07-10 auditoria] mismo filtro que calib_lab: sin lead-1 (nowcast bug #5).
        p = fit_all(fc[fc.lead_h > 24], obs, sorted(obs.date.unique()))
    except Exception as e:
        print(f"[WARN] fit_all en vivo: {e}", file=sys.stderr); p = {}
    _CACHE["params"] = (now, p)
    return p


def _load_s2():
    """ultimo s2 modelado por (station, model, lead_day), cacheado 1h."""
    now = time.monotonic()
    ts, m = _CACHE["s2"]
    if m is not None and now - ts < PARAMS_TTL:
        return m
    try:
        fc = _pd.read_csv(FC_HIST, parse_dates=["avail"])
        fc["ld"] = fc["lead_h"].map(_lead_day)
        fc = fc.sort_values("avail")
        m = {(r.station, r.model, r.ld): r.s2 for r in fc.itertuples()}
    except Exception as e:
        print(f"[WARN] s2 en vivo: {e}", file=sys.stderr); m = {}
    _CACHE["s2"] = (now, m)
    return m


def calibrated_live(code, d, fc_day):
    """Recalcula (mu_cal, sigma_cal) con los m EN VIVO (corrida mas reciente) — identico pipeline
    que accumulate_predictions. Devuelve None si falta info. Es lo que 'recalibra con cada corrida'."""
    params = _load_emos().get(code)
    if params is None or not fc_day or not fc_day.get("max"):
        return None
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    peak = peak_utc(code, d)
    lead_h_now = (peak - now).total_seconds() / 3600.0
    if not (1.0 < lead_h_now <= 78.0):
        return None
    s2map = _load_s2()
    lag = {"gefs": 5.0, "ecmwf": 7.0, "icon": 7.0}
    pm = {}
    for model, m in fc_day["max"].items():
        avail_today = dt.datetime.combine(dt.date.today(), dt.time()) + dt.timedelta(hours=lag.get(model, 6))
        run_day = dt.date.today() if avail_today <= now else dt.date.today() - dt.timedelta(days=1)
        avail = dt.datetime.combine(run_day, dt.time()) + dt.timedelta(hours=lag.get(model, 6))
        lh = (peak - avail).total_seconds() / 3600.0
        if not (1.0 < lh <= 78.0):
            continue
        s2 = s2map.get((code, model, _lead_day(lh)))
        if s2 is None:
            continue
        pm[model] = (m - 0, s2)  # m absoluto; la anomalia se resta abajo
    if len(pm) < 3:
        return None
    c = clim_val(params["clim"], d)
    pm_a = {k: (m - c, s2) for k, (m, s2) in pm.items()}
    pr = predict(params["emos"], pm_a, ld=lead_h_now / 24.0)
    if pr is None:
        return None
    # CALIBRADOR V2: sesgo rolling-60d por estacion (station_bias.json; lab: hit 39%->43%).
    try:
        _b = _json.load(open(os.path.join(os.path.dirname(__file__), "..", "data",
                                          "station_bias.json"), encoding="utf-8")).get("bias", {})
    except Exception:
        _b = {}
    return round(c + pr[0] - _b.get(code, 0.0), 2), round(pr[1], 2)


def load_audit():
    if not os.path.exists(AUDIT_JSON):
        return {}
    try:
        return _json.load(open(AUDIT_JSON, encoding="utf-8"))
    except Exception:
        return {}


def save_audit(a):
    try:
        _json.dump(a, open(AUDIT_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] guardando audit: {e}", file=sys.stderr)


# ---------------------- ALERTAS INTELIGENTES (objetivo #14, 2026-07-10) ----------------------
# Por EVENTOS, no por tiempo: se comparan los datos de cada refresco contra una linea base
# persistida en data/alerts.json y se dispara solo cuando algo CAMBIA de verdad. Cada alerta
# queda visible hasta que Santiago la cierra a mano (el cierre vive en localStorage del browser,
# igual que el tachito de auditoria, asi sobrevive reloads y morphs del --watch).
ALERTS_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "alerts.json")
ALERT_TEMP_JUMP = {"F": 2.0, "C": 1.5}   # salto de la max EN VIVO que amerita alerta
ALERT_PRICE_MOVE = 0.15                  # movimiento de prob del favorito del mercado
ALERT_EDGE_BORN, ALERT_EDGE_DEAD = 0.15, 0.02   # nace oportunidad / dejo de ser favorable


def load_alerts():
    try:
        a = _json.load(open(ALERTS_JSON, encoding="utf-8"))
        return {"items": a.get("items", []), "base": a.get("base", {}), "new": []}
    except Exception:
        return {"items": [], "base": {}, "new": []}


def save_alerts(ctx, today):
    try:
        items = (ctx["items"] + ctx["new"])[-200:]
        # linea base: solo mercados de ayer en adelante (los viejos no pueden disparar nada)
        cut = (today - dt.timedelta(days=1)).isoformat()
        base = {k: v for k, v in ctx["base"].items() if k.split("|")[1] >= cut}
        _json.dump({"items": items, "base": base}, open(ALERTS_JSON, "w", encoding="utf-8"),
                   ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] guardando alerts: {e}", file=sys.stderr)


def detect_alerts(ctx, code, d, unit, priced, pbot, lost, mkt_decided, mkt_win, live_max, rank):
    """Compara el estado actual del mercado contra la linea base y emite eventos. La base de cada
    metrica se actualiza SOLO cuando dispara (o al verse por primera vez): asi un drift lento
    (0.3 grados por refresco) acumula hasta cruzar el umbral en vez de esconderse."""
    if not priced:
        return
    key = f"{code}|{d.isoformat()}"
    ciudad = STATION_META[code][2]
    deg = "°F" if unit == "F" else "°C"
    price_of = {lab: p for lab, lo, hi, p in priced}
    mkt_top = max(priced, key=lambda x: x[3])
    top_bot = rank[0] if rank else None
    edge_lab, edge_top = None, 0.0
    for lab, pb in (pbot or {}).items():
        if lab in lost:
            continue
        e = pb - price_of.get(lab, 0.0)
        if e > edge_top:
            edge_top, edge_lab = e, lab
    cur = dict(top_bot=top_bot, mkt_lab=mkt_top[0], mkt_p=mkt_top[3], live=live_max,
               decided=bool(mkt_decided), edge=edge_top, edge_lab=edge_lab)
    base = ctx["base"].get(key)
    if base is None:
        ctx["base"][key] = cur      # primera vista: sembrar base, sin alertas
        return
    now_art = to_art(dt.datetime.now(dt.timezone.utc))

    def fire(typ, lvl, txt):
        ctx["new"].append({"id": f"{key}|{typ}|{int(time.time())}", "epoch": time.time(),
                           "ts": now_art.strftime("%d/%m %H:%M"), "key": key, "type": typ,
                           "lvl": lvl, "text": f"<b>{code} · {ciudad} {ddmmyyyy(d)}</b>: {txt}"})

    # 1) el pick del bot cambio de bucket
    if top_bot and base.get("top_bot") and top_bot != base["top_bot"]:
        cause = " (el anterior quedó eliminado por la máxima en vivo)" if base["top_bot"] in lost else ""
        fire("flip", "warn", f"el pick del bot cambió {base['top_bot']} → {top_bot}{cause}")
        base["top_bot"] = top_bot
    elif top_bot and not base.get("top_bot"):
        base["top_bot"] = top_bot
    # 2) el mercado se movio fuerte (cambio de favorito, o el favorito salto de prob)
    if mkt_top[0] != base.get("mkt_lab") and mkt_top[3] >= 0.30:
        fire("mkt", "info", f"el mercado cambió de favorito: ahora {mkt_top[0]} @{mkt_top[3]:.2f} "
                            f"(antes {base.get('mkt_lab')} @{base.get('mkt_p', 0):.2f})")
        base["mkt_lab"], base["mkt_p"] = mkt_top[0], mkt_top[3]
    elif mkt_top[0] == base.get("mkt_lab") and abs(mkt_top[3] - base.get("mkt_p", mkt_top[3])) >= ALERT_PRICE_MOVE:
        fire("mkt", "info", f"la prob del favorito {mkt_top[0]} se movió "
                            f"{base.get('mkt_p', 0):.2f} → {mkt_top[3]:.2f}")
        base["mkt_p"] = mkt_top[3]
    # 3) practicamente definido pero todavia sin liquidar
    if mkt_decided and not base.get("decided"):
        fire("decidido", "ok", f"prácticamente definido: {mkt_win} @{mkt_top[3]:.2f} — aún sin liquidar")
        base["decided"] = True
    # 4) salto de la maxima en vivo
    if live_max is not None and base.get("live") is not None \
            and live_max - base["live"] >= ALERT_TEMP_JUMP[unit]:
        fire("temp", "warn", f"la máxima en vivo saltó {base['live']:.0f} → {live_max:.0f}{deg}")
        base["live"] = live_max
    elif live_max is not None and base.get("live") is None:
        base["live"] = live_max
    # 5) una ventaja dejo de ser favorable / aparecio una nueva
    bl, be = base.get("edge_lab"), base.get("edge", 0.0)
    if bl and be >= 0.10:
        e_now = (pbot or {}).get(bl, 0.0) - price_of.get(bl, 0.0)
        if bl in lost or e_now <= ALERT_EDGE_DEAD:
            gone = "quedó eliminado" if bl in lost else f"se esfumó (Δ {be*100:+.0f}¢ → {e_now*100:+.0f}¢)"
            fire("edge", "warn", f"la ventaja en {bl} {gone} — dejó de ser favorable")
            base["edge"], base["edge_lab"] = edge_top, edge_lab
    if edge_top >= ALERT_EDGE_BORN and base.get("edge", 0.0) < 0.05 and edge_lab:
        fire("edge", "info", f"oportunidad nueva: {edge_lab} con Δ {edge_top*100:+.0f}¢ "
                             f"(p bot {(pbot or {}).get(edge_lab, 0):.2f} vs precio {price_of.get(edge_lab, 0):.2f})")
        base["edge"], base["edge_lab"] = edge_top, edge_lab
    ctx["base"][key] = base


def alerts_panel(ctx):
    """Panel fijo bajo la topbar. SIEMPRE presente en el DOM (hijo estable para el morph del
    --watch); el JS lo oculta si no quedan alertas visibles. Las filas cerradas se ocultan por
    localStorage (wxbt-alerts-closed) — persisten cerradas entre refrescos y reinicios."""
    rows, cutoff = [], time.time() - 48 * 3600
    for a in reversed(ctx["items"] + ctx["new"]):           # mas nuevas arriba
        if a.get("epoch", 0) < cutoff:
            continue
        rows.append(f'<div class="arow-al {a["lvl"]}" data-aid="{a["id"]}">'
                    f'<span class="at">{a["ts"]}</span><span class="atx">{a["text"]}</span>'
                    f'<span class="aclose" data-aid="{a["id"]}" data-tip="cerrar esta alerta (no vuelve a aparecer)">✕</span></div>')
    return (f'<div id="alerts-box" data-noanim class="empty"><div class="ahead">🔔 Alertas por evento'
            f'<span class="abadge" id="alerts-count"></span>'
            f'<span class="ahint">cambio de pick · salto de temperatura · mercado decidido sin liquidar '
            f'· ventaja que nace o muere — persisten hasta que las cierres</span>'
            f'<span class="aclearall" id="alerts-clear" data-tip="cerrar TODAS las alertas visibles '
            f'de una (pedido 2026-07-12); las nuevas siguen apareciendo">🗑 limpiar todas</span></div>'
            f'<div id="alerts-list">{"".join(rows)}</div></div>')


def load_timing():
    if not os.path.exists(TIMING_JSON):
        return None
    try:
        return _json.load(open(TIMING_JSON, encoding="utf-8"))
    except Exception:
        return None


# ------------------------------- render -------------------------------

CSS = """
/* ============ WXBT TERMINAL v2 (2026-07-11, rehecho de 0 a pedido de Santiago) ============
   Estetica terminal financiera: fondo profundo, paneles con borde fino, verde fosforo para el
   bot, cyan para el mercado, ambar para warnings, numeros SIEMPRE en monospace. Dark-only. */
html,body{margin:0;padding:0;background:#06090d;min-height:100vh;}
.viz-root{--s1:#0c1218;--s2:#111a23;--page:#06090d;--ink:#d7e5f0;--ink2:#9fb6c9;--mut:#587085;
  --grid:#1a2836;--base:#2b3f52;--bd:#1d2d3c;--mkt:#38c6ff;--fc:#00e5a0;
  --fcw:rgba(0,229,160,.08);--live:#ffc247;--fin:#2fd575;--finw:rgba(47,213,117,.10);
  --warn:#ffb020;--soon:#ffb020;--red:#ff5468;
  --mono:"Cascadia Mono","Consolas",ui-monospace,monospace;
  font-family:"Segoe UI",system-ui,-apple-system,sans-serif;background:var(--page);color:var(--ink);
  padding:0 22px 40px;max-width:1500px;margin:0 auto;}
.viz-root a{color:inherit;}
.viz-root .topbar{position:sticky;top:0;z-index:20;padding:10px 0 8px;
  background:linear-gradient(180deg,#08101a 0%,var(--page) 100%);
  border-bottom:1px solid var(--bd);box-shadow:0 1px 0 rgba(0,229,160,.12);}
.viz-root .row1{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;}
.viz-root h1{font-size:15px;margin:0;letter-spacing:.14em;text-transform:uppercase;
  font-family:var(--mono);color:var(--fc);}
.viz-root h1::before{content:"▮ ";animation:blink 1.4s steps(1) infinite;}
@keyframes blink{50%{opacity:.15;}}
.viz-root .clock{font-size:15px;font-weight:700;font-family:var(--mono);color:var(--live);
  text-shadow:0 0 12px rgba(255,194,71,.35);}
.viz-root .clock small{color:var(--mut);font-weight:400;font-size:10px;margin-left:6px;font-family:"Segoe UI",sans-serif;}
.viz-root .subt{color:var(--mut);font-size:11px;}
.viz-root .runs{font-size:10.5px;color:var(--mut);display:flex;gap:12px;flex-wrap:wrap;margin-top:4px;
  font-family:var(--mono);}
.viz-root .runs .next{color:var(--fc);font-weight:600;}
.viz-root .filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px;}
.viz-root .filters select,.viz-root .filters input[type=date]{background:var(--s2);color:var(--ink);
  border:1px solid var(--bd);border-radius:4px;padding:4px 8px;font-size:12px;font-family:inherit;}
.viz-root .chip{font-size:11px;padding:3px 10px;border-radius:3px;border:1px solid var(--bd);
  background:var(--s2);color:var(--ink2);cursor:pointer;user-select:none;transition:all .15s;}
.viz-root .chip.on{background:var(--fc);color:#03110c;border-color:var(--fc);font-weight:700;}
.viz-root .count{font-size:11px;color:var(--mut);margin-left:auto;font-family:var(--mono);}
.viz-root .reset{font-size:11px;color:var(--mkt);cursor:pointer;text-decoration:underline;}
.viz-root h3.dia{font-size:12px;color:var(--fc);margin:20px 0 10px;text-transform:uppercase;
  letter-spacing:.14em;font-family:var(--mono);}
.viz-root h3.dia::before{content:"┌─ ";color:var(--base);}
.viz-root .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(345px,1fr));gap:10px;}
.viz-root .card{background:var(--s1);border:1px solid var(--bd);border-radius:6px;
  padding:11px 13px 9px;transition:opacity .2s;position:relative;}
.viz-root .card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;
  background:var(--base);border-radius:6px 0 0 6px;}
.viz-root .card[data-estado="encurso"]::before{background:var(--fc);box-shadow:0 0 8px rgba(0,229,160,.5);}
.viz-root .card[data-estado="soon"]::before{background:var(--warn);box-shadow:0 0 8px rgba(255,176,32,.5);}
.viz-root .card[data-estado="resol"]::before,.viz-root .card[data-estado="pendrev"]::before{background:var(--mkt);}
.viz-root .card.fin::before{background:var(--fin);}
.viz-root .card:hover{border-color:var(--base);}
.viz-root .card-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;gap:8px;}
.viz-root .card-head .st a{font-size:14px;font-weight:700;text-decoration:none;font-family:var(--mono);
  letter-spacing:.02em;border-bottom:1px dashed var(--base);}
.viz-root .card-head .st a:hover{color:var(--mkt);border-color:var(--mkt);}
.viz-root .card-head .city{font-size:10px;color:var(--mut);display:block;margin-top:2px;}
.viz-root .card-head .city.local{white-space:nowrap;color:var(--ink2);margin-top:1px;}
.viz-root .card-head .city.local b{color:var(--live);font-family:var(--mono);}
.viz-root .badges{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;}
.viz-root .badge{font-size:9px;padding:2px 7px;border-radius:3px;background:var(--s2);
  color:var(--mut);white-space:nowrap;border:1px solid var(--bd);font-family:var(--mono);
  text-transform:uppercase;letter-spacing:.05em;}
.viz-root .badge.encurso{color:var(--fc);border-color:rgba(0,229,160,.4);background:var(--fcw);}
.viz-root .badge.fin{color:#03110c;background:var(--fin);border-color:var(--fin);font-weight:700;}
.viz-root .badge.soon{color:var(--soon);border-color:rgba(255,176,32,.45);background:rgba(255,176,32,.08);}
.viz-root .badge.resol{color:var(--mkt);border-color:rgba(56,198,255,.4);font-weight:700;}
.viz-root .badge.pendrev{color:var(--warn);border-color:rgba(255,176,32,.5);font-weight:700;}
.viz-root .badge.frozen{color:#0a0f14;background:var(--mut);border-color:var(--mut);font-weight:700;}
.viz-root .badge.abierto{color:var(--mkt);border-color:rgba(56,198,255,.4);}
.viz-root .badge.wu{cursor:pointer;text-decoration:none;display:inline-block;}
.viz-root .badge.wu:hover{color:var(--mkt);border-color:var(--mkt);}
.viz-root .badge.tlb{cursor:pointer;color:var(--ink2);}
.viz-root .badge.tlb:hover{color:var(--fc);border-color:rgba(0,229,160,.5);}
.viz-root .trio{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin:0 0 8px;}
.viz-root .stat{background:var(--s2);border:1px solid var(--grid);border-radius:4px;padding:6px 9px 5px;}
.viz-root .stat .lbl{font-size:8px;color:var(--mut);text-transform:uppercase;letter-spacing:.09em;margin-bottom:2px;}
.viz-root .stat .val{font-size:18px;font-weight:700;font-family:var(--mono);line-height:1.12;}
.viz-root .stat .sub{font-size:9.5px;color:var(--ink2);font-family:var(--mono);}
.viz-root .stat.bot .val{color:var(--fc);text-shadow:0 0 14px rgba(0,229,160,.3);}
.viz-root .stat.live .val{color:var(--live);text-shadow:0 0 14px rgba(255,194,71,.3);}
.viz-root .stat.finres .val{color:var(--fin);}
.viz-root .models{font-size:10px;color:var(--mut);margin:0 0 6px;font-family:var(--mono);}
.chg{animation:chg 2.5s ease-out;}
@keyframes chg{0%{background:rgba(0,229,160,.30);}100%{background:transparent;}}
.viz-root .lostwarn{font-size:10.5px;color:var(--mut);background:var(--s2);border-radius:4px;
  border:1px solid var(--grid);padding:4px 8px;margin:0 0 6px;}
.viz-root .wstat{font-size:11px;font-weight:600;padding:4px 8px;border-radius:4px;margin:0 0 8px;
  font-family:var(--mono);border:1px solid transparent;}
.viz-root .wstat.ok{color:var(--fin);background:var(--finw);border-color:rgba(47,213,117,.25);}
.viz-root .wstat.late{color:var(--warn);background:rgba(255,176,32,.08);border-color:rgba(255,176,32,.25);}
.viz-root .wstat.closed{color:var(--red);background:rgba(255,84,104,.08);border-color:rgba(255,84,104,.25);}
.viz-root .wstat.pend{color:var(--warn);background:rgba(255,176,32,.10);border-color:rgba(255,176,32,.3);}
.viz-root table.bkts td .miss{text-decoration:line-through;text-decoration-thickness:1.5px;color:var(--mut);}
.viz-root .actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px;}
.viz-root .qbtn{font-size:11px;padding:4px 10px;border-radius:3px;border:1px solid var(--bd);
  background:var(--s2);color:var(--ink);cursor:pointer;font-family:inherit;text-decoration:none;
  display:inline-block;transition:all .12s;}
.viz-root .qbtn:hover{border-color:var(--fc);color:var(--fc);box-shadow:0 0 8px rgba(0,229,160,.2);}
.viz-root .qbtn.dl{border-color:rgba(47,213,117,.5);color:var(--fin);}
.viz-root .qbtn:disabled{opacity:.4;cursor:not-allowed;}
.viz-root .qbtn.busy{opacity:.6;pointer-events:none;}
.viz-root .qmsg{font-size:11px;color:var(--mut);margin-left:4px;}
.viz-root .qmsg.ok{color:var(--fin);} .viz-root .qmsg.err{color:var(--red);}
.viz-root .models b{color:var(--ink2);font-weight:600;}
.viz-root table.bkts{border-collapse:collapse;width:100%;font-size:11.5px;}
.viz-root table.bkts th{font-size:8px;color:var(--mut);text-align:left;font-weight:600;
  text-transform:uppercase;letter-spacing:.08em;padding:2px 4px;border-bottom:1px solid var(--grid);}
.viz-root table.bkts td{padding:2.5px 4px;font-family:var(--mono);font-size:11px;
  border-bottom:1px solid rgba(26,40,54,.55);}
.viz-root table.bkts tr:hover td{background:var(--s2);}
.viz-root table.bkts .num{text-align:right;}
.viz-root .track{background:var(--grid);border-radius:0 2px 2px 0;height:8px;min-width:56px;display:block;}
.viz-root .fill{background:var(--mkt);height:100%;border-radius:0 2px 2px 0;min-width:2px;display:block;
  box-shadow:0 0 6px rgba(56,198,255,.35);}
.viz-root .dot{display:inline-block;width:7px;height:7px;border-radius:1px;background:var(--fc);}
.viz-root .edgehi{font-weight:700;color:var(--fc);}
.viz-root .chipno{font-size:8.5px;font-weight:700;color:var(--warn);border:1px solid rgba(255,176,32,.4);
  border-radius:3px;padding:0 4px;font-family:var(--mono);}
.viz-root .verdict{display:flex;gap:10px;align-items:center;font-size:12px;margin:4px 0 2px;
  font-family:var(--mono);}
.viz-root .verdict .ok{color:var(--fin);font-weight:700;}
.viz-root .verdict .bad{color:var(--red);font-weight:700;}
.viz-root details.acc{margin-top:8px;border-top:1px solid var(--grid);padding-top:6px;}
.viz-root details.acc summary{cursor:pointer;font-size:10.5px;color:var(--mut);list-style:none;}
.viz-root details.acc summary::before{content:"▸ ";transition:transform .2s;}
.viz-root details.acc[open] summary::before{content:"▾ ";}
.viz-root details.acc .inner{font-size:11px;color:var(--ink2);padding:6px 2px 2px;line-height:1.6;
  animation:accIn .18s ease;}
@keyframes accIn{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
.viz-root .empty{font-size:11px;color:var(--mut);font-style:italic;}
.viz-root .timing{background:var(--s1);border:1px solid var(--bd);border-radius:12px;
  padding:13px 16px;margin-top:22px;font-size:12px;color:var(--ink2);}
.viz-root .timing h4{margin:0 0 6px;font-size:12.5px;color:var(--ink);}
.viz-root .timing table{border-collapse:collapse;font-size:11px;}
.viz-root .timing td,.viz-root .timing th{padding:2px 12px 2px 0;font-variant-numeric:tabular-nums;
  color:var(--ink2);text-align:left;}
.viz-root .timing th{color:var(--mut);font-size:9px;text-transform:uppercase;}
.viz-root .cont-lbl{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.12em;
  margin:10px 0 6px;font-weight:700;}
.viz-root .cont-lbl::after{content:"";display:inline-block;width:60px;height:1px;background:var(--grid);
  margin-left:10px;vertical-align:middle;}
.viz-root .alog{display:flex;flex-direction:column;gap:2px;max-height:340px;overflow-y:auto;}
.viz-root .arow{display:flex;gap:10px;align-items:baseline;font-size:11.5px;padding:3px 0;
  border-bottom:1px solid var(--s2);font-variant-numeric:tabular-nums;}
.viz-root .arow .at{color:var(--mut);min-width:78px;font-size:10.5px;}
.viz-root .arow .ast{font-weight:700;min-width:44px;}
.viz-root .arow .ad{color:var(--mut);min-width:74px;}
.viz-root .arow .aval{color:var(--ink2);}
.viz-root .arow .aval b{color:var(--ink);}
.viz-root .arow .up{color:#d03b3b;font-weight:700;}
.viz-root .arow .down{color:var(--mkt);font-weight:700;}
.viz-root .arow .frzt{color:var(--mut);font-size:10px;border:1px solid var(--bd);border-radius:3px;padding:0 5px;}
.viz-root .fill.y2{background:#ffd23e;box-shadow:0 0 6px rgba(255,210,62,.4);}
.viz-root .fill.o3{background:#ff8c42;box-shadow:0 0 6px rgba(255,140,66,.4);}
.viz-root .fill.lost{background:var(--base);opacity:.4;box-shadow:none;}
.viz-root tr.lostrow td{color:var(--mut);text-decoration:line-through;text-decoration-thickness:1px;}
.viz-root tr.lostrow td:first-child,.viz-root tr.lostrow td:nth-child(3){text-decoration:none;}
.viz-root .dot.y2{background:#ffd23e;}
/* colores pedidos 2026-07-12: pick/EXACTO verde, top-2 amarillo, top-3 naranja */
.viz-root .dot.g1{background:var(--fin);}
.viz-root .dot.o3{background:#ff8c42;}
.viz-root .fill.g1{background:var(--fin);box-shadow:0 0 6px rgba(47,213,117,.4);}
.viz-root .okc{color:var(--fin);font-weight:800;}
.viz-root .badc{color:var(--red);font-weight:800;}
.viz-root .sgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:4px 0 8px;}
.viz-root .scard{background:var(--s2);border:1px solid var(--grid);border-radius:4px;padding:9px 11px;}
.viz-root .scard .lbl{font-size:8.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.09em;}
.viz-root .scard .big{font-size:23px;font-weight:700;font-family:var(--mono);line-height:1.2;color:var(--fc);}
.viz-root .scard .sub{font-size:10px;color:var(--ink2);font-family:var(--mono);}
.viz-root .scard.y .big{color:#ffd23e;} .viz-root .scard.o .big{color:#ff8c42;}
.viz-root .scard.bad .big{color:var(--red);}
.viz-root .cchips{display:flex;gap:8px;flex-wrap:wrap;}
.viz-root .cchip{font-size:11px;color:var(--ink2);background:var(--s2);border:1px solid var(--grid);
  border-radius:3px;padding:3px 9px;font-family:var(--mono);}
#viz-tooltip{position:fixed;pointer-events:none;background:#e8f2fa;color:#0a1017;font-size:11px;
  padding:4px 8px;border-radius:4px;opacity:0;transition:opacity .1s;z-index:100;max-width:340px;
  white-space:normal;font-family:"Segoe UI",system-ui,sans-serif;}
.viz-root .hidden{display:none !important;}
/* ---------- TIMELINE 24h (modal fuera de .viz-root, a salvo del morph) ---------- */
#tl-modal{position:fixed;inset:0;background:rgba(3,6,9,.78);z-index:200;display:flex;
  align-items:center;justify-content:center;backdrop-filter:blur(2px);}
#tl-modal .tl-box{background:#0c1218;border:1px solid #2b3f52;border-radius:8px;width:min(560px,92vw);
  max-height:86vh;overflow-y:auto;padding:14px 18px;color:#d7e5f0;
  font-family:"Segoe UI",system-ui,sans-serif;box-shadow:0 0 40px rgba(0,229,160,.12);}
#tl-modal .tl-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;
  font-family:"Cascadia Mono","Consolas",monospace;font-size:13px;color:#00e5a0;letter-spacing:.06em;}
#tl-modal .tl-x{cursor:pointer;color:#587085;font-weight:700;padding:2px 8px;}
#tl-modal .tl-x:hover{color:#ff5468;}
#tl-modal .tl-ctl{display:flex;gap:12px;align-items:center;margin:6px 0 10px;}
#tl-modal input[type=range]{flex:1;accent-color:#00e5a0;}
#tl-modal .tl-time{font-family:"Cascadia Mono","Consolas",monospace;font-size:13px;color:#ffc247;
  min-width:150px;text-align:right;white-space:nowrap;}
#tl-modal .tl-bot{font-size:12px;margin:0 0 8px;color:#9fb6c9;font-family:"Cascadia Mono",monospace;}
#tl-modal .tl-bot b{color:#00e5a0;}
#tl-modal table{border-collapse:collapse;width:100%;font-size:11.5px;}
#tl-modal th{font-size:8px;color:#587085;text-align:left;text-transform:uppercase;letter-spacing:.08em;
  padding:2px 4px;border-bottom:1px solid #1a2836;}
#tl-modal td{padding:2.5px 4px;font-family:"Cascadia Mono","Consolas",monospace;
  border-bottom:1px solid rgba(26,40,54,.55);}
#tl-modal .num{text-align:right;}
#tl-modal .track{background:#1a2836;height:8px;min-width:120px;display:block;border-radius:0 2px 2px 0;}
#tl-modal .fill{background:#38c6ff;height:100%;min-width:2px;display:block;border-radius:0 2px 2px 0;}
#tl-modal .tl-note{font-size:10px;color:#587085;margin-top:8px;}
#tl-modal .tl-bot .tl-y{color:#ffd23e;} #tl-modal .tl-bot .tl-o{color:#ff8c42;}
#tl-modal .tl-dot{display:inline-block;width:7px;height:7px;border-radius:1px;}
#tl-modal .tl-dot.g{background:#00e5a0;} #tl-modal .tl-dot.y{background:#ffd23e;} #tl-modal .tl-dot.o{background:#ff8c42;}
#tl-modal tr.tl-r1 td:nth-child(2){color:#00e5a0;font-weight:700;}
#tl-modal tr.tl-r2 td:nth-child(2){color:#ffd23e;} #tl-modal tr.tl-r3 td:nth-child(2){color:#ff8c42;}
"""


def state_of(code, d, info, now_utc):
    """(clave, label) del estado por HORA REAL de la ciudad, NO por fecha-calendario de Argentina.
    OJO [BUG CORREGIDO 2026-07-09]: el endDate de Gamma es NOMINAL (12:00Z del dia target) y NO es
    el fin real — a las 12Z todo el dia se marcaba FINALIZADO con Europa recien a la tarde.

    REGLA DE RESOLUCION (objetivo #1, 2026-07-10): un mercado es FINALIZADO solo cuando GAMMA validó
    (info['winner'] viene del outcomePrices/resolucion de Gamma). Paso previo: si el payout de algun
    bucket ya llego a >=99.5% Y el pico ya paso, el resultado esta de-facto decidido pero AUN sin
    validar -> estado 'pendrev' (badge "Pendiente de revisión Gamma"), NUNCA FINALIZADO antes de tiempo."""
    off = local_offset(code, d)   # DST-aware: el dia LOCAL del mercado corre con el offset real
    now = now_utc.replace(tzinfo=None)
    day_start = dt.datetime.combine(d, dt.time()) - dt.timedelta(hours=off)   # 00:00 local del target, en UTC
    day_end = day_start + dt.timedelta(hours=24)
    peak = peak_utc(code, d)
    if info and info.get("winner"):
        return "fin", "FINALIZADO"
    # de-facto decidido por el mercado (>=99.5%) + pico pasado, pero Gamma todavia no resolvio
    mtop = max((p for _, _, _, p in info["buckets"] if p is not None), default=0.0) if info and info.get("buckets") else 0.0
    if mtop >= MKT_RESOLVED_MIN and now >= peak:
        return "pendrev", "PENDIENTE GAMMA"
    if now >= day_end:
        return "resol", "RESOLVIENDO"       # dia local completo; esperando el dato WU del dia sig.
    if now >= day_start:
        if 0 <= (peak - now).total_seconds() < 3 * 3600:
            return "soon", "PICO CERCA"
        return "encurso", "EN CURSO"
    return "prox", "PRÓXIMO"


def forecast_frozen(code, d, now_utc):
    """True si ya paso el deadline de recalibracion (04:30 hora local del target): FIJADO."""
    return now_utc.replace(tzinfo=None) >= freeze_utc(code, d)


def _last_rev_before(hist, d, ddl_utc):
    """mu de la ultima revision del audit con timestamp <= ddl_utc. Formato de hist (timeline):
    [("snapshot"|"dd/mm HH:MM" en ART, mu), ...] en orden cronologico. None si no hay ninguna.
    Usado para capturar el freeze TARDE sin contaminarlo con datos post-deadline."""
    best = None
    for ts_s, mu in hist or []:
        if ts_s == "snapshot":
            if best is None:
                best = float(mu)
            continue
        try:
            dd_, rest = ts_s.split(" ")
            day, mon = dd_.split("/")
            hh, mm = rest.split(":")
            t_utc = dt.datetime(d.year, int(mon), int(day), int(hh), int(mm)) + dt.timedelta(hours=3)
        except Exception:
            continue
        if t_utc <= ddl_utc:
            best = float(mu)
    return best


def card_attrs(code, d, state, conf, reco, pmax):
    cont, pais, ciudad, _, _ = STATION_META[code]
    return (f'data-cont="{cont}" data-pais="{pais}" data-ciudad="{ciudad}" data-st="{code}" '
            f'data-fecha="{d.isoformat()}" data-estado="{state}" data-conf="{conf}" '
            f'data-reco="{1 if reco else 0}" data-pmax="{pmax:.2f}"')


def window_status(code, d, now_utc):
    """¿Estoy a tiempo de entrar? Relativo al deadline (pico −1.5h, cuando el bot deja de recalibrar):
    ANTES = el pronostico es mas fresco que lo que el mercado quiza ya precio (ahi vive el edge, si
    existe). DESPUES = el pronostico esta fijado y la info nueva (termometro) el mercado tambien la ve."""
    now = now_utc.replace(tzinfo=None)
    peak = peak_utc(code, d)
    ddl = freeze_utc(code, d)
    if now < ddl:
        h = (ddl - now).total_seconds() / 3600
        dtag = "" if to_art(ddl).date() == to_art(now).date() else f" ({ddmmyyyy(to_art(ddl).date())})"
        return "ok", f"✅ A TIEMPO · quedan <span class='wtime'>{int(h)}h {int((h % 1) * 60):02d}m</span> para el bloqueo{dtag}"
    if now < peak:
        h = (peak - now).total_seconds() / 3600
        return "late", f"⚠️ TARDE · pronóstico ya fijado · pico en <span class='wtime'>{int(h)}h {int((h % 1) * 60):02d}m</span>"
    return "closed", "🔴 PICO PASADO · ya no hay ventaja de pronóstico"


def fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def pbot_floor(mu, sg, lo, hi):
    """P(el bucket gana) bajo la resolución REAL de WU = FLOOR: el bucket [lo,hi] gana si
    floor(obs) ∈ [lo,hi] ⇔ obs ∈ [lo, hi+1). El bucket_prob del motor asume half-up ([lo−0.5,hi+0.5]);
    correrle el mu en −0.5 lo convierte EXACTAMENTE en la ventana floor (sin tocar el motor/tests).
    Así el ranking y el p bot que ve Santiago coinciden con floor(mu) (35.9 → 35, no 36)."""
    return bucket_prob(mu - 0.5, sg, lo, hi)


def card_html(code, d, today, now_utc, unit, fc_day, info, pred=None, live=None, hist=None, stats=None, alerts=None, audit=None):
    deg = "°F" if unit == "F" else "°C"
    width = BUCKET_WIDTH[unit]
    live_st = (live or {}).get((code, d))   # obs EN VIVO de ESTA estacion y ESTA fecha
    cont, pais, ciudad, _, _ = STATION_META[code]
    state, state_lbl = state_of(code, d, info, now_utc)
    if state == "resol" and hist and hist.get("win"):
        state, state_lbl = "fin", "FINALIZADO"   # historico del backfill: ganador ya conocido
    frozen = forecast_frozen(code, d, now_utc) and state != "fin"
    if hist and state == "fin" and not (info and info.get("winner")):
        winner = hist.get("win")
    else:
        winner = (info or {}).get("winner")

    # pred: hoy/futuro del snapshot; pasado del backfill
    mu = sg = None
    if pred:
        mu, sg = pred
    elif hist:
        mu, sg = hist["mu"], hist["sigma"]

    consensus = tmin = None
    mx_models = (fc_day or {}).get("max", {})
    if mx_models:
        mxs = list(mx_models.values())
        consensus = sum(mxs) / len(mxs)
        mins = list((fc_day or {}).get("min", {}).values())
        tmin = sum(mins) / len(mins) if mins else None

    priced = [(lab, lo, hi, p) for lab, lo, hi, p in (info or {}).get("buckets", []) if p is not None]

    def center(lo, hi):
        if lo is None:
            lo = hi - width
        if hi is None:
            hi = lo + width
        return (lo + hi) / 2
    priced.sort(key=lambda x: center(x[1], x[2]))

    # BUCKETS YA IMPOSIBLES (pedido Santiago 2026-07-10): el MAX del dia solo sube. Si la obs EN VIVO
    # ya marca X, todo bucket cuyo techo < floor(X) YA PERDIO -> no puede ser top-2/top-3 ni tener edge.
    live_max = (live_st or {}).get("max") if state in ("encurso", "soon") else None
    floor_live = int(math.floor(live_max)) if live_max is not None else None
    def is_lost(lo, hi):
        return floor_live is not None and hi is not None and hi < floor_live
    lost = {lab for lab, lo, hi, p in priced if is_lost(lo, hi)}
    # MERCADO DE-FACTO DECIDIDO (objetivo #1): el estado 'pendrev' ya exige payout >=99.5% + pico
    # pasado. Ese bucket es el ganador probable (aun sin validar Gamma); el resto perdio y ya no
    # hay "A TIEMPO". Umbral y momento unificados en state_of (antes: 0.95 hardcodeado aca).
    mkt_top = max(priced, key=lambda x: x[3]) if priced else None
    mkt_decided = state == "pendrev" and mkt_top is not None
    mkt_win = mkt_top[0] if mkt_decided else None
    if mkt_decided:
        lost |= {lab for lab, lo, hi, p in priced if lab != mkt_win}

    # CONGELAMIENTO INMUTABLE (objetivo #2): al pasar el deadline, el pick/top-2/top-3 quedan FIJADOS
    # en data/forecast_audit.json y NUNCA se recalculan (ni con precios/obs nuevas). Asi la evaluacion
    # historica compara SIEMPRE contra lo que el bot dijo al bloquear, no contra un valor que se mueve.
    key = f"{code}|{d.isoformat()}"
    froze = (audit or {}).get(key, {}).get("froze") if audit is not None else None
    if froze and froze.get("mu") is not None:
        mu = froze["mu"]; sg = froze.get("sg", sg)   # el mu tambien queda fijado al valor de bloqueo

    pbot, pmax = {}, 0.0
    if mu is not None and priced:
        for lab, lo, hi, p in priced:
            pbot[lab] = pbot_floor(mu, sg, lo, hi)   # resolucion FLOOR de WU (no half-up)
        pmax = max((v for k, v in pbot.items() if k not in lost), default=0.0)

    def _rank_floor():
        """Ranking pick-first IDENTICO al del timeline 24h: pick = bucket que contiene floor(mu),
        resto por prob floor-consistente desc. NO excluye 'lost' — es el pronostico puro."""
        if not pbot or mu is None:
            return []
        fb = int(math.floor(mu))
        pick_lab = None
        for lab, lo2, hi2, _p in priced:
            if (lo2 is None or fb >= lo2) and (hi2 is None or fb <= hi2):
                pick_lab = lab; break
        rest = [l for l, _ in sorted(pbot.items(), key=lambda kv: -kv[1]) if l != pick_lab]
        return ([pick_lab] if pick_lab else []) + rest

    _rank_alive = [lab for lab, _ in sorted(pbot.items(), key=lambda kv: -kv[1])
                   if lab not in lost] if pbot else []
    if froze:
        # FIJADO INMUTABLE (pedido Santiago 2026-07-12): post-deadline el top-1/2/3 se deriva del
        # mu CONGELADO con el MISMO ranking del timeline (pick-first, sin excluir muertos). NADA se
        # re-sugiere sobre la marcha: los muertos se tachan pero conservan lugar y color.
        _rank = _rank_floor()
        top2 = set(_rank[:2]); top3 = set(_rank[:3])
    else:
        # ABIERTO: ranking solo entre buckets aun posibles (guia operativa pre-bloqueo)
        _rank = _rank_alive
        top2 = set(_rank[:2]); top3 = set(_rank[:3])
    # capturar el freeze la PRIMERA vez post-deadline. Si el watcher NO corria en el momento
    # exacto del bloqueo, usar la ULTIMA revision del audit ANTERIOR al deadline (no el mu fresco
    # de ahora): el freeze refleja lo que el bot decia al bloquear, aunque se capture tarde.
    if not froze and audit is not None and mu is not None and pbot \
            and forecast_frozen(code, d, now_utc) and state != "fin":
        ddl_utc = freeze_utc(code, d)
        mu_h = _last_rev_before((audit.get(key) or {}).get("hist", []), d, ddl_utc)
        if mu_h is not None and abs(mu_h - mu) > 1e-9:
            mu = mu_h
            for lab, lo, hi, p in priced:
                pbot[lab] = pbot_floor(mu, sg, lo, hi)
        _rank = _rank_floor()
        top2 = set(_rank[:2]); top3 = set(_rank[:3])
        if _rank:
            audit.setdefault(key, {})["froze"] = {
                "mu": round(mu, 2), "sg": (round(sg, 2) if sg is not None else None), "top": _rank[:3]}
            _FROZE["dirty"] = True
    reco = False
    if pbot and state in ("encurso", "prox", "soon"):
        # señal operativa: SOLO buckets aun posibles (recomendar un bucket muerto no tiene sentido)
        alive = {k: v for k, v in pbot.items() if k not in lost} or pbot
        best = max(alive, key=alive.get)
        px = next(p for lab, lo, hi, p in priced if lab == best)
        reco = (alive[best] - px) * 100 >= RECO_EDGE_MIN
    conf = "alta" if (sg is not None and pmax >= PMAX_HI) else ("media" if pmax >= 0.25 else "baja")

    # alertas por evento (objetivo #14): solo mercados vigentes y aun operables/observables.
    # Reciben el ranking VIVO (excluye muertos): son señal de trading, no el pronostico fijado.
    if alerts is not None and d >= today and state in ("encurso", "soon", "resol"):
        detect_alerts(alerts, code, d, unit, priced, pbot, lost, mkt_decided, mkt_win, live_max, _rank_alive)

    close_art = to_art(info["close_utc"]).strftime("%H:%M") if info and info.get("close_utc") else None
    wins, ddl = entry_windows(code, d)

    h = [f'<div class="card{" fin" if state == "fin" else ""}" {card_attrs(code, d, state, conf, reco, pmax)}>']
    frozen_badge = (f'<span class="badge frozen" data-tip="pasó el deadline de recalibración — '
                    f'el pronóstico quedó FIJADO a las {ddl} AR">🔒 FIJADO</span>' if frozen else
                    f'<span class="badge abierto" data-tip="el bot aún recalibra con cada corrida; '
                    f'se bloquea a las {ddl} AR">◷ ABIERTO</span>' if state in ("encurso", "soon") else '')
    if state in ("resol", "pendrev"):
        frozen_badge = '<span class="badge frozen" data-tip="dia local terminado — pronostico fijado; WU resuelve con el primer dato de manana">🔒 FIJADO</span>'
    # HORA LOCAL de la ciudad del mercado (objetivo #12): en su PROPIA linea, corta, sin cortarse
    # a mitad (nowrap). data-noanim para que el morph del --watch no la haga parpadear cada minuto.
    hora_local = (now_utc.replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, d))).strftime("%H:%M")
    sub_estado = (f' · pico ~{to_art(peak_utc(code, d)).strftime("%H:%M")} AR' if state in ("encurso", "soon", "prox")
                  else (' · esperando validación Gamma' if state == "pendrev"
                        else (' · esperando resolución WU' if state == "resol" else '')))
    h.append(f'<div class="card-head"><span class="st">'
             f'<a href="https://polymarket.com/event/{pm_slug(code, d)}" target="_blank" '
             f'data-tip="abrir este mercado en Polymarket">{code} · {ciudad}</a>'
             f'<span class="city">{pais} · {fecha_es(d)}{sub_estado}</span>'
             f'<span class="city local">Hora local: <b data-noanim>{hora_local}</b></span></span>'
             f'<span class="badges"><span class="badge {state}">{state_lbl}</span>{frozen_badge}'
             f'<a class="badge wu" target="_blank" href="{wu_url(code, d)}" '
             f'data-tip="WU es la fuente que RESUELVE este mercado — abrir la pagina oficial de la '
             f'estacion para esta fecha">WU ↗</a>'
             f'<a class="badge wu" href="city_{code}.html" '
             f'data-tip="vista ciudad: mercado + modelos + PWS + historial">🏙</a>'
             f'<span class="badge tlb" data-tlst="{code}" data-tlfe="{d.isoformat()}" '
             f'data-tip="TIMELINE: como se movieron las cuotas del mercado y la prediccion del bot '
             f'en las ultimas 24h — slider de 30 min, hora UTC-3">⏱ 24h</span></span></div>')

    # bucket GANADOR en cards finalizadas: para marcarlo ✓ y TACHAR los top-2/top-3 del bot que
    # NO ganaron (objetivo #2: tacharlos, mantener el valor visible, no ocultar ni poner X).
    fin_view = state == "fin" and bool(winner)
    win_lab = None
    if fin_view:
        wl = str(winner)
        wn = [int(x) for x in re.findall(r"\d+", wl)]
        for lab, lo, hi, p in priced:
            if lab == wl:
                win_lab = lab; break
        if win_lab is None and wn:
            for lab, lo, hi, p in priced:
                loo = lo if lo is not None else (hi - width if hi is not None else wn[0])
                hii = hi if hi is not None else (lo + width if lo is not None else wn[0])
                if ("higher" in wl or ">=" in wl) and hi is None and loo <= wn[0]:
                    win_lab = lab; break
                if ("below" in wl or "<=" in wl) and lo is None and wn[0] <= hii:
                    win_lab = lab; break
                if loo <= wn[0] <= hii:
                    win_lab = lab; break
    win_mark_lab = mkt_win or win_lab   # bucket a resaltar con ✓ (pendrev o finalizado)

    # tabla de buckets (se muestra en cards ACTIVAS y tambien FINALIZADAS — pedido 2026-07-09)
    table_html = ""
    if priced:
        hdr_p = '<th class="num">p bot</th><th class="num">Δ¢</th>' if pbot else ''
        t = [f'<table class="bkts"><tr><th></th><th>bucket</th><th>precio</th>'
             f'<th class="num">$</th>{hdr_p}<th></th></tr>']
        for lab, lo, hi, p in priced:
            pb = pbot.get(lab)
            lostb = lab in lost
            # [FIX 2026-07-10] NO ocultar mas filas baratas: Santiago tiene posiciones NO en
            # buckets de cola (NY 90-91) y desaparecian de la vista. Se muestran TODOS.
            in3 = lab in top3 and lab not in top2
            was_topk = lab in top2 or lab in top3   # el bot lo tenia en su top-2/top-3 congelado
            # GANADOR (mercado de-facto >=99.5%, o resultado WU en finalizado): check verde
            if lab == win_mark_lab:
                tip = ("el mercado lo da por ganador (>=99.5%, pendiente Gamma)" if mkt_win
                       else "bucket ganador segun WU")
                dot = f'<span class="okc" data-tip="{tip}">✓</span>'
                pcols = f'<td class="num">{pbot.get(lab, 0):.2f}</td><td class="num">—</td>' if pbot else ''
                t.append(f'<tr style="background:var(--finw)"><td>{dot}</td><td><b>{lab}</b></td>'
                         f'<td><span class="track"><span class="fill" style="width:{p*100:.0f}%;background:var(--fin)"></span></span></td>'
                         f'<td class="num"><b>{p:.2f}</b></td>{pcols}<td></td></tr>')
                continue
            # TACHADO (objetivo #2): en finalizado, un top-2/top-3 del bot que NO gano se tacha,
            # manteniendo su valor visible (no se oculta, no se pone X).
            miss = fin_view and was_topk and lab != win_mark_lab
            labcell = f'<span class="miss" data-tip="el bot lo tenia en su top-2/3 y NO gano">{lab}</span>' if miss else lab
            # COLORES (pedido Santiago 2026-07-12): EXACTO/pick=VERDE, top-2=AMARILLO, top-3=NARANJA
            r1 = _rank[0] if _rank else None
            # bucket YA PERDIDO: si estaba en el top FIJADO conserva su color (solo se tacha);
            # si no, gris. El pronostico fijado NO se re-sugiere sobre la marcha.
            if lostb:
                if lab == r1 or lab in top3:
                    kcls = 'g1' if lab == r1 else ('y2' if lab in top2 else 'o3')
                    dot = (f'<span class="dot {kcls}" data-tip="fijado en el top del bot y ya '
                           f'imposible — se tacha, no se recalcula"></span>')
                else:
                    dot = '<span class="dot" style="background:var(--mut)" data-tip="ya imposible">✕</span>'
                fillcls = 'fill lost'
            else:
                dot = ('<span class="dot g1"></span>' if lab == r1 else
                       ('<span class="dot y2"></span>' if lab in top2 else
                        ('<span class="dot o3"></span>' if in3 else '')))
                fillcls = ('fill g1' if lab == r1 else
                           ('fill y2' if lab in top2 else ('fill o3' if in3 else 'fill')))
            is_no = pb is not None and p >= NO_PRICE_MIN and pb <= NO_PBOT_MAX and state != "fin" and not lostb
            chip = f'<span class="chipno" data-tip="mercado paga {p:.2f}, bot ve {pb:.0%}">NO?</span>' if is_no else ''
            pcols = ''
            if pb is not None:
                if lostb:
                    pcols = f'<td class="num" style="color:var(--mut)">{pb:.2f}</td><td class="num" style="color:var(--mut)">—</td>'
                else:
                    edge = (pb - p) * 100
                    cls = ' class="num edgehi"' if abs(edge) >= 10 else ' class="num"'
                    pcols = f'<td class="num">{pb:.2f}</td><td{cls}>{edge:+.0f}</td>'
            rowcls = ' class="lostrow"' if lostb else ''
            t.append(f'<tr{rowcls}><td>{dot}</td><td>{labcell}</td>'
                     f'<td><span class="track"><span class="{fillcls}" style="width:{p*100:.0f}%"></span></span></td>'
                     f'<td class="num">{p:.2f}</td>{pcols}<td>{chip}</td></tr>')
        t.append('</table>')
        table_html = "".join(t)

    if state == "fin" and winner:
        # ------- FINALIZADO: resultado + pronostico + acierto -------
        # REGLA DE REDONDEO (Santiago, confirmada en vivo Milan 34.x->34 / Beijing 35.9->35, e
        # insistida explicitamente 2026-07-10): WU FLOOREA SIEMPRE. El pick del bot = floor(mu) en
        # AMBAS unidades. 35.9 -> 35, NUNCA 36. (Reemplaza el half-up °C que se habia probado.)
        def floor_lbl(m):
            fb = int(math.floor(m))
            if unit == "F":
                lo = fb if fb % 2 == 0 else fb - 1
                return f"{lo}-{lo+1}°F", lo
            return f"{fb}°C", fb
        bot_lbl, bot_n = floor_lbl(mu) if mu is not None else (None, None)
        hit = None
        if bot_lbl is not None and winner:
            wtxt = str(winner)
            wnums = [int(x) for x in re.findall(r"\d+", wtxt)]
            # dos formatos posibles de 'winner': texto vivo de Gamma ("33°C or higher"/"or below")
            # y el del backfill historico (blabel(): ">= 33°C"/"<= 10°C") — matchear los dos.
            if "higher" in wtxt or ">=" in wtxt:
                hit = int(bot_n >= wnums[0]) if wnums else None
            elif "below" in wtxt or "<=" in wtxt:
                hit = int(bot_n <= wnums[0]) if wnums else None
            elif wnums:
                hit = int(wnums[0] <= bot_n <= wnums[-1])
        if hit is None and hist:
            hit = hist.get("hit")
        pw = (pbot.get(winner) if pbot and winner else None) or (hist.get("pwin") if hist else None)
        maxreal = hist.get("max_real") if hist else (live_st or {}).get("max")
        hit3 = int(winner in top3) if (pbot and winner) else None
        hit2 = int(winner in top2) if (pbot and winner) else None
        # NIVEL de acierto (Santiago compra top-2/top-3): exacto > top-2 > top-3 > pérdida.
        # performance ✓ = el ganador cayó en alguno de los buckets del bot; ✗ = quedó afuera del top-3.
        nivel = ("EXACTO" if hit == 1 else ("TOP-2" if hit2 == 1 else ("TOP-3" if hit3 == 1 else None)))
        acerto = nivel is not None if (hit3 is not None) else None
        if stats is not None and (hit is not None or hit3 is not None):
            # err para MAE/RMSE/MAPE (objetivos PDF): max predicho (mu) vs max REAL (IEM),
            # en la unidad local de la estacion (F o C — el panel lo aclara).
            err = (mu - maxreal) if (mu is not None and isinstance(maxreal, (int, float))) else None
            stats.append(dict(st=code, cont=cont, hit1=hit, hit2=hit2, hit3=hit3, pw=pw,
                              live=bool(pbot), err=err,
                              mr=(float(maxreal) if isinstance(maxreal, (int, float)) else None),
                              unit=unit))
        # performance ✓/✗ = bucket EXACTO (floor) del bot vs ganador (pedido Santiago); el nivel
        # top-2/top-3 va en el verdict de abajo.
        perf = ("<span class='okc'>✓</span>" if hit == 1 else
                ("<span class='badc'>✗</span>" if hit == 0 else "—"))
        h.append('<div class="trio">'
                 f'<div class="stat finres"><div class="lbl">resultado (WU)</div>'
                 f'<div class="val">{winner or "—"}</div>'
                 f'<div class="sub">max real (IEM) {fmt(maxreal)}{deg} · WU florea (35.9→35)</div></div>'
                 f'<div class="stat bot"><div class="lbl">bot predijo (max)</div>'
                 f'<div class="val">{fmt(mu)}{deg if mu is not None else ""}</div>'
                 f'<div class="sub">{"→ bucket " + bot_lbl if bot_lbl else "sin prediccion"}'
                 f'{" · ±" + format(sg, ".1f") if sg is not None else ""}</div></div>'
                 f'<div class="stat"><div class="lbl">performance</div>'
                 f'<div class="val">{perf}</div>'
                 f'<div class="sub">{"p ganador " + format(pw, ".2f") if pw is not None else ""}</div></div></div>')
        if acerto is not None:
            v = (f'<span class="ok">ACIERTO {nivel}</span>' if acerto
                 else '<span class="bad">PÉRDIDA</span>')
            h.append(f'<div class="verdict">{v}<span style="color:var(--mut);font-size:10.5px">'
                     f'bucket del bot (floor) vs ganador · p al ganador: '
                     f'{fmt(pw, 2) if pw is not None else "—"}</span></div>')
        h.append(table_html)
    else:
        # ------- ACTIVO/PROXIMO: triada BOT / MODELOS / EN VIVO -------
        live_d = live_st   # obs de ESTA fecha (Asia en curso = today+1 en AR, ya cubierto)
        h.append('<div class="trio">'
                 f'<div class="stat bot" data-tip="prediccion CALIBRADA (EMOS) del MAX — la que decide">'
                 f'<div class="lbl">bot · max predicho</div><div class="val">{fmt(mu)}{deg if mu is not None else ""}</div>'
                 f'<div class="sub">{"±" + format(sg, ".1f") + " σ" if sg is not None else "sin prediccion"}</div></div>'
                 f'<div class="stat" data-tip="consenso CRUDO gefs/ecmwf/icon">'
                 f'<div class="lbl">modelos · max / min</div><div class="val">{fmt(consensus)}{deg if consensus is not None else ""}</div>'
                 f'<div class="sub">{"min " + format(tmin, ".1f") + deg if tmin is not None else ""}</div></div>'
                 f'<div class="stat live" data-tip="observado HASTA AHORA hoy (IEM, misma estacion fisica que WU). El pico suele ser ~15:00 local, si aun no llego el max sube.">'
                 f'<div class="lbl">en vivo · max / min</div>'
                 f'<div class="val">{fmt((live_d or {}).get("max"))}{deg if live_d else ""}</div>'
                 f'<div class="sub">{"min " + fmt((live_d or {}).get("min")) + deg if live_d else ("aun no empezo el dia alli" if state == "prox" else "sin dato IEM")}</div></div></div>')
        if mx_models:
            rng_lo, rng_hi = min(mx_models.values()), max(mx_models.values())
            # [2026-07-15] badge del MEJOR modelo en ESTA ciudad (model_city_rank.csv, n>=5) —
            # referencia informativa (pedido Santiago), no cambia el mix del bot.
            bm = _load_model_rank().get(code)
            bm_txt = (f' · 🏅 <b data-tip="mejor modelo en esta ciudad vs bucket ganador '
                      f'({bm[3]}, n={bm[2]}) — referencia, no cambia el mix">{bm[0]} {bm[1]:.0%}</b>'
                      if bm else '')
            h.append(f'<div class="models">gefs <b>{mx_models.get("gefs","-")}</b> · '
                     f'ecmwf <b>{mx_models.get("ecmwf","-")}</b> · icon <b>{mx_models.get("icon","-")}</b>'
                     f' · desacuerdo <b>{rng_hi-rng_lo:.1f}{deg}</b>{bm_txt}</div>')
        # ¿ESTOY A TIEMPO DE ENTRAR? — en TODAS las cards operables, incluida PRÓXIMO
        # (fix 2026-07-09: solo se mostraba en encurso/soon; a media tarde todos los de HOY ya
        # estaban post-pico y los de MAÑANA — donde SÍ estás a tiempo — no tenían badge)
        if mkt_decided:
            # de-facto decidido (payout >=99.5% + pico pasado) pero Gamma AUN no valido -> pendiente
            # de revision, NO "A TIEMPO" ni FINALIZADO (objetivo #1).
            h.append(f'<div class="wstat pend" data-noanim>⏳ PENDIENTE DE REVISIÓN GAMMA · '
                     f'resultado de-facto <b>{mkt_win}</b> a {mkt_top[3]:.1%} · sin validar aún</div>')
        elif state in ("encurso", "soon", "prox"):
            wc, wt = window_status(code, d, now_utc)
            h.append(f'<div class="wstat {wc}" data-noanim>{wt}</div>')
        if lost and live_max is not None and not mkt_decided:
            if frozen:
                h.append(f'<div class="lostwarn">⛔ EN VIVO ya en {live_max:.1f}{deg} → '
                         f'{len(lost)} bucket(s) por debajo ya no pueden ganar (tachados). '
                         f'Pronóstico 🔒 FIJADO: el pick/top-2/top-3 NO se recalculan.</div>')
            else:
                h.append(f'<div class="lostwarn">⛔ EN VIVO ya en {live_max:.1f}{deg} → '
                         f'{len(lost)} bucket(s) por debajo YA no pueden ganar (tachados). El bot '
                         f'sugiere solo sobre lo que aún es posible (se fija al bloqueo).</div>')
        if priced:
            h.append(table_html)
        elif not mx_models:
            h.append('<p class="empty">sin datos para esta fecha</p>')
        else:
            h.append('<p class="empty">mercado aun no abierto en Polymarket</p>')

    # ------- accordion (punto 6): ventanas sugeridas + deadline, en ART -------
    wins_txt = " · ".join(f"<b>{w}</b>" for w in wins) if wins else "—"
    h.append('<details class="acc"><summary>detalle del modelo y ventanas de entrada</summary>'
             '<div class="inner">'
             f'Ventanas de entrada sugeridas (llegada de corridas antes del bloqueo): {wins_txt}<br>'
             f'🔒 Pronostico bloqueado a las <b>{ddl}</b> (UTC-3) — despues de esa hora el bot ya no '
             f'recalibra (la info nueva es el termometro en vivo, que el mercado tambien ve).<br>'
             f'Pico de calor: ~{int(PEAK_HOUR[code]):02d}:{int((PEAK_HOUR[code]%1)*60):02d} hora local de {STATION_META[code][2]} '
             f'(medido de METAR; costeros de Asia pican a media mañana) '
             f'(= <b>{to_art(peak_utc(code, d)).strftime("%H:%M")} AR</b>) · hora alli ahora: '
             f'<b>{(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, d))).strftime("%H:%M")}</b> · '
             f'Estacion de resolucion: <b>{code}</b> (reglas del mercado) · '
             f'<a href="{wu_url(code, d)}" target="_blank">historial WU de este dia ↗</a> · '
             f'<a href="https://polymarket.com/event/{pm_slug(code, d)}" target="_blank">mercado ↗</a>'
             '</div></details>')
    h.append('</div>')
    return "".join(h)


def actions_bar():
    """Botones rapidos (objetivo #9). Cada uno hace POST /action?do=<id>; solo funcionan con --serve
    (http). El JS los deshabilita si la pagina se abrio como archivo local. 'kind' controla el color."""
    # [2026-07-13, pedido Santiago "tengo muchos botones"] botonera REDUCIDA a lo que se usa:
    # 'live' ya ES el combo (caché+modelos+calibración+sync+redibujo) — regen/cache/forecasts/
    # models/calib/stats/leaderboard/export quedaron adentro de live o de sync (run_daily encadena
    # leaderboard+stats+export). Los ids viejos siguen funcionando por URL para compatibilidad.
    btns = [
        ("live",         "🎯 Pronóstico en vivo",     "COMBO TODO-EN-UNO: limpia caché + re-baja pronósticos + corre modelos locales + lanza calibración y sincronización (leaderboard/stats/Excel incluidos) + redibuja"),
        ("orderbook",    "🔃 Recargar orderbook",     "re-baja precios/buckets de Polymarket AHORA (si las cards quedaron atrás del mercado)"),
        ("sync",         "♻ Sincronización completa", "lanza run_daily.ps1 en background: acumuladores + fuentes + leaderboard + stats + Excel/DB"),
        ("alerts_clear", "🗑 Limpiar alertas",        "borra TODAS las alertas por evento del servidor (se limpian en todos los dispositivos, celu incluido)"),
        ("pages",        "🏙 Regenerar páginas",      "regenera historial (desde 08/07), modelos por ciudad y vistas por ciudad (PWS incluido) — tarda ~1-2 min"),
    ]
    cells = "".join(
        f'<button class="qbtn" data-do="{did}" data-tip="{tip}">{lbl}</button>' for did, lbl, tip in btns)
    return (f'<div class="actions" id="actions-bar">{cells}'
            f'<a class="qbtn dl" id="qbtn-dl" href="wxbt_export.xlsx" download '
            f'data-tip="descargar el último Excel generado">⬇ Descargar .xlsx</a>'
            f'<span class="qmsg" id="q-msg"></span></div>')


def toolbar_html(today, all_dates=()):
    conts = sorted({v[0] for v in STATION_META.values()})
    paises = sorted({v[1] for v in STATION_META.values()})
    ciudades = sorted({v[2] for v in STATION_META.values()})
    sts = sorted(STATION_META)
    opt = lambda vals: "".join(f'<option value="{v}">{v}</option>' for v in vals)
    return (
        '<div class="filters">'
        f'<select id="f-cont"><option value="">Continente</option>{opt(conts)}</select>'
        f'<select id="f-pais"><option value="">País</option>{opt(paises)}</select>'
        f'<select id="f-ciudad"><option value="">Ciudad</option>{opt(ciudades)}</select>'
        f'<select id="f-st"><option value="">Aeropuerto</option>{opt(sts)}</select>'
        '<select id="f-fecha"><option value="">Fecha (todas)</option>' + "".join(
            f'<option value="{d.isoformat()}">{ddmmyyyy(d)}{" · HOY" if d == today else ""}</option>'
            for d in all_dates) + '</select>'
        '<select id="f-estado"><option value="">Estado</option>'
        '<option value="encurso">En curso</option><option value="prox">Próximo</option>'
        '<option value="soon">Pico cerca</option><option value="resol">Resolviendo</option>'
        '<option value="fin">Finalizado</option></select>'
        '<select id="f-conf"><option value="">Confianza</option>'
        '<option value="alta">Alta</option><option value="media">Media</option>'
        '<option value="baja">Baja</option></select>'
        '<span class="chip" id="f-reco" data-tip="top-1 del bot con Δ¢ ≥ +10 (edge BRUTO, sin fricciones — no es señal)">★ recomendados</span>'
        '<span class="chip" id="f-pmax" data-tip="p bot máxima ≥ 0.40">prob alta</span>'
        '<span class="reset" id="f-reset">limpiar</span>'
        '<span class="count" id="f-count"></span></div>')


VB_CSS = """
/* [2026-07-15] panel VALUE BETS + bot-vs-vivo */
.viz-root #value-panel .vbrow{display:flex;gap:12px;align-items:baseline;padding:5px 0;
  border-bottom:1px solid var(--grid);font-size:12px;flex-wrap:wrap;}
.viz-root #value-panel .vbe{font-family:var(--mono);font-weight:700;color:var(--fc);
  min-width:44px;text-align:right;font-size:14px;}
.viz-root #value-panel .vbst{min-width:150px;font-weight:700;}
.viz-root #value-panel .vbst a{color:var(--mkt);text-decoration:none;}
.viz-root #value-panel .vbtier{font-family:var(--mono);font-size:10px;min-width:52px;}
.viz-root #value-panel .vbtxt{color:var(--ink2);}
.viz-root table.vbt{border-collapse:collapse;width:100%;max-width:720px;font-size:12px;}
.viz-root table.vbt th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:left;
  padding:4px 8px;border-bottom:1px solid var(--bd);}
.viz-root table.vbt th.num,.viz-root table.vbt td.num{text-align:right;}
.viz-root table.vbt td{padding:4px 8px;border-bottom:1px solid var(--grid);font-family:var(--mono);}
.viz-root table.vbt tr.vbw td{background:rgba(255,176,32,.07);}
.viz-root .vbwarn{color:var(--warn);font-weight:700;}
"""

ALERT_CSS = """
.viz-root #alerts-box{margin:10px 0 2px;border:1px solid var(--bd);border-left:3px solid #d99b23;
  border-radius:10px;background:var(--s2);padding:8px 12px;}
.viz-root #alerts-box.empty{display:none;}
.viz-root .aclearall{margin-left:10px;cursor:pointer;font-size:10px;color:var(--mut);
  border:1px solid var(--bd);border-radius:3px;padding:1px 7px;white-space:nowrap;vertical-align:middle;}
.viz-root .aclearall:hover{color:var(--red);border-color:var(--red);}
.viz-root .ahead{font-weight:700;font-size:13px;}
.viz-root .ahint{font-weight:400;font-size:10.5px;color:var(--mut);margin-left:8px;}
.viz-root .abadge{display:inline-block;min-width:16px;text-align:center;background:#d03b3b;
  color:#fff;border-radius:9px;font-size:10.5px;margin-left:6px;padding:1px 6px;font-weight:700;}
.viz-root .abadge:empty{display:none;}
.viz-root .arow-al{display:flex;gap:8px;align-items:baseline;padding:4px 0;
  border-top:1px dashed var(--grid);font-size:12.5px;}
.viz-root #alerts-list .arow-al:first-child{border-top:0;margin-top:4px;}
.viz-root .arow-al .at{color:var(--mut);font-size:10.5px;white-space:nowrap;
  font-variant-numeric:tabular-nums;}
.viz-root .arow-al .atx{flex:1;}
.viz-root .arow-al.warn .atx{color:#d99b23;}
.viz-root .arow-al.ok .atx{color:var(--fin);}
.viz-root .arow-al .aclose{cursor:pointer;color:var(--mut);font-weight:700;padding:0 5px;}
.viz-root .arow-al .aclose:hover{color:#d03b3b;}
"""

FILTER_JS = """
(function(){
  var tip=document.getElementById('viz-tooltip');
  document.addEventListener('mousemove',function(e){
    var t=e.target.closest('[data-tip]');
    if(!t){tip.style.opacity=0;return;}
    tip.textContent=t.getAttribute('data-tip');
    tip.style.left=Math.min(e.clientX+12,window.innerWidth-360)+'px';
    tip.style.top=(e.clientY+12)+'px';tip.style.opacity=1;
  });
  // reloj UTC-3 con segundos (punto 8)
  function clock(){
    var now=new Date(Date.now()-3*3600*1000);
    var p=function(n){return String(n).padStart(2,'0')};
    document.getElementById('viz-clock').textContent=
      p(now.getUTCHours())+':'+p(now.getUTCMinutes())+':'+p(now.getUTCSeconds())+
      ' - '+p(now.getUTCDate())+'/'+p(now.getUTCMonth()+1)+'/'+now.getUTCFullYear();
  }
  clock(); setInterval(clock,1000);
  // filtros combinables (punto 2) + calendario (punto 3)
  var sels=['f-cont','f-pais','f-ciudad','f-st','f-estado','f-conf'].map(function(i){return document.getElementById(i)});
  var fecha=document.getElementById('f-fecha');
  var reco=document.getElementById('f-reco'), pmax=document.getElementById('f-pmax');
  var reset=document.getElementById('f-reset'), count=document.getElementById('f-count');
  var attrs=['cont','pais','ciudad','st','estado','conf'];
  function apply(){
    var vals=sels.map(function(s){return s.value});
    var fv=fecha.value, shown=0, total=0;
    document.querySelectorAll('.card').forEach(function(c){
      total++;
      var ok=true;
      attrs.forEach(function(a,i){ if(vals[i] && c.dataset[a]!==vals[i]) ok=false; });
      if(fv && c.dataset.fecha!==fv) ok=false;
      // dias pasados: ocultos por defecto; visibles si el calendario los pide o Estado=Finalizado
      if(!fv && c.dataset.old==='1' && sels[4].value!=='fin') ok=false;
      if(reco.classList.contains('on') && c.dataset.reco!=='1') ok=false;
      if(pmax.classList.contains('on') && parseFloat(c.dataset.pmax)<0.40) ok=false;
      c.classList.toggle('hidden',!ok);
      if(ok) shown++;
    });
    document.querySelectorAll('.cont-lbl').forEach(function(cl){
      var g=cl.nextElementSibling;
      var any=g && g.querySelector('.card:not(.hidden)');
      cl.classList.toggle('hidden',!any); if(g)g.classList.toggle('hidden',!any);
    });
    document.querySelectorAll('h3.dia').forEach(function(h){
      var any=false, n=h.nextElementSibling;
      while(n && !n.matches('h3.dia')){ if(n.querySelector && n.querySelector('.card:not(.hidden)')) any=true; n=n.nextElementSibling; }
      h.classList.toggle('hidden',!any);
    });
    count.textContent=shown+' de '+total+' mercados';
    save();
  }
  function save(){
    try{ localStorage.setItem('wxbt-filters', JSON.stringify({
      s: sels.map(function(x){return x.value}), f: fecha.value,
      r: reco.classList.contains('on'), p: pmax.classList.contains('on')})); }catch(e){}
  }
  function restore(){
    try{
      var st=JSON.parse(localStorage.getItem('wxbt-filters')||'null');
      if(!st) return;
      (st.s||[]).forEach(function(v,i){ if(sels[i]) sels[i].value=v; });
      fecha.value=st.f||'';
      reco.classList.toggle('on',!!st.r); pmax.classList.toggle('on',!!st.p);
    }catch(e){}
  }
  sels.forEach(function(s){s.addEventListener('change',apply)});
  fecha.addEventListener('change',apply);
  [reco,pmax].forEach(function(ch){ch.addEventListener('click',function(){ch.classList.toggle('on');apply();})});
  reset.addEventListener('click',function(){
    sels.forEach(function(s){s.value=''});fecha.value='';
    [reco,pmax].forEach(function(c){c.classList.remove('on')});apply();
  });
  restore(); apply();
  // alertas por evento (#14): las cerradas viven en localStorage y se re-ocultan tras cada
  // morph/reload; el panel entero se esconde cuando no queda ninguna visible.
  function hideAlerts(){
    var hid=JSON.parse(localStorage.getItem('wxbt-alerts-closed')||'[]');
    var vis=0;
    document.querySelectorAll('.arow-al').forEach(function(r){
      var h=hid.indexOf(r.dataset.aid)>=0;
      r.classList.toggle('hidden',h); if(!h)vis++;
    });
    var b=document.getElementById('alerts-count'); if(b)b.textContent=vis?String(vis):'';
    var box=document.getElementById('alerts-box');
    if(box)box.classList.toggle('empty',vis===0);
  }
  hideAlerts();
  document.addEventListener('click',function(e){
    var c=e.target.closest('.aclose'); if(!c)return;
    var hid=JSON.parse(localStorage.getItem('wxbt-alerts-closed')||'[]');
    if(hid.indexOf(c.dataset.aid)<0)hid.push(c.dataset.aid);
    while(hid.length>300)hid.shift();
    localStorage.setItem('wxbt-alerts-closed',JSON.stringify(hid));
    hideAlerts();
  });
  // limpiar TODAS las alertas de una (pedido 2026-07-12): marca todas las visibles como cerradas
  document.addEventListener('click',function(e){
    var b=e.target.closest('#alerts-clear'); if(!b)return;
    var hid=JSON.parse(localStorage.getItem('wxbt-alerts-closed')||'[]');
    document.querySelectorAll('.arow-al:not(.hidden)').forEach(function(r){
      if(hid.indexOf(r.dataset.aid)<0)hid.push(r.dataset.aid);
    });
    while(hid.length>300)hid.shift();
    localStorage.setItem('wxbt-alerts-closed',JSON.stringify(hid));
    hideAlerts();
  });
  window.__wxbtApply = function(){ apply(); hideAudit(); hideAlerts(); };
  // TIMELINE 24h por card (slider 30 min, hora UTC-3). Modal appendeado a <body>, FUERA de
  // .viz-root: el morph del --watch jamas lo toca, sobrevive refrescos.
  function tlOpen(st, fe){
    var m=document.getElementById('tl-modal');
    if(!m){
      m=document.createElement('div'); m.id='tl-modal';
      m.innerHTML='<div class="tl-box"><div class="tl-head"><span id="tl-title"></span>'
        +'<span class="tl-x" id="tl-x">✕</span></div><div id="tl-body"></div></div>';
      document.body.appendChild(m);
      m.addEventListener('click',function(e){ if(e.target===m) m.style.display='none'; });
      m.querySelector('#tl-x').addEventListener('click',function(){ m.style.display='none'; });
    }
    m.style.display='flex';
    document.getElementById('tl-title').textContent='⏱ '+st+' · '+fe;
    var body=document.getElementById('tl-body');
    body.textContent='cargando timeline de 24h…';
    fetch('/timeline?st='+encodeURIComponent(st)+'&date='+encodeURIComponent(fe))
      .then(function(r){return r.json()})
      .then(function(j){ if(!j.ok){ body.textContent='sin datos: '+(j.msg||''); return; } tlRender(body,j,st); })
      .catch(function(e){ body.textContent='error: '+e; });
  }
  function tlRender(body, j, st){
    var n=j.times.length;
    // [2026-07-13] mercado PASADO = el ancla (ultimo precio real) esta >2h antes de ahora: el
    // extremo del slider es el CIERRE, no AHORA, y la Δ se mide contra el cierre.
    var isPast = (Date.now()/1000 - j.times[n-1]) > 7200;
    var anchorTxt = isPast ? 'cierre' : 'AHORA';
    body.innerHTML='<div class="tl-ctl"><input type="range" id="tl-sl" min="0" max="'+(n-1)+'" value="'+(n-1)+'" step="1">'
      +'<span class="tl-time" id="tl-time"></span></div>'
      +'<div class="tl-bot" id="tl-bot"></div><table id="tl-tab"></table>'
      +'<div class="tl-note">arrastra el slider: cada paso = 30 min · Δ = cuanto se movio el precio de ese momento al '+anchorTxt+' · '
      +j.city+(isPast?' · mercado ya resuelto: ventana = 24h antes del cierre':'')+'</div>';
    var sl=document.getElementById('tl-sl');
    function f2(x){return (x<10?'0':'')+x;}
    function draw(){
      var i=+sl.value;
      var t=new Date((j.times[i]-3*3600)*1000);   // epoch UTC -> mostrado como UTC-3
      document.getElementById('tl-time').textContent =
        f2(t.getUTCDate())+'/'+f2(t.getUTCMonth()+1)+' '+f2(t.getUTCHours())+':'+f2(t.getUTCMinutes())
        +(i===n-1?' AR · '+anchorTxt
                 :' AR · '+(((n-1-i)*30)/60).toFixed(1)+'h antes del '+anchorTxt);
      var mu=j.mu[i], rk=(j.ranks&&j.ranks[i])||[];
      var t2=rk[1], t3=rk[2];
      // [2026-07-13] marca del BLOQUEO: desde j.frz el pronostico esta FIJADO (mu clavado por el
      // server) — visible para verificar que nada se mueve despues del freeze.
      var frozen = j.frz && j.times[i] >= j.frz;
      var ftag = '';
      if (j.frz) {
        var ft = new Date((j.frz - 3*3600) * 1000);
        var fs = f2(ft.getUTCDate())+'/'+f2(ft.getUTCMonth()+1)+' '+f2(ft.getUTCHours())+':'+f2(ft.getUTCMinutes());
        ftag = frozen ? ' · <b style="color:#ffb020">🔒 FIJADO desde '+fs+' AR</b>'
                      : ' · <span style="color:#587085">se fija '+fs+' AR (04:30 local)</span>';
      }
      document.getElementById('tl-bot').innerHTML = ((mu==null)
        ? 'bot: sin prediccion registrada en ese momento'
        : 'bot predecia <b>'+mu.toFixed(1)+j.unit+'</b> → pick <b>'+j.pick[i]+'</b>'
          +(t2?'  ·  <span class="tl-y">top-2 '+t2+'</span>':'')
          +(t3?'  ·  <span class="tl-o">top-3 '+t3+'</span>':'')) + ftag;
      var rows='<tr><th></th><th>bucket</th><th>precio en ese momento</th><th class="num">$</th><th class="num">Δ→'+anchorTxt+'</th></tr>';
      j.labels.forEach(function(lab){
        var p=j.prices[lab][i], pn=j.prices[lab][n-1];
        var w=(p==null)?0:Math.max(2,Math.round(p*100));
        var dl=(p!=null&&pn!=null)?(((pn-p)>=0?'+':'')+Math.round((pn-p)*100)+'c'):'—';
        // marca del bot EN ESE MOMENTO: top-1 pick verde, top-2 amarillo, top-3 naranja
        var dot='', cls='';
        if(lab===rk[0]){dot='<span class="tl-dot g"></span>';cls='tl-r1';}
        else if(lab===rk[1]){dot='<span class="tl-dot y"></span>';cls='tl-r2';}
        else if(lab===rk[2]){dot='<span class="tl-dot o"></span>';cls='tl-r3';}
        rows+='<tr class="'+cls+'"><td>'+dot+'</td><td>'+lab+'</td>'
          +'<td><span class="track"><span class="fill" style="width:'+w+'%"></span></span></td>'
          +'<td class="num">'+(p==null?'—':p.toFixed(2))+'</td><td class="num">'+dl+'</td></tr>';
      });
      document.getElementById('tl-tab').innerHTML=rows;
    }
    sl.addEventListener('input',draw); draw();
  }
  document.addEventListener('click',function(e){
    var b=e.target.closest('.tlb'); if(!b)return;
    if(location.protocol.indexOf('http')!==0){
      if(qmsg){qmsg.className='qmsg err';qmsg.textContent='el timeline necesita el modo http (puerto 8765)';}
      return;
    }
    tlOpen(b.dataset.tlst, b.dataset.tlfe);
  });
  // tachito de auditoria: TOGGLE limpiar <-> mostrar todo. Las revisiones NUEVAS (corrida nueva,
  // ts distinto) siempre aparecen porque no estan en la lista de ocultas.
  var AC=document.getElementById('audit-clear');
  function hideAudit(){
    var hid=JSON.parse(localStorage.getItem('wxbt-audit-hidden')||'[]');
    document.querySelectorAll('.arow').forEach(function(r){
      r.classList.toggle('hidden', hid.indexOf(r.dataset.k)>=0);
    });
    if(AC) AC.textContent = hid.length ? '↺ mostrar todo' : '🗑 limpiar';
  }
  hideAudit();
  if(AC) AC.addEventListener('click',function(){
    var hid=JSON.parse(localStorage.getItem('wxbt-audit-hidden')||'[]');
    if(hid.length){ localStorage.setItem('wxbt-audit-hidden','[]'); }
    else { document.querySelectorAll('.arow:not(.hidden)').forEach(function(r){hid.push(r.dataset.k)});
           localStorage.setItem('wxbt-audit-hidden',JSON.stringify(hid)); }
    hideAudit();
  });
  // BOTONES RAPIDOS (#9): POST /action?do=X. Solo con --serve (http); si es archivo local, se
  // deshabilitan con un aviso. El mensaje de estado queda visible hasta la proxima accion.
  var isHttp = location.protocol.indexOf('http')===0;
  var qbtns = document.querySelectorAll('.qbtn[data-do]');
  var qmsg = document.getElementById('q-msg');
  if(!isHttp){
    qbtns.forEach(function(b){ b.disabled=true; });
    if(qmsg) qmsg.textContent='(abrí el dashboard vía http://…:8765 para usar los botones)';
  } else {
    qbtns.forEach(function(b){
      b.addEventListener('click',function(){
        var did=b.dataset.do;
        qbtns.forEach(function(x){x.classList.add('busy')});
        if(qmsg){qmsg.className='qmsg';qmsg.textContent='⏳ '+b.textContent.trim()+'…';}
        fetch('/action?do='+encodeURIComponent(did),{method:'POST'})
          .then(function(r){return r.json()})
          .then(function(j){
            if(qmsg){qmsg.className='qmsg '+(j.ok?'ok':'err');qmsg.textContent=(j.ok?'✓ ':'✗ ')+(j.msg||did);}
            // acciones que cambian el HTML: refrescar la vista al toque
            if(j.ok && (did==='regen'||did==='cache'||did==='forecasts'||did==='orderbook'||did==='live')){
              setTimeout(function(){ if(window.__wxbtReload)window.__wxbtReload(); },400);
            }
          })
          .catch(function(e){ if(qmsg){qmsg.className='qmsg err';qmsg.textContent='✗ '+e;} })
          .then(function(){ qbtns.forEach(function(x){x.classList.remove('busy')}); });
      });
    });
  }
  // restaurar scroll tras un reload de --watch
  var sy=sessionStorage.getItem('wxbt-scroll');
  if(sy) window.scrollTo(0, parseInt(sy));
})();
"""


# [Refactor #6] Auto-refresco del modo --watch. Lee el intervalo de body[data-interval] (0 = modo
# estatico, sin refresco). Antes iba inline templado con %d; ahora vive en data/wxbt.js.
INTERVAL_JS = """
(function(){
  var iv = parseInt((document.body.getAttribute('data-interval')||'0'),10);
  if(!iv){ return; }
  var total=iv,left=total;
  var el=document.getElementById('viz-countdown');
  function morph(a,b){
    if(a.children.length!==b.children.length||a.tagName!==b.tagName){a.replaceWith(b.cloneNode(true));return;}
    if(a.children.length===0){ if(a.textContent!==b.textContent){a.textContent=b.textContent;
      if(!(a.closest&&a.closest('[data-noanim]'))){a.classList.remove('chg');void a.offsetWidth;a.classList.add('chg');}}
      if(a.getAttribute('style')!==b.getAttribute('style'))a.setAttribute('style',b.getAttribute('style')||'');
      return;}
    for(var i=0;i<a.children.length;i++)morph(a.children[i],b.children[i]);}
  function pull(){ if(location.protocol.indexOf('http')!==0){
      sessionStorage.setItem('wxbt-scroll', String(window.scrollY)); location.reload(); return; }
    fetch(location.href,{cache:'no-store'}).then(function(r){return r.text()}).then(function(t){
      var nd=new DOMParser().parseFromString(t,'text/html');
      var nr=nd.querySelector('.viz-root'),or=document.querySelector('.viz-root');
      if(nr&&or){morph(or,nr);if(window.__wxbtApply)window.__wxbtApply();}}).catch(function(){}); }
  window.__wxbtReload=pull;
  function tick(){left--;if(el)el.textContent='refresco en '+Math.max(left,0)+'s';
    if(left>0)return; left=total; pull(); }
  setInterval(tick,1000);
})();
"""


def write_assets():
    """[Refactor #6] Escribe data/wxbt.css y data/wxbt.js (arquitectura separada HTML/CSS/JS). Se
    regeneran en cada generate_once para reflejar cambios de codigo. El HTML los referencia por
    <link>/<script src>, servibles tanto por file:// (doble-click) como por http (--serve)."""
    d = os.path.dirname(os.path.abspath(OUT))
    with open(os.path.join(d, "wxbt.css"), "w", encoding="utf-8") as f:
        f.write(CSS + ALERT_CSS + VB_CSS)
    with open(os.path.join(d, "wxbt.js"), "w", encoding="utf-8") as f:
        f.write(FILTER_JS + "\n" + INTERVAL_JS)


def audit_panel(audit, today):
    """Registro de auditoria: cada revision del MAX predicho, ANTES -> DESPUES, con ciudad y
    fecha del mercado. El tachito limpia la VISTA (persistente en el navegador); el historial
    completo sigue en data/forecast_audit.json."""
    rows = []
    for key, rec in (audit or {}).items():
        code, ds = key.split("|")
        try:
            d = dt.date.fromisoformat(ds)
        except Exception:
            continue
        if d < today or code not in STATION_META:
            continue
        hist = rec.get("hist", [])
        deg = "°F" if STATIONS.get(code, (0, 0, 0, "C"))[3] == "F" else "°C"
        for i in range(len(hist) - 1, 0, -1):
            ts_new, mu_new = hist[i]
            ts_old, mu_old = hist[i - 1]
            arrow, acol = ("▲", "up") if mu_new > mu_old else ("▼", "down")
            frz = rec.get("frozen", False) and i == len(hist) - 1
            rows.append((ts_new, code, d, mu_old, mu_new, arrow, acol, deg, frz, ts_old))
    rows.sort(key=lambda r: r[0], reverse=True)
    items = []
    for ts, code, d, mo, mn, arr, acol, deg, frz, ts_old in rows[:40]:
        k = f"{code}|{d.isoformat()}|{ts}"
        frz_tag = ' <span class="frzt">🔒 fijado</span>' if frz else ''
        src = "snapshot de la mañana" if ts_old == "snapshot" else f"revisión de {ts_old}"
        items.append(
            f'<div class="arow" data-k="{k}"><span class="at">{ts} AR</span>'
            f'<span class="ast">{code}</span>'
            f'<span class="ad">{STATION_META[code][2]} · mercado del {ddmmyyyy(d)}</span>'
            f'<span class="aval" data-tip="venia de: {src}">{mo:.1f}{deg} '
            f'<span class="{acol}">{arr}</span> <b>{mn:.1f}{deg}</b></span>{frz_tag}</div>')
    body = ('<div class="alog">' + "".join(items) + '</div>') if items else         ('<p style="color:var(--mut);font-size:11px">Sin cambios registrados aún — cuando una '
         'corrida nueva mueva el máx predicho ≥0.1°, la revisión aparece acá.</p>')
    return (f'<div class="timing"><h4>📋 Registro de auditoría — cambios del MAX predicho '
            f'<span id="audit-clear" class="reset" data-tip="limpia la vista (el historial completo '
            f'queda en data/forecast_audit.json)">🗑 limpiar</span></h4>'
            f'<p style="color:var(--mut);font-size:10.5px;margin:0 0 6px">Cada corrida nueva '
            f'recalibra el bot; si el máx predicho cambia, queda asentado ANTES → DESPUÉS. '
            f'🔒 = ya bloqueado, no se mueve más.</p>{body}</div>')


def stats_panel(stats):
    """ESTADISTICAS del bot (cards) sobre mercados resueltos. TOP-2/TOP-3 = el ganador estaba
    entre las 2/3 opciones mas probables del bot. PÉRDIDA = ni siquiera en el top-3."""
    live = [x for x in (stats or []) if x.get("live")]
    n = len(live)
    def rate(sel):
        v = [x for x in live if x.get(sel) is not None]
        return (sum(1 for x in v if x[sel] == 1), len(v))
    if n:
        e1, n1 = rate("hit1"); h2, n2 = rate("hit2"); h3, n3 = rate("hit3")
        losses = n3 - h3   # mercados donde el ganador NO estuvo ni en el top-3
        pws = [x["pw"] for x in live if x.get("pw") is not None]
        pw = sum(pws) / len(pws) if pws else 0
        stns = len({x["st"] for x in live})
        def card(lbl, val, sub, cls=""):
            return (f'<div class="scard {cls}"><div class="lbl">{lbl}</div>'
                    f'<div class="big">{val}</div><div class="sub">{sub}</div></div>')
        cards = (
            card("mercados resueltos", str(n), f"{stns} estaciones") +
            card("acierto EXACTO", f"{e1/max(n1,1):.0%}", f"{e1}/{n1} (bucket floor)") +
            card("acierto TOP-2 🟡", f"{h2/max(n2,1):.0%}", f"{h2}/{n2}", "y") +
            card("acierto TOP-3 🟠", f"{h3/max(n3,1):.0%}", f"{h3}/{n3}", "o") +
            card("PÉRDIDAS", str(losses), f"de {n3} (fuera del top-3)", "bad") +
            card("prob al ganador", f"{pw:.2f}", "media asignada")
        )
        # MAE / RMSE / MAPE (metricas del PDF de objetivos): err = max predicho − max REAL (IEM),
        # cada estacion en su unidad local; si conviven °F y °C se aclara en la nota chica.
        errs = [x for x in live if x.get("err") is not None]
        if errs:
            ae = [abs(x["err"]) for x in errs]
            mae = sum(ae) / len(ae)
            rmse = (sum(e * e for e in ae) / len(ae)) ** 0.5
            mp = [abs(x["err"]) / abs(x["mr"]) * 100 for x in errs if x.get("mr")]
            mape = sum(mp) / len(mp) if mp else None
            units = {x.get("unit") for x in errs if x.get("unit")}
            usub = ("°F y °C mezclados" if len(units) > 1
                    else ("°" + next(iter(units)) if units else "°"))
            esub = f"{len(errs)} mkt · {usub}"
            cards += (
                card("MAE", f"{mae:.2f}°", esub) +
                card("RMSE", f"{rmse:.2f}°", esub) +
                card("MAPE", (f"{mape:.2f}%" if mape is not None else "—"),
                     (esub if mape is not None else "sin max real"))
            )
        conts = ""
        for cont in ["Asia", "Europa", "America"] + sorted(
                {v[0] for v in STATION_META.values()} - {"Asia", "Europa", "America"}):
            g = [x for x in live if x["cont"] == cont]
            if g:
                gg = lambda sel: (sum(1 for x in g if x.get(sel) == 1), sum(1 for x in g if x.get(sel) is not None))
                a2, b2 = gg("hit2"); a3, b3 = gg("hit3")
                conts += (f'<span class="cchip"><b>{cont}</b> · {len(g)} mkt · '
                          f'top2 {a2}/{b2} · top3 {a3}/{b3}</span>')
        body = f'<div class="sgrid">{cards}</div><div class="cchips">{conts}</div>'
    else:
        body = '<p class="empty">todavia sin mercados resueltos con precios en la ventana visible</p>'
    return (f'<div class="timing"><h4>📊 Estadísticas del bot</h4>{body}'
            f'<p style="font-size:10.5px;color:var(--mut);margin-top:8px">Histórico walk-forward '
            f'60 días (lab, lead-2): exacto 42.8% · MAE 1.04°. Las cifras vivas son el track record '
            f'real y crecen cada día.</p></div>')


def _load_model_rank():
    """{station: (model, rate, n, src)} mejor modelo por ciudad (data/model_city_rank.csv, lo
    genera models_page.py). Solo con n>=5. Cache 1h."""
    now = time.monotonic()
    ts, m = _CACHE.get("mrank", (0.0, None))
    if m is not None and now - ts < PARAMS_TTL:
        return m
    m = {}
    p = os.path.join(os.path.dirname(os.path.abspath(OUT)), "model_city_rank.csv")
    try:
        import csv as _csv
        for r in _csv.DictReader(open(p, encoding="utf-8")):
            if r["rank"] == "1" and int(r["n"]) >= 5:
                m[r["station"]] = (r["model"], float(r["rate"]), int(r["n"]), r["src"])
    except Exception:
        m = {}
    _CACHE["mrank"] = (now, m)
    return m


def value_bets_panel(mk, preds, live, audit, today, horizon, now_utc):
    """💰 VALUE BETS (pedido Santiago 2026-07-15): pbot del pick (congelado si existe) vs mid del
    mercado, sobre datos YA fetcheados por este mismo refresco (cero requests extra). Edge BRUTO
    sin fees/spread/shrink — screener, NO señal. Excluye buckets ya imposibles por la obs viva."""
    try:
        from playbook import STRONG, WEAK   # lazy: playbook importa dashboard (ciclo solo en import-time)
    except Exception:
        STRONG, WEAK = set(), set()
    rows = []
    for code in STATIONS:
        unit = STATIONS[code][3]
        for d in [today + dt.timedelta(days=k) for k in range(horizon + 1)]:
            info = (mk or {}).get(code, {}).get(d)
            if not info or not info.get("buckets"):
                continue
            state, _ = state_of(code, d, info, now_utc)
            if state not in ("encurso", "soon", "prox"):
                continue
            # pico pasado = tmax ya ocurrio, el mercado ya lo vio (nowcast): edge ilusorio.
            if now_utc > peak_utc(code, d) + dt.timedelta(hours=1):
                continue
            pr = (preds or {}).get((code, d))
            if not pr or pr[0] is None:
                continue
            mu, sg = pr
            sg = sg or (2.6 if unit == "F" else 1.5)
            priced = [(lab, lo, hi, p) for lab, lo, hi, p in info["buckets"] if p is not None]
            if not priced:
                continue
            live_max = ((live or {}).get((code, d)) or {}).get("max") if state in ("encurso", "soon") else None
            floor_live = int(math.floor(live_max)) if live_max is not None else None
            lost = {lab for lab, lo, hi, p in priced
                    if floor_live is not None and hi is not None and hi < floor_live}
            pbot = {lab: pbot_floor(mu, sg, lo, hi) for lab, lo, hi, p in priced}
            px = {lab: p for lab, lo, hi, p in priced}
            rank = [l for l, _ in sorted(pbot.items(), key=lambda kv: -kv[1]) if l not in lost]
            if not rank:
                continue
            t1 = rank[0]
            t2 = rank[1] if len(rank) > 1 else None
            edge1 = (pbot[t1] - px.get(t1, 1.0)) * 100
            pair = ((pbot[t1] + (pbot.get(t2, 0) if t2 else 0)) -
                    (px.get(t1, 1.0) + (px.get(t2, 1.0) if t2 else 1.0))) * 100
            longs = [(lab, px[lab], pbot[lab]) for lab, lo, hi, p in priced
                     if lab not in lost and 0.005 <= p <= 0.10
                     and pbot.get(lab, 0) >= max(0.15, 3 * p)]
            frozen = forecast_frozen(code, d, now_utc)
            tier = "FUERTE" if code in STRONG else ("DEBIL" if code in WEAK else "MEDIA")
            jug = None
            if edge1 >= RECO_EDGE_MIN and pbot[t1] >= 0.35:
                jug = (f'comprar top-1 <b>{t1}</b> (bot {pbot[t1]:.0%} vs {px.get(t1, 0):.2f})', edge1)
            elif t2 and pair >= 12:
                jug = (f'par top-2 <b>{t1}+{t2}</b> (bot {pbot[t1] + pbot.get(t2, 0):.0%} vs '
                       f'{px.get(t1, 0) + px.get(t2, 0):.2f})', pair)
            elif longs:
                lab, p, pb = longs[0]
                jug = (f'🎯 longshot <b>{lab}</b> @{p:.2f} (bot {pb:.0%}) — size chico', (pb - p) * 100)
            if jug:
                rows.append((jug[1], code, d, tier, frozen, jug[0]))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        body = ('<p class="empty">sin value bets ahora — ningún top-1 con Δ¢ ≥ +10, par ≥ +12 ni '
                'longshot vivo.</p>')
    else:
        tcol = {"FUERTE": "var(--fin)", "MEDIA": "#ffd23e", "DEBIL": "#d03b3b"}
        items = []
        for edge, code, d, tier, frozen, txt in rows[:12]:
            items.append(
                f'<div class="vbrow"><span class="vbe">{edge:+.0f}¢</span>'
                f'<span class="vbst"><a href="https://polymarket.com/event/{pm_slug(code, d)}" '
                f'target="_blank">{STATION_META[code][2]} · {ddmmyyyy(d)}</a></span>'
                f'<span class="vbtier" style="color:{tcol[tier]}">{tier}</span>'
                f'<span class="vbtxt">{txt}{" · 🔒" if frozen else " · ◷"}</span></div>')
        body = "".join(items)
    return (f'<div class="timing" id="value-panel"><h4>💰 Value bets — bot vs mercado</h4>'
            f'<p style="color:var(--mut);font-size:10.5px;margin:0 0 6px">Δ¢ BRUTO (pbot − mid), '
            f'sin fees/spread/shrink — screener, NO señal. Reglas playbook: solo FUERTES, maker, '
            f'entrar temprano; DÉBIL = no operar. Buckets ya imposibles por la obs viva quedan '
            f'afuera.</p>{body}</div>')


def botlive_panel(fc, preds, audit, today, horizon):
    """🤖 Predicciones del bot vs EN VIVO (pedido Santiago 2026-07-15): por mercado vigente, el
    pick CONGELADO (lo que se opera/mide) al lado de lo que los modelos dicen AHORA — para ver de
    un vistazo si el pronóstico vivo se movió después del freeze."""
    rows = []
    for code in STATIONS:
        unit = STATIONS[code][3]
        deg = "°F" if unit == "F" else "°C"
        for d in [today + dt.timedelta(days=k) for k in range(horizon + 1)]:
            key = f"{code}|{d.isoformat()}"
            froze = ((audit or {}).get(key) or {}).get("froze") or {}
            mu_f = froze.get("mu")
            fcd = (fc or {}).get(code, {}).get(d)
            cl = calibrated_live(code, d, fcd) if fcd else None
            mu_l = cl[0] if cl else None
            if mu_f is None and mu_l is None:
                continue
            if mu_f is None:               # aún no congeló: mostrar snapshot/vivo solamente
                mu_show, tag = mu_l, "◷"
                drift = None
            else:
                mu_show, tag = mu_f, "🔒"
                drift = (mu_l - mu_f) if mu_l is not None else None
            fb = int(math.floor(mu_show))
            pick = (f"{fb if fb % 2 == 0 else fb - 1}-{(fb if fb % 2 == 0 else fb - 1) + 1}°F"
                    if unit == "F" else f"{fb}°C")
            warn = drift is not None and abs(drift) >= 1.0
            rows.append((warn, abs(drift or 0), code, d, tag, mu_show, deg, pick, mu_l, drift))
    if not rows:
        return ""
    rows.sort(key=lambda r: (-r[0], -r[1]))
    items = []
    for warn, _, code, d, tag, mu_show, deg, pick, mu_l, drift in rows[:60]:
        dr = (f'<span class="{"vbwarn" if warn else ""}">{drift:+.1f}{deg}</span>'
              if drift is not None else "—")
        lv = f"{mu_l:.1f}{deg}" if mu_l is not None else "—"
        items.append(f'<tr{" class=vbw" if warn else ""}>'
                     f'<td>{STATION_META[code][2]} <span style="color:var(--mut)">{code}</span></td>'
                     f'<td>{ddmmyyyy(d)}</td><td>{tag} {mu_show:.1f}{deg} → <b>{pick}</b></td>'
                     f'<td class="num">{lv}</td><td class="num">{dr}</td></tr>')
    return (f'<details class="timing" id="botlive-panel"><summary style="cursor:pointer">'
            f'<b>🤖 Bot congelado vs pronóstico EN VIVO</b> <span style="color:var(--mut);font-size:10.5px">'
            f'(abrir — ⚠ = el vivo se movió ≥1° del pick fijado)</span></summary>'
            f'<p style="color:var(--mut);font-size:10.5px;margin:6px 0">🔒 = pick fijado a las 04:30 '
            f'locales (lo que se opera y se mide). "vivo" = recalibrado con la corrida más reciente. '
            f'Si divergen fuerte, el mercado probablemente ya lo sabe (nowcast) — sirve para decidir '
            f'salidas, no para re-pickear.</p>'
            f'<table class="vbt"><thead><tr><th>ciudad</th><th>fecha</th><th>pick</th>'
            f'<th class="num">vivo</th><th class="num">Δ</th></tr></thead>'
            f'<tbody>{"".join(items)}</tbody></table></details>')


def render(today, horizon, fc, mk, interval=None, preds=None, timing=None, live=None, hist=None, audit=None, alerts_ctx=None):
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    now_art = to_art(now_utc)
    runs = []
    nxt = False
    for initz, h0, h1 in [(0, 5, 7), (6, 11, 13), (12, 17, 19), (18, 23, 25)]:
        a0, a1 = (h0 - 3) % 24, (h1 - 3) % 24
        cls = "run"
        nm = {0: "corrida 00h", 6: "corrida 06h", 12: "corrida 12h", 18: "corrida 18h"}[initz]
        lbl = f"{nm} → {a0:02d}:00-{a1:02d}:00"
        if now_utc.hour < h1 and not nxt:
            nxt, cls = True, "next"
            lbl += " ←"
        runs.append(f'<span class="{cls}">{lbl}</span>')
    sub = f'--watch {interval}s · <span id="viz-countdown"></span>' if interval else \
        'estatico · <code>python scripts/dashboard.py --watch</code>'
    # VENTANA <=48h (objetivo #11): SOLO ayer/hoy/mañana/día+2. No renderizamos el histórico viejo
    # del backfill (bloat + Santiago pidió explícito no trabajar ventanas >48h). El calendario ofrece
    # las mismas 4 fechas.
    ayer = today - dt.timedelta(days=1)
    win_lo, win_hi = ayer, today + dt.timedelta(days=horizon)
    dates_for_picker = [today + dt.timedelta(days=k) for k in range(horizon, -2, -1)]  # +2,+1,hoy,ayer
    parts = ['<div class="viz-root"><div class="topbar"><div class="row1">'
             '<h1>WXBT://TERMINAL</h1>'
             '<span class="clock"><span id="viz-clock">--:--:--</span><small>UTC-3 Argentina</small></span>'
             '<a href="leaderboard.html" class="reset" style="margin-left:10px" data-tip="ranking de estaciones por track record vivo">🏆 Leaderboard</a>'
             '<a href="stats.html" class="reset" style="margin-left:8px" data-tip="estadísticas generales + rendimiento día por día (ganó/perdió por mercado)">📊 Estadísticas</a>'
             '<a href="history.html" class="reset" style="margin-left:8px" data-tip="pronósticos de días anteriores desde el 08/07: pick congelado vs lo que pagó Polymarket, con los modelos de cada día">🗓 Historial</a>'
             '<a href="models.html" class="reset" style="margin-left:8px" data-tip="qué modelo acierta en cada ciudad (capturas vivas pre-freeze + retro 90d)">🧪 Modelos</a>'
             '<a href="cities.html" class="reset" style="margin-left:8px" data-tip="dashboard individual por ciudad: mercado + modelos + PWS + historial">🏙 Ciudades</a>'
             f'<span class="subt">{sub}</span></div>'
             f'<div class="runs"><span data-tip="Los modelos corren 4 veces al dia (00/06/12/18 UTC). Cada una tarda ~6h en publicarse; abajo, en hora Argentina. El bot recalibra al llegar cada una — esas son las mejores ventanas de entrada.">🕓 Nuevas corridas del modelo llegan (hora AR):</span>{"".join(runs)}</div>'
             + actions_bar() + toolbar_html(today, dates_for_picker) + '</div>',
             '<p class="subt" style="margin:8px 0 0">Δ¢ = p bot − precio, edge BRUTO sin '
             'fees/spread/shrink — NO es señal. 🟡 top-2 del bot (amarillo). Días pasados: usá el calendario '
             'o Estado=Finalizado.</p>']
    # fechas: pasadas (backfill) ocultas por defecto + hoy..+horizon. Dentro de cada fecha,
    # continentes en el orden del reloj desde AR (ASIA -> EUROPA -> AMERICA -> OCEANIA), cada uno
    # en su fila. [2026-07-13] Oceania agregada con Wellington. Cualquier continente nuevo de
    # STATION_META se agrega al final para no romper (evita KeyError como el de 'Oceania').
    CONT_ORDER = ["Asia", "Europa", "America", "Oceania"]
    CONT_ORDER += sorted({v[0] for v in STATION_META.values()} - set(CONT_ORDER))
    stats_acc = []   # lo llenan las cards FINALIZADAS (panel de estadisticas)
    all_dates = sorted({d for st in STATIONS for d in set(fc.get(st, {})) | set(mk.get(st, {}))}
                       | {d for (st, d) in (hist or {}).keys()})
    # ventana <=48h ESTRICTA (objetivo #11): AYER / HOY / MAÑANA / DÍA+2 y nada más.
    win_dates = [d for d in all_dates if win_lo <= d <= win_hi]
    for d in win_dates:
        is_past = d < today
        by_cont = {c: [] for c in CONT_ORDER}
        for code, (lat, lon, off, unit) in STATIONS.items():
            fc_day = fc.get(code, {}).get(d)
            info = mk.get(code, {}).get(d)
            hh = (hist or {}).get((code, d))
            if not fc_day and not info and not hh:
                continue
            c = card_html(code, d, today, dt.datetime.now(dt.timezone.utc), unit, fc_day, info,
                          pred=(preds or {}).get((code, d)), live=live, hist=hh, stats=stats_acc,
                          alerts=alerts_ctx, audit=audit)
            if is_past:   # AYER: marca de "pasado" para estilo, pero VISIBLE (dentro de la ventana 48h)
                c = c.replace('<div class="card', '<div data-pasado="1" class="card', 1)
            by_cont[STATION_META[code][0]].append(c)
        if any(by_cont.values()):
            pref = ("HOY — " if d == today else ("AYER — " if d == ayer else
                    ("MAÑANA — " if d == today + dt.timedelta(days=1) else "")))
            lbl = pref + fecha_es(d)
            sec = [f'<h3 class="dia">{lbl}</h3>']
            for cont in CONT_ORDER:
                if by_cont[cont]:
                    sec.append(f'<div class="cont-lbl">{cont.upper()}</div>'
                               f'<div class="grid">' + "".join(by_cont[cont]) + '</div>')
            parts.append("".join(sec))
    # [2026-07-15] panel de VALUE BETS + panel bot-congelado-vs-vivo, arriba de las cards
    # (datos ya fetcheados por este refresco: cero requests extra).
    parts.insert(1, botlive_panel(fc, preds, audit, today, horizon))
    parts.insert(1, value_bets_panel(mk, preds, live, audit, today, horizon, now_utc))
    # panel de alertas: se arma DESPUES del loop (las cards llenan alerts_ctx["new"]) pero se
    # inserta arriba de todo (posicion 1, bajo la topbar) — hijo estable para el morph.
    if alerts_ctx is not None:
        parts.insert(1, alerts_panel(alerts_ctx))
    parts.append(stats_panel(stats_acc))
    parts.append(audit_panel(audit, today))
    # [Refactor #6 2026-07-10] El CSS y el JS ya NO van inline: viven en data/wxbt.css y data/wxbt.js
    # (arquitectura separada HTML/CSS/JS). Aca solo queda el contenedor del tooltip; el <link> y el
    # <script src> los agrega el wrapper en generate_once. El auto-refresco lee data-interval del body.
    parts.append('<div id="viz-tooltip"></div>')
    parts.append('</div>')
    return "".join(parts)


def generate_once(today_s, horizon, interval=None):
    today = dt.date.fromisoformat(today_s) if today_s else dt.date.today()
    now = time.monotonic()
    ts, fc = _CACHE["fc"]
    if fc is None or now - ts > FC_TTL:
        fc = fetch_forecast_minmax(today, horizon)
        _CACHE["fc"] = (now, fc)
    ts, live = _CACHE["obs"]
    if live is None or now - ts > OBS_TTL:
        live = fetch_obs_live(today)
        _CACHE["obs"] = (now, live)
    mk = fetch_market_full(today, horizon)
    # RECALCULO EN VIVO del calibrado + AUDITORIA de cambios del max predicho (pedido de Santiago)
    audit = load_audit()
    alerts_ctx = load_alerts()             # alertas por evento (#14): base + historial
    snap = load_preds(today)               # snapshot oficial (calculo de la manana)
    preds_live = dict(snap)                # base/fallback
    now_iso = to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m %H:%M")
    changed = False
    for code in STATIONS:
        for dd in [today + dt.timedelta(days=k) for k in range(horizon + 1)]:
            fcd = fc.get(code, {}).get(dd)
            if not fcd:
                continue
            key = f"{code}|{dd.isoformat()}"
            rec = audit.get(key)
            if rec is None:
                # sembrar el punto 0 con el SNAPSHOT oficial de la manana (valor real, no inventado)
                # para que la primera revision del dia (snapshot -> corrida nueva) quede registrada
                seed = snap.get((code, dd))
                rec = {"hist": ([["snapshot", round(seed[0], 2)]] if seed else []), "frozen": False}
            if forecast_frozen(code, dd, dt.datetime.now(dt.timezone.utc)):
                # congelado: el max predicho queda en el ultimo valor pre-deadline
                if rec["hist"]:
                    preds_live[(code, dd)] = (rec["hist"][-1][1], preds_live.get((code, dd), (None, 1.5))[1])
                rec["frozen"] = True
                audit[key] = rec
                continue
            cl = calibrated_live(code, dd, fcd)
            if cl is None:
                continue
            mu_new, sg_new = cl
            preds_live[(code, dd)] = (mu_new, sg_new)
            last = rec["hist"][-1][1] if rec["hist"] else None
            if last is None or abs(mu_new - last) >= AUDIT_MIN_DELTA:
                rec["hist"].append([now_iso, mu_new])
                rec["hist"] = rec["hist"][-12:]   # ultimas 12 revisiones por mercado
                audit[key] = rec
                changed = True
    _FROZE["dirty"] = False
    html = render(today, horizon, fc, mk, interval=interval, preds=preds_live,
                  timing=load_timing(), live=live, hist=load_history(), audit=audit,
                  alerts_ctx=alerts_ctx)
    if changed or _FROZE["dirty"]:   # el freeze inmutable (#2) se captura DENTRO de render/card_html
        save_audit(audit)
    if alerts_ctx["new"]:
        print(f"[alertas] +{len(alerts_ctx['new'])} evento(s) nuevo(s)")
    save_alerts(alerts_ctx, today)
    write_assets()   # [Refactor #6] CSS/JS separados en data/wxbt.css y data/wxbt.js
    out_path = os.path.abspath(OUT)
    body_iv = (interval + 3) if interval else 0   # 0 = estatico (el JS no auto-refresca)
    # [FIX 2026-07-13] CACHE-BUST: versionar css/js con su mtime -> el navegador SIEMPRE baja la
    # version fresca tras un cambio de codigo (antes servia wxbt.js viejo cacheado: 'tlOpen no
    # definida', timeline con JS viejo). Sin esto, un F5 normal no alcanzaba.
    d_assets = os.path.dirname(out_path)
    vcss = int(os.path.getmtime(os.path.join(d_assets, "wxbt.css")))
    vjs = int(os.path.getmtime(os.path.join(d_assets, "wxbt.js")))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>WXBT — pronostico vs mercado</title>"
                f"<link rel='stylesheet' href='wxbt.css?v={vcss}'></head>"
                f"<body data-interval='{body_iv}'>{html}"
                f"<script src='wxbt.js?v={vjs}'></script></body></html>")
    return out_path, sum(len(v) for v in mk.values())


def main(today_s, horizon):
    out_path, _ = generate_once(today_s, horizon)
    print(f"Dashboard escrito en {out_path}")
    print("Abrir con doble-click. Modo vivo: python scripts/dashboard.py --watch")


def _lan_ip():
    """IP LAN de esta maquina (objetivo #10). Truco del socket UDP: no envia nada, solo hace que
    el SO elija la interfaz de salida hacia la red -> su IP local (192.168.x / 10.x)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _make_action_runner(today_s, horizon, interval):
    """Devuelve run_action(do) -> (ok, msg) para los botones rapidos (#9). Corre en el hilo del
    servidor; un lock serializa los generate_once para no pisar el _CACHE con el loop principal."""
    import subprocess, threading
    lock = threading.Lock()
    SCR = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.abspath(os.path.join(SCR, ".."))
    today = today_s or dt.date.today().isoformat()

    def regen(clear=()):
        with lock:
            for k in clear:
                _CACHE[k] = (0.0, None)   # forzar re-fetch en el proximo generate_once
            generate_once(today_s, horizon, interval=interval)

    def run_py(script, args=(), timeout=180):
        r = subprocess.run([sys.executable, os.path.join(SCR, script), *args],
                           cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        tail = (r.stdout or r.stderr or "").strip().splitlines()
        return r.returncode == 0, (tail[-1][:120] if tail else f"rc={r.returncode}")

    def clear_orderbook():
        """Vacia el cache de mercados por-slug (precios/buckets resueltos). Los mercados VIVOS ya se
        re-bajan en cada generate_once; esto fuerza tambien los cacheados a re-consultarse a Gamma."""
        _CACHE["slug"].clear()

    def run_action(do):
        try:
            if do in ("regen", "dashboard"):
                regen(); return True, "dashboard actualizado"
            if do == "orderbook":
                clear_orderbook(); regen(); return True, "orderbook/precios recargados desde Polymarket"
            if do == "live":
                # COMBO "pronóstico en vivo": limpia TODO el cache, corre las fuentes calibradas en
                # el momento, lanza calibración y sincronización en background, y redibuja con datos
                # frescos. Deja al dashboard mostrando exactamente el pronóstico vivo.
                clear_orderbook()
                mres = []
                for scr in ("capture_nbm.py", "capture_mosmix.py", "accumulate_mosmix.py", "capture_cwa.py"):
                    try:
                        ok, _m = run_py(scr, ["--date", today], timeout=150)
                        mres.append(scr.split(".")[0].replace("capture_", "").replace("accumulate_", "") + ("✓" if ok else "·"))
                    except Exception:
                        mres.append(scr.split(".")[0] + "✗")
                subprocess.Popen([sys.executable, os.path.join(SCR, "calib_lab.py")], cwd=ROOT)
                subprocess.Popen(["powershell", "-NoProfile", "-File",
                                  os.path.join(SCR, "run_daily.ps1")], cwd=ROOT)
                regen(clear=("fc", "obs", "params", "s2"))
                return True, ("pronóstico en vivo: caché limpia + modelos (" + " ".join(mres) +
                              ") + calibración y sync lanzadas + redibujado")
            if do == "cache":
                regen(clear=("fc", "obs", "params", "s2")); return True, "caché limpiada + redibujado"
            if do == "forecasts":
                regen(clear=("fc", "obs")); return True, "pronósticos re-bajados y recalibrados"
            if do == "models":
                outs = []
                for scr in ("capture_nbm.py", "capture_mosmix.py", "accumulate_mosmix.py", "capture_cwa.py"):
                    try:
                        ok, m = run_py(scr, ["--date", today], timeout=150)
                        outs.append(scr.split(".")[0] + (" ok" if ok else " skip/err"))
                    except Exception as e:
                        outs.append(scr.split(".")[0] + f" fallo ({e})")
                return True, "modelos: " + ", ".join(outs)
            if do == "pages":
                # [2026-07-15] regenera las paginas nuevas: modelos por ciudad + historial +
                # vistas por ciudad (con PWS). Por URL: POST /action?do=pages
                outs = []
                for scr in ("models_page.py", "history_page.py", "city_pages.py"):
                    try:
                        ok, m = run_py(scr, ["--refresh"] if scr != "city_pages.py" else [],
                                       timeout=420)
                        outs.append(scr.split("_")[0] + ("✓" if ok else "·"))
                    except Exception:
                        outs.append(scr.split("_")[0] + "✗")
                return True, "páginas regeneradas: " + " ".join(outs)
            if do == "leaderboard":
                ok, m = run_py("leaderboard.py"); return ok, "leaderboard: " + m
            if do == "stats":
                ok, m = run_py("stats_page.py"); return ok, "estadísticas: " + m
            if do == "export":
                ok, m = run_py("export_data.py", ["--date", today]); return ok, "Excel/DB: " + m
            if do == "calib":
                subprocess.Popen([sys.executable, os.path.join(SCR, "calib_lab.py")], cwd=ROOT)
                return True, "calibración lanzada en background (calib_lab.py)"
            if do == "sync":
                subprocess.Popen(["powershell", "-NoProfile", "-File",
                                  os.path.join(SCR, "run_daily.ps1")], cwd=ROOT)
                return True, "sincronización completa lanzada en background"
            if do == "alerts_clear":
                # [2026-07-13] limpia las alertas EN EL SERVIDOR (alerts.json) -> desaparecen en
                # todos los dispositivos (el ✕/limpiar del panel solo oculta en ESTE navegador).
                # Se conserva 'base' (estado de referencia): las alertas NUEVAS siguen saliendo.
                try:
                    a = _json.load(open(ALERTS_JSON, encoding="utf-8"))
                except Exception:
                    a = {"items": [], "base": {}}
                a["items"] = []
                _json.dump(a, open(ALERTS_JSON, "w", encoding="utf-8"), ensure_ascii=False)
                regen()
                return True, "alertas borradas del servidor (todos los dispositivos)"
            return False, f"acción desconocida: {do}"
        except subprocess.TimeoutExpired:
            return False, f"{do}: tardó demasiado (timeout)"
        except Exception as e:
            return False, f"{do}: {e}"
    return run_action


LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(OUT)), ".dashboard_watch.lock")


def _pid_alive(pid):
    """¿El proceso `pid` está vivo? En Windows NO usamos os.kill (con sig 0 es poco fiable y puede
    llegar a TerminateProcess) -> tasklist, que es seguro. En POSIX, os.kill(pid, 0)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import subprocess
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            return str(pid) in out       # si no existe, tasklist imprime "INFO: No tasks..."
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_single_instance(reloading):
    """UN SOLO watcher a la vez. Si ya hay otro vivo, sale. Los watchers DUPLICADOS eran la causa
    del jitter de buckets: dos procesos con estado distinto escribian alternadamente el mismo
    forecast_audit.json -> el mu 'rebotaba' 30.5<->30.7 y la recomendacion saltaba. En un reload
    el padre entrega la posta (WXBT_WATCH_RELOAD=1) y el hijo pisa el lock sin chequear."""
    try:
        if not reloading and os.path.exists(LOCK_FILE):
            old = int((open(LOCK_FILE).read().strip() or "0"))
            if old != os.getpid() and _pid_alive(old):
                print(f"[watch] YA hay un watcher corriendo (PID {old}). No arranco otro para no "
                      f"duplicar — los duplicados hacian 'saltar' los buckets. Salgo.")
                sys.exit(0)
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except SystemExit:
        raise
    except Exception as e:
        print(f"[watch] lock: {e}", file=sys.stderr)


def _release_single_instance():
    try:
        if os.path.exists(LOCK_FILE) and int((open(LOCK_FILE).read().strip() or "0")) == os.getpid():
            os.remove(LOCK_FILE)
    except Exception:
        pass


def watch(today_s, horizon, interval, max_iters, serve=0):
    _acquire_single_instance(os.environ.get("WXBT_WATCH_RELOAD") == "1")
    if serve:
        import json as _json2, threading, http.server, urllib.parse
        dd = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        run_action = _make_action_runner(today_s, horizon, interval)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=dd, **k)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/timeline":
                    q = urllib.parse.parse_qs(u.query)
                    st = (q.get("st") or [""])[0]
                    ds = (q.get("date") or [""])[0]
                    try:
                        assert st in STATIONS
                        payload = build_timeline(st, dt.date.fromisoformat(ds))
                    except Exception as e:
                        payload = {"ok": False, "msg": str(e)}
                    body = _json2.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/action":
                    do = (urllib.parse.parse_qs(u.query).get("do") or [""])[0]
                    ok, msg = run_action(do)
                    body = _json2.dumps({"ok": ok, "msg": msg}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404); self.end_headers()

            def log_message(self, *a):
                pass   # servidor silencioso (no ensuciar la consola del watch)

        http.server.ThreadingHTTPServer.allow_reuse_address = True
        # bind 0.0.0.0 (objetivo #10): accesible desde otros equipos de la MISMA red LAN.
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", serve), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        lan = _lan_ip()
        print(f"Servidor local:  http://127.0.0.1:{serve}/live_dashboard.html   <- en ESTA maquina")
        print(f"Desde el celu/otra PC (misma WiFi): http://{lan}:{serve}/live_dashboard.html")
        print("(si no abre desde otro equipo: permitir Python en el Firewall de Windows para red privada).")
        print("con http el refresco actualiza SOLO los textos que cambian (sin parpadeo).")
    out_path, _ = generate_once(today_s, horizon, interval=interval)
    print(f"Dashboard en vivo: {out_path}")
    print(f"Abrilo UNA vez y dejalo — se recarga solo cada {interval}s. Ctrl+C para parar.")
    # AUTO-RELOAD DE CODIGO: si dashboard.py (o show_live.py) cambia en disco, el watcher se re-lanza.
    # Entrega la posta con WXBT_WATCH_RELOAD=1 para que el hijo tome el lock sin verse a si mismo como
    # duplicado. Asi el reload NUNCA deja dos watchers vivos de forma persistente.
    def _srcs_mtime():
        fs = [os.path.abspath(__file__),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "show_live.py")]
        return tuple(os.path.getmtime(f) if os.path.exists(f) else 0 for f in fs)
    my_mtime = _srcs_mtime()
    i = 1
    try:
        while max_iters == 0 or i < max_iters:
            time.sleep(interval)
            if _srcs_mtime() != my_mtime:
                print("[watch] codigo cambio -> reiniciando con la version nueva...")
                import subprocess
                _release_single_instance()   # el hijo escribira su propio PID
                subprocess.Popen([sys.executable] + sys.argv,
                                 env=dict(os.environ, WXBT_WATCH_RELOAD="1"))
                sys.exit(0)
            try:
                out_path, n = generate_once(today_s, horizon, interval=interval)
                print(f"[{to_art(dt.datetime.now(dt.timezone.utc)).strftime('%H:%M:%S')} AR] refresco #{i+1} OK ({n} mercados)")
            except Exception as e:
                print(f"[WARN] refresco #{i+1} fallo: {e} — reintento", file=sys.stderr)
            i += 1
    except KeyboardInterrupt:
        print("\nParado (Ctrl+C). El dashboard quedo con el ultimo dato.")
    finally:
        _release_single_instance()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Dashboard v5: plataforma read-only pronostico vs mercado.")
    ap.add_argument("--date", default=None)
    # horizon 2 (objetivo #11): ventana <=48h -> hoy, mañana, dia+2. Antes era 3 (llegaba a +3).
    ap.add_argument("--horizon", type=int, default=2)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--max-iters", type=int, default=0)
    ap.add_argument("--serve", type=int, nargs="?", const=8765, default=0,
                    help="servir por http local (default puerto 8765): refresco sin parpadeo")
    a = ap.parse_args()
    if a.watch:
        watch(a.date, a.horizon, a.interval, a.max_iters, serve=a.serve)
    else:
        main(a.date, a.horizon)
