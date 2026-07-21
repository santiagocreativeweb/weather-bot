#!/usr/bin/env python3
# scripts/stats_page.py — ESTADISTICAS del bot: generales + rendimiento DIA POR DIA, marcando en
# cada mercado si GANO o PERDIO (pedido Santiago 2026-07-11). Genera data/stats.html (tab aparte,
# linkeado desde el dashboard). Track record VIVO: bot vs bucket ganador oficial de Polymarket.
#
# Regla (coherente con leaderboard/check_predictions/dashboard): pick = floor(mu_cal); top-2/3 por
# bucket_prob(mu-0.5, sigma, lo, hi); ganador = Gamma (o fisica IEM floreada si el dia paso sin
# ganador de mercado). WU FLOOREA la obs SIEMPRE. Verdicto por mercado: EXACTO / TOP-2 / TOP-3 / PERDIDA.
import collections
import concurrent.futures as cf
import json, math, os, sys
import datetime as dt
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import STATION_META, CSS, ddmmyyyy, fecha_es, STATIONS, to_art       # noqa: E402
from check_predictions import resolved_buckets, fetch_obs_iem, winner_by_temp        # noqa: E402
from wxbt.market import bucket_prob                                                  # noqa: E402
from wxbt.forward_scoring import frozen_forecast, audit_only_targets                 # noqa: E402
from wxbt_nav import nav_html, NAV_CSS                                               # noqa: E402
import wxbt_insights as I                                                            # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")


def lbl_of(bucket, unit):
    """(lo,hi) -> etiqueta legible del bucket, coherente con el mercado."""
    lo, hi = bucket
    deg = "°F" if unit == "F" else "°C"
    if lo is None:
        return f"≤{hi}{deg}"
    if hi is None:
        return f"≥{lo}{deg}"
    return (f"{lo}-{hi}{deg}" if lo != hi else f"{lo}{deg}")


def records(today):
    """Una fila por (station, target) resuelto con pick/ganador/nivel/error (para stats + dia a dia)."""
    p = pd.read_csv(os.path.join(D, "predictions_forward.csv"), parse_dates=["target"])
    p["target"] = p["target"].dt.date
    due = p[p["target"] <= today].sort_values("lead_h").drop_duplicates(
        ["station", "target"], keep="first")
    try:
        with open(os.path.join(D, "forecast_audit.json"), encoding="utf-8") as fh:
            audit = json.load(fh)
    except (OSError, ValueError):
        audit = {}
    # UNIVERSO = acumulador UNION audit congelado (mismo criterio que leaderboard.py): sin esto,
    # un dia que el acumulador se perdio (Asia/NZ post-pico, alta nueva) desaparece del KPI.
    cand = [(r.station, r.target, r.mu_cal, r.sigma_cal) for r in due.itertuples()]
    known = {(st, tg) for st, tg, _, _ in cand}
    sig_fb = p.groupby("station").sigma_cal.median().to_dict()
    for st, tg in audit_only_targets(audit, known, today, STATIONS):
        cand.append((st, tg, float("nan"), sig_fb.get(st, 1.5)))
    pairs = [(st, tg) for st, tg, _, _ in cand]
    # IEM por red, fila por fila, hacia que la tarea diaria tardara minutos. La historia local es
    # la fuente validada; solo completar en paralelo las fechas forward que aun no llegaron a obs.csv.
    obs_cache = {}
    try:
        oh = pd.read_csv(os.path.join(D, "obs.csv"), parse_dates=["date"])
        oh["date"] = oh.date.dt.date
        obs_cache = {(r.station, r.date): float(r.tmax) for r in oh.itertuples()}
    except (OSError, ValueError, AttributeError):
        pass
    missing = [k for k in pairs if k not in obs_cache]
    if missing:
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            vals = pool.map(lambda k: fetch_obs_iem(*k), missing)
            obs_cache.update({k: v for k, v in zip(missing, vals) if v is not None})
    resb = resolved_buckets(pairs)
    Cand = collections.namedtuple("Cand", "station target mu_cal sigma_cal")
    recs = []
    for r in (Cand(*c) for c in cand):
        info = resb.get((r.station, r.target))
        if not info or not info["buckets"]:
            continue
        buckets, winner, res = info["buckets"], info["winner"], "mercado"
        obs = obs_cache.get((r.station, r.target))
        if winner is None and r.target < today and obs is not None:
            winner, res = winner_by_temp(buckets, int(math.floor(obs))), "fisica"
        if winner is None:
            continue
        unit = STATIONS[r.station][3]
        mu, sigma, forecast_source = frozen_forecast(
            audit, r.station, r.target, r.mu_cal, r.sigma_cal)
        if forecast_source == "forward-fallback":
            continue  # no acreditar un resultado sin snapshot congelado/reconstruible
        pick = winner_by_temp(buckets, int(math.floor(mu)))
        # FUENTE DE VERDAD (2026-07-21): el top-1/2/3 CONGELADO del freeze — mismo criterio que
        # leaderboard/historiales/telegram. Ranking re-derivado solo como fallback legacy.
        stored = ((audit.get(f"{r.station}|{r.target.isoformat()}") or {}).get("froze") or {}).get("top")
        nivel = I.nivel_vs_top(stored, winner[0], winner[1])
        if nivel is not None:
            pick_lbl = stored[0]
        else:
            probs = [bucket_prob(mu - 0.5, sigma, lo, hi) for lo, hi in buckets]
            order = sorted(range(len(buckets)), key=lambda i: -probs[i])
            rank_w = order.index(buckets.index(winner)) + 1
            exact_f = int(pick == winner)
            nivel = ("EXACTO" if exact_f else ("TOP-2" if rank_w <= 2 else
                     ("TOP-3" if rank_w <= 3 else "PERDIDA")))
            pick_lbl = lbl_of(pick, unit) if pick else "—"
        exact = int(nivel == "EXACTO")
        top2 = int(nivel in ("EXACTO", "TOP-2"))
        top3 = int(nivel in ("EXACTO", "TOP-2", "TOP-3"))
        recs.append(dict(station=r.station, target=r.target, unit=unit, res=res,
                         pick=pick_lbl,
                         win=lbl_of(winner, unit), nivel=nivel, exact=exact, top2=top2, top3=top3,
                         err=(abs(mu - obs) if obs is not None else None),
                         mu=mu, real=obs, forecast_source=forecast_source))
    return recs


