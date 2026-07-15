#!/usr/bin/env python3
# scripts/wxbt_nav.py — NAVBAR compartido (pedido Santiago 2026-07-15: "armame un navbar para
# navegar entre las paginas"). UNICA fuente de verdad: todas las paginas (dashboard, leaderboard,
# stats, historial, modelos, ciudades, ciudad) insertan nav_html(<clave>) como primer elemento de
# su topbar sticky -> queda fijo arriba y resalta la pagina activa. El CSS (NAV_CSS) se sirve por
# el mismo canal que el resto: el dashboard lo appendea a wxbt.css; las demas lo embeben inline.
# Reusa las variables CSS del tema (--fc/--mkt/--bd/...) para verse identico en las 7 vistas.

# (clave, href, icono, etiqueta corta, tooltip)
NAV_ITEMS = [
    ("dashboard",   "live_dashboard.html", "🖥", "Terminal",
     "pronostico vs mercado en vivo + value bets + ventana 48h"),
    ("cities",      "cities.html",         "🏙", "Ciudades",
     "dashboard individual por ciudad: mercado + modelos + PWS + historial"),
    ("history",     "history.html",        "🗓", "Historial",
     "pronosticos desde el 08/07: pick congelado vs lo que pago Polymarket"),
    ("models",      "models.html",         "🧪", "Modelos",
     "que modelo acierta en cada ciudad (vivo pre-freeze + retro 90d)"),
    ("leaderboard", "leaderboard.html",    "🏆", "Leaderboard",
     "ranking de estaciones por track record vivo (exactos/top-2)"),
    ("stats",       "stats.html",          "📊", "Estadisticas",
     "estadisticas generales + rendimiento dia por dia (gano/perdio)"),
]

NAV_CSS = """
/* ===== NAVBAR compartido (2026-07-15) ===== */
.viz-root .wxnav{display:flex;gap:2px;align-items:stretch;flex-wrap:wrap;
  margin:0 -22px 8px;padding:0 22px;border-bottom:1px solid var(--bd);}
.viz-root .wxnav a{display:inline-flex;align-items:center;gap:6px;text-decoration:none;
  font-family:var(--mono);font-size:12px;color:var(--ink2);padding:8px 13px;
  border:1px solid transparent;border-bottom:2px solid transparent;
  border-radius:6px 6px 0 0;white-space:nowrap;transition:all .13s;letter-spacing:.02em;}
.viz-root .wxnav a .ico{font-size:13px;filter:saturate(.6);}
.viz-root .wxnav a:hover{color:var(--ink);background:var(--s2);}
.viz-root .wxnav a.on{color:var(--fc);background:var(--fcw);border-color:var(--bd);
  border-bottom-color:var(--fc);font-weight:700;}
.viz-root .wxnav a.on .ico{filter:none;}
.viz-root .wxnav .navspacer{flex:1 1 auto;}
.viz-root .wxnav .navbrand{display:inline-flex;align-items:center;color:var(--mut);
  font-family:var(--mono);font-size:10px;letter-spacing:.14em;padding:8px 6px;text-transform:uppercase;}
@media (max-width:640px){
  .viz-root .wxnav a{padding:7px 9px;font-size:11px;}
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
