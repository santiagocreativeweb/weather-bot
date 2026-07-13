# WXBT — Contexto de traspaso (al 2026-07-12)

> Pegá este documento en un chat nuevo para que entienda el proyecto igual que acá.
> Escrito para Santiago (Argentina, UTC-3). Estilo de trabajo pedido: **terso** — código primero,
> bullets de 1-2 líneas, sin cortesías, CODE → BULLETS → DONE. Español rioplatense. El TRABAJO
> mantiene la profundidad total; solo se comprime la prosa.

---

## 1. Qué es WXBT
- Bot que predice la **temperatura MÁXIMA diaria** de 12 aeropuertos y opera los mercados
  "Highest temperature in {city} on {date}" de **Polymarket** (buckets de temperatura).
- Santiago opera MANUAL, size chico, mientras corre una **validación forward** (el backtest de
  18 meses quedó INVALIDADO, ver §4).
- Objetivo: subir el **% de aciertos** (exacto y top-2) y construir una plataforma profesional.

## 2. Las 12 estaciones (código ICAO → ciudad, unidad, huso de verano)
- **Asia**: RJTT Tokio (°C, +9), RKSI Seúl (°C, +9), ZSPD Shanghái (°C, +8), ZBAA Beijing (°C, +8), RCSS Taipei (°C, +8). NO usan DST.
- **Europa**: EGLC Londres (°C, +1 BST), LFPB París (°C, +2), LEMD Madrid (°C, +2), EDDM Múnich (°C, +2), LIMC Milán (°C, +2). DST verano.
- **América**: KLGA Nueva York (°F, −4 EDT), KORD Chicago (°F, −5 CDT). DST verano.
- **OJO estación de resolución**: el mercado resuelve con **Weather Underground (WU)** de la estación
  exacta. London = **EGLC** (City), NO Heathrow — ese error causaba 46% de mercados a 0. Verificar
  siempre la estación en la description del mercado de Gamma.

## 3. Reglas de resolución y redondeo (CRÍTICAS, confirmadas en vivo)
- **WU FLOOREA la observación SIEMPRE**: 35.9 → 35, nunca 36. Confirmado 3× (Milan, Beijing).
- **Pick del bot = `floor(mu)`** en ambas unidades. °F: buckets pares par-impar (84-85). °C: 1 grado.
- **Probabilidad de bucket floor-consistente** = `bucket_prob(mu − 0.5, sigma, lo, hi)`. El motor
  (`wxbt/market.py`) es half-up; correr mu −0.5 lo vuelve floor exacto SIN tocar el motor/tests.
- Se corrigió un half-up en °C que subreportaba aciertos: al pasar todo a floor, el hit calibrado
  saltó de 0.44 a 0.62 en la medición vs Gamma.

## 4. Estado del EDGE (leer antes de creer cualquier número de backtest)
- **Bug #5 (invalidó el backtest de 18 meses)**: la API Previous-Runs de Open-Meteo ancla al VALID
  TIME → el "lead 1" histórico es un NOWCAST con `avail` falso. ROI +766% → −6% al corregir. **El
  único edge honesto es la validación FORWARD** (predictions_forward.csv, sí es point-in-time).
- **Track record VIVO (08-11/07, ~37 mercados)**: exacto **43%**, top-2 **65%**, top-3 **86%**, MAE 0.94°.
- **Sábado 11/07 fue el PEOR día**: 2 exactos, 2 pérdidas de 12 (el 9/7 había sido 8 exactos, 0 pérdidas).
- **Invariante mental**: el bot es un **identificador de TOP-2 (~65%)**, NO de exacto (~43%).

## 5. Estrategia de trading (tras perder $189 el 11/07 apostando al bucket exacto)
1. Operar **SOLO estaciones fuertes**; SALTAR las débiles hasta tener fuente local validada.
2. NO comprar el bucket exacto al ask (32% + pagás el spread). En su lugar: **comprar el PAR top-2**
   cuando el mercado lo subvalúa, o **VENDER NO** en buckets descartados (top-3 cubre 76% → el resto <24%).
