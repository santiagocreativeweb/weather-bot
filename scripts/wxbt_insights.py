#!/usr/bin/env python3
# scripts/wxbt_insights.py — capa de LECTURA compartida para las vistas nuevas (2026-07-15, pedido
# Santiago): historial de pronosticos desde 08/07, performance por MODELO por CIUDAD, leaderboard
# de estabilidad y VALUE BETS. La consumen history_page.py / models_page.py / city_pages.py /
# telegram_bot.py y el dashboard (badge de mejor modelo).
#
# HONESTIDAD (no negociable):
#   * El historial scorea SOLO evidencia congelada (audit froze / legacy pre-deadline) — mismo
#     criterio que leaderboard.py (universo = audit ∪ predictions_forward; forward-fallback NO
#     cuenta). El ganador oficial es Gamma (= lo que pago WU).
#   * La perf por modelo tiene DOS fuentes etiquetadas: "vivo" = models_forward.csv (captura
#     point-in-time real, ultima captura ANTERIOR al freeze) y "retro" = lab_m8.csv (Previous-Runs
#     retrospectivo, hereda la ambiguedad de frescura del bug #5 — sirve de referencia, no de
#     veredicto). NUNCA mezclar sin etiquetar.
#   * value_bets() reporta edge BRUTO (pbot − mid), sin fees/spread/shrink — es un screener, no señal.
# Unico estado propio: data/winners_cache.json (ganadores Gamma ya resueltos; inmutables por diseño).
import csv
import json
import math
import os
import re
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from show_live import (STATIONS, CITY_STATION, parse_bucket, local_offset, GAMMA)  # noqa: E402
from wxbt.market import bucket_prob, resolve_bucket                                # noqa: E402
from wxbt.forward_scoring import frozen_forecast                                   # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
HISTORY_START = dt.date(2026, 7, 8)     # primer dia con freeze auditado ("desde 08/07")
FREEZE_LOCAL_H = 4.5                    # = dashboard.FREEZE_LOCAL_H (duplicado a proposito: evitar
#                                         importar dashboard —pesado/red— desde la capa offline)
CITY_OF = {v: k for k, v in CITY_STATION.items()}
MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
WINNERS_CACHE = os.path.join(DATA, "winners_cache.json")
MODELS_FWD = os.path.join(DATA, "models_forward.csv")
LAB_M8 = os.path.join(DATA, "lab_m8.csv")
BACKFILL = os.path.join(DATA, "backfill_check.csv")
GAMMA_LABELS = os.path.join(DATA, "gamma_labels.csv")
AUDIT_JSON = os.path.join(DATA, "forecast_audit.json")
PREDS_FWD = os.path.join(DATA, "predictions_forward.csv")


def freeze_utc(code, d):
    """Instante naive-UTC del freeze (04:30 hora local del target) = dashboard.freeze_utc."""
    return dt.datetime.combine(d, dt.time()) + dt.timedelta(hours=FREEZE_LOCAL_H - local_offset(code, d))


def pm_url(code, d):
    city = CITY_OF.get(code, "")
    return (f"https://polymarket.com/event/highest-temperature-in-{city}-on-"
            f"{MONTHS_EN[d.month - 1]}-{d.day}-{d.year}")


def parse_win_label(lbl):
    """Etiqueta ganadora ('23°C', '72-73°F', '>= 64°F', '12°C or higher') -> (lo, hi)."""
    if not lbl:
        return None, None
    t = str(lbl).strip()
    t = re.sub(r"^\s*>=\s*(\d+)", r"\1 or higher", t)
    t = re.sub(r"^\s*<=\s*(\d+)", r"\1 or below", t)
    return parse_bucket(t)


def bucket_label(lo, hi, unit):
    deg = "°F" if unit == "F" else "°C"
    if lo is None and hi is None:
        return "—"
    if lo is None:
        return f"≤{hi}{deg}"
    if hi is None:
        return f"≥{lo}{deg}"
    return f"{lo}-{hi}{deg}" if lo != hi else f"{lo}{deg}"


