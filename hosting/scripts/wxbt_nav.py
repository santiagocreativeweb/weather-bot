#!/usr/bin/env python3
# scripts/wxbt_nav.py — NAVBAR compartido (pedido Santiago 2026-07-15: "armame un navbar para
# navegar entre las paginas"). UNICA fuente de verdad: todas las paginas (dashboard, leaderboard,
# stats, historial, modelos, ciudades, ciudad) insertan nav_html(<clave>) como primer elemento de
# su topbar sticky -> queda fijo arriba y resalta la pagina activa. El CSS (NAV_CSS) se sirve por
# el mismo canal que el resto: el dashboard lo appendea a wxbt.css; las demas lo embeben inline.
# Reusa las variables CSS del tema (--fc/--mkt/--bd/...) para verse identico en las 7 vistas.

# (clave, href, icono, etiqueta corta, tooltip)
# [2026-07-15] Modelos e Historial SACADOS del nav (pedido Santiago): la perf por modelo ya vive
# en las city pages; Estadisticas reemplaza al historial. Value bets pasa a tab propio.
NAV_ITEMS = [
    ("dashboard",   "live_dashboard.html", "🖥", "Terminal",
     "pronostico vs mercado en vivo, ventana 48h"),
    ("cities",      "cities.html",         "🏙", "Ciudades",
     "dashboard por ciudad: mercado + modelos que mejor aciertan + PWS"),
    # [2026-07-16] Value bets ELIMINADA "de momento" (pedido Santiago). La funcion
    # wxbt_insights.value_bets() queda como infra por si vuelve.
    ("leaderboard", "leaderboard.html",    "🏆", "Leaderboard",
     "ranking de estaciones por track record vivo (exactos/top-2)"),
    ("stats",       "stats.html",          "📊", "Estadisticas",
     "estadisticas generales + rendimiento dia por dia (gano/perdio) — tabs 24hs y 48hs"),
]

NAV_CSS = """
/* ===== NAVBAR compartido (v3 2026-07-15) ===== */
.viz-root .wxnav{display:flex;gap:4px;align-items:center;flex-wrap:wrap;
  margin:0 -24px 4px;padding:8px 24px 0;}
.viz-root .wxnav a{display:inline-flex;align-items:center;gap:7px;text-decoration:none;
  font-size:12.5px;font-weight:600;color:var(--ink2);padding:7px 14px;
  border:1px solid transparent;border-radius:999px;white-space:nowrap;transition:all .14s;}
.viz-root .wxnav a .ico{font-size:13px;filter:grayscale(.4) opacity(.85);}
.viz-root .wxnav a:hover{color:var(--ink);background:var(--s2);border-color:var(--bd);}
.viz-root .wxnav a.on{color:#04120c;background:linear-gradient(180deg,var(--fc),#1fbf8a);
  border-color:var(--fc);font-weight:700;box-shadow:0 0 18px -4px var(--fcs);}
.viz-root .wxnav a.on .ico{filter:none;}
.viz-root .wxnav .navspacer{flex:1 1 auto;}
.viz-root .wxnav .navbrand{display:inline-flex;align-items:center;color:var(--fc);font-weight:800;
  font-family:var(--mono);font-size:12px;letter-spacing:.18em;padding:7px 10px 7px 0;}
@media (max-width:680px){
  .viz-root .wxnav{margin:0 -14px 4px;padding:8px 14px 0;}
  .viz-root .wxnav a{padding:7px 11px;font-size:11.5px;}
  .viz-root .wxnav a .lbl{display:none;}   /* en pantalla chica: solo iconos */
  .viz-root .wxnav .navbrand{display:none;}
}
"""


def nav_html(active=""):
    """Barra de navegacion; `active` = clave de la pagina actual (se resalta). Va como PRIMER hijo
    del .topbar sticky de cada pagina para quedar siempre visible."""
    links = []
    for key, href, ico, label, tip in NAV_ITEMS:
        cls = "on" if key == active else ""
        links.append(
            f'<a class="{cls}" href="{href}" data-tip="{tip}" title="{tip}">'
            f'<span class="ico">{ico}</span><span class="lbl">{label}</span></a>')
    return ('<nav class="wxnav"><span class="navbrand">WXBT</span>'
            + "".join(links) + '<span class="navspacer"></span></nav>')
