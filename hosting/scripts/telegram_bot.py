#!/usr/bin/env python3
# scripts/telegram_bot.py — BOT DE TELEGRAM WXBT v2 (rediseño 2026-07-16, pedido Santiago:
# "mucho mas profesional, con menu y selectors, no andar consultando por comandos").
#
# UX: menus INLINE (botones) + navegacion editando el mismo mensaje:
#   /picks  -> selector de ciudades -> PANEL por ciudad: clima actual (soleado/lloviendo...),
#              max registrada del dia, temperatura actual, hora local, hora del pico, estado del
#              mercado, pronostico fijado si/no, top-3 bids del mercado, posicion de estabilidad
#              (# ranking + % exacto y top-2 en esa ciudad), picks fijados 24h/48h, y botones
#              para HISTORIAL y ESTADISTICA de la ciudad.
#   /top    -> ranking completo de ciudades (1 = mejor).
#   /status -> estado del bot y de las fuentes.
#   /estadisticas -> generales + por continente.
#
# SETUP: token de @BotFather en data/.telegram_token (gitignoreado) o env WXBT_TG_TOKEN.
#   correr:  python scripts/telegram_bot.py --poll     (o scripts/run_telegram.ps1)
#   OJO: UN solo poller a la vez (dos -> 409 Conflict y ninguno responde).
# El bot es de SOLO LECTURA: jamas opera ni toca el motor.
import argparse
import html
import json
import math
import os
import sys
import time
import unicodedata
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import requests                                                      # noqa: E402
import wxbt_insights as I                                            # noqa: E402
from show_live import STATIONS, CITY_STATION, PEAK_HOUR, local_offset, peak_utc  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
# El token NUNCA va en el codigo (se commitea y queda expuesto). data/.telegram_token esta
# gitignoreado; alternativa: env WXBT_TG_TOKEN.
TOKEN_FILE = os.path.join(DATA, ".telegram_token")
STATE_FILE = os.path.join(DATA, "telegram_chats.json")
POLL_TIMEOUT = 50
MK_TTL = 60                 # cache mercados (Gamma)
RANK_TTL = 600              # cache ranking de estabilidad
WX_TTL = 300                # cache clima actual por ciudad
START_TS = time.time()

_CACHE = {"mk": (0.0, None), "preds": (0.0, None), "rank": (0.0, None), "wx": {}}

# WMO weather codes (Open-Meteo current) -> (emoji, texto es)
WMO = {0: ("☀️", "Despejado"), 1: ("🌤", "Mayormente despejado"), 2: ("⛅", "Parcialmente nublado"),
       3: ("☁️", "Nublado"), 45: ("🌫", "Niebla"), 48: ("🌫", "Niebla escarchada"),
       51: ("🌦", "Llovizna leve"), 53: ("🌦", "Llovizna"), 55: ("🌧", "Llovizna intensa"),
       61: ("🌧", "Lluvia leve"), 63: ("🌧", "Lluvia"), 65: ("🌧", "Lluvia fuerte"),
       66: ("🌧", "Lluvia helada"), 67: ("🌧", "Lluvia helada fuerte"),
       71: ("🌨", "Nieve leve"), 73: ("🌨", "Nieve"), 75: ("❄️", "Nieve fuerte"),
       80: ("🌦", "Chaparrones leves"), 81: ("🌧", "Chaparrones"), 82: ("⛈", "Chaparrones fuertes"),
       95: ("⛈", "Tormenta"), 96: ("⛈", "Tormenta con granizo"), 99: ("⛈", "Tormenta fuerte")}
STATE_ES = {"encurso": "🟢 EN CURSO", "soon": "🟠 CERCA DEL PICO", "prox": "🔵 PRÓXIMO",
            "resol": "🟣 RESOLVIENDO", "pendrev": "🟡 PENDIENTE DE RESOLUCIÓN", "fin": "🏁 FINALIZADO"}


# ------------------------------- infra telegram -------------------------------

def get_token():
    tok = os.environ.get("WXBT_TG_TOKEN", "").strip()
    if tok:
        return tok
    if os.path.exists(TOKEN_FILE):
        return open(TOKEN_FILE, encoding="utf-8").read().strip()
    return None


def api(token, method, **params):
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=70)
    j = r.json()
    if not j.get("ok") and method != "editMessageText":   # edit repetido = "not modified", benigno
        print(f"[WARN] telegram {method}: {j}", file=sys.stderr)
    return j


def send(token, chat_id, text, kb=None):
    params = dict(chat_id=chat_id, text=text[:4000], parse_mode="HTML",
                  disable_web_page_preview=True)
    if kb:
        params["reply_markup"] = {"inline_keyboard": kb}
    return api(token, "sendMessage", **params)


