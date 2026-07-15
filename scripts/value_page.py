#!/usr/bin/env python3
# scripts/value_page.py — TAB DEDICADO DE VALUE BETS (pedido Santiago 2026-07-15: "para las value
# bets crea un tab mejor"). Genera data/value.html: donde el bot ve MAS ventaja contra lo que paga
# el mercado, como cards ordenadas por edge, con filtros por tier y jugada.
#
# Edge BRUTO = pbot(pick congelado) − mid del mercado, SIN fees/spread/shrink — es un SCREENER, no
# una señal. Reglas del playbook horneadas: solo FUERTES operables, maker, entrada temprana;
# excluye buckets ya imposibles por la obs viva y mercados con el pico ya pasado.
import argparse
import html
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wxbt_insights as I                                     # noqa: E402
import dashboard as D                                          # noqa: E402
from wxbt_nav import nav_html, NAV_CSS                         # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT = os.path.join(DATA, "value.html")

TIER_META = {"FUERTE": ("🟢", "var(--fin)", "operable"),
             "MEDIA":  ("🟡", "var(--t2)", "en observacion"),
             "DEBIL":  ("🔴", "var(--red)", "NO operar (sin fuente local)")}

EXTRA_CSS = """
.viz-root .vgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;margin-top:14px;}
.viz-root .vb{position:relative;background:linear-gradient(180deg,var(--s1),#0b1119);border:1px solid var(--bd);
  border-radius:var(--r);padding:14px 15px;box-shadow:var(--sh-1);transition:transform .16s,border-color .16s,box-shadow .16s;overflow:hidden;}
.viz-root .vb::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--tcol,var(--base));}
.viz-root .vb:hover{transform:translateY(-2px);border-color:var(--base);box-shadow:var(--sh-2);}
.viz-root .vb-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:10px;}
.viz-root .vb-city{font-size:15px;font-weight:700;font-family:var(--mono);}
.viz-root .vb-city a{color:var(--ink);} .viz-root .vb-city a:hover{color:var(--mkt);}
.viz-root .vb-sub{font-size:10.5px;color:var(--mut);margin-top:2px;}
.viz-root .vb-edge{text-align:right;white-space:nowrap;}
.viz-root .vb-edge .n{font-size:26px;font-weight:800;font-family:var(--mono);color:var(--fc);line-height:1;letter-spacing:-.02em;}
.viz-root .vb-edge .u{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;}
.viz-root .vb-play{font-size:12.5px;color:var(--ink);background:var(--s2);border:1px solid var(--grid);
  border-radius:var(--r-sm);padding:8px 11px;margin-bottom:9px;line-height:1.4;}
.viz-root .vb-play b{color:var(--fc);}
.viz-root .vb-cmp{display:flex;gap:8px;align-items:center;font-size:11px;font-family:var(--mono);color:var(--ink2);margin-bottom:8px;}
.viz-root .vb-bars{flex:1;display:flex;flex-direction:column;gap:3px;}
.viz-root .vb-bar{height:9px;border-radius:3px;background:var(--grid);overflow:hidden;position:relative;}
.viz-root .vb-bar span{display:block;height:100%;border-radius:3px;}
.viz-root .vb-bar .bot{background:var(--fc);box-shadow:0 0 8px -1px var(--fcs);}
.viz-root .vb-bar .mkt{background:var(--mkt);box-shadow:0 0 8px -1px rgba(66,201,255,.5);}
.viz-root .vb-cmp .lg{min-width:118px;}
.viz-root .vb-cmp .lg i{font-style:normal;} .viz-root .vb-cmp .lg .cbot{color:var(--fc);} .viz-root .vb-cmp .lg .cmkt{color:var(--mkt);}
.viz-root .vb-long{font-size:11px;color:var(--live);margin-top:4px;}
.viz-root .vb-foot{display:flex;justify-content:space-between;align-items:center;margin-top:9px;font-size:11px;}
.viz-root .vb-foot a{color:var(--mkt);font-weight:600;}
.viz-root .vb-tier{font-size:9.5px;font-family:var(--mono);font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:2px 8px;border-radius:999px;border:1px solid var(--bd);}
.viz-root .vfilters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px;}
.viz-root .none{color:var(--mut);font-style:italic;padding:20px 0;}
"""