3. **Maker siempre** (limit al mid, no cruzar spread), entrada **tardía** (lead-2 fresco), size chico.
4. `scripts/playbook.py` imprime la ACCIÓN por mercado hoy siguiendo estas reglas.
- **Tiers (track record 45d + vivo)**: FUERTES = KORD, LEMD, LIMC, EGLC, LFPB. DÉBILES (no operar) =
  **RCSS 6.7% exacto, ZSPD 0/3, KLGA** — Open-Meteo usa grilla marina fría ahí.

## 6. Calibración — dónde está el techo
- **Producción = V2**: EMOS (sobre ANOMALÍAS vs climatología, no nivel absoluto) + **sesgo rolling
  60d por estación** (`data/station_bias.json`, se resta al mu). Hit lab 39.6% → 42.8%.
- **V6/V7/V8 TODAS RECHAZADAS** (labs walk-forward, 2026-07): 8 modelos en consenso/EMOS, bias
  regime-conditional, selección de modelos por estación, drop de outliers → ninguna le gana a V2
  con significancia; varias lo empeoran. **V2 está en el techo de los modelos GLOBALES a lead 2.**
- Ironía clave: sacar el modelo "malo" de cada estación (lo que pedía la autopsia) igual PIERDE — ese
  modelo aporta los demás días. Selección con 30-60d = overfit.
- **CONCLUSIÓN**: la palanca ya NO es tunear el mixture. Es (a) **fuentes LOCALES** para las débiles,
  (b) **selección de apuesta** (§5). No perder tiempo en más variantes del EMOS global.
- **Bug operativo del refresh: CERRADO (2026-07-12)**: `D1` dinámico en calib_lab.py (min(ayer,
  cobertura backfill)) + invalidación de cache + `backfill_check.py --extend` (solape 3d, coalesce
  de labels) encadenados en run_check.ps1. El refresh semanal ya no puede ser no-op.
- **Sweep de ventana del bias (2026-07-12, pedido "90/60/30/7")**: W60 queda (7d 32.3% / 30d 32.7%
  / 60d 33.1% / 90d 32.7% exacto lead 2). MED60 (+1.6pp) = winner's curse exacto (E[max|nulo]=+1.60pp,
  p ajustado 0.44) → NO adoptado; corre en SOMBRA con regla pre-registrada (n≥45d desde 07-12,
  ver header de scripts/lab_bias_window.py). Verificación adversarial 4 agentes: look-ahead limpio.

## 7. Fuentes de datos
- **Base (mixture del bot)**: Open-Meteo Previous-Runs, 3 modelos deterministas — gfs_seamless,
  ecmwf_ifs025, icon_seamless. (5 seamless extra probados: no aportan significativo.)
- **Fuentes LOCALES capturando FORWARD** (para validar y, si le ganan al bot con n≥15-20 días, MEZCLAR):
  - **NBM** (NOAA) → KLGA/KORD. `capture_nbm.py`. Único con archivo point-in-time (backtest honesto futuro).
  - **DWD MOSMIX** → 11 estaciones. `capture_mosmix.py` (max horario) + `accumulate_mosmix.py` (TX nativo).
  - **CWA Taiwan** → RCSS. `capture_cwa.py` (mirror AWS S3, sin key).
  - **JMA** → RJTT (Tokio/Otemachi, NO Haneda → señal, no exacto). `capture_jma.py`, gratis sin key.
  - **QWeather** → ZBAA/ZSPD. `capture_qweather.py`. Key + API Host en `data/.qweather_key` (ya funciona).
- **REGLA DE ORO**: ninguna fuente local toca el `mu` del bot hasta que `validate_sources.py` muestre
  que le GANA al bot con n≥15-20 días. Todas son forward-only (sin archivo histórico salvo NBM).
- **Gate en el playbook**: si el bot y la fuente local divergen ≥2°, marca "seguí a la fuente" (RCSS→CWA,
  RJTT→JMA, ZBAA/ZSPD→QWeather). Forward-safe, no cambia la predicción.

## 8. Timing — pico y bloqueo por estación (corregido con datos)
- El pico de tmax **NO es 15:00 para todas**. Medido de 25-31 días de METAR: costeros de Asia pican a
  **media mañana** por brisa marina (Shanghái 12:00, Seúl/Tokio 12:48, Taipei 12:00); inland más tarde
  (Beijing 14:30); Europa/América 15-17h. `PEAK_HOUR` + `local_offset` DST-aware en `show_live.py`.