NIV_CLS = {"EXACTO": "n-ex", "TOP-2": "n-t2", "TOP-3": "n-t3", "PERDIDA": "n-bad"}
NIV_ICON = {"EXACTO": "✓", "TOP-2": "✓", "TOP-3": "~", "PERDIDA": "✗"}


def scard(lbl, big, sub, cls=""):
    return f'<div class="scard {cls}"><div class="lbl">{lbl}</div><div class="big">{big}</div><div class="sub">{sub}</div></div>'


def pct(a, b):
    return f"{a/b:.0%}" if b else "—"


def section_html(recs, empty_msg, suffix="t24"):
    """Cards KPI + rendimiento dia-por-dia para un set de records (comun a tabs 24h y 48h).
    Las filas llevan data-st/data-cont y las cards viven en #cards-<suffix> para que el filtro
    por continente/ciudad (2026-07-21, pedido Santiago) recalcule todo client-side."""
    n = len(recs)
    ex = sum(r["exact"] for r in recs)
    t2 = sum(r["top2"] for r in recs)
    t3 = sum(r["top3"] for r in recs)
    perd = sum(1 for r in recs if r["nivel"] == "PERDIDA")
    errs = [r["err"] for r in recs if r["err"] is not None]
    mae = sum(errs) / len(errs) if errs else float("nan")
    rmse = (sum(e * e for e in errs) / len(errs)) ** 0.5 if errs else float("nan")
    cards = (
        scard("mercados resueltos", str(n), f"desde {ddmmyyyy(min(r['target'] for r in recs))}" if n else "—") +
        scard("acierto EXACTO", pct(ex, n), f"{ex}/{n} · bucket clavado") +
        scard("acierto TOP-2", pct(t2, n), f"{t2}/{n} · ganador en top-2", "y") +
        scard("acierto TOP-3", pct(t3, n), f"{t3}/{n}", "o") +
        scard("PÉRDIDAS", str(perd), f"de {n} · fuera del top-3", "bad") +
        scard("MAE / RMSE", f"{mae:.2f}°" if mae == mae else "—", f"RMSE {rmse:.2f}°" if rmse == rmse else "")
    )
    days = sorted({r["target"] for r in recs}, reverse=True)
    day_html = []
    CONT = {"Asia": 0, "Europa": 1, "America": 2}
    for d in days:
        drecs = [r for r in recs if r["target"] == d]
        drecs.sort(key=lambda r: (CONT.get(STATION_META.get(r["station"], ("?",))[0], 9), r["station"]))
        dex = sum(r["exact"] for r in drecs); dt2 = sum(r["top2"] for r in drecs)
        dpe = sum(1 for r in drecs if r["nivel"] == "PERDIDA")
        trs = []
        for r in drecs:
            cont, pais, ciudad = STATION_META.get(r["station"], ("?", "?", r["station"]))[:3]
            fis = ' <span class="fis" title="resuelto por obs fisica IEM (mercado sin ganador aun)">·fís</span>' if r["res"] == "fisica" else ""
            trs.append(
                f'<tr data-st="{r["station"]}" data-cont="{cont}">'
                f'<td class="stn">{r["station"]}<span>{ciudad}</span></td>'
                f'<td>{r["pick"]}</td><td class="win">{r["win"]}{fis}</td>'
                f'<td class="verd {NIV_CLS[r["nivel"]]}">{NIV_ICON[r["nivel"]]} {r["nivel"]}</td>'
                f'<td class="num">{("%.1f°"%r["err"]) if r["err"] is not None else "—"}</td></tr>')
        day_html.append(
            f'<div class="daysec"><h3>{fecha_es(d)}'
            f'<span class="droll">{dex} exacto{"s" if dex!=1 else ""} · {dt2} top-2 · '
            f'<b class="dbad">{dpe} pérdida{"s" if dpe!=1 else ""}</b></span></h3>'
            f'<table class="dtab"><thead><tr><th>estación</th><th>pick bot</th><th>ganó</th>'
            f'<th>resultado</th><th class="num">error</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>')
    stats = dict(n=n, ex=ex, t2=t2, t3=t3, perd=perd, mae=mae, days=days)
    return (f'<div class="sgrid" id="cards-{suffix}">{cards}</div>'
            + ("".join(day_html) if day_html else f'<p class="subt">{empty_msg}</p>')), stats


