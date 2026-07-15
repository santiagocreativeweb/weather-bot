#!/usr/bin/env python3
# scripts/telegram_bot.py — BOT DE TELEGRAM WXBT (pedido Santiago 2026-07-15): pick por ciudad,
# rango de Polymarket con las probabilidades que PAGA el mercado, nuestra prediccion, link al
# mercado, leaderboard de ciudades estables y VALUE BETS.
#
# SETUP (una sola vez):
#   1. En Telegram hablar con @BotFather -> /newbot -> copiar el token.
#   2. Guardarlo en data/.telegram_token (una linea) o en la env var WXBT_TG_TOKEN.
#      (data/.telegram_token esta gitignoreado via secrets? NO: agregado a .gitignore).
#   3. Correr:  python scripts/telegram_bot.py --poll     (loop; dejar corriendo o Task Scheduler)
#      Extra:   python scripts/telegram_bot.py --push     (resumen diario a los chats registrados;
#               encadenado en run_daily.ps1 — no falla si no hay token)
#      Test:    python scripts/telegram_bot.py --dry-run "/pick milan"   (sin token, imprime)
#
# COMANDOS: /picks /pick <ciudad> /value /top /modelos <ciudad> /vivo <ciudad>
#           /historial <ciudad> /pws <ciudad> /help
#
# HONESTIDAD: el "edge" mostrado es BRUTO (pbot − mid, sin fees/spread/shrink) — screener, no
# señal. Los picks mostrados son el CONGELADO del audit cuando existe (lo que se opera), si no el
# snapshot forward. El bot es de SOLO LECTURA: jamas opera ni toca el motor.
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
from show_live import STATIONS, CITY_STATION                         # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
# El token NUNCA va en el codigo (se commitea y queda expuesto en el remoto). Vive en
# data/.telegram_token (gitignoreado) o en la env WXBT_TG_TOKEN. Ya quedo guardado ahi.
TOKEN_FILE = os.path.join(DATA, ".telegram_token")
STATE_FILE = os.path.join(DATA, "telegram_chats.json")
POLL_TIMEOUT = 50          # long-poll de getUpdates
MK_TTL = 60                # cache de mercados (seg) para no golpear Gamma por cada mensaje

_CACHE = {"mk": (0.0, None), "preds": (0.0, None)}


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
    if not j.get("ok"):
        print(f"[WARN] telegram {method}: {j}", file=sys.stderr)
    return j


def send(token, chat_id, text):
    # Telegram corta a 4096 chars -> partir por bloques de linea
    while text:
        chunk = text[:4000]
        if len(text) > 4000:
            cut = chunk.rfind("\n")
            if cut > 1000:
                chunk = chunk[:cut]
        api(token, "sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML",
            disable_web_page_preview=True)
        text = text[len(chunk):]


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"chats": {}, "offset": 0}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ------------------------------- resolucion de ciudad -------------------------------

def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().replace("-", " ").strip()


def find_station(q):
    """'milan' / 'sao paulo' / 'KLGA' / 'nyc' -> codigo ICAO. None si no matchea."""
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
    for city, st in CITY_STATION.items():        # prefijo/contiene, ultimo recurso
        if qn in _norm(city):
            return st
    for st, meta in D.STATION_META.items():
        if qn in _norm(meta[2]):
            return st
    return None


# ------------------------------- datos compartidos (con cache) -------------------------------

def get_market(today, horizon=1):
    import dashboard as D
    now = time.monotonic()
    ts, mk = _CACHE["mk"]
    if mk is None or now - ts > MK_TTL:
        mk = D.fetch_market_full(today, horizon)
        _CACHE["mk"] = (now, mk)
    return mk


def get_preds(today):
    import dashboard as D
    now = time.monotonic()
    ts, p = _CACHE["preds"]
    if p is None or now - ts > MK_TTL:
        p = D.load_preds(today)
        _CACHE["preds"] = (now, p)
    return p