- **Bloqueo del pronóstico** = pico − 45 min (antes 1.5h). Después no recalibra y se congela.
- Sin esto, el bot creía que faltaban 3h cuando el mercado ya se estaba resolviendo (solo Asia costera).

## 9. Dashboard (TERMINAL v2)
- `scripts/dashboard.py` genera `data/live_dashboard.html` + `data/wxbt.css` + `data/wxbt.js` (HTML/CSS/JS
  SEPARADOS). Look terminal financiera dark: verde fósforo (bot), cyan (mercado), ámbar (en vivo), mono.
- Correr en vivo: `python scripts/dashboard.py --watch --serve` → sirve en `http://127.0.0.1:8765` y
  en la **IP LAN** (celular misma WiFi). **CANDADO de instancia única** (`data/.dashboard_watch.lock`):
  arrancar 2 veces NO duplica. **Los watchers duplicados eran la causa de que los buckets "saltaran"**
  (dos procesos escribían alternadamente el mismo audit → mu oscilaba). Regla: matar TODOS y confirmar 0
  antes de arrancar si se sospecha duplicado.
- Features: cards por ciudad ordenadas ASIA→EUROPA→AMÉRICA, ventana ≤48h (ayer/hoy/mañana/+2), hora local
  por card, estados EN CURSO/PICO CERCA/PENDIENTE GAMMA/RESOLVIENDO/FINALIZADO, freeze inmutable + tachado
  de top-2/3 fallidos, alertas por evento persistentes, **timeline ⏱24h** por card (slider 30 min, precios
  del orderbook CLOB + mu/top-2/top-3 del bot en cada momento), 10 botones rápidos + descarga xlsx.
- **Leaderboard** (`leaderboard.py` → leaderboard.html): ranking por track record VIVO (exactos→top2→pwin).
- **Estadísticas** (`stats_page.py` → stats.html): cards generales + rendimiento DÍA POR DÍA con ganó/perdió
  por mercado (EXACTO✓/TOP-2✓/TOP-3~/PÉRDIDA✗).

## 10. Datos y automatización
- **SQLite** `data/wxbt.db` + **Excel** `data/wxbt_export.xlsx` (12 hojas) via `export_data.py`.
- `run_daily.ps1` encadena: accumulate_books/ensemble/predictions + capture_nbm/mosmix/accumulate_mosmix/
  cwa/jma/qweather + validate_sources + leaderboard + stats_page + export_data.
- **HECHO 2026-07-13**: `run_daily.ps1` registrado en Task Scheduler como `wxbt-accumulate`, diario
  12:00 hora local, modo interactivo; corrida manual verificada con Last Result=0. También puede
  dispararse manualmente con el botón "sincronización completa" del dashboard.

### Auditoría init-anclada de exactitud (2026-07-13)
- Se reemplazó Previous-Runs por **Single Runs con `run=` explícito**, corrida conservadora cuya
  publicación (`run + lag`) es anterior al freeze. Archivo reproducible: `backfill_single_runs.py`.
- 19.197 predicciones, 90 targets, 29 estaciones, 8 modelos globales; cero `avail > freeze`.
- Corte anidado: DEV 10/05-20/06; HOLDOUT intacto 21/06-11/07. Una receta global perdió; el selector
  congelado por ciudad subió **32,4% -> 39,6% exacto** (+7,2pp, p=0,0085), top-2 **64,8% -> 64,8%**.
- Los regionales (HRRR/NBM/ICON-EU/AROME/HARMONIE/UKV/JMA-MSM/ICON-2I) fueron testeados con el
  mismo corte: **39,4% -> 37,3% exacto**, rechazados para exacto aunque mejoraron algo MAE/top-2.
- El selector por ciudad queda como challenger reproducible; no se mezcla silenciosamente con V2.
  El nivel vivo continúa acumulándose forward y Gamma sigue siendo la verdad del payout.
- **MOS físico Open Data, 4 ciudades (hipótesis posterior):** 560 filas de corridas exactas HRRR,
  NBM, UKV, ICON-EU y ARPEGE-EU con humedad, nubes, radiación, precipitación y viento. Split fijado
  antes de bajar features: train hasta 06/06, validación 07/06-27/06, test 28/06-11/07. ET_D2 fue
  congelado en validación. Test: CITYX1 **53,6%** vs MOS **48,2%** exacto (−5,4pp, p=0,9559),
  top-2 80,4% ambos; MAE 1,141 -> 1,057. **RECHAZADO para exacto**. No justifica pagar Professional.