def records48(today):
    """Records del PICK 48H (froze48 del audit, fijado 24h antes del bloqueo normal — pedido
    Santiago 2026-07-16: 'como nos iria apostando mas largo con mejor precio de entrada').
    Acumula desde el 16/07; solo evidencia congelada explicita."""
    import wxbt_insights as I
    rows = I.bot_history(today=today, kind="froze48")
    recs = []
    for r in rows:
        if r["nivel"] is None:
            continue
        recs.append(dict(station=r["station"], target=r["target"], unit=r["unit"], res="mercado",
                         pick=r["pick_lbl"] or "—", win=r["win_lbl"] or "—", nivel=r["nivel"],
                         exact=int(r["nivel"] == "EXACTO"),
                         top2=int(r["nivel"] in ("EXACTO", "TOP-2")),
                         top3=int(r["nivel"] in ("EXACTO", "TOP-2", "TOP-3")),
                         err=(abs(r["mu"] - r["max_real"]) if r.get("max_real") is not None else None),
                         mu=r["mu"], real=r.get("max_real"), forecast_source="froze48"))
    return recs


def main():
    today = dt.date.today()
    print(f"Estadisticas del bot al {today} ...")
    recs = records(today)
    sec24, s24 = section_html(recs, "Sin mercados resueltos todavía — vuelve cuando el día haya cerrado.", "t24")
    recs48 = records48(today)
    sec48, s48 = section_html(
        recs48, "El pick 48h se fija 24h antes que el normal (madrugada del día anterior) y "
                "empezó a capturarse el 16/07/2026 — los primeros resultados aparecen cuando "
                "resuelvan los mercados del 17/07 en adelante.", "t48")
    n, days = s24["n"], s24["days"]
    ex, t2, t3, perd, mae = s24["ex"], s24["t2"], s24["t3"], s24["perd"], s24["mae"]

    # datos minimos para que el filtro por continente/ciudad recalcule las cards client-side
    def _mini(rs):
        return [dict(st=r["station"], cont=STATION_META.get(r["station"], ("?",))[0],
                     niv=r["nivel"], err=(round(r["err"], 2) if r["err"] is not None else None))
                for r in rs]
    conts = sorted({STATION_META.get(r["station"], ("?",))[0] for r in recs + recs48})
    cities = sorted({(r["station"], STATION_META.get(r["station"], ("?", "?", r["station"]))[2])
                     for r in recs + recs48}, key=lambda x: x[1])
    cont_chips = "".join(f'<button class="chip" data-fc="{c}">{c}</button>' for c in conts)
    city_opts = "".join(f'<option value="{st}">{city} · {st}</option>' for st, city in cities)
    payload = json.dumps({"t24": _mini(recs), "t48": _mini(recs48)}, ensure_ascii=False)

    updated = to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")
    body = f'''<div class="viz-root">
<div class="topbar">{nav_html("stats")}<div class="row1"><h1>📊 ESTADÍSTICAS — rendimiento del bot</h1>
<span class="subt">track record vivo vs ganador oficial de Polymarket · crece cada día</span></div>
<div class="updbar">🕒 Actualizada: <b>{updated}</b> (hora Argentina) · se regenera con run_daily y los botones del dashboard</div>
<div class="vfilters" style="margin-top:10px">
<button class="chip on" data-tab="t24">⏱ 24hs — pick fijado 04:30 local</button>
<button class="chip" data-tab="t48">⏳ 48hs — fijado un día antes ({s48["n"]} resueltos)</button>
<span style="width:14px"></span>
<button class="chip on" data-fc="all">Todos</button>{cont_chips}
<select id="f-city" class="citysel"><option value="">Ciudad (todas)</option>{city_opts}</select>
</div></div>
<p class="subt" style="margin:8px 0 12px">En cada mercado, el <b>pick</b> del bot contra
el <b>bucket que ganó</b> — <span class="verd n-ex">✓ EXACTO</span> ·
<span class="verd n-t2">✓ TOP-2</span> · <span class="verd n-t3">~ TOP-3</span> ·
<span class="verd n-bad">✗ PÉRDIDA</span> (nivel = vs el top-1/2/3 CONGELADO al freeze, mismo
criterio que leaderboard e historiales). El tab <b>48hs</b> mide el pick fijado ~44h antes;
acumula desde el 16/07. Los filtros de continente/ciudad recalculan las cards.</p>
<div id="t24">{sec24}</div>
<div id="t48" style="display:none">{sec48}</div>
<p class="subt" style="margin-top:16px">Regenerar: <code>python scripts/stats_page.py</code> o el botón
📊 del dashboard.</p></div>
<script>window.__WXR={payload};</script>
<script>
(function(){{
  var fc='all', fcity='';
  function scard(l,b,s,c){{return '<div class="scard '+(c||'')+'"><div class="lbl">'+l+'</div><div class="big">'+b+'</div><div class="sub">'+s+'</div></div>';}}
  function pct(a,b){{return b?Math.round(100*a/b)+'%':'—';}}
  function recalc(tab){{
    var rs=(window.__WXR[tab]||[]).filter(function(r){{
      return (fc==='all'||r.cont===fc)&&(!fcity||r.st===fcity);}});
    var n=rs.length,ex=0,t2=0,t3=0,pe=0,errs=[];
    rs.forEach(function(r){{
      if(r.niv==='EXACTO')ex++;
      if(r.niv==='EXACTO'||r.niv==='TOP-2')t2++;
      if(r.niv==='EXACTO'||r.niv==='TOP-2'||r.niv==='TOP-3')t3++;
      if(r.niv==='PERDIDA')pe++;
      if(r.err!=null)errs.push(r.err);}});
    var mae=errs.length?errs.reduce(function(a,b){{return a+b;}},0)/errs.length:null;
    var rmse=errs.length?Math.sqrt(errs.reduce(function(a,b){{return a+b*b;}},0)/errs.length):null;
    var el=document.getElementById('cards-'+tab); if(!el)return;
    el.innerHTML=scard('mercados resueltos',n,(fc==='all'&&!fcity)?'todos':'con el filtro aplicado')+
      scard('acierto EXACTO',pct(ex,n),ex+'/'+n+' · bucket clavado')+
      scard('acierto TOP-2',pct(t2,n),t2+'/'+n+' · ganador en top-2','y')+
      scard('acierto TOP-3',pct(t3,n),t3+'/'+n,'o')+
      scard('PÉRDIDAS',pe,'de '+n+' · fuera del top-3','bad')+
      scard('MAE / RMSE',mae!=null?mae.toFixed(2)+'°':'—',rmse!=null?'RMSE '+rmse.toFixed(2)+'°':'');
  }}
  function applyRows(){{
    ['t24','t48'].forEach(function(tab){{
      var box=document.getElementById(tab); if(!box)return;
      box.querySelectorAll('.daysec').forEach(function(sec){{
        var vis=0;
        sec.querySelectorAll('tbody tr').forEach(function(tr){{
          var ok=(fc==='all'||tr.dataset.cont===fc)&&(!fcity||tr.dataset.st===fcity);
          tr.style.display=ok?'':'none'; if(ok)vis++;
        }});
        sec.style.display=vis?'':'none';
        var roll=sec.querySelector('.droll');
        if(roll&&vis){{
          var dex=0,dt2=0,dpe=0;
          sec.querySelectorAll('tbody tr').forEach(function(tr){{
            if(tr.style.display==='none')return;
            var v=tr.querySelector('.verd')||{{className:''}};
            if(v.className.indexOf('n-ex')>=0)dex++;
            if(v.className.indexOf('n-ex')>=0||v.className.indexOf('n-t2')>=0)dt2++;
            if(v.className.indexOf('n-bad')>=0)dpe++;
          }});
          roll.innerHTML=dex+' exacto'+(dex!==1?'s':'')+' · '+dt2+' top-2 · <b class="dbad">'+dpe+' pérdida'+(dpe!==1?'s':'')+'</b>';
        }}
      }});
      recalc(tab);
    }});
  }}
  document.querySelectorAll('.chip[data-tab]').forEach(function(b){{
    b.addEventListener('click',function(){{
      document.querySelectorAll('.chip[data-tab]').forEach(function(x){{x.classList.remove('on');}});
      b.classList.add('on');
      document.getElementById('t24').style.display=(b.dataset.tab==='t24')?'':'none';
      document.getElementById('t48').style.display=(b.dataset.tab==='t48')?'':'none';
      try{{sessionStorage.setItem('wxbt-stab',b.dataset.tab);}}catch(e){{}}
    }});
  }});
  document.querySelectorAll('.chip[data-fc]').forEach(function(b){{
    b.addEventListener('click',function(){{
      document.querySelectorAll('.chip[data-fc]').forEach(function(x){{x.classList.remove('on');}});
      b.classList.add('on'); fc=b.dataset.fc; applyRows();
    }});
  }});
  var cs=document.getElementById('f-city');
  if(cs)cs.addEventListener('change',function(){{fcity=cs.value;applyRows();}});
  // restaurar tab tras auto-refresh + recargar cada 3 min si visible
  try{{var t=sessionStorage.getItem('wxbt-stab');if(t==='t48'){{var b=document.querySelector('.chip[data-tab="t48"]');if(b)b.click();}}}}catch(e){{}}
  setInterval(function(){{ if(!document.hidden) location.reload(); }}, 180000);
}})();
</script>'''

    extra = '''
.viz-root .vfilters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
.viz-root .updbar{font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px;}
.viz-root .updbar b{color:var(--live);}
.viz-root select.citysel{background:var(--s2);color:var(--ink);border:1px solid var(--bd);
  border-radius:6px;padding:5px 9px;font-size:12px;font-family:inherit;}
.viz-root .daysec{margin:16px 0 8px;}
.viz-root .daysec h3{font-size:13px;color:var(--fc);margin:0 0 6px;font-family:var(--mono);
  letter-spacing:.06em;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;}
.viz-root .daysec h3::before{content:"┌─ ";color:var(--base);}
.viz-root .droll{font-size:11px;color:var(--ink2);font-family:var(--mono);}
.viz-root .droll .dbad{color:var(--red);}
.viz-root table.dtab{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:6px;}
.viz-root table.dtab th{font-size:8.5px;color:var(--mut);text-transform:uppercase;text-align:left;
  letter-spacing:.06em;padding:3px 10px;border-bottom:1px solid var(--bd);}
.viz-root table.dtab td{padding:5px 10px;border-bottom:1px solid var(--grid);font-family:var(--mono);}
.viz-root table.dtab td.num,.viz-root table.dtab th.num{text-align:right;}
.viz-root table.dtab tr:hover td{background:var(--s2);}
.viz-root .dtab .stn{font-weight:700;}
.viz-root .dtab .stn span{display:block;font-size:9.5px;color:var(--mut);font-weight:400;font-family:"Segoe UI",sans-serif;}
.viz-root .dtab .win{color:var(--live);}
.viz-root .dtab .fis{font-size:9px;color:var(--mut);}
.viz-root .verd{font-weight:700;font-size:11px;white-space:nowrap;}
.viz-root .verd.n-ex{color:var(--fin);} .viz-root .verd.n-t2{color:#ffd23e;}
.viz-root .verd.n-t3{color:#ff8c42;} .viz-root .verd.n-bad{color:var(--red);}
.viz-root a{color:var(--mkt);}
'''
    html = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Estadísticas</title><style>{CSS}{NAV_CSS}{extra}</style></head><body>{body}</body></html>")
    out = os.path.abspath(os.path.join(D, "stats.html"))
    open(out, "w", encoding="utf-8").write(html)
    print(f"Stats -> {out}")
    print(f"General: n={n} exacto {pct(ex,n)} top2 {pct(t2,n)} top3 {pct(t3,n)} perdidas {perd} "
          f"MAE {mae:.2f}" if n else "General: sin resueltos aun")
    for d in days:
        dr = [r for r in recs if r["target"] == d]
        print(f"  {d}: {sum(x['exact'] for x in dr)} exactos, "
              f"{sum(x['top2'] for x in dr)} top2, {sum(1 for x in dr if x['nivel']=='PERDIDA')} perdidas "
              f"(n={len(dr)})")


if __name__ == "__main__":
    main()