def edit(token, chat_id, message_id, text, kb=None):
    params = dict(chat_id=chat_id, message_id=message_id, text=text[:4000], parse_mode="HTML",
                  disable_web_page_preview=True)
    if kb:
        params["reply_markup"] = {"inline_keyboard": kb}
    return api(token, "editMessageText", **params)


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"chats": {}, "offset": 0}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _log(msg):
    # consola Windows (cp1252): degradar a ascii — un emoji en el log NO puede matar el update
    # (crasheaba el _log ANTES del edit() y el boton parecia muerto).
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}".encode("ascii", "replace").decode(), flush=True)


def h(s):
    return html.escape(str(s), quote=False)


# ------------------------------- datos (con cache) -------------------------------

def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().replace("-", " ").strip()


def find_station(q):
    import dashboard as D
    qn = _norm(q)
    if not qn:
        return None
    if q.strip().upper() in STATIONS:
        return q.strip().upper()
    for city, st in CITY_STATION.items():
        if _norm(city) == qn:
            return st
    for st, meta in D.STATION_META.items():
        if _norm(meta[2]) == qn:
            return st
    for city, st in CITY_STATION.items():
        if qn in _norm(city):
            return st
    for st, meta in D.STATION_META.items():
        if qn in _norm(meta[2]):
            return st
    return None


def get_market(today, horizon=2):
    import dashboard as D
    now = time.monotonic()
    ts, mk = _CACHE["mk"]
    if mk is None or now - ts > MK_TTL:
        mk = D.fetch_market_full(today, horizon)
        _CACHE["mk"] = (now, mk)
    return mk


def fetch_city_market(code, dates):
    """{date: parsed_event} SOLO para esta ciudad (1 request por slug, concurrente) — reemplaza el
    fetch global de 30 ciudades: el callback del boton respondia lento porque bajaba TODO el
    mercado. Cache 60s por ciudad."""
    import dashboard as D
    now = time.monotonic()
    hit = _CACHE.setdefault("citymk", {}).get(code)
    if hit and now - hit[0] < MK_TTL:
        return hit[1]

    def one(d):
        try:
            r = requests.get(f"{D.GAMMA}/events", params={"slug": D.pm_slug(code, d)}, timeout=12)
            evs = r.json() if r.status_code == 200 else []
            return d, (D._parse_event(evs[0]) if evs else None)
        except Exception:
            return d, None
    out = {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as tp:
        for d, ev in tp.map(one, dates):
            if ev:
                out[d] = ev
    _CACHE["citymk"][code] = (now, out)
    return out


PICK_ICON = ["🎯", "🥈", "🥉"]   # top-1 exacto · top-2 · top-3


def top3_labels(code, mu, sg, info, stored_top=None):
    """[(emoji, label)] top-1/2/3. Prefiere el top-3 GUARDADO del freeze (froze['top']); si no,
    rankea los buckets del mercado pick-first; si no hay mercado, buckets sinteticos."""
    import dashboard as D
    if stored_top:
        return [(PICK_ICON[i], lab) for i, lab in enumerate(stored_top[:3])]
    unit = STATIONS[code][3]
    if info and info.get("buckets"):
        priced = [(lab, lo, hi) for lab, lo, hi, p in info["buckets"]]
        fb = int(math.floor(mu))
        pick = next((lab for lab, lo, hi in priced
                     if (lo is None or fb >= lo) and (hi is None or fb <= hi)), None)
        pb = {lab: D.pbot_floor(mu, sg or 1.5, lo, hi) for lab, lo, hi in priced}
        rest = [l for l, _ in sorted(pb.items(), key=lambda kv: -kv[1]) if l != pick]
        top = ([pick] if pick else []) + rest
        return [(PICK_ICON[i], lab) for i, lab in enumerate(top[:3])]
    fb = int(math.floor(mu))

    def lbl(k):
        if unit == "F":
            lo = k if k % 2 == 0 else k - 1
            return f"{lo}-{lo + 1}°F"
        return f"{k}°C"
    return [(PICK_ICON[0], lbl(fb)), (PICK_ICON[1], lbl(fb + 1)), (PICK_ICON[2], lbl(fb - 1))]


def get_preds(today):
    import dashboard as D
    now = time.monotonic()
    ts, p = _CACHE["preds"]
    if p is None or now - ts > MK_TTL:
        p = D.load_preds(today)
        _CACHE["preds"] = (now, p)
    return p


def get_rank():
    """[(pos, station, exact, top2, n, score)] ordenado 1=mejor. Cache 10 min."""
    now = time.monotonic()
    ts, r = _CACHE["rank"]
    if r is None or now - ts > RANK_TTL:
        rows = I.stability()
        r = [(i + 1, x["station"], x["exact"], x["top2"], x["n"], x["score"])
             for i, x in enumerate(rows)]
        _CACHE["rank"] = (now, r)
    return r


def current_weather(code):
    """(emoji, descripcion, temp_actual) — Open-Meteo current por lat/lon (para HKO ademas la
    temp EXACTA del Observatory). Cache 5 min por ciudad."""
    now = time.monotonic()
    hit = _CACHE["wx"].get(code)
    if hit and now - hit[0] < WX_TTL:
        return hit[1]
    lat, lon, off, unit = STATIONS[code]
    out = ("·", "s/d", None)
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params=dict(latitude=lat, longitude=lon,
                                     current="temperature_2m,weather_code",
                                     temperature_unit=("fahrenheit" if unit == "F" else "celsius")),
                         timeout=8)
        cur = r.json().get("current", {})
        ico, txt = WMO.get(int(cur.get("weather_code", -1)), ("🌡", "—"))
        out = (ico, txt, cur.get("temperature_2m"))
    except Exception:
        pass
    if code == "HKO":   # temperatura EXACTA de la estacion que resuelve
        try:
            import hko_source
            t = hko_source.live_now()
            if t is not None:
                out = (out[0], out[1], t)
        except Exception:
            pass
    _CACHE["wx"][code] = (now, out)
    return out