- **Clasificación directa del bucket:** logística pooled, walk-forward 10/06-11/07, 367 mercados:
  CITYX1 43,9% vs directo 43,3%, top-2 68,1% vs 67,0%, p=0,5839. Rechazada.
- **Consenso CLOB+CITYX1 pre-freeze:** selección DEV 10/05-10/06; test 11/06-01/07, 94 mercados.
  Mezcla 50/50 a freeze−3h: 42,6% -> 46,8% exacto (+4,3pp) pero p=0,1088; selector por estación
  +2,1pp, p=0,235. No se adopta. Queda pre-registrado `MKTWX1-20260713` en sombra desde targets
  14/07, gate forward a 45 días; captura solo precios y CITYX publicados antes del cutoff.
  `check_accumulation.py` exige una fila por snapshot elegible y frena si forecast/precio cae después
  del cutoff, si el precio tiene >8h o si hay menos de cuatro buckets cotizados.

## 11. Invariantes que NO se rompen sin avisar (de CLAUDE.md)
1. `evaluate_market()` en `wxbt/engine.py` es función PURA (sin I/O ni estado oculto).
2. Anti-look-ahead: `avail` = instante REAL de publicación. Ninguna fuente lo viola.
3. `validate_world()` corre antes de cualquier backtest.
4. `test_null_market` debe dar ROI≤0 siempre (si da positivo, el motor está roto).
5. EMOS calibra sobre ANOMALÍAS vs climatología, no nivel absoluto.
- **Tests**: `python -m pytest tests/ -q` → 7 passed. Correr tras cualquier cambio.
- Windows: comando `python` (no python3). Prints de scripts en ASCII (consola cp1252).

## 12. Mapa de archivos clave
- Motor: `wxbt/engine.py` (evaluate_market, fit_all), `wxbt/calibration.py` (EMOS), `wxbt/market.py` (bucket_prob).
- Predicción/scoring: `scripts/accumulate_predictions.py`, `scripts/check_predictions.py`.
- Fuentes: `scripts/capture_{nbm,mosmix,cwa,jma,qweather}.py`, `accumulate_mosmix.py`, `validate_sources.py`.
- Timing/metadata: `scripts/show_live.py` (STATIONS, PEAK_HOUR, local_offset, peak_utc).
- Dashboard/UI: `scripts/dashboard.py`, `leaderboard.py`, `stats_page.py`, `playbook.py`, `export_data.py`.
- Labs: `scripts/calib_lab.py` (V2), `lab_v6/v7/v8.py` (rechazados).
- Datos: `data/predictions_forward.csv`, `station_bias.json`, `backfill_check.csv`, `*_forward.csv`, `forecast_audit.json`.
- Contexto largo: `PROJECT_CONTEXT.md`, `CLAUDE.md` (instrucciones de proyecto).

## 13. Pendientes / próximos pasos
- **Bajar la sangría de las débiles**: validar CWA/JMA/QWeather forward (n≥15-20 días) y, si ganan, meterlas
  como miembro del ensemble por estación (validar en calib_lab antes).
- **calib_lab D1 dinámico** + extender backfill_check semanalmente (el refresh del bias hoy se congela).
- **Task Scheduler registrado**; vigilar semanalmente Last Result y `check_accumulation.py`.
- **Alta de ciudades nuevas** ADD (Wellington/Ankara/Miami/Singapore/KL/Shenzhen superan la mediana) — gate:
  verificar acuerdo IEM-vs-Gamma antes de operar.
- Seguir puliendo dashboard/stats según feedback.

## 14. Cómo hablarle a Santiago
- Terso, directo, en bullets. Sin "Certainly/Sure". Verdades incómodas de una (ej. "infalible no existe").
- ✓ verde / ✗ rojo. "PÉRDIDA" no "ERROR". Todo en hora Argentina (UTC-3).
- Cuando algo no se puede o es overfit, decírselo con la métrica que lo respalda. Él valora la honestidad
  por sobre la promesa.
