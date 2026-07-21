#!/usr/bin/env python3
# scripts/leaderboard.py — TRACK RECORD VIVO: ranking de estaciones por los RESULTADOS REALES del
# bot contra el bucket ganador oficial de Polymarket (data/predictions_forward.csv, targets
# 2026-07-08 -> hoy, crece cada dia). "Si Tokio acerto 2 exactos, va top 1."
# Genera data/leaderboard.html (tab aparte, linkeado desde el dashboard).
#
# [2026-07-13, pedido Santiago] Cada fila es CLICKEABLE -> despliega un GAMELOG por ciudad estilo
# app de apuestas (Fecha | Ganó WU | Pick bot | Resultado ✅/❌).
# [2026-07-21, pedidos Santiago]:
#   * el NIVEL sale del top-1/2/3 CONGELADO (froze['top'] via wxbt_insights.nivel_vs_top) — antes
#     cada vista re-derivaba su ranking y el historial de ciudad podia contradecir al leaderboard.
#   * tab 48hs (froze48) al lado del 24hs.
#   * "p ganador" = % de veces que el TOP-2 congelado gano (ej. 10/11 = 90.9%).
#   * filtros por continente + orden clickeando los encabezados EXACTOS/TOP-2.
#   * el boton 🔄 dispara do=results (regenera TAMBIEN historiales de ciudad y stats — coherencia).
import collections
import json
import math
import os
import sys
import datetime as dt
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dashboard import STATION_META, CSS, ddmmyyyy, to_art, STATIONS               # noqa: E402
from check_predictions import resolved_buckets, fetch_obs_iem, winner_by_temp     # noqa: E402
from wxbt.market import bucket_prob                                               # noqa: E402
from wxbt.forward_scoring import frozen_forecast, audit_only_targets              # noqa: E402
from wxbt_nav import nav_html, NAV_CSS                                            # noqa: E402
import wxbt_insights as I                                                         # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "data")


def lbl_of(bucket, unit):
    """(lo,hi) -> etiqueta legible del bucket, coherente con el mercado."""
    if bucket is None:
        return "—"
    lo, hi = bucket
    deg = "°F" if unit == "F" else "°C"
    if lo is None:
        return f"≤{hi}{deg}"
    if hi is None:
        return f"≥{lo}{deg}"
    return (f"{lo}-{hi}{deg}" if lo != hi else f"{lo}{deg}")