def frozen_or_snapshot(code, d, preds):
    """(mu, sigma, frozen?) — el pick congelado manda; snapshot forward de fallback."""
    audit = I._load_audit()
    froze = (audit.get(f"{code}|{d.isoformat()}") or {}).get("froze") or {}
    if froze.get("mu") is not None:
        sg = froze.get("sg") or (preds.get((code, d)) or (None, 2.0))[1] or 2.0
        return froze["mu"], sg, True
    if preds.get((code, d)):
        mu, sg = preds[(code, d)]
        return mu, sg, False
    return None, None, False


# ------------------------------- formateo de respuestas -------------------------------

def h(s):
    return html.escape(str(s), quote=False)


def fmt_pick(code, d, today):
    """Card de un mercado: nuestra prediccion + rango Polymarket con precios/pbot/edge + link."""
    import dashboard as D
    mk = get_market(today, horizon=2)
    preds = get_preds(today)
    info = mk.get(code, {}).get(d)
    ciudad = D.STATION_META[code][2]
    unit = STATIONS[code][3]
    deg = "°F" if unit == "F" else "°C"
    mu, sg, frozen = frozen_or_snapshot(code, d, preds)
    lines = [f"<b>{h(ciudad)} ({code}) — {d.strftime('%d/%m/%Y')}</b>"]
    if mu is not None:
        tag = "🔒 congelado" if frozen else "◷ snapshot (aun recalibra)"
        lines.append(f"Bot: <b>{mu:.1f}{deg}</b> (σ {sg:.1f}) · {tag}")
        bm = best_model_line(code)
        if bm:
            lines.append(bm)
    else:
        lines.append("Bot: sin prediccion para esa fecha todavia.")
    if not info or not info.get("buckets"):
        lines.append("Mercado: sin evento vivo en Polymarket para esa fecha.")
        return "\n".join(lines)
    priced = [(lab, lo, hi, p) for lab, lo, hi, p in info["buckets"] if p is not None]

    def center(lo, hi):
        w = 2 if unit == "F" else 1
        lo = lo if lo is not None else (hi - w if hi is not None else 0)
        hi = hi if hi is not None else lo + w
        return (lo + hi) / 2
    priced.sort(key=lambda x: center(x[1], x[2]))
    pbot = {lab: (D.pbot_floor(mu, sg, lo, hi) if mu is not None else None)
            for lab, lo, hi, p in priced}
    rows = ["<pre>rango        mercado   bot    Δ¢"]
    fb = int(math.floor(mu)) if mu is not None else None
    for lab, lo, hi, p in priced:
        pb = pbot.get(lab)
        star = "→" if (fb is not None and (lo is None or fb >= lo) and (hi is None or fb <= hi)) else " "
        pbs = f"{pb:5.0%}" if pb is not None else "    -"
        edge = f"{(pb - p) * 100:+4.0f}" if pb is not None else "   -"
        rows.append(f"{star}{lab:<12.12} {p:6.2f}  {pbs}  {edge}")
    rows.append("</pre>")
    lines += rows
    if info.get("winner"):
        lines.append(f"🏁 RESUELTO — gano <b>{h(info['winner'])}</b>")
    lines.append(f'<a href="{I.pm_url(code, d)}">abrir en Polymarket ↗</a>')
    lines.append("<i>Δ¢ = pbot − precio, edge BRUTO (sin fees/spread) — screener, no señal.</i>")
    return "\n".join(lines)


def best_model_line(code):
    """Linea 'mejor modelo en esta ciudad' desde data/model_city_rank.csv (si existe y n>=5)."""
    path = os.path.join(DATA, "model_city_rank.csv")
    if not os.path.exists(path):
        return None
    import csv as _csv
    best = None
    for r in _csv.DictReader(open(path, encoding="utf-8")):
        if r["station"] == code and r["rank"] == "1":
            best = r
            break
    if not best or int(best["n"]) < 5:
        return None
    mae = f", MAE {float(best['mae']):.2f}" if best.get("mae") else ""
    return (f"🏅 mejor modelo aca: <b>{best['model']}</b> "
            f"{float(best['rate']):.0%} exacto (n={best['n']}{mae}) [{best['src']}]")


