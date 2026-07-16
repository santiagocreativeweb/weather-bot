# wxbt/config.py — Reglas duras (FASE 3) + parámetros de mercado (FASE 1).
# Etiquetas: [VERIFICAR-VIVO] = dato recuperado de fuentes, confirmar antes de dinero real.
#            [ASUNCION]      = supuesto propio; si es falso, cambia el resultado.
from dataclasses import dataclass

# --- Fees (Polymarket weather, Fee Structure V2 mar-2026) ---
FEE_RATE_WEATHER = 0.05      # [VERIFICAR-VIVO] taker fee = shares * rate * p*(1-p); pico $1.25/100sh @50c
MAKER_FEE = 0.0              # makers pagan 0 (rebate ignorado a propósito: conservador)

# --- Reglas de entrada / salida (FASE 3) ---
EDGE_MIN_NET = 0.10          # edge neto de fee/spread >= 10 puntos de prob
EXIT_PROB_SHIFT = 0.15
SIGMA_STRESS = 1.30          # [ASUNCION FASE 4] el edge debe sobrevivir sigma*1.3 (margen de
                             # calibración de FASE 3 hecho operativo; corta edges-artefacto)       # salida si la prob del modelo se mueve >=15 pts en contra ("forecast changed")

# --- Sizing / riesgo (FASE 3) ---
KELLY_FRACTION = 0.25        # quarter-Kelly
PER_MARKET_CAP_FRAC = 0.02   # costo máx por mercado: 2% del capital  [ASUNCION: liquidez fina]
PER_MARKET_CAP_USD = 40.0    # tope duro FASE 3 ($20-40/mercado por liquidez fina observada)
PAYOUT_CAP_USD = 100.0       # [ASUNCION] cap de payout por posición: mata la "lotería de colas"
                             # (comprar 5c con payout 20x) que domina la varianza con liquidez fina
MIN_MODELS_ENTRY = 3         # [ASUNCION FASE 3 "ensemble converge"]: entrar solo con los 3 modelos
EDGE_SHRINK = 0.17           # [MEDIDO FASE 4, market-settled] el edge REALIZADO es ~17-22% del
                             # aparente (optimizer's curse: seleccionar por edge selecciona los
                             # sobreestimados). Pooled 4 estaciones limpias: 0.20 (OOS H1/H2 =
                             # 0.17/0.22, estable); se usa la mitad BAJA como cota conservadora.
                             # POR ESTACION los shrinks (0.17-0.55) NO pasaron OOS individual
                             # (KORD 0.38->0.08, RJTT 0.89->0.28) -> NO usar por-estacion, es el
                             # mismo patron de overfit que d,e de EMOS con muestra chica.
                             # Aplica SOLO al sizing (Kelly), NO al gate de entrada: cambiar la
                             # seleccion requiere su propia validacion OOS.
MIN_ENTRY_PRICE = 0.03       # [ASUNCION FASE 4] piso de precio (mid) del token que compramos: por
                             # debajo NO hay liquidez real para llenar el payout cap. Sin esto, el
                             # backtest compra $100 de payout a ~0.1¢ (lotería de colas, fills
                             # ficticios) — el placebo (forecasts desalineados +14d) mostró que ~50-60%
                             # del ROI venía de esos fills sub-cent, no de señal. Ver PROJECT_CONTEXT §6.
REENTRY_COOLDOWN_H = 12.0    # [ASUNCION FASE 4] tras salida forecast-changed, no re-entrar al mismo
                             # mercado por 12h (1 ciclo de corridas): el backtest mostró whipsaw churn