def live_records(today):
    """DataFrame por (station, target) YA RESUELTO con exact/top2/top3/pwin/res + labels de pick y
    ganador y nivel (para el gamelog por ciudad). El nivel usa el top CONGELADO cuando existe."""
    p = pd.read_csv(os.path.join(D, "predictions_forward.csv"), parse_dates=["target"])
    p["target"] = p["target"].dt.date
    due = p[p["target"] <= today].sort_values("lead_h").drop_duplicates(
        ["station", "target"], keep="first")  # fallback si el audit historico no existe
    try:
        with open(os.path.join(D, "forecast_audit.json"), encoding="utf-8") as fh:
            audit = json.load(fh)
    except (OSError, ValueError):
        audit = {}
    # UNIVERSO = acumulador UNION audit congelado: el acumulador puede perderse un dia entero
    # (Asia/NZ post-pico, primer dia de una ciudad nueva) aunque el freeze exista en el audit.
    cand = [(r.station, r.target, r.mu_cal, r.sigma_cal) for r in due.itertuples()]
    known = {(st, tg) for st, tg, _, _ in cand}
    sig_fb = p.groupby("station").sigma_cal.median().to_dict()   # sigma fallback filas solo-audit
    for st, tg in audit_only_targets(audit, known, today, STATIONS):
        cand.append((st, tg, float("nan"), sig_fb.get(st, 1.5)))
    resb = resolved_buckets([(st, tg) for st, tg, _, _ in cand])
    Cand = collections.namedtuple("Cand", "station target mu_cal sigma_cal")
    recs = []
    for r in (Cand(*c) for c in cand):
        info = resb.get((r.station, r.target))
        if not info or not info["buckets"]:
            continue
        buckets, winner, res = info["buckets"], info["winner"], "mercado"
        if winner is None and r.target < today:                 # fisica solo con el dia completo
            obs = fetch_obs_iem(r.station, r.target)
            if obs is not None:
                winner, res = winner_by_temp(buckets, int(math.floor(obs))), "fisica"
        if winner is None:
            continue                                            # todavia no resuelto -> no cuenta
        unit = STATIONS[r.station][3]
        mu, sigma, forecast_source = frozen_forecast(
            audit, r.station, r.target, r.mu_cal, r.sigma_cal)
        if forecast_source == "forward-fallback":
            continue  # sin evidencia point-in-time del pick operable: no entra al KPI oficial
        pick = winner_by_temp(buckets, int(math.floor(mu)))
        probs = [bucket_prob(mu - 0.5, sigma, lo, hi) for lo, hi in buckets]
        pwin = probs[buckets.index(winner)]
        # FUENTE DE VERDAD: el top-1/2/3 congelado en el freeze (mismo criterio en historiales,
        # stats y telegram). El ranking re-derivado por probabilidad queda SOLO para legacy.
        stored = ((audit.get(f"{r.station}|{r.target.isoformat()}") or {}).get("froze") or {}).get("top")
        nivel = I.nivel_vs_top(stored, winner[0], winner[1])
        if nivel is not None:
            pick_lbl = stored[0]
        else:
            order = sorted(range(len(buckets)), key=lambda i: -probs[i])
            rank_w = order.index(buckets.index(winner)) + 1     # 1 = nuestro bucket mas probable
            exact_f = int(pick == winner)
            nivel = ("EXACTO" if exact_f else ("TOP-2" if rank_w <= 2 else
                     ("TOP-3" if rank_w <= 3 else "PERDIDA")))
            pick_lbl = lbl_of(pick, unit)
        exact = int(nivel == "EXACTO")
        top2 = int(nivel in ("EXACTO", "TOP-2"))
        top3 = int(nivel in ("EXACTO", "TOP-2", "TOP-3"))
        recs.append(dict(station=r.station, target=r.target, res=res, exact=exact,
                         top2=top2, top3=top3, pwin=pwin, nivel=nivel,
                         pick_lbl=pick_lbl, win_lbl=lbl_of(winner, unit),
                         forecast_source=forecast_source))
    return pd.DataFrame(recs)


def records48(today):
    """Records del pick 48H (froze48, scoreado con SU top congelado) en el mismo formato."""
    recs = []
    for r in I.bot_history(today=today, kind="froze48"):
        if r["nivel"] is None:
            continue
        recs.append(dict(station=r["station"], target=r["target"], res="mercado",
                         exact=int(r["nivel"] == "EXACTO"),
                         top2=int(r["nivel"] in ("EXACTO", "TOP-2")),
                         top3=int(r["nivel"] in ("EXACTO", "TOP-2", "TOP-3")),
                         pwin=r.get("pwin"), nivel=r["nivel"],
                         pick_lbl=r["pick_lbl"] or "—", win_lbl=r["win_lbl"] or "—",
                         forecast_source="froze48"))
    return pd.DataFrame(recs)


NIV_ICON = {"EXACTO": "✅", "TOP-2": "✅", "TOP-3": "🔶", "PERDIDA": "❌"}
NIV_CLS = {"EXACTO": "g-ex", "TOP-2": "g-t2", "TOP-3": "g-t3", "PERDIDA": "g-bad"}


def build_rows(df, lab):
    """[{st, ciudad, cont, n, ex, t2, t3, lab}] ordenado + gamelog por estacion."""
    stations = sorted(set(df.station.unique() if len(df) else []) | set(lab) | set(STATIONS))
    rows, gamelog = [], {}
    for st in stations:
        g = df[df.station == st] if len(df) else pd.DataFrame()
        n = len(g)
        ex = int(g.exact.sum()) if n else 0
        t2 = int(g.top2.sum()) if n else 0
        t3 = int(g.top3.sum()) if n else 0
        nfis = int((g.res == "fisica").sum()) if n else 0
        cont, pais, ciudad = STATION_META.get(st, ("?", "?", st))[:3]
        rows.append(dict(st=st, ciudad=ciudad, cont=cont, n=n, ex=ex, t2=t2, t3=t3,
                         nfis=nfis, lab=lab.get(st, float("nan"))))
        gl = []
        for r in (g.sort_values("target", ascending=False).itertuples() if n else []):
            gl.append({"d": ddmmyyyy(r.target), "win": r.win_lbl, "pick": r.pick_lbl,
                       "niv": r.nivel, "fis": (r.res == "fisica")})
        gamelog[st] = gl
    rows.sort(key=lambda r: (-r["ex"], -r["t2"], -(r["t2"] / r["n"] if r["n"] else 0),
                             -r["t3"], r["st"]))
    return rows, gamelog