def fmt_picks(today):
    import dashboard as D
    mk = get_market(today, horizon=1)
    preds = get_preds(today)
    lines = [f"<b>Picks del bot — {today.strftime('%d/%m/%Y')}</b>", "<pre>ciudad          pick     μ      edge"]
    n = 0
    for code in sorted(STATIONS, key=lambda c: D.STATION_META[c][2]):
        info = mk.get(code, {}).get(today)
        if not info or not info.get("buckets"):
            continue
        unit = STATIONS[code][3]
        mu, sg, frozen = frozen_or_snapshot(code, today, preds)
        if mu is None:
            continue
        priced = [(lab, lo, hi, p) for lab, lo, hi, p in info["buckets"] if p is not None]
        if not priced:
            continue
        fb = int(math.floor(mu))
        pick = next((lab for lab, lo, hi, p in priced
                     if (lo is None or fb >= lo) and (hi is None or fb <= hi)), "—")
        px = next((p for lab, lo, hi, p in priced if lab == pick), None)
        pb = D.pbot_floor(mu, sg, *next(((lo, hi) for lab, lo, hi, p in priced if lab == pick),
                                        (None, None))) if pick != "—" else None
        edge = f"{(pb - px) * 100:+4.0f}¢" if (pb is not None and px is not None) else "   -"
        lock = "🔒" if frozen else "◷"
        ciudad = D.STATION_META[code][2][:14]
        lines.append(f"{ciudad:<15.15} {pick:<8.8} {mu:5.1f}{'F' if unit == 'F' else 'C'} {edge} {lock}")
        n += 1
    lines.append("</pre>")
    lines.append("Detalle: /pick &lt;ciudad&gt; · value bets: /value")
    if not n:
        return "Sin mercados vivos con prediccion para hoy."
    return "\n".join(lines)


def fmt_value(today):
    vb = I.value_bets(today=today, horizon=1, mk=get_market(today, horizon=1),
                      preds=get_preds(today))
    hits = [r for r in vb if r["value"]]
    lines = [f"<b>💰 Value bets — {today.strftime('%d/%m/%Y')}</b>",
             "<i>edge BRUTO (pbot − mid), sin fees/spread/shrink. Screener, NO señal. "
             "Regla playbook: solo FUERTES, maker, temprano.</i>"]
    if not hits:
        lines.append("Sin value bets ahora (edge top-1 &lt; 10¢, par &lt; 12¢, sin longshots).")
        # mostrar el mejor edge igual, como contexto
        for r in vb[:3]:
            lines.append(f"· {h(r['city'])} {r['date'].strftime('%d/%m')}: top-1 {h(r['t1'])} "
                         f"edge {r['edge1'] * 100:+.0f}¢ ({r['tier']})")
        return "\n".join(lines)
    for r in hits[:12]:
        star = {"FUERTE": "🟢", "MEDIA": "🟡", "DEBIL": "🔴"}[r["tier"]]
        l1 = (f"{star} <b>{h(r['city'])}</b> {r['date'].strftime('%d/%m')} "
              f"[{r['tier']}{' · 🔒' if r['frozen'] else ''}]")
        l2 = (f"   top-1 <b>{h(r['t1'])}</b>: bot {r['pbot1']:.0%} vs mercado "
              f"{(r['px1'] or 0):.2f} → edge <b>{r['edge1'] * 100:+.0f}¢</b>")
        parts = [l1, l2]
        if r["t2"] and r["pair_edge"] >= 0.12:
            parts.append(f"   par top-2 {h(r['t1'])}+{h(r['t2'])}: edge {r['pair_edge'] * 100:+.0f}¢")
        for lab, px, pb in r["longshots"][:2]:
            parts.append(f"   🎯 longshot {h(lab)} @{px:.2f} (bot {pb:.0%}) — size chico")
        parts.append(f'   <a href="{r["url"]}">Polymarket ↗</a>')
        lines.append("\n".join(parts))
    if any(r["tier"] == "DEBIL" for r in hits[:12]):
        lines.append("🔴 = estacion DEBIL: el playbook dice NO operar (sin fuente local).")
    return "\n".join(lines)