GROUP_CAP_FRAC = 0.30        # costo comprometido máx por grupo sinóptico correlacionado
DAILY_KILL_SWITCH = -0.05    # freno de entradas si PnL del día (UTC) <= -5% del equity al inicio del día
# Freno TRAILING: completa el hueco del kill diario, que no frena GOTEOS multi-dia (el maxDD -44%
# real fue 47 dias de -1/-3% con solo 7 dias <=-5%; el kill diario nunca corto la sangria).
# Reacciona a perdida REALIZADA sin importar si viene concentrada en 1 estacion o repartida entre
# 6 correlacionadas (cubre correlacion TEMPORAL, que ni GROUP_CAP ni concurrencia cubren).
# Umbrales por REGLA EXTERNA (multiplos del kill diario, no ajustados mirando el DD): high-water
# ABSOLUTO (ATH), sin ventana — una v1 con ventana de 30d fallo mecanicamente: el goteo real duro
# 47 dias > ventana, el pico salia de la ventana a mitad de la sangria y el freno se soltaba solo.
# Freno GEOMETRICO: factor = TRAILING_BRAKE_FACTOR ^ floor(dd_ATH / TRAILING_BRAKE_DD).
# Cada -10% adicional desde el ATH divide el tamano por 2 (-10%: x0.5, -20%: x0.25, -30%: x0.125).
# SIN escalon de cero: un stop total (v2 probada) es estado ABSORBENTE — con cero entradas el
# equity no puede recuperarse y el bot queda muerto para siempre (paso en el backtest: 1 anio
# parado). El geometrico frena fuerte pero siempre deja tamano para volver.
TRAILING_BRAKE_DD = 2 * DAILY_KILL_SWITCH   # escalon: -10% (2x el kill diario) desde el ATH
TRAILING_BRAKE_FACTOR = 0.5                 # divisor por escalon

# --- Calibración (FASE 3) ---
MIN_TRAIN_DAYS = 60          # [ASUNCION] mínimo de días por (estación, lead) antes de confiar el ajuste
LEAD_DAYS = (1, 2, 3)        # DOCUMENTACION (codigo muerto: el engine NO filtra por lead). lead_h se
                             # mide de avail al PICO (~15:00 local): 1 = corrida de la misma manana
                             # del target (el bot OPERA el dia del target), 2 = del dia anterior,
                             # 3 = de 2 dias antes. En Asia (UTC+9) el "1" lo llenan corridas del
                             # dia anterior (el pico 06:00 UTC llega antes que la corrida del dia).

# --- Estaciones (códigos = placeholder; la estación REAL sale de las rules de cada mercado) ---
@dataclass(frozen=True)
class Station:
    code: str      # [VERIFICAR-VIVO] por serie de mercado (FASE 1: puede cambiar entre series)
    name: str
    group: str     # grupo de correlación sinóptica para el cap de portfolio
    unit: str      # 'F' (buckets de 2°F) | 'C' (buckets de 1°C)
    utc_off: int
    clim_mean: float   # media anual aprox (solo synth)
    clim_amp: float    # amplitud estacional (solo synth)