def live_max_today(code, today, live=None):
    """Max registrada HOY (dia local) en la estacion. Usa el fetch del dashboard (METAR/HKO)."""
    import dashboard as D
    d_local = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, today))).date()
    if code == "HKO":
        try:
            import hko_source
            mm = hko_source.live_maxmin()
            return (mm[0] if mm else None), d_local
        except Exception:
            return None, d_local
    try:
        unit = STATIONS[code][3]
        ext = D._fresh_metar_extremes(code, today, unit)
        if d_local in ext:
            return ext[d_local][0], d_local
        return (ext[max(ext)][0], max(ext)) if ext else (None, d_local)
    except Exception:
        return None, d_local


# ------------------------------- vistas -------------------------------

def kb_cities():
    import dashboard as D
    codes = sorted(STATIONS, key=lambda c: D.STATION_META[c][2])
    kb, row = [], []
    for c in codes:
        row.append({"text": D.STATION_META[c][2], "callback_data": f"c|{c}"})
        if len(row) == 3:
            kb.append(row); row = []
    if row:
        kb.append(row)
    return kb


def kb_city(code, mkt_date=None):
    """Teclado del panel: botones DIRECTOS a Polymarket y a la fuente de resolucion (WU o HKO)
    del dia del mercado vigente (pedido Santiago 2026-07-16), + historial/estadistica/refresh."""
    import dashboard as D
    d = mkt_date or (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, dt.date.today()))).date()
    wu_txt = "🇭🇰 HKO" if code == "HKO" else "🌡 WU"
    return [
        [{"text": "📈 Polymarket", "url": f"https://polymarket.com/event/{D.pm_slug(code, d)}"},
         {"text": wu_txt, "url": D.wu_url(code, d)}],
        [{"text": "🗓 Historial", "callback_data": f"h|{code}"},
         {"text": "📊 Estadística", "callback_data": f"e|{code}"}],
        [{"text": "🔄 Actualizar", "callback_data": f"c|{code}"},
         {"text": "« Ciudades", "callback_data": "menu"}],
    ]


def kb_back(code):
    return [[{"text": f"« {code}", "callback_data": f"c|{code}"},
             {"text": "« Ciudades", "callback_data": "menu"}]]


def view_menu():
    return "<b>🏙 Elegí una ciudad</b>\n(picks, mercado, clima en vivo, historial y estadística)", kb_cities()


def _pick_lbl(code, mu):
    unit = STATIONS[code][3]
    fb = int(math.floor(mu))
    if unit == "F":
        lo = fb if fb % 2 == 0 else fb - 1
        return f"{lo}-{lo + 1}°F"
    return f"{fb}°C"