def esc(s):
    return html.escape(str(s), quote=False)


def bar_row(label, bot, mkt):
    b = max(2, min(100, bot * 100))
    m = max(2, min(100, (mkt or 0) * 100))
    return (f'<div class="vb-cmp"><span class="lg">{label} '
            f'<i class="cbot">{bot:.0%}</i> / <i class="cmkt">{(mkt or 0):.2f}</i></span>'
            f'<div class="vb-bars"><div class="vb-bar"><span class="bot" style="width:{b:.0f}%"></span></div>'
            f'<div class="vb-bar"><span class="mkt" style="width:{m:.0f}%"></span></div></div></div>')


def card(r):
    ico, col, tnote = TIER_META.get(r["tier"], ("·", "var(--base)", ""))
    unit = r["unit"]
    edge = r["edge1"] * 100
    # jugada recomendada (misma logica que el playbook/panel)
    if r["edge1"] >= 0.10 and r["pbot1"] >= 0.35:
        play = f'Comprar <b>top-1 {esc(r["t1"])}</b> · maker/limit'
        show_edge = edge
    elif r["t2"] and r["pair_edge"] >= 0.12:
        play = f'Comprar <b>par top-2</b> {esc(r["t1"])} + {esc(r["t2"])} · maker'
        show_edge = r["pair_edge"] * 100
    elif r["longshots"]:
        lab, px, pb = r["longshots"][0]
        play = f'🎯 <b>Longshot {esc(lab)}</b> @{px:.2f} (bot {pb:.0%}) · size chico'
        show_edge = (pb - px) * 100
    else:
        play = f'Top-1 <b>{esc(r["t1"])}</b> (edge chico, mirar)'
        show_edge = edge
    lock = "🔒 congelado" if r["frozen"] else "◷ snapshot"
    cmp_html = bar_row("bot / mkt", r["pbot1"], r["px1"])
    longs = ""
    extra = [l for l in r["longshots"] if not (r["longshots"] and l == r["longshots"][0]
             and "Longshot" in play)]
    if extra:
        longs = ('<div class="vb-long">🎯 ' +
                 " · ".join(f'{esc(l)} @{p:.2f} (bot {pb:.0%})' for l, p, pb in extra[:2]) + '</div>')
    return (f'<div class="vb" style="--tcol:{col}" data-tier="{r["tier"]}" '
            f'data-city="{esc(r["city"]).lower()}">'
            f'<div class="vb-top"><div><div class="vb-city">'
            f'<a href="{r["url"]}" target="_blank">{esc(r["city"])} · {r["station"]}</a></div>'
            f'<div class="vb-sub">{r["date"].strftime("%d/%m")} · {lock} · μ {r["mu"]:.1f}°{unit}</div></div>'
            f'<div class="vb-edge"><div class="n">{show_edge:+.0f}¢</div><div class="u">edge bruto</div></div></div>'
            f'<div class="vb-play">{play}</div>{cmp_html}{longs}'
            f'<div class="vb-foot"><span class="vb-tier" style="color:{col}">{ico} {r["tier"]} · {tnote}</span>'
            f'<a href="{r["url"]}" target="_blank">Polymarket ↗</a></div></div>')