def fmt_top():
    rows = I.stability()
    import dashboard as D
    lines = ["<b>🏆 Ciudades mas ESTABLES (desde 08/07, pick congelado vs Gamma)</b>",
             "<pre>#  ciudad          ex   top2  n  score"]
    for i, r in enumerate(rows[:15], 1):
        ciudad = D.STATION_META.get(r["station"], ("?", "?", r["station"]))[2][:14]
        lines.append(f"{i:<2} {ciudad:<15.15} {r['exact']:>2}   {r['top2']:>2}   {r['n']:>2}  "
                     f"{r['score']:.2f}")
    lines.append("</pre>")
    lines.append("<i>score = cota inferior Wilson del TOP-2 (n chico penaliza solo — 2/2 no "
                 "le gana a 6/7). MAE y detalle: /historial &lt;ciudad&gt;</i>")
    return "\n".join(lines)


def fmt_models(code):
    import dashboard as D
    perf = I.model_perf(days=90)
    ciudad = D.STATION_META[code][2]
    rows = [r for r in perf if r["station"] == code]
    if not rows:
        return f"Sin datos de modelos para {h(ciudad)} todavia."
    lines = [f"<b>🧪 Modelos en {h(ciudad)} ({code})</b>"]
    for src, tag in (("vivo", "VIVO — capturas reales pre-freeze"),
                     ("retro", "RETRO — Previous-Runs 90d (bug #5: referencia)")):
        sub = sorted([r for r in rows if r["src"] == src],
                     key=lambda r: (-(r["rate"] if r["rate"] == r["rate"] else -1),
                                    r["mae"] if r["mae"] == r["mae"] else 99))
        if not sub:
            continue
        lines.append(f"<i>{tag}</i>")
        body = ["<pre>modelo       exactos  %     MAE"]
        for r in sub:
            mae = f"{r['mae']:.2f}" if r["mae"] == r["mae"] else "  - "
            body.append(f"{r['model']:<12.12} {r['hits']:>2}/{r['n']:<3}  {r['rate']:>4.0%}  {mae}")
        body.append("</pre>")
        lines.append("\n".join(body))
    lines.append("<i>% = veces que el floor del modelo cayo en el bucket que PAGO Polymarket.</i>")
    return "\n".join(lines)


def fmt_historial(code, nmax=12):
    import dashboard as D
    hist = [r for r in I.bot_history() if r["station"] == code]
    hist.sort(key=lambda r: r["target"], reverse=True)
    ciudad = D.STATION_META[code][2]
    if not hist:
        return f"Sin historial congelado para {h(ciudad)} aun."
    icon = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌", None: "⏳"}
    lines = [f"<b>🗓 Historial {h(ciudad)} ({code})</b>", "<pre>fecha  pick     gano     resultado"]
    for r in hist[:nmax]:
        res = r["nivel"] or "pendiente"
        lines.append(f"{r['target'].strftime('%d/%m')}  {(r['pick_lbl'] or '—'):<8.8} "
                     f"{(r['win_lbl'] or '—'):<8.8} {icon[r['nivel']]} {res}")
    lines.append("</pre>")
    sc = [r for r in hist if r["nivel"]]
    if sc:
        ex = sum(r["nivel"] == "EXACTO" for r in sc)
        t2 = sum(r["nivel"] in ("EXACTO", "TOP-2") for r in sc)
        lines.append(f"Total: {ex}/{len(sc)} exactos · {t2}/{len(sc)} top-2")
    return "\n".join(lines)