def view_city(code, today=None):
    """PANEL de ciudad (pedido Santiago 2026-07-16, campos textuales)."""
    import dashboard as D
    today = today or dt.date.today()
    unit = STATIONS[code][3]
    deg = "°F" if unit == "F" else "°C"
    cont, pais, ciudad = D.STATION_META[code][:3]
    now_utc = dt.datetime.now(dt.timezone.utc)
    audit = I._load_audit()
    preds = get_preds(today)

    # dia LOCAL del mercado vigente (Asia puede ir un dia adelante de AR)
    d_local = (now_utc + dt.timedelta(hours=local_offset(code, today))).date()
    # LATENCIA: bajar SOLO el mercado de esta ciudad (2-3 slugs) en vez de las 30 (fetch_market_full)
    cand = sorted({today + dt.timedelta(days=k) for k in range(0, 3)}
                  | {d_local + dt.timedelta(days=k) for k in range(0, 2)})
    cm = fetch_city_market(code, cand)
    info = cm.get(d_local) or cm.get(today)
    d_mkt = d_local if cm.get(d_local) else today
    state, _ = D.state_of(code, d_mkt, info, now_utc) if info else ("prox", "")

    ico, wtxt, tnow = current_weather(code)
    tmax, _ = live_max_today(code, today)
    hora_local = (now_utc.replace(tzinfo=None) + dt.timedelta(hours=local_offset(code, d_mkt))).strftime("%H:%M")
    peak_h = PEAK_HOUR[code]
    peak_s = f"{int(peak_h):02d}:{int((peak_h % 1) * 60):02d}"
    frozen = D.forecast_frozen(code, d_mkt, now_utc)
    frz_local = (D.freeze_utc(code, d_mkt) + dt.timedelta(hours=local_offset(code, d_mkt))).strftime("%H:%M")

    L = [f"<b>🏙 {h(ciudad).upper()} ({code})</b> · {h(pais)}",
         f"{ico} <b>{wtxt}</b>"]
    # temperatura: 1 sola card si actual == max registrada (pedido explicito)
    if tnow is not None and tmax is not None and abs(float(tnow) - float(tmax)) < 0.05:
        L.append(f"🌡 La temperatura actual (<b>{tnow:.1f}{deg}</b>) es la máxima registrada hasta el momento.")
    else:
        if tmax is not None:
            L.append(f"🔺 Máxima registrada del día: <b>{tmax:.1f}{deg}</b>")
        if tnow is not None:
            L.append(f"🌡 Temperatura actual: <b>{float(tnow):.1f}{deg}</b>")
    L.append(f"🕐 Hora local: <b>{hora_local}</b> · Pico: ~<b>{peak_s}</b>")
    L.append(f"Estado: <b>{STATE_ES.get(state, state)}</b>")
    L.append("Pronóstico fijado: " + (f"✅ sí (desde {frz_local} local)" if frozen
                                      else f"⏳ no — se fija {frz_local} local"))

    # mercado: top-3 bids
    if info and info.get("buckets"):
        priced = sorted([(lab, p) for lab, lo, hi, p in info["buckets"] if p is not None],
                        key=lambda x: -x[1])[:3]
        if priced:
            L.append(f"\n<b>📈 Mercado {d_mkt.strftime('%d/%m')} (top-3):</b>")
            for lab, p in priced:
                L.append(f"   {h(lab)} — <b>{p:.2f}</b>")
        if info.get("winner"):
            L.append(f"🏁 ganó <b>{h(info['winner'])}</b>")
    else:
        L.append("\n📈 Mercado: sin evento vivo ahora.")

    # estabilidad: posicion + % exacto y top-2 (solo exacto y top2, pedido explicito)
    rank = get_rank()
    mine = next((r for r in rank if r[1] == code), None)
    if mine and mine[4]:
        pos, _, ex, t2, n, _ = mine
        L.append(f"\n🏆 Estabilidad: <b>#{pos}</b> de {len(rank)} · "
                 f"exacto <b>{ex / n:.0%}</b> · top-2 <b>{t2 / n:.0%}</b> (n={n})")
    else:
        L.append(f"\n🏆 Estabilidad: sin mercados resueltos aún (ciudad nueva)")

    # picks fijados para los proximos 2 dias (24h y 48h) — con TOP-1/2/3 (pedido Santiago):
    # 🎯 exacto (top-1) · 🥈 top-2 · 🥉 top-3.
    L.append("\n<b>🔒 Picks — 🎯 exacto · 🥈 top-2 · 🥉 top-3:</b>")
    for k in range(0, 3):
        d = d_mkt + dt.timedelta(days=k)
        if d > today + dt.timedelta(days=2):
            break
        rec = audit.get(f"{code}|{d.isoformat()}") or {}
        froze = rec.get("froze") or {}
        f48 = rec.get("froze48") or {}
        pr = preds.get((code, d))
        info_d = cm.get(d)
        if froze.get("mu") is not None:
            top = top3_labels(code, froze["mu"], froze.get("sg"), info_d, froze.get("top"))
            tag = f"🔒 fijado · μ {froze['mu']:.1f}{deg}"
        elif f48.get("mu") is not None:
            top = top3_labels(code, f48["mu"], f48.get("sg"), info_d, f48.get("top"))
            tag = f"⏳ fijado 48h (el de 24h se fija {frz_local} local) · μ {f48['mu']:.1f}{deg}"
        elif pr:
            top = top3_labels(code, pr[0], pr[1], info_d)
            tag = f"◷ preliminar · μ {pr[0]:.1f}{deg}"
        else:
            continue
        picks_txt = "  ".join(f"{emo}<b>{h(lab)}</b>" for emo, lab in top)
        L.append(f"   <u>{d.strftime('%d/%m')}</u> {tag}\n      {picks_txt}")
    bm = best_model_line(code)
    if bm:
        L.append("\n" + bm)
    return "\n".join(L), kb_city(code, d_mkt)