def _load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ------------------------------- ganadores oficiales (Gamma/WU) -------------------------------

def load_winners(refresh=False, today=None, lookback_days=14):
    """{(station, date): {"lbl", "lo", "hi", "max_real"}} — ganador oficial por mercado.
    Fuentes offline: backfill_check.csv (trae ademas max_real IEM) + gamma_labels.csv +
    winners_cache.json. Con refresh=True consulta Gamma POR SLUG los pares faltantes de los
    ultimos `lookback_days` (solo resueltos; se cachean para siempre — un resuelto no cambia)."""
    today = today or dt.date.today()
    out = {}
    for r in _load_csv(BACKFILL):
        try:
            key = (r["station"], dt.date.fromisoformat(r["target"]))
        except (KeyError, ValueError):
            continue
        lbl = (r.get("win_mkt") or "").strip()
        mx = r.get("max_real")
        cur = out.get(key, {})
        if lbl and not cur.get("lbl"):
            lo, hi = parse_win_label(lbl)
            cur.update(lbl=lbl, lo=lo, hi=hi)
        if mx not in (None, "") and cur.get("max_real") is None:
            try:
                cur["max_real"] = float(mx)
            except ValueError:
                pass
        if cur:
            out[key] = cur
    for r in _load_csv(GAMMA_LABELS):
        try:
            key = (r["station"], dt.date.fromisoformat(r["target"]))
        except (KeyError, ValueError):
            continue
        lbl = (r.get("win_mkt") or "").strip()
        if lbl and not out.get(key, {}).get("lbl"):
            lo, hi = parse_win_label(lbl)
            out.setdefault(key, {}).update(lbl=lbl, lo=lo, hi=hi)
    # cache propio (targets posteriores a los CSVs de labels)
    cache = {}
    if os.path.exists(WINNERS_CACHE):
        try:
            cache = json.load(open(WINNERS_CACHE, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    for k, v in cache.items():
        st, _, ds = k.partition("|")
        try:
            key = (st, dt.date.fromisoformat(ds))
        except ValueError:
            continue
        lbl = v.get("lbl") if isinstance(v, dict) else v      # legado: string suelto
        mx = v.get("mx") if isinstance(v, dict) else None
        cur = out.setdefault(key, {})
        if lbl and not cur.get("lbl"):
            lo, hi = parse_win_label(lbl)
            cur.update(lbl=lbl, lo=lo, hi=hi)
        if mx is not None and cur.get("max_real") is None:
            cur["max_real"] = mx
    if refresh:
        changed = False
        missing = [(st, d) for st in STATIONS
                   for d in (today - dt.timedelta(days=k) for k in range(1, lookback_days + 1))
                   if d >= HISTORY_START and not out.get((st, d), {}).get("lbl")
                   and f"{st}|{d.isoformat()}" not in cache]
        for (st, d), lbl in _fetch_winners_gamma(missing).items():
            cache[f"{st}|{d.isoformat()}"] = {"lbl": lbl}
            lo, hi = parse_win_label(lbl)
            out.setdefault((st, d), {}).update(lbl=lbl, lo=lo, hi=hi)
            changed = True
        # TOP-UP de obs (max_real) para dias resueltos recientes que obs.csv aun no cubre —
        # habilita el MAE de la fuente 'vivo'. Cacheado: la obs de un dia terminado no cambia.
        need_obs = [(st, d) for (st, d), w in out.items()
                    if w.get("lbl") and w.get("max_real") is None and d < today
                    and d >= today - dt.timedelta(days=lookback_days)]
        if need_obs:
            from concurrent.futures import ThreadPoolExecutor
            from check_predictions import fetch_obs_iem
            with ThreadPoolExecutor(max_workers=8) as tp:
                obs = list(tp.map(lambda p: (p, fetch_obs_iem(*p)), need_obs))
            for (st, d), v in obs:
                if v is None:
                    continue
                out[(st, d)]["max_real"] = float(v)
                k = f"{st}|{d.isoformat()}"
                prev = cache.get(k)
                cache[k] = {"lbl": (prev.get("lbl") if isinstance(prev, dict) else prev)
                            or out[(st, d)].get("lbl"), "mx": float(v)}
                changed = True
        if changed:
            json.dump(cache, open(WINNERS_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return out


def _fetch_winners_gamma(pairs):
    """Ganador oficial por slug para (station, date) — SOLO mercados resueltos (closed/UMA)."""
    import requests
    out = {}
    for st, d in pairs:
        city = CITY_OF.get(st)
        if not city:
            continue
        slug = f"highest-temperature-in-{city}-on-{MONTHS_EN[d.month - 1]}-{d.day}-{d.year}"
        try:
            r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=25)
            evs = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"[WARN] gamma {slug}: {e}", file=sys.stderr)
            continue
        if not evs:
            continue
        ev_closed = bool(evs[0].get("closed"))
        for mk in evs[0].get("markets", []):
            op = mk.get("outcomePrices")
            try:
                yes = float(json.loads(op)[0]) if isinstance(op, str) else float(op[0])
            except Exception:
                yes = None
            resolved = (ev_closed or bool(mk.get("closed"))
                        or str(mk.get("umaResolutionStatus") or "").lower() == "resolved")
            if yes is not None and yes >= 0.99 and resolved and mk.get("groupItemTitle"):
                out[(st, d)] = mk["groupItemTitle"]
                break
    return out


# --------------------------- performance por MODELO por CIUDAD ---------------------------

def model_captures_pre_freeze():
    """{(station, target): {model: tmax}} — ULTIMA captura de models_forward.csv ANTERIOR al
    freeze de ese target (point-in-time honesto: lo que el modelo decia cuando el pick se fija)."""
    best = {}   # (st, tg, model) -> (capture_dt, tmax)
    for r in _load_csv(MODELS_FWD):
        try:
            st, model = r["station"], r["model"]
            tg = dt.date.fromisoformat(r["target"])
            cap = dt.datetime.fromisoformat(r["capture_utc"].replace("Z", "+00:00"))
            cap = cap.astimezone(dt.timezone.utc).replace(tzinfo=None)
            v = float(r["tmax"])
        except (KeyError, TypeError, ValueError):
            continue
        if st not in STATIONS or cap > freeze_utc(st, tg):
            continue
        k = (st, tg, model)
        if k not in best or cap > best[k][0]:
            best[k] = (cap, v)
    out = {}
    for (st, tg, model), (_, v) in best.items():
        out.setdefault((st, tg), {})[model] = v
    return out


def _retro_models(lead=2):
    """{(station, target): {model: m}} del lab retrospectivo lab_m8.csv (Previous-Runs).
    CAVEAT bug #5: frescura retrospectiva ambigua — referencia, no veredicto."""
    out = {}
    for r in _load_csv(LAB_M8):
        try:
            if int(r["lead"]) != lead:
                continue
            st, model = r["station"], r["model"]
            tg = dt.date.fromisoformat(r["target"])
            v = float(r["m"])
        except (KeyError, TypeError, ValueError):
            continue
        if st in STATIONS:
            out.setdefault((st, tg), {})[model] = v
    return out


def model_perf(winners=None, days=90, today=None, refresh=False):
    """[{station, model, src, n, hits, rate, mae, n_mae}] contra el ganador oficial de Gamma.
    Hit por modelo = floor(tmax_modelo) cae en el bucket ganador (regla FLOOR de WU).
    src='vivo' (models_forward point-in-time) | 'retro' (lab_m8, bug #5)."""
    today = today or dt.date.today()
    winners = winners if winners is not None else load_winners(refresh=refresh, today=today)
    lo_d = today - dt.timedelta(days=days) if days else None
    acc = {}   # (st, model, src) -> [n, hits, sum_ae, n_ae]

    def _tally(caps, src):
        for (st, tg), models in caps.items():
            if lo_d and tg < lo_d:
                continue
            w = winners.get((st, tg))
            if not w or not w.get("lbl"):
                continue
            mx = w.get("max_real")
            for model, v in models.items():
                k = (st, model, src)
                a = acc.setdefault(k, [0, 0, 0.0, 0])
                a[0] += 1
                if resolve_bucket(int(math.floor(v)), w["lo"], w["hi"]):
                    a[1] += 1
                if mx is not None:
                    a[2] += abs(v - mx)
                    a[3] += 1

    _tally(model_captures_pre_freeze(), "vivo")
    _tally(_retro_models(), "retro")
    rows = []
    for (st, model, src), (n, hits, sae, nae) in sorted(acc.items()):
        rows.append(dict(station=st, model=model, src=src, n=n, hits=hits,
                         rate=hits / n if n else float("nan"),
                         mae=(sae / nae) if nae else float("nan"), n_mae=nae))
    return rows


def best_models(perf=None, min_n_vivo=5, min_n_retro=20, **kw):
    """{station: {"src": 'vivo'|'retro', "rank": [(model, rate, n, mae), ...]}} — ranking de
    modelos por ciudad. Prefiere la fuente VIVO si ya junto n>=min_n_vivo; si no, retro."""
    perf = perf if perf is not None else model_perf(**kw)
    by_st = {}
    for r in perf:
        by_st.setdefault((r["station"], r["src"]), []).append(r)
    out = {}
    for st in STATIONS:
        vivo = by_st.get((st, "vivo"), [])
        retro = by_st.get((st, "retro"), [])
        use, src = (vivo, "vivo") if vivo and max(r["n"] for r in vivo) >= min_n_vivo else \
                   ((retro, "retro") if retro and max(r["n"] for r in retro) >= min_n_retro else
                    ((vivo, "vivo") if vivo else (retro, "retro")))
        if not use:
            continue
        rank = sorted(use, key=lambda r: (-(r["rate"] if r["rate"] == r["rate"] else -1),
                                          r["mae"] if r["mae"] == r["mae"] else 99))
        out[st] = {"src": src,
                   "rank": [(r["model"], r["rate"], r["n"], r["mae"]) for r in rank]}
    return out


# ------------------------------- historial congelado del bot -------------------------------

def _load_audit():
    try:
        return json.load(open(AUDIT_JSON, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _synthetic_buckets(unit, win_lo, win_hi, mu, span=6):
    """Grilla de buckets contigua alrededor de mu, ALINEADA al bucket ganador (ancho 1°C / 2°F).
    Sirve para rankear top-2/top-3 offline cuando no persistimos la lista real de buckets del
    mercado — los mercados reales SON grillas contiguas, esto replica su geometria."""
    w = 2 if unit == "F" else 1
    anchor = win_lo if win_lo is not None else (win_hi - w + 1 if win_hi is not None else int(math.floor(mu)))
    lo0 = anchor - w * span
    return [(lo0 + i * w, lo0 + i * w + w - 1) for i in range(2 * span + 1)]


def bot_history(start=HISTORY_START, end=None, refresh=False, today=None):
    """[{station, target, mu, sg, src, pick_lo, pick_hi, pick_lbl, win_lbl, nivel, pwin}] —
    historial dia por dia del pick CONGELADO vs el ganador oficial. Mismo criterio de honestidad
    que leaderboard.py: filas sin evidencia point-in-time (forward-fallback) NO se scorean."""
    today = today or dt.date.today()
    end = end or today
    audit = _load_audit()
    winners = load_winners(refresh=refresh, today=today)
    # sigma fallback por estacion desde predictions_forward
    sig_fb, mu_fb = {}, {}
    for r in _load_csv(PREDS_FWD):
        try:
            key = (r["station"], dt.date.fromisoformat(r["target"]))
            sig_fb.setdefault(r["station"], []).append(float(r["sigma_cal"]))
            cur = mu_fb.get(key)
            lh = float(r.get("lead_h") or 999)
            if cur is None or lh < cur[0]:
                mu_fb[key] = (lh, float(r["mu_cal"]), float(r["sigma_cal"]))
        except (KeyError, TypeError, ValueError):
            continue
    sig_med = {st: sorted(v)[len(v) // 2] for st, v in sig_fb.items()}

    rows = []
    keys = {(st, tg) for st, tg in mu_fb} | {
        (k.split("|")[0], dt.date.fromisoformat(k.split("|")[1]))
        for k in audit if "|" in k and _valid_date(k.split("|")[1])}
    for st, tg in sorted(keys):
        if st not in STATIONS or not (start <= tg <= end) or tg > today:
            continue
        fb = mu_fb.get((st, tg))
        mu, sg, src = frozen_forecast(audit, st, tg,
                                      fb[1] if fb else float("nan"),
                                      (fb[2] if fb else sig_med.get(st, 1.5)))
        if src == "forward-fallback" or not (mu == mu):
            continue   # sin evidencia congelada -> no entra al KPI (honestidad)
        unit = STATIONS[st][3]
        w = winners.get((st, tg)) or {}
        win_lbl = w.get("lbl")
        rec = dict(station=st, target=tg, mu=mu, sg=sg, src=src, unit=unit,
                   win_lbl=win_lbl, max_real=w.get("max_real"),
                   nivel=None, pwin=None, pick_lbl=None)
        fbk = int(math.floor(mu))
        if win_lbl:
            buckets = _synthetic_buckets(unit, w.get("lo"), w.get("hi"), mu)
            # colas abiertas del ganador real
            win_b = next((b for b in buckets if resolve_bucket_open(w, b)), None)
            pick_b = next((b for b in buckets if b[0] <= fbk <= b[1]), None)
            probs = {b: bucket_prob(mu - 0.5, sg, b[0], b[1]) for b in buckets}
            rank = sorted(buckets, key=lambda b: -probs[b])
            if pick_b:   # pick-first (mismo ranking que timeline/leaderboard)
                rank = [pick_b] + [b for b in rank if b != pick_b]
            rw = rank.index(win_b) + 1 if win_b in rank else 99
            exact = int(pick_b == win_b)
            nivel = ("EXACTO" if exact else "TOP-2" if rw <= 2 else "TOP-3" if rw <= 3 else "PERDIDA")
            rec.update(nivel=nivel, pwin=probs.get(win_b),
                       pick_lbl=bucket_label(pick_b[0], pick_b[1], unit) if pick_b else None,
                       win_lbl=win_lbl)
        else:
            pick_w = 2 if unit == "F" else 1
            plo = fbk - (fbk % pick_w) if unit == "F" else fbk
            rec["pick_lbl"] = bucket_label(plo, plo + pick_w - 1, unit)
        rows.append(rec)
    return rows


def resolve_bucket_open(w, b):
    """El bucket sintetico b equivale al ganador real w (respetando colas abiertas)."""
    lo, hi = w.get("lo"), w.get("hi")
    if lo is None and hi is None:
        return False
    if lo is None:
        return b[1] <= hi
    if hi is None:
        return b[0] >= lo
    return (b[0], b[1]) == (lo, hi) or (b[0] <= lo and hi <= b[1])


def _valid_date(s):
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


# ------------------------------- estabilidad por ciudad -------------------------------

def wilson_low(k, n, z=1.28):
    """Cota INFERIOR de Wilson (z=1.28 ~ 80%) — castiga n chico: 3/3 NO le gana a 8/10."""
    if not n:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    center = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - rad) / den)


def stability(hist=None, **kw):
    """Ranking de ciudades ESTABLES: wilson-low del TOP-2 congelado (desde 08/07), desempate
    exacto y MAE vs obs. n chico penaliza solo (Wilson): recien-llegadas no inflan el top."""
    hist = hist if hist is not None else bot_history(**kw)
    by_st = {}
    for r in hist:
        if r["nivel"] is None:
            continue
        a = by_st.setdefault(r["station"], dict(n=0, ex=0, t2=0, t3=0, ae=[], pw=[]))
        a["n"] += 1
        a["ex"] += r["nivel"] == "EXACTO"
        a["t2"] += r["nivel"] in ("EXACTO", "TOP-2")
        a["t3"] += r["nivel"] in ("EXACTO", "TOP-2", "TOP-3")
        if r.get("max_real") is not None:
            a["ae"].append(abs(r["mu"] - r["max_real"]))
        if r.get("pwin") is not None:
            a["pw"].append(r["pwin"])
    rows = []
    for st, a in by_st.items():
        rows.append(dict(
            station=st, n=a["n"], exact=a["ex"], top2=a["t2"], top3=a["t3"],
            exact_rate=a["ex"] / a["n"], top2_rate=a["t2"] / a["n"],
            mae=(sum(a["ae"]) / len(a["ae"])) if a["ae"] else float("nan"),
            pwin=(sum(a["pw"]) / len(a["pw"])) if a["pw"] else float("nan"),
            score=wilson_low(a["t2"], a["n"])))
    rows.sort(key=lambda r: (-r["score"], -r["exact_rate"], r["station"]))
    return rows


# ------------------------------- value bets (red: Gamma en vivo) -------------------------------

_VB_CACHE = {"live": (0.0, None)}


def value_bets(today=None, horizon=1, mk=None, preds=None, edge_min=0.10, pair_min=0.12,
               live=None):
    """Screener de VALUE BETS sobre mercados VIVOS: pbot (pick congelado si existe, sino snapshot
    forward) vs mid del book. Edge BRUTO — sin fees/spread/shrink. Reusa umbrales del playbook.
    Filtra los buckets YA IMPOSIBLES por la obs en vivo (el max del dia solo sube) — misma regla
    `lost` que playbook/dashboard, si no un bucket muerto parece "value" gigante.
    Devuelve [{station, city, date, mu, sg, frozen, t1, pbot1, px1, edge1, t2, pair_edge,
               longshots, nos, url, tier, state}] orden edge desc."""
    import time as _time
    import dashboard as D           # lazy: trae red/pandas solo cuando hace falta
    try:
        from playbook import STRONG, WEAK
    except Exception:
        STRONG, WEAK = set(), set()
    today = today or dt.date.today()
    mk = mk if mk is not None else D.fetch_market_full(today, horizon)
    preds = preds if preds is not None else D.load_preds(today)
    if live is None:
        ts, lv = _VB_CACHE["live"]
        if lv is None or _time.monotonic() - ts > 600:
            lv = D.fetch_obs_live(today)
            _VB_CACHE["live"] = (_time.monotonic(), lv)
        live = lv
    audit = _load_audit()
    now_utc = dt.datetime.now(dt.timezone.utc)
    out = []
    for code in STATIONS:
        unit = STATIONS[code][3]
        for d in [today + dt.timedelta(days=k) for k in range(horizon + 1)]:
            info = mk.get(code, {}).get(d)
            if not info or not info.get("buckets"):
                continue
            state, _lbl = D.state_of(code, d, info, now_utc)
            if state not in ("encurso", "soon", "prox"):
                continue
            # pico ya pasado = el tmax ya ocurrio y el mercado lo vio (nowcast): el "edge" del
            # pick congelado contra ese precio es ILUSORIO -> afuera del screener.
            if now_utc.replace(tzinfo=None) > D.peak_utc(code, d) + dt.timedelta(hours=1):
                continue
            fb = preds.get((code, d))
            mu, sg, frozen = (fb[0], fb[1], False) if fb else (None, None, False)
            froze = (audit.get(f"{code}|{d.isoformat()}") or {}).get("froze") or {}
            if froze.get("mu") is not None:
                mu = froze["mu"]
                sg = froze.get("sg") or sg or (2.6 if unit == "F" else 1.5)
                frozen = True
            if mu is None or sg is None:
                continue
            priced = [(lab, lo, hi, p) for lab, lo, hi, p in info["buckets"] if p is not None]
            if not priced:
                continue
            live_max = (live.get((code, d)) or {}).get("max") if state in ("encurso", "soon") else None
            floor_live = int(math.floor(live_max)) if live_max is not None else None
            lost = {lab for lab, lo, hi, p in priced
                    if floor_live is not None and hi is not None and hi < floor_live}
            pbot = {lab: D.pbot_floor(mu, sg, lo, hi) for lab, lo, hi, p in priced}
            px = {lab: p for lab, lo, hi, p in priced}
            rank = [l for l, _ in sorted(pbot.items(), key=lambda kv: -kv[1]) if l not in lost]
            if not rank:
                continue
            t1 = rank[0]
            t2 = rank[1] if len(rank) > 1 else None
            edge1 = pbot[t1] - px.get(t1, 1.0)
            pair_edge = (pbot[t1] + (pbot.get(t2, 0.0) if t2 else 0.0)) - \
                        (px.get(t1, 1.0) + (px.get(t2, 1.0) if t2 else 1.0))
            longs = [(lab, px[lab], pbot[lab]) for lab, lo, hi, p in priced
                     if lab not in lost and 0.005 <= p <= 0.10
                     and pbot.get(lab, 0) >= max(0.15, 3 * p)]
            nos = [(lab, px[lab], pbot[lab]) for lab, lo, hi, p in priced
                   if (p >= 0.08 and pbot.get(lab, 1) <= 0.04) or lab in lost]
            tier = "FUERTE" if code in STRONG else ("DEBIL" if code in WEAK else "MEDIA")
            out.append(dict(station=code, city=D.STATION_META[code][2], date=d, state=state,
                            mu=mu, sg=sg, frozen=frozen, unit=unit,
                            t1=t1, pbot1=pbot[t1], px1=px.get(t1), edge1=edge1,
                            t2=t2, pbot2=(pbot.get(t2) if t2 else None),
                            px2=(px.get(t2) if t2 else None), pair_edge=pair_edge,
                            longshots=longs, nos=nos, tier=tier, url=pm_url(code, d),
                            value=(edge1 >= edge_min and pbot[t1] >= 0.35) or pair_edge >= pair_min
                                  or bool(longs)))
    out.sort(key=lambda r: -r["edge1"])
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Insights WXBT: perf por modelo, historial, estabilidad.")
    ap.add_argument("--refresh", action="store_true", help="completar ganadores faltantes desde Gamma")
    ap.add_argument("--days", type=int, default=90)
    a = ap.parse_args()
    perf = model_perf(days=a.days, refresh=a.refresh)
    bm = best_models(perf)
    print("=== mejor modelo por ciudad (exacto vs ganador oficial) ===")
    for st, info in sorted(bm.items()):
        top = info["rank"][:3]
        s = " | ".join(f"{m} {r:.0%} (n={n}, mae {mae:.2f})" for m, r, n, mae in top)
        print(f"  {st}: [{info['src']}] {s}")
    print("\n=== estabilidad (wilson top-2, desde 08/07) ===")
    for r in stability()[:15]:
        print(f"  {r['station']}: score {r['score']:.2f}  exact {r['exact']}/{r['n']}  "
              f"top2 {r['top2']}/{r['n']}  mae {r['mae']:.2f}")