def main(a):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    print("Calculando value bets (mercado vivo)...")
    vb = I.value_bets(today=today, horizon=1)
    hits = [r for r in vb if r["value"]]
    hits.sort(key=lambda r: -max(r["edge1"], r["pair_edge"]))
    updated = D.to_art(dt.datetime.now(dt.timezone.utc)).strftime("%d/%m/%Y %H:%M")

    n_f = sum(r["tier"] == "FUERTE" for r in hits)
    n_m = sum(r["tier"] == "MEDIA" for r in hits)
    n_d = sum(r["tier"] == "DEBIL" for r in hits)
    kpis = (
        f'<div class="sgrid" style="max-width:640px">'
        f'<div class="scard"><div class="lbl">value bets ahora</div><div class="big">{len(hits)}</div>'
        f'<div class="sub">de {len(vb)} mercados vivos</div></div>'
        f'<div class="scard"><div class="lbl">🟢 fuertes</div><div class="big" style="color:var(--fin)">{n_f}</div>'
        f'<div class="sub">operables</div></div>'
        f'<div class="scard y"><div class="lbl">🟡 medias</div><div class="big">{n_m}</div>'
        f'<div class="sub">en observacion</div></div>'
        f'<div class="scard bad"><div class="lbl">🔴 debiles</div><div class="big">{n_d}</div>'
        f'<div class="sub">no operar</div></div></div>')

    cards = "".join(card(r) for r in hits) if hits else \
        '<p class="none">Sin value bets ahora — ningún top-1 con Δ¢ ≥ +10, par ≥ +12 ni longshot vivo. Volvé cuando lleguen corridas nuevas (madrugada / mañana AR).</p>'

    body = f"""<div class="viz-root">
<div class="topbar">{nav_html("value")}<div class="row1"><h1>💰 Value bets</h1>
<span class="subt">donde el bot ve más ventaja vs lo que paga el mercado</span>
<span class="clock" style="margin-left:auto">{updated}<small>AR</small></span></div>
<div class="vfilters">
<button class="chip on" data-f="all">Todas</button>
<button class="chip" data-f="FUERTE">🟢 Fuertes</button>
<button class="chip" data-f="MEDIA">🟡 Medias</button>
<button class="chip" data-f="DEBIL">🔴 Débiles</button>
<input type="search" id="vsearch" placeholder="buscar ciudad…"
  style="background:var(--s2);color:var(--ink);border:1px solid var(--bd);border-radius:6px;padding:6px 10px;font-size:12px;margin-left:auto">
<span class="count" id="vcount"></span></div></div>
{kpis}
<p class="subt" style="max-width:900px;margin:10px 0 0">
<b>Δ¢ = probabilidad del bot − precio del mercado</b>, edge BRUTO (sin fees/spread/shrink) — es un
<b>screener, NO una señal</b>. Reglas del playbook: entrar <b>temprano</b> (apenas está la corrida,
≥24h al cierre), <b>maker</b> (limit al mid), size chico, concentrar top-1 o el par top-2. Solo
🟢 <b>fuertes</b> son operables; 🔴 débiles no (sin fuente local). Buckets ya imposibles por la
obs viva y mercados con el pico pasado quedan afuera.</p>
<div class="vgrid" id="vgrid">{cards}</div>
<p class="subt" style="margin-top:18px">Regenerar: <code>python scripts/value_page.py</code> ·
en el dashboard, botón <b>🏙 Regenerar páginas</b>.</p></div>"""

    vjs = """<script>
(function(){
  var grid=document.getElementById('vgrid'), cnt=document.getElementById('vcount');
  var srch=document.getElementById('vsearch'), tier='all';
  function apply(){
    var q=(srch.value||'').trim().toLowerCase(), n=0;
    grid.querySelectorAll('.vb').forEach(function(c){
      var okt=(tier==='all'||c.dataset.tier===tier);
      var okq=(!q||c.dataset.city.indexOf(q)>=0);
      var show=okt&&okq; c.style.display=show?'':'none'; if(show)n++;
    });
    cnt.textContent=n+' mostradas';
  }
  document.querySelectorAll('.chip[data-f]').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('.chip[data-f]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on'); tier=b.dataset.f; apply();
    });
  });
  srch.addEventListener('input',apply); apply();
})();
</script>"""
    html_doc = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>WXBT · Value bets</title><style>{D.CSS}{NAV_CSS}{EXTRA_CSS}</style></head>"
                f"<body>{body}{vjs}</body></html>")
    open(OUT, "w", encoding="utf-8").write(html_doc)
    print(f"Value bets -> {os.path.abspath(OUT)}  ({len(hits)} value / {len(vb)} mercados)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Tab de value bets: bot vs mercado, edge bruto.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--refresh", action="store_true", help="(compat run_daily; value bets es live)")
    main(ap.parse_args())