def best_model_line(code):
    path = os.path.join(DATA, "model_city_rank.csv")
    if not os.path.exists(path):
        return None
    import csv as _csv
    for r in _csv.DictReader(open(path, encoding="utf-8")):
        if r["station"] == code and r["rank"] == "1" and int(r["n"]) >= 5:
            return (f"🏅 Mejor modelo acá: <b>{r['model']}</b> {float(r['rate']):.0%} "
                    f"exacto (n={r['n']})")
    return None


def view_historial(code):
    import dashboard as D
    ciudad = D.STATION_META[code][2]
    hist = sorted([r for r in I.bot_history() if r["station"] == code],
                  key=lambda r: r["target"], reverse=True)
    if not hist:
        return f"Sin historial congelado para {h(ciudad)} aún.", kb_back(code)
    icon = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌", None: "⏳"}
    L = [f"<b>🗓 Historial {h(ciudad)} ({code})</b>", "<pre>fecha  pick     ganó     resultado"]
    for r in hist[:14]:
        L.append(f"{r['target'].strftime('%d/%m')}  {(r['pick_lbl'] or '—'):<8.8} "
                 f"{(r['win_lbl'] or '—'):<8.8} {icon[r['nivel']]} {r['nivel'] or 'pendiente'}")
    L.append("</pre>")
    sc = [r for r in hist if r["nivel"]]
    if sc:
        ex = sum(r["nivel"] == "EXACTO" for r in sc)
        t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in sc)
        L.append(f"Total: <b>{ex}/{len(sc)}</b> exactos · <b>{t2}/{len(sc)}</b> top-2")
    return "\n".join(L), kb_back(code)


def view_estadistica(code):
    import dashboard as D
    ciudad = D.STATION_META[code][2]
    sc = [r for r in I.bot_history() if r["station"] == code and r["nivel"]]
    if not sc:
        return f"Sin mercados resueltos para {h(ciudad)} aún.", kb_back(code)
    n = len(sc)
    ex = sum(r["nivel"] == "EXACTO" for r in sc)
    t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in sc)
    t3 = sum(r["nivel"] in ("EXACTO", "TOP-2", "TOP-3") for r in sc)
    perd = sum(r["nivel"] == "PERDIDA" for r in sc)
    aes = [abs(r["mu"] - r["max_real"]) for r in sc if r.get("max_real") is not None]
    mae = sum(aes) / len(aes) if aes else None
    rank = get_rank()
    mine = next((r for r in rank if r[1] == code), None)
    L = [f"<b>📊 Estadística {h(ciudad)} ({code})</b> — desde el 08/07",
         f"Mercados resueltos: <b>{n}</b>",
         f"✅ Exactos: <b>{ex}</b> ({ex / n:.0%})",
         f"🟡 Top-2: <b>{t2}</b> ({t2 / n:.0%})",
         f"🟠 Top-3: <b>{t3}</b> ({t3 / n:.0%})",
         f"❌ Pérdidas: <b>{perd}</b>"]
    if mae is not None:
        L.append(f"📏 MAE del pick vs obs: <b>{mae:.2f}°</b>")
    if mine:
        L.append(f"🏆 Posición de estabilidad: <b>#{mine[0]}</b> de {len(rank)}")
    bm = best_model_line(code)
    if bm:
        L.append(bm)
    return "\n".join(L), kb_back(code)