def table_html(rows, gamelog, suffix, with_lab=True):
    """Tabla + gamelogs embebidos. suffix distingue ids/data-attrs entre tabs 24/48."""
    def cls_of(r):
        if not r["n"]:
            return "wait"
        if r["ex"] / r["n"] >= 0.5:
            return "top"
        return "ok" if r["ex"] >= 1 else "bad"

    def pct(x):
        return f"{x:.0%}" if x == x else "&mdash;"

    trs = []
    for i, r in enumerate(rows, 1):
        cls = cls_of(r)
        has_gl = len(gamelog.get(r["st"], [])) > 0
        caret = '<span class="caret">▸</span>' if has_gl else '<span class="caret" style="opacity:.2">·</span>'
        pgan = f"{r['t2'] / r['n']:.1%}" if r["n"] else "&mdash;"
        exr = (r["ex"] / r["n"]) if r["n"] else -1
        t2r = (r["t2"] / r["n"]) if r["n"] else -1
        lab_td = f'<td class="num lab">{pct(r["lab"])}</td>' if with_lab else ""
        trs.append(
            f'<tr class="lbrow {cls}" data-st="{r["st"]}{suffix}" data-cont="{r["cont"]}" '
            f'data-has="{1 if has_gl else 0}" data-ex="{r["ex"]}" data-exr="{exr:.4f}" '
            f'data-t2="{r["t2"]}" data-t2r="{t2r:.4f}" data-n="{r["n"]}">'
            f'<td class="rk">{i}</td>'
            f'<td class="stn">{caret}{r["st"]}<span>{r["ciudad"]} · {r["cont"]}</span></td>'
            f'<td class="num big">{r["ex"]}/{r["n"]}</td>'
            f'<td class="num">{r["t2"]}/{r["n"]}</td>'
            f'<td class="num">{r["t3"]}/{r["n"]}</td>'
            f'<td class="num pg">{pgan}</td>'
            f'<td class="num">{r["n"]}</td>{lab_td}</tr>')
        if has_gl:
            gl_rows = "".join(
                f'<tr><td class="gd">{e["d"]}</td>'
                f'<td class="gw">{e["win"]}{" ·fís" if e["fis"] else ""}</td>'
                f'<td class="gp">{e["pick"]}</td>'
                f'<td class="gv {NIV_CLS[e["niv"]]}">{NIV_ICON[e["niv"]]} {e["niv"]}</td></tr>'
                for e in gamelog[r["st"]])
            ncols = 8 if with_lab else 7
            trs.append(
                f'<tr class="glrow hidden" data-for="{r["st"]}{suffix}" data-cont="{r["cont"]}">'
                f'<td></td><td colspan="{ncols - 1}">'
                f'<div class="glbox"><div class="glhead">📊 GAMELOG {r["st"]} · {r["ciudad"]} '
                f'— {r["ex"]} exacto{"s" if r["ex"] != 1 else ""} · {r["t2"]} top-2 · {r["t3"]} top-3 '
                f'de {r["n"]}</div>'
                f'<table class="gl"><thead><tr><th>fecha</th><th>ganó (WU)</th><th>pick bot</th>'
                f'<th>resultado</th></tr></thead><tbody>{gl_rows}</tbody></table></div></td></tr>')
    lab_th = '<th>lab 60d</th>' if with_lab else ''
    return (f'<table class="lb"><thead><tr><th>#</th><th>estación</th>'
            f'<th class="sortable" data-sort="exr">EXACTOS ⇅</th>'
            f'<th class="sortable" data-sort="t2r">TOP-2 ⇅</th>'
            f'<th>TOP-3</th><th>p ganador</th><th>n</th>{lab_th}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')