def fmt_vivo(code, today):
    """Obs en vivo (max del dia hasta ahora) + pick + top del mercado."""
    import dashboard as D
    unit = STATIONS[code][3]
    deg = "°F" if unit == "F" else "°C"
    ciudad = D.STATION_META[code][2]
    lines = [f"<b>📡 {h(ciudad)} ({code}) EN VIVO</b>"]
    try:
        ext = D._fresh_metar_extremes(code, today, unit)
        for dd in sorted(ext):
            if dd >= today - dt.timedelta(days=1):
                mx, mn = ext[dd]
                lines.append(f"{dd.strftime('%d/%m')}: max {mx:.1f}{deg} · min {mn:.1f}{deg} (METAR IEM)")
    except Exception as e:
        lines.append(f"obs en vivo no disponible ({h(e)})")
    lines.append("")
    lines.append(fmt_pick(code, today, today))
    return "\n".join(lines)


def fmt_pws(code):
    import dashboard as D
    path = os.path.join(DATA, "pws_reference.csv")
    ciudad = D.STATION_META[code][2]
    if not os.path.exists(path):
        return ("Sin referencia PWS todavia. Correr: "
                "<code>python scripts/pws_setup.py --stations " + code + "</code>")
    import csv as _csv
    rows = [r for r in _csv.DictReader(open(path, encoding="utf-8")) if r["station"] == code]
    if not rows:
        return f"Sin PWS de referencia para {h(ciudad)} — correr pws_setup.py --stations {code}."
    unit = STATIONS[code][3]
    lines = [f"<b>📍 PWS de referencia — {h(ciudad)} ({code})</b>",
             "<pre>pws            km   n    bias   σdif"]
    for r in rows:
        lines.append(f"{r['pws_id']:<14.14} {float(r['dist_km']):4.1f} {r['n']:>3}  "
                     f"{float(r['bias']):+5.2f}  {float(r['std']):5.2f}")
    lines.append("</pre>")
    lines.append(f"<i>bias = PWS − estacion ({'°F' if unit == 'F' else '°C'}, mediana del rango "
                 f"evaluado). Estimador: mediana(PWS_vivo − bias). Detalle: city_{code}.html</i>")
    return "\n".join(lines)


HELP = """<b>WXBT bot — comandos</b>
/picks — picks de HOY en todas las ciudades
/pick &lt;ciudad&gt; — prediccion + rango Polymarket con precios y edge
/value — value bets ahora (edge bruto vs mercado)
/top — ciudades mas estables (desde 08/07)
/modelos &lt;ciudad&gt; — que modelo acierta en esa ciudad
/vivo &lt;ciudad&gt; — obs en vivo + mercado
/historial &lt;ciudad&gt; — ultimos resultados del bot ahi
/pws &lt;ciudad&gt; — PWS de referencia y su bias vs la estacion
Ej: /pick milan · /modelos nyc · /historial seul"""


def handle(text, today=None):
    """Comando -> respuesta (string HTML). Puro (para --dry-run y tests)."""
    today = today or dt.date.today()
    t = (text or "").strip()
    if not t.startswith("/"):
        return None
    parts = t.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/start", "/help", "/ayuda"):
        return HELP
    if cmd == "/picks":
        return fmt_picks(today)
    if cmd in ("/value", "/valuebets"):
        return fmt_value(today)
    if cmd in ("/top", "/leaderboard", "/estables"):
        return fmt_top()
    if cmd in ("/pick", "/modelos", "/models", "/vivo", "/live", "/historial",
               "/history", "/pws"):
        d = today
        if arg.lower().endswith(("mañana", "manana")):
            d = today + dt.timedelta(days=1)
            arg = arg.rsplit(None, 1)[0] if " " in arg else ""
        code = find_station(arg)
        if not code:
            return (f"No encontre la ciudad «{h(arg)}». Proba con el nombre de Polymarket "
                    f"(milan, nyc, sao paulo...) o el ICAO (LIMC).")
        if cmd == "/pick":
            return fmt_pick(code, d, today)
        if cmd in ("/modelos", "/models"):
            return fmt_models(code)
        if cmd in ("/vivo", "/live"):
            return fmt_vivo(code, today)
        if cmd in ("/historial", "/history"):
            return fmt_historial(code)
        if cmd == "/pws":
            return fmt_pws(code)
    return "Comando desconocido. /help para la lista."