STATIONS = [
    Station("KLGA", "New York (LaGuardia)", "US_E",  "F", -5, 55.0, 22.0),
    Station("KORD", "Chicago (O'Hare)",     "US_E",  "F", -6, 50.0, 25.0),
    Station("EGLC", "London (City)",        "EU",    "C",  0, 11.0,  7.0),
    Station("LFPB", "Paris (Le Bourget)",   "EU",    "C",  1, 12.0,  8.5),
    Station("RJTT", "Tokyo (Haneda)",       "ASIA",  "C",  9, 16.0, 10.0),
    Station("RKSI", "Seoul (Incheon)",      "ASIA",  "C",  9, 13.0, 13.0),
    # --- ampliación 2026-07-08 (top-volumen Polymarket; estaciones de las REGLAS de cada mercado,
    #     [VERIFICAR-VIVO] confirmadas contra description de Gamma). Grupos sinópticos CONSERVADORES:
    #     toda Asia oriental junta y toda Europa junta -> GROUP_CAP muerde más (prudente hasta medir
    #     correlaciones reales entre ciudades nuevas).
    Station("ZSPD", "Shanghai (Pudong)",    "ASIA",  "C",  8, 17.0, 12.0),
    Station("ZBAA", "Beijing (Capital)",    "ASIA",  "C",  8, 13.0, 15.0),
    Station("RCSS", "Taipei (Songshan)",    "ASIA",  "C",  8, 23.0,  7.0),
    Station("LEMD", "Madrid (Barajas)",     "EU",    "C",  1, 15.0, 10.0),
    Station("EDDM", "Munich (Franz Josef)", "EU",    "C",  1,  9.0, 10.0),
    Station("LIMC", "Milan (Malpensa)",     "EU",    "C",  1, 13.0, 10.0),
    # --- ampliación 2026-07-13 (scout + verificación Gamma/IEM; estación de las REGLAS de cada
    #     mercado confirmada contra description). clim_mean/clim_amp = APROX anual (solo synth; la
    #     climatología real sale del kernel sobre obs). Grupos sinópticos NUEVOS y distintos:
    #     Wellington aislado (hemisferio sur), Miami subtropical, SE-Asia tropical, sur de China.
    Station("NZWN", "Wellington (Intl)",    "OCE",     "C", 12, 13.0,  5.0),
    Station("LTAC", "Ankara (Esenboga)",    "EU",      "C",  3, 12.0, 15.0),
    Station("KMIA", "Miami (Intl)",         "US_SE",   "F", -5, 83.0, 10.0),
    Station("WSSS", "Singapore (Changi)",   "SEA",     "C",  8, 31.0,  2.0),
    Station("WMKK", "Kuala Lumpur (Intl)",  "SEA",     "C",  8, 32.0,  2.0),
    Station("ZGSZ", "Shenzhen (Bao'an)",    "S_CHINA", "C",  8, 26.0,  9.0),
    # [2026-07-16] HK Observatory: resolucion OFICIAL propia (CLMMAXT, 1 decimal) — a diferencia
    # de ZGSZ aca la verdad SI es la fuente de resolucion. Clima practicamente igual a Shenzhen.
    Station("HKO",  "Hong Kong (Observatory)", "S_CHINA", "C", 8, 26.0, 9.0),
    # --- ampliación 2026-07-13 tarde (+11; HK afuera por resolución decimal). Grupos sinópticos
    #     distintos: costa oeste US, Texas, sureste US, Toronto con el este, Mexico, Sudamerica,
    #     Helsinki con EU. clim_mean/clim_amp = APROX (solo synth).
    Station("KSFO", "San Francisco (Intl)", "US_W",    "F", -8, 60.0,  8.0),
    Station("KLAX", "Los Angeles (Intl)",   "US_W",    "F", -8, 66.0,  8.0),
    Station("KDAL", "Dallas (Love Field)",  "US_TX",   "F", -6, 77.0, 20.0),
    Station("KATL", "Atlanta (Hartsfield)", "US_SE",   "F", -5, 72.0, 18.0),
    Station("KHOU", "Houston (Hobby)",      "US_TX",   "F", -6, 79.0, 16.0),
    Station("KAUS", "Austin (Bergstrom)",   "US_TX",   "F", -6, 79.0, 18.0),
    Station("CYYZ", "Toronto (Pearson)",    "US_E",    "C", -5, 10.0, 15.0),
    Station("SBGR", "Sao Paulo (Guarulhos)","S_AMER",  "C", -3, 21.0,  5.0),
    Station("SAEZ", "Buenos Aires (Ezeiza)","S_AMER",  "C", -3, 18.0, 10.0),
    Station("MMMX", "Mexico City (Juarez)", "MEX",     "C", -6, 21.0,  5.0),
    Station("EFHK", "Helsinki (Vantaa)",    "EU",      "C",  2,  7.0, 13.0),
]
STATION_BY_CODE = {s.code: s for s in STATIONS}
GROUPS = sorted({s.group for s in STATIONS})

# --- Modelos de pronóstico: corridas y lag de disponibilidad (FASE 1) ---
# lag_h aproximado tras el init; el motor SOLO usa forecasts con avail <= t (anti look-ahead).
MODELS = {
    "gefs":  dict(members=31, lag_h=5.0, runs=(0, 6, 12, 18)),
    "ecmwf": dict(members=51, lag_h=7.0, runs=(0, 6, 12, 18)),
    "icon":  dict(members=40, lag_h=7.0, runs=(0, 6, 12, 18)),
}

SIGMA_FLOOR = {"F": 0.9, "C": 0.5}   # piso de sigma predictiva por unidad

CAPITAL_INICIAL = 2000.0