def view_top():
    import dashboard as D
    rank = get_rank()
    L = ["<b>🏆 Ranking de ciudades</b> (1 = mejor · Wilson del top-2, desde 08/07)",
         "<pre># ciudad          ex   top2   n"]
    for pos, st, ex, t2, n, score in rank:
        ciudad = D.STATION_META.get(st, ("?", "?", st))[2][:14]
        L.append(f"{pos:<2} {ciudad:<15.15} {ex:>2}   {t2:>2}   {n:>2}")
    con = [st for st in STATIONS if not any(r[1] == st for r in rank)]
    L.append("</pre>")
    if con:
        L.append("Sin resueltos aún: " + ", ".join(
            D.STATION_META.get(s, ("?", "?", s))[2] for s in con))
    L.append("Detalle: /picks → ciudad → 📊")
    return "\n".join(L), [[{"text": "🏙 Ver ciudades", "callback_data": "menu"}]]


def view_estadisticas_gen():
    import dashboard as D
    hist = [r for r in I.bot_history() if r["nivel"]]
    n = len(hist)
    if not n:
        return "Sin mercados resueltos aún.", None
    ex = sum(r["nivel"] == "EXACTO" for r in hist)
    t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in hist)
    t3 = sum(r["nivel"] in ("EXACTO", "TOP-2", "TOP-3") for r in hist)
    aes = [abs(r["mu"] - r["max_real"]) for r in hist if r.get("max_real") is not None]
    L = [f"<b>📊 Estadísticas generales</b> (desde 08/07, pick congelado vs Gamma)",
         f"Mercados: <b>{n}</b> · ✅ exacto <b>{ex / n:.0%}</b> ({ex}) · "
         f"🟡 top-2 <b>{t2 / n:.0%}</b> ({t2}) · 🟠 top-3 <b>{t3 / n:.0%}</b>"]
    if aes:
        L.append(f"📏 MAE: <b>{sum(aes) / len(aes):.2f}°</b>")
    # por continente
    L.append("\n<b>Por continente:</b>")
    by = {}
    for r in hist:
        cont = D.STATION_META.get(r["station"], ("?",))[0]
        a = by.setdefault(cont, [0, 0, 0])
        a[0] += 1
        a[1] += r["nivel"] == "EXACTO"
        a[2] += r["nivel"] in ("EXACTO", "TOP-2")
    for cont in sorted(by):
        n_, ex_, t2_ = by[cont]
        L.append(f"   {cont}: exacto {ex_ / n_:.0%} · top-2 {t2_ / n_:.0%} (n={n_})")
    L.append("\nTab 48hs (pick fijado un día antes): en la página 📊 Estadísticas — acumula desde el 16/07.")
    return "\n".join(L), [[{"text": "🏆 Ranking", "callback_data": "top"},
                           {"text": "🏙 Ciudades", "callback_data": "menu"}]]