# ------------------------------- loop / push -------------------------------

def poll(token):
    st = load_state()
    print(f"WXBT telegram bot corriendo (long-poll). Chats registrados: {len(st['chats'])}. Ctrl+C para parar.")
    while True:
        try:
            j = api(token, "getUpdates", offset=st.get("offset", 0) + 1, timeout=POLL_TIMEOUT)
        except requests.RequestException as e:
            print(f"[WARN] getUpdates: {e}", file=sys.stderr)
            time.sleep(5)
            continue
        for u in j.get("result", []):
            st["offset"] = max(st.get("offset", 0), u["update_id"])
            msg = u.get("message") or u.get("edited_message")
            if not msg or not msg.get("text"):
                continue
            chat = msg["chat"]
            cid = str(chat["id"])
            if cid not in st["chats"]:
                st["chats"][cid] = {"name": chat.get("username") or chat.get("first_name") or "?",
                                    "since": dt.date.today().isoformat()}
                print(f"[+] chat nuevo: {st['chats'][cid]['name']} ({cid})")
            try:
                resp = handle(msg["text"])
            except Exception as e:
                resp = f"Error procesando el comando: {h(e)}"
                import traceback
                traceback.print_exc()
            if resp:
                send(token, chat["id"], resp)
            save_state(st)
        save_state(st)


def push(token, today=None):
    """Resumen diario (picks + value bets) a todos los chats registrados. Para run_daily.ps1."""
    st = load_state()
    if not st["chats"]:
        print("push: sin chats registrados (alguien tiene que hablarle al bot primero).")
        return
    today = today or dt.date.today()
    text = fmt_picks(today) + "\n\n" + fmt_value(today)
    for cid in st["chats"]:
        try:
            send(token, int(cid), text)
        except Exception as e:
            print(f"[WARN] push a {cid}: {e}", file=sys.stderr)
    print(f"push: resumen enviado a {len(st['chats'])} chat(s).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Bot de Telegram WXBT (solo lectura).")
    ap.add_argument("--poll", action="store_true", help="loop de long-polling (dejar corriendo)")
    ap.add_argument("--push", action="store_true", help="mandar resumen diario y salir")
    ap.add_argument("--dry-run", default=None, metavar="CMD",
                    help='probar un comando sin token, ej: --dry-run "/pick milan"')
    ap.add_argument("--date", default=None, help="fecha para --dry-run/--push (YYYY-MM-DD)")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else None
    if a.dry_run:
        out = handle(a.dry_run, today=day)
        # consola Windows cp1252: degradar a ascii para no reventar (los emojis van a Telegram)
        print((out or "(sin respuesta)").encode("ascii", "replace").decode())
        sys.exit(0)
    tok = get_token()
    if not tok:
        print("Falta el token: crear el bot con @BotFather y guardar el token en "
              "data/.telegram_token o en la env WXBT_TG_TOKEN.")
        sys.exit(0 if a.push else 1)   # push encadenado en run_daily no debe romper la cadena
    if a.push:
        push(tok, today=day)
    elif a.poll:
        poll(tok)
    else:
        print("Usar --poll (loop), --push (resumen diario) o --dry-run '/comando'.")