def main():
    today = dt.date.today()
    print(f"Track record vivo al {today} (consultando ganadores en Gamma)...")
    df = live_records(today)
    df48 = records48(today)
    try:
        bf = pd.read_csv(os.path.join(D, "backfill_check.csv"))
        bf = bf[(bf.lead == 2) & bf.max_real.notna()]
        lab = bf.groupby("station").hit_cal.mean().to_dict()
    except Exception:
        lab = {}
    rows, gamelog = build_rows(df, lab)
    rows48, gamelog48 = build_rows(df48, {})
    conts = sorted({r["cont"] for r in rows})

    if len(df):
        d0, d1 = min(df.target), max(df.target)
        rango = f"{ddmmyyyy(d0)}&ndash;{ddmmyyyy(d1)}"
        rango_txt = f"{ddmmyyyy(d0)}..{ddmmyyyy(d1)}"
    else:
        rango = rango_txt = "sin targets resueltos aun"
    nfis_tot = sum(r["nfis"] for r in rows)
    nota_fis = (f" ({nfis_tot} resuelto{'s' if nfis_tot != 1 else ''} por obs IEM, "
                f"mercado sin ganador publicado)") if nfis_tot else ""
    updated = to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")
    n48 = int(df48.shape[0]) if len(df48) else 0
    cont_chips = "".join(f'<button class="chip" data-cont="{c}">{c}</button>' for c in conts)

    body = f'''<div class="viz-root">
<div class="topbar">{nav_html("leaderboard")}<div class="row1"><h1>🏆 Track record VIVO del bot — WXBT</h1>
<span class="subt">ranking por RESULTADOS REALES contra el bucket ganador oficial de Polymarket
· targets resueltos {rango}{nota_fis}</span>
<button class="qbtn" id="lb-refresh" style="margin-left:auto"
  data-tip="re-consulta los ganadores en Gamma y regenera leaderboard + historiales de ciudad + estadísticas (todo junto, coherente)">🔄 Actualizar resultados</button></div>
<div class="updbar">🕒 Tabla actualizada: <b>{updated}</b> (hora Argentina) · se regenera cada corrida ·
<span id="lb-msg"></span></div>
<div class="vfilters" style="margin-top:10px">
<button class="chip on" data-tab="lb24">⏱ 24hs</button>
<button class="chip" data-tab="lb48">⏳ 48hs ({n48} resueltos)</button>
<span style="width:14px"></span>
<button class="chip on" data-cont="all">Todos los continentes</button>{cont_chips}
</div></div>
<p class="subt" style="margin:8px 0 14px"><b>Clic en cualquier fila</b> para el GAMELOG de esa
ciudad — <span class="gv g-ex">✅ EXACTO</span> · <span class="gv g-t2">✅ TOP-2</span> ·
<span class="gv g-t3">🔶 TOP-3</span> · <span class="gv g-bad">❌ PÉRDIDA</span>.
El nivel se scorea contra el <b>top-1/2/3 CONGELADO</b> en el freeze (mismo criterio que el
historial de cada ciudad y estadísticas). <b>p ganador</b> = % de mercados donde ganó el top-2
congelado. Clic en <b>EXACTOS</b> o <b>TOP-2</b> ordena por ese %. <b>lab 60d</b> = hit del
backfill (referencia).</p>
<div id="lb24">{table_html(rows, gamelog, "", with_lab=True)}</div>
<div id="lb48" style="display:none">
<p class="subt" style="margin:0 0 8px">Pick fijado ~44h antes del cierre (entrada más temprana =
mejor precio) — acumula desde el 16/07.</p>
{table_html(rows48, gamelog48, "@48", with_lab=False)}</div>
<p class="subt" style="margin-top:14px">Pick oficial = top-1 congelado 04:30 local. Con pocos días
esto es indicativo, no veredicto. Regenerar: <code>python scripts/leaderboard.py</code></p></div>'''

    extra_css = '''
.viz-root .vfilters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
.viz-root table.lb{border-collapse:collapse;width:100%;font-size:13px;}
.viz-root table.lb th{font-size:10px;color:var(--mut);text-transform:uppercase;text-align:right;
  padding:6px 10px;border-bottom:1px solid var(--bd);letter-spacing:.04em;}
.viz-root table.lb th:nth-child(-n+2){text-align:left;}
.viz-root table.lb th.sortable{cursor:pointer;color:var(--ink2);}
.viz-root table.lb th.sortable:hover{color:var(--fc);}
.viz-root table.lb th.sortable.on{color:var(--fc);}
.viz-root table.lb td{padding:8px 10px;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;}
.viz-root table.lb td.num{text-align:right;}
.viz-root table.lb td.big{font-size:17px;font-weight:700;}
.viz-root table.lb td.pg{color:var(--fc);font-weight:700;}
.viz-root table.lb td.lab{font-size:11px;color:var(--mut);}
.viz-root tr.lbrow[data-has="1"]{cursor:pointer;}
.viz-root tr.lbrow[data-has="1"]:hover td{background:var(--s2);}
.viz-root tr.lbrow.open td{background:var(--s2);}
.viz-root .caret{display:inline-block;width:12px;color:var(--mut);font-size:10px;transition:transform .12s;}
.viz-root tr.lbrow.open .caret{transform:rotate(90deg);color:var(--fc);}
.viz-root .rk{color:var(--mut);font-weight:700;}
.viz-root .stn{font-weight:700;}
.viz-root .stn span{display:block;font-size:10.5px;color:var(--mut);font-weight:400;margin-left:12px;}
.viz-root tr.top td.big{color:var(--fin);} .viz-root tr.bad td.big{color:#d03b3b;}
.viz-root tr.wait td{color:var(--mut);} .viz-root tr.wait td.big{color:var(--mut);font-weight:400;}
.viz-root .updbar{font-size:11px;color:var(--ink2);font-family:var(--mono);margin-top:4px;}
.viz-root .updbar b{color:var(--live);}
.viz-root tr.glrow.hidden{display:none;}
.viz-root .glbox{padding:6px 4px 12px 22px;}
.viz-root .glhead{font-size:11px;color:var(--fc);font-family:var(--mono);letter-spacing:.05em;margin-bottom:6px;}
.viz-root table.gl{border-collapse:collapse;width:100%;max-width:560px;font-size:12px;}
.viz-root table.gl th{font-size:9px;color:var(--mut);text-transform:uppercase;text-align:left;
  padding:3px 10px;border-bottom:1px solid var(--bd);letter-spacing:.05em;}
.viz-root table.gl td{padding:5px 10px;border-bottom:1px solid var(--grid);font-family:var(--mono);}
.viz-root .gl .gd{color:var(--ink2);} .viz-root .gl .gw{color:var(--live);font-weight:700;}
.viz-root .gl .gp{color:var(--ink);}
.viz-root .gv{font-weight:700;white-space:nowrap;}
.viz-root .gv.g-ex{color:var(--fin);} .viz-root .gv.g-t2{color:#ffd23e;}
.viz-root .gv.g-t3{color:#ff8c42;} .viz-root .gv.g-bad{color:#d03b3b;}
.viz-root a{color:var(--mkt);}
'''
    lb_js = '''<script>
(function(){
  // gamelog desplegable
  document.querySelectorAll('tr.lbrow[data-has="1"]').forEach(function(row){
    row.addEventListener('click',function(){
      var st=row.dataset.st;
      var det=document.querySelector('tr.glrow[data-for="'+st+'"]');
      if(!det) return;
      var open=det.classList.toggle('hidden')===false;
      row.classList.toggle('open',open);
    });
  });
  // tabs 24/48
  document.querySelectorAll('.chip[data-tab]').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.chip[data-tab]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on');
      document.getElementById('lb24').style.display=(b.dataset.tab==='lb24')?'':'none';
      document.getElementById('lb48').style.display=(b.dataset.tab==='lb48')?'':'none';
      try{sessionStorage.setItem('wxbt-lbtab',b.dataset.tab);}catch(e){}
    });
  });
  try{var t=sessionStorage.getItem('wxbt-lbtab');if(t==='lb48'){var b=document.querySelector('.chip[data-tab="lb48"]');if(b)b.click();}}catch(e){}
  // filtro por continente (aplica a ambas tablas, renumera el ranking visible)
  var cont='all';
  function applyCont(){
    ['lb24','lb48'].forEach(function(id){
      var box=document.getElementById(id); if(!box)return;
      var i=0;
      box.querySelectorAll('tr.lbrow').forEach(function(r){
        var ok=(cont==='all'||r.dataset.cont===cont);
        r.style.display=ok?'':'none';
        var det=box.querySelector('tr.glrow[data-for="'+r.dataset.st+'"]');
        if(det&&!ok)det.classList.add('hidden');
        if(det)det.style.display=ok?'':'none';
        if(ok){i++;var rk=r.querySelector('.rk');if(rk)rk.textContent=i;r.classList.remove('open');}
      });
    });
  }
  document.querySelectorAll('.chip[data-cont]').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.chip[data-cont]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on'); cont=b.dataset.cont; applyCont();
    });
  });
  // orden clickeable por EXACTOS / TOP-2 (%): reordena pares fila+gamelog
  var sortKey=null, sortAsc=false;
  document.querySelectorAll('th.sortable').forEach(function(th){
    th.addEventListener('click',function(){
      var key=th.dataset.sort;
      if(sortKey===key){sortAsc=!sortAsc;}else{sortKey=key;sortAsc=false;}
      document.querySelectorAll('th.sortable').forEach(function(x){x.classList.remove('on');});
      document.querySelectorAll('th.sortable[data-sort="'+key+'"]').forEach(function(x){x.classList.add('on');});
      var tb=th.closest('table').querySelector('tbody');
      var pairs=[];
      tb.querySelectorAll('tr.lbrow').forEach(function(r){
        pairs.push([r, tb.querySelector('tr.glrow[data-for="'+r.dataset.st+'"]')]);
      });
      pairs.sort(function(a,b){var va=+a[0].dataset[key],vb=+b[0].dataset[key];return sortAsc?(va-vb):(vb-va);});
      pairs.forEach(function(p){tb.appendChild(p[0]);if(p[1])tb.appendChild(p[1]);});
      applyCont();
    });
  });
  // Refresh: do=results — regenera leaderboard + historiales de ciudad + stats con el MISMO
  // cache de ganadores (coherencia entre vistas, pedido Santiago 2026-07-21).
  var btn=document.getElementById('lb-refresh'), msg=document.getElementById('lb-msg');
  if(btn) btn.addEventListener('click',function(){
    if(location.protocol==='file:'){ msg.textContent='abrí el dashboard servido (http) para usar el refresh en vivo'; return; }
    btn.classList.add('busy'); btn.disabled=true; msg.textContent='actualizando resultados… (~1-2 min)';
    fetch('/action?do=results',{method:'POST'}).then(function(r){
        var ct=(r.headers&&r.headers.get&&r.headers.get('content-type'))||'';
        if(!r.ok||ct.indexOf('application/json')<0){ throw new Error('abrí el dashboard con --serve (http://…:8765) para usar el refresh en vivo'); }
        return r.json();
      })
      .then(function(j){ msg.textContent=(j.ok?'✓ ':'✗ ')+(j.msg||''); setTimeout(function(){location.reload();},800); })
      .catch(function(e){ btn.classList.remove('busy'); btn.disabled=false; msg.textContent=(e&&e.message)||(''+e); });
  });
  setInterval(function(){ if(!document.hidden) location.reload(); }, 180000);
})();
</script>'''
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>WXBT · Track record vivo</title>"
            f"<style>{CSS}{NAV_CSS}{extra_css}</style></head><body>{body}{lb_js}</body></html>")
    out = os.path.abspath(os.path.join(D, "leaderboard.html"))
    open(out, "w", encoding="utf-8").write(html)
    print(f"Leaderboard -> {out}   (actualizado {updated} AR)")
    print(f"Ranking vivo 24h ({rango_txt}, n = targets resueltos):")
    for i, r in enumerate(rows, 1):
        pg = f"{r['t2'] / r['n']:.1%}" if r["n"] else "-"
        labs = f"{r['lab']:.0%}" if r["lab"] == r["lab"] else "-"
        print(f"  {i:2d}. {r['st']} ({r['ciudad']}): exactos {r['ex']}/{r['n']}  "
              f"top2 {r['t2']}/{r['n']}  top3 {r['t3']}/{r['n']}  p-ganador {pg}  lab60d {labs}")
    if len(df):
        src = df["forecast_source"].value_counts().to_dict()
        print("Fuente temporal del score: " + ", ".join(f"{k}={v}" for k, v in src.items()))
    if n48:
        print(f"Tab 48hs: {n48} resueltos.")


if __name__ == "__main__":
    main()