def view_status():
    st = load_state()
    up = time.time() - START_TS
    hh, mm = int(up // 3600), int(up % 3600 // 60)
    def _mtime(f):
        p = os.path.join(DATA, f)
        return dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d/%m %H:%M") if os.path.exists(p) else "—"
    L = ["<b>🤖 Estado del bot WXBT</b>",
         f"Uptime: <b>{hh}h {mm}m</b> · Chats: <b>{len(st.get('chats', {}))}</b> · "
         f"Ciudades: <b>{len(STATIONS)}</b>",
         "\n<b>Fuentes (última actualización local):</b>",
         f"   predicciones: {_mtime('predictions_forward.csv')}",
         f"   modelos vivos: {_mtime('models_forward.csv')}",
         f"   audit (freezes): {_mtime('forecast_audit.json')}",
         f"   bias V2: {_mtime('station_bias.json')}",
         f"   PWS refs: {_mtime('pws_reference.csv')}",
         "\nComandos: /picks /top /estadisticas /status /pws /modelos /help"]
    return "\n".join(L), [[{"text": "🏙 Ciudades", "callback_data": "menu"}]]


def fmt_pws(code):
    import dashboard as D
    path = os.path.join(DATA, "pws_reference.csv")
    ciudad = D.STATION_META[code][2]
    if not os.path.exists(path):
        return "Sin referencia PWS todavía."
    import csv as _csv
    rows = [r for r in _csv.DictReader(open(path, encoding="utf-8")) if r["station"] == code]
    if not rows:
        return f"Sin PWS de referencia para {h(ciudad)}."
    unit = STATIONS[code][3]
    L = [f"<b>📍 PWS de referencia — {h(ciudad)} ({code})</b>",
         "<pre>pws            km   n    bias   σdif"]
    for r in rows:
        L.append(f"{r['pws_id']:<14.14} {float(r['dist_km']):4.1f} {r['n']:>3}  "
                 f"{float(r['bias']):+5.2f}  {float(r['std']):5.2f}")
    L.append("</pre>")
    L.append(f"<i>bias = PWS − estación ({'°F' if unit == 'F' else '°C'}). "
             f"Estimador vivo: mediana(PWS − bias).</i>")
    return "\n".join(L)


def fmt_models(code):
    import dashboard as D
    perf = I.model_perf(days=90)
    ciudad = D.STATION_META[code][2]
    rows = [r for r in perf if r["station"] == code]
    if not rows:
        return f"Sin datos de modelos para {h(ciudad)} todavía."
    L = [f"<b>🧪 Modelos en {h(ciudad)} ({code})</b>"]
    for src, tag in (("vivo", "VIVO — capturas reales pre-freeze"),
                     ("retro", "RETRO — Previous-Runs 90d (referencia)")):
        sub = sorted([r for r in rows if r["src"] == src],
                     key=lambda r: (-(r["rate"] if r["rate"] == r["rate"] else -1),
                                    r["mae"] if r["mae"] == r["mae"] else 99))
        if not sub:
            continue
        L.append(f"<i>{tag}</i>")
        body = ["<pre>modelo       exactos  %     MAE"]
        for r in sub:
            mae = f"{r['mae']:.2f}" if r["mae"] == r["mae"] else "  - "
            body.append(f"{r['model']:<12.12} {r['hits']:>2}/{r['n']:<3}  {r['rate']:>4.0%}  {mae}")
        body.append("</pre>")
        L.append("\n".join(body))
    return "\n".join(L)


HELP = """<b>WXBT bot — menú</b>
/picks — selector de ciudades (mercado, clima vivo, picks fijados, historial, estadística)
/top — ranking de ciudades (1 = mejor)
/estadisticas — generales + por continente
/status — estado del bot y las fuentes
/pws &lt;ciudad&gt; — PWS de referencia · /modelos &lt;ciudad&gt; — modelos que aciertan ahí
Todo se navega con BOTONES: tocá /picks y elegí la ciudad."""


# ------------------------------- routing -------------------------------

def handle(text, today=None):
    """Comandos de TEXTO -> (texto, keyboard|None). Compat con --dry-run."""
    t = (text or "").strip()
    if not t.startswith("/"):
        return None, None
    parts = t.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/start", "/help", "/ayuda"):
        return HELP, [[{"text": "🏙 Ver ciudades", "callback_data": "menu"}]]
    if cmd in ("/picks", "/pick", "/ciudades", "/cities"):
        if arg:
            code = find_station(arg)
            if code:
                return view_city(code)
        return view_menu()
    if cmd in ("/top", "/leaderboard", "/ranking"):
        return view_top()
    if cmd in ("/estadisticas", "/stats", "/estadistica"):
        return view_estadisticas_gen()
    if cmd == "/status":
        return view_status()
    if cmd in ("/pws", "/modelos", "/models", "/historial", "/history"):
        code = find_station(arg)
        if not code:
            return (f"No encontré la ciudad «{h(arg)}». Probá /picks y elegila con botones.", None)
        if cmd == "/pws":
            return fmt_pws(code), kb_back(code)
        if cmd in ("/modelos", "/models"):
            return fmt_models(code), kb_back(code)
        return view_historial(code)
    return "Comando desconocido. /help para el menú.", None


def handle_callback(data):
    """callback_data -> (texto, keyboard). Rutas: menu | top | c|CODE | h|CODE | e|CODE."""
    if data == "menu":
        return view_menu()
    if data == "top":
        return view_top()
    kind, _, code = data.partition("|")
    if code in STATIONS:
        if kind == "c":
            return view_city(code)
        if kind == "h":
            return view_historial(code)
        if kind == "e":
            return view_estadistica(code)
    return "Menú desactualizado — mandá /picks de nuevo.", None


# ------------------------------- loop / push -------------------------------

COMMANDS = [
    ("picks", "elegir ciudad (mercado, clima, picks, historial)"),
    ("top", "ranking de ciudades (1 = mejor)"),
    ("estadisticas", "estadisticas generales y por continente"),
    ("status", "estado del bot y las fuentes"),
    ("help", "ayuda"),
]


def setup_commands(token):
    try:
        api(token, "setMyCommands",
            commands=[{"command": c, "description": d} for c, d in COMMANDS])
    except Exception as e:
        _log(f"[WARN] setMyCommands: {e}")


def poll(token):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    st = load_state()
    setup_commands(token)
    me = api(token, "getMe").get("result", {})
    _log(f"WXBT bot @{me.get('username', '?')} ONLINE (long-poll). "
         f"Chats: {len(st['chats'])}. Ctrl+C para parar.")
    for cid in st["chats"]:
        try:
            send(token, int(cid), "🟢 <b>WXBT bot online</b> — tocá /picks para elegir ciudad.",
                 kb=[[{"text": "🏙 Ver ciudades", "callback_data": "menu"}]])
        except Exception:
            pass
    last_beat = time.time()
    while True:
        try:
            j = api(token, "getUpdates", offset=st.get("offset", 0) + 1, timeout=POLL_TIMEOUT,
                    allowed_updates=["message", "callback_query"])
        except requests.RequestException as e:
            _log(f"[WARN] getUpdates: {e} — reintento en 5s")
            time.sleep(5)
            continue
        if not j.get("ok"):
            if j.get("error_code") == 409:
                _log("[ERROR] 409 Conflict: hay OTRO poller corriendo (o un webhook). "
                     "Cerrá el otro proceso. Reintento en 10s.")
            else:
                _log(f"[WARN] getUpdates not-ok: {j}")
            time.sleep(10)
            continue
        for u in j.get("result", []):
            st["offset"] = max(st.get("offset", 0), u["update_id"])
            try:
                # ---- CALLBACKS (botones): navegar editando el mismo mensaje ----
                if "callback_query" in u:
                    cq = u["callback_query"]
                    chat = cq["message"]["chat"]
                    api(token, "answerCallbackQuery", callback_query_id=cq["id"])
                    text, kb = handle_callback(cq.get("data") or "")
                    _log(f"← callback {cq.get('data')!r} de {chat.get('username') or chat['id']}")
                    if text:
                        edit(token, chat["id"], cq["message"]["message_id"], text, kb)
                    continue
                # ---- MENSAJES DE TEXTO ----
                msg = u.get("message") or u.get("edited_message")
                if not msg or not msg.get("text"):
                    continue
                chat = msg["chat"]
                cid = str(chat["id"])
                who = chat.get("username") or chat.get("first_name") or "?"
                if cid not in st["chats"]:
                    st["chats"][cid] = {"name": who, "since": dt.date.today().isoformat()}
                    _log(f"[+] chat nuevo: {who} ({cid})")
                text, kb = handle(msg["text"])
                _log(f"← {who}: {msg['text']!r} → {len(text) if text else 0} chars")
                if text:
                    send(token, chat["id"], text, kb)
            except Exception as e:
                import traceback
                traceback.print_exc()
                _log(f"[ERROR] update {u.get('update_id')}: {e}")
            save_state(st)
        save_state(st)
        if time.time() - last_beat > 600:
            _log(f"… escuchando ({len(st['chats'])} chat/s)")
            last_beat = time.time()


def push(token, today=None):
    """Resumen diario a los chats registrados (encadenado en run_daily)."""
    st = load_state()
    if not st["chats"]:
        print("push: sin chats registrados.")
        return
    today = today or dt.date.today()
    text, _ = view_top()
    text = f"<b>☀️ Resumen WXBT {today.strftime('%d/%m')}</b>\n\n" + text
    for cid in st["chats"]:
        try:
            send(token, int(cid), text, kb=[[{"text": "🏙 Ver ciudades", "callback_data": "menu"}]])
        except Exception as e:
            print(f"[WARN] push a {cid}: {e}", file=sys.stderr)
    print(f"push: resumen enviado a {len(st['chats'])} chat(s).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Bot de Telegram WXBT v2 (solo lectura, con menus).")
    ap.add_argument("--poll", action="store_true", help="loop long-poll (dejar corriendo)")
    ap.add_argument("--push", action="store_true", help="resumen diario y salir")
    ap.add_argument("--dry-run", default=None, metavar="CMD",
                    help='probar un comando sin enviar, ej: --dry-run "/picks milan"')
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else None
    if a.dry_run:
        out, kb = handle(a.dry_run, today=day)
        print((out or "(sin respuesta)").encode("ascii", "replace").decode())
        if kb:
            print("[keyboard]", json.dumps(kb, ensure_ascii=True)[:400])
        sys.exit(0)
    tok = get_token()
    if not tok:
        print("Falta el token: guardarlo en data/.telegram_token o WXBT_TG_TOKEN.")
        sys.exit(0 if a.push else 1)
    if a.push:
        push(tok, today=day)
    elif a.poll:
        try:
            poll(tok)
        except KeyboardInterrupt:
            print("\nbot detenido (Ctrl+C).")
    else:
        print("Bot WXBT v2. Dejalo escuchando:  python scripts/telegram_bot.py --poll\n"
              "  (o scripts/run_telegram.ps1; UN solo poller a la vez).\n"
              "Otros: --push (resumen diario) · --dry-run '/picks milan'.")
