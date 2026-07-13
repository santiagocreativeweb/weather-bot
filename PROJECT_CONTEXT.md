# WXBT — Contexto completo del proyecto

Este documento es la memoria persistente del proyecto: qué se investigó, qué se decidió, por qué,
y qué se construyó. Un LLM no tiene memoria entre sesiones — este archivo ES la memoria. Léelo
completo antes de tocar código.

**Perfil del usuario:** argentino, capital $2.000 USD, opera desde VPS en Dublín (Irlanda).
Polymarket está bloqueado en Argentina (fallo judicial, ver Riesgos §5) — el VPS resuelve
geobloqueo Y latencia (el CLOB corre en AWS eu-west-2 Londres). Estilo de trabajo: fases, frenar
para confirmación al final de cada una, honestidad ante todo ("si no hay edge, decilo").

---

## 1. Qué es esto

Bot de trading en Polymarket sobre mercados de **temperatura máxima diaria** por ciudad
(resolución vía estación de aeropuerto específica: KLGA en NYC, LFPB en París, etc. — NO la
ciudad en general). Alcance: **multi-ciudad desde el arranque** (decisión explícita del usuario,
más varianza-diversificación a cambio de más complejidad de correlación entre mercados).

## 2. La tesis de edge (FASE 2) — por qué esto podría funcionar

**Edge principal: calibración estadística estación-específica, NO latencia.**
Se descartó explícitamente competir en velocidad — hay bots ya establecidos con esa ventaja
(ver competencia §4). La apuesta es que el consenso multimodelo crudo (GEFS/ECMWF/ICON) está
sesgado de forma sistemática y predecible por estación, y ese sesgo es corregible con EMOS/NGR
(literatura documenta mejoras de CRPS ~34-44%, "muere lento" como edge).

**Edge secundario combinable:** delta estación-ciudad (el mercado resuelve por la estación del
aeropuerto, que puede diferir sistemáticamente del percibido "clima de la ciudad").

**Condiciones de muerte del edge** (documentadas desde el día 1, no acomodadas después):
- Fees taker se comen el edge → mitigación: preferencia estructural por maker (fee=0).
- Liquidez fina ($749–$26k por mercado) → caps duros de tamaño, no todo es escalable.
- Riesgo de oráculo (ver §5) → no cubierto por ninguna regla de sizing, es riesgo de cola aceptado.
- Retail aprendiendo a mirar la estación → el edge se degrada con el tiempo, hay que remedir.

## 3. Qué se investigó y confirmó (FASE 0-1)

**Mecánica de mercado (verificado):**
- Resolución por estación de aeropuerto específica, leída de Weather Underground "History" tras
  el cierre del día; settlement vía UMA optimistic oracle (~2h de ventana de disputa).
- ~12 ciudades activas en Polymarket weather (Shanghai, Tokio, Beijing, HK, Seúl, Taipei, Wuhan,
  Londres, París, NYC, LA). Volúmenes finos: $749–$26k por mercado.
- Fees taker en weather desde 30-mar-2026 (Fee Structure V2): `rate·p·(1-p)`, rate≈0.05, pico
  ~$1.25 por 100 shares a 50¢. Makers pagan 0 (+rebates, que el motor ignora por conservador).
  **[VERIFICAR-VIVO]** contra docs.polymarket.com antes de operar con dinero real.

**Marketing de bots de Twitter, descartado como evidencia (FASE 0):**
- "86% win rate": 24W-4L de picks públicos SIN precios — win rate sin EV no dice nada.
- Screenshots solo-de-ganadas, "+22.9%" con n=2 trades resueltos (ruido, no señal).
- Bot "LIVE" mostrado con balance $0 en la screenshot (marketing vacío).

**Rescatable de ese mismo material (señal real detrás del ruido de marketing):**
- Calibración EMOS/bias-correction por estación+lead es una técnica real y documentada.
- Lag de repricing post-corrida de modelos (ventana de oportunidad tras cada corrida GFS/ECMWF).
- Reglas operativas de bots reales: EV mínimo ~10%, Kelly fraccional 0.25, salida "forecast changed".

**Competencia on-chain verificable (no marketing, PnL on-chain real):**
gopfan2 +$351.942, aenews2 +$286.705, ColdMath +$135.489, Hans323 +$80.697 (estudiante alemán 23
años). El CEO de Jua (empresa de forecasting) declaró que la liquidez de estos mercados es "too
low for a well-sized fund" — señal de que el edge institucional grande no entra, pero no dice
nada sobre un fondo chico ($2.000).

**Riesgo de oráculo — casos reales, no hipotéticos:**
- Fraude sensor en París CDG (abr-2026): alguien usó un secador de pelo cerca del sensor,
  ~$34k ganados, Météo-France denunció, Polymarket migró la resolución a LFPB.
- Fallo de resolución masivo (18-may-2026): brackets equivocados en Miami/CDMX/Seúl/HK.
- **Esto NO tiene cobertura de ninguna regla de trading.** Se documenta y se acepta como riesgo
  de cola, mitigado (no eliminado) diversificando estaciones.

**Stack de datos recomendado (FASE 1):**
- Open-Meteo: free 10k llamadas/día no-comercial. Ensemble API (GEFS 31 miembros, ECMWF ENS 51,
  ICON EPS 40) para forecast en vivo. **Pero no tiene archivo histórico de ensembles en el free
  tier** — descubierto recién en FASE 4/5 al intentar bajar data real (ver §7, decisión pendiente
  resuelta). Historical Forecast/Previous Runs API sí da point-in-time multi-año, pero
  determinístico (sin miembros, sin s2 real).
- IEM ASOS/METAR (mesonet.agron.iastate.edu) para obs, gratis. Página clave: "Wagering on ASOS
  Temperatures" — leerla antes de operar en serio.
- WU history: la API pública murió en 2018, hay que scrapear `/history/`.
- Polymarket: py-clob-client / Gamma API (`gamma-api.polymarket.com`) + CLOB
  (`clob.polymarket.com`). Cobertura de terceros (PolymarketData.co, Dune) para precios
  históricos de weather específicamente: **sin verificar**, por eso el proyecto arma su propio
  downloader en vez de depender de eso.

**Modelos meteorológicos (contexto para entender `synth.py` y la calibración):**
ECMWF IFS/ENS es el mejor global. AIFS (versión IA de ECMWF) mejora ~4-6% en T2m, datos abiertos
CC-BY. HRRR es solo CONUS (no sirve para ciudades fuera de EEUU). GFS/GEFS tienen lag ~4-6h.
**Todos los ensembles crudos son subdispersivos** (subestiman su propia incertidumbre) — esto es
exactamente lo que EMOS corrige inflando la varianza.

## 4. Reglas de estrategia — FASE 3 (las que vive `config.py`)

| Regla | Valor | Motivo |
|---|---|---|
| Edge mínimo neto | ≥10% | Deja margen sobre fee + error de calibración |
| Sizing | ¼-Kelly | Estándar en los bots reales relevados en FASE 0 |
| Cap por mercado | $20-40 | Liquidez fina observada ($749-$26k por mercado) |
| Cap por grupo sinóptico | 25-30% del capital | Multi-ciudad correlaciona (ola de calor mueve varias juntas) |
| Kill-switch diario | -5% equity | Freno de 24h a nuevas entradas |
| Salida | forecast≥15pts en contra | "Forecast changed", visto en bots reales |
| Timing | 24-72h a resolución | Lead time con mejor calibración documentada en literatura EMOS |
| Ejecución | maker preferido | Fee=0; taker solo si esperar pierde el edge |

Estos valores viven como constantes documentadas en `wxbt/config.py` con etiquetas
`[VERIFICAR-VIVO]` (dato de fuente externa, confirmar antes de $ real) y `[ASUNCION]` (supuesto
propio, si es falso cambia el resultado).

## 5. FASE 4 — Motor de backtest: qué se construyó y qué se rompió en el camino

**Decisión de diseño clave:** este entorno (sandbox de Claude) tiene red restringida — no hay
acceso a Open-Meteo/IEM/Polymarket desde acá. Por eso FASE 4 se dividió en:
(a) construir y VALIDAR el motor con un **mundo sintético** de propiedades conocidas, y
(b) escribir los scripts de descarga para que el usuario los corra en su VPS con datos reales.
**Los resultados sintéticos validan la MÁQUINA, no el edge real.** Esto se repite en cada
entregable a propósito — es fácil confundir "el backtest da +3500% ROI" con "hay edge", cuando en
realidad el ROI sintético es un artefacto del tamaño de la ineficiencia que YO planté a mano.

**Arquitectura (`wxbt/`):**
- `config.py` — constantes de FASE 3, ver tabla arriba.
- `calibration.py` — EMOS-lite: pesos por modelo ∝ 1/MSE histórico, mezcla, corrección afín de
  media (a+b·m), varianza c+d·s2+e·lead. Ajustado sobre **anomalías respecto a climatología
  kernel**, no sobre nivel absoluto (ver bug #4 abajo, es la razón).
- `market.py` — buckets con redondeo entero half-up, fee taker, Kelly, bracketing de fills.
- `synth.py` — mundo sintético con dos regímenes: `efficient` (crowd = posterior bayesiano
  exacto → NULL TEST) e `inefficient` (sesgos plantados a mano, para probar que el motor SÍ
  encuentra edge cuando existe).
- `engine.py` — `run_backtest()` walk-forward event-driven. **`evaluate_market()` es el núcleo
  puro de decisión** (snapshot de mercado → orden o None) — se reusa idéntico en paper/live
  (FASE 5), cero I/O adentro, cero estado oculto. Esta es la pieza más importante del repo.
- `checks.py` — `validate_world()`, corre siempre antes de backtestear.
- `tests/test_core.py` — 7 tests. El más valioso es `test_null_market`: si el motor "gana"
  contra un mercado que ya sabe todo (posterior bayesiano exacto), hay fuga. Cazó bugs reales.

**Bugs reales encontrados y corregidos durante la construcción** (dejarlo documentado importa
porque el patrón se repite — sirve como lista de sospechosos para bugs futuros similares):

1. **NaN silencioso en mids** — `pandas` coerciona `None→NaN` en columnas numéricas. `mk.lo`/`mk.hi`
   con colas abiertas (`None`) se pasaban por pandas y volvían `NaN`, y `bucket_prob(NaN)`
   envenenaba la normalización de probabilidades. Fix: `_open()` en `market.py` trata `None` y
   `NaN` como la misma cosa (cola abierta). **Patrón:** cualquier columna que debería tener
   `None` pero pasa por un DataFrame numérico puede volverse `NaN` sin avisar.

2. **El null test "pasaba" por la razón equivocada** — con el bug de NaN, `bucket_prob` daba
   basura, el motor no encontraba ningún candidato válido, 0 trades, ROI=0. Un null test que da
   0 trades no prueba nada — hay que verificar que el motor esté REALMENTE evaluando mercados,
   no que esté silenciosamente paralizado.

3. **El mismo patrón de NaN en la fuente de datos, no en el motor** — al escribir
   `download_openmeteo.py` v1, dejé `s2=""` para "completar después". `pd.read_csv` parsea eso a
   `NaN`, y `(fc["s2"] <= 0).any()` en `checks.py` es **False** para NaN (`NaN<=0` no es `True`)
   → el sanity check no lo atrapaba y el NaN se filtraba silencioso al motor. Mismo bug de bug
   #1, ahora en la capa de ingesta. Fix: chequeo explícito `.isna().any()` en `checks.py`
   (defensa permanente, no depende de que cada downloader esté bien escrito) + el downloader
   ahora aborta con error si la API no da lo esperado, en vez de escribir vacío.

4. **Sesgo sistemático en el crowd "eficiente" del null test por atenuación de OLS** —
   diagnóstico completo: al calibrar con OLS `y = a + b·m` sobre una serie con estacionalidad
   fuerte (temperatura), el ajuste sufre atenuación (`b<1`) y arrastra las predicciones hacia la
   media del período de entrenamiento. Si el período de test tiene una climatología distinta al
   de train (ej. entrenás en invierno, predecís en verano), aparece un sesgo sistemático que
   parece "edge" pero es artefacto metodológico puro. Se detectó porque el null test daba
   ROI positivo cuando debía dar ≤0. **Fix: EMOS se ajusta sobre ANOMALÍAS respecto a una
   climatología kernel (ventana gaussiana por día-del-año, ajustada SOLO con datos de
   entrenamiento), no sobre el nivel absoluto.** Este es el fix metodológico más importante del
   proyecto y aplica igual a datos reales — si algún día alguien "simplifica" `calibration.py`
   sacando la resta de climatología, hay que volver a leer este punto.

5. **Varianza sin término de lead-time** — el ajuste `c + d·s2` no capturaba que el error crece
   con el horizonte de predicción (lead time). Fix: se agregó término `e·lead_days` a la
   fórmula de varianza en `calibration.py`.

6. **Sin gate de robustez, el motor entraba en edges-artefacto de un solo sigma mal estimado** —
   se agregó `SIGMA_STRESS=1.3`: una entrada solo se toma si el edge sobrevive también con
   `sigma×1.3`. Filtra entradas que dependen de haber acertado la dispersión exacta.

7. **Churn de re-entrada** — sin cooldown, el motor entraba/salía del mismo mercado
   repetidamente por ruido de la propia señal, perdiendo en el spread cada vez. Fix: cooldown de
   12h tras una salida por "forecast changed".

**Estado final tras estos fixes:** 7/7 tests pasan, incluido el null test (ROI≤0 en 6/6 corridas
con distintas seeds), y el régimen ineficiente encuentra edge (CRPS calibrado < crudo, PnL>0).

## 6. FASE 4 con DATOS REALES — resuelto y corrido (sesión Claude Code, 7-jul-2026)

Los 3 downloaders se **reescribieron y corrieron con datos reales** (este entorno SÍ tenía red,
a diferencia del sandbox sintético). Pipeline completo punta a punta sobre **6 meses (ene–jun
2026)**: forecasts 8.643 filas, markets 8.156, prices 353.504, obs 12.048. `validate_world`: cero
issues. Estado por script:

- **`download_iem_obs.py`** (sin cambios): ✅ **verificado**. La conversión °F→°C SÍ corrió para
  las no-US: Londres jul 18-29°C, Tokio 28-31°C, NY quedó en °F. Rangos físicos plausibles. (El
  riesgo IEM-vs-WU sigue siendo pre-dinero-real, no bloquea el backtest.)

- **`download_openmeteo.py`** (v3, reescrito): ✅. Confirmado EN VIVO que Open-Meteo free **no
  archiva ensembles** (miembros `None` >~3 días atrás; el 400 era el RANGO, no el modelo). Ahora
  usa **Previous Runs API** (`previous-runs-api.open-meteo.com/v1/forecast`, `temperature_2m_previous_dayN`):
  forecast determinístico point-in-time multi-año, anti-look-ahead POR CONSTRUCCIÓN (previous_dayN
  = corrida N días más vieja → más error; confirmado empírico rmse crece con lead). `m` mapea
  lead→columna (1=base, 2=previous_day1, 3=previous_day2). `s2` se MODELA: varianza de residuos
  (m−obs) **por (estación, modelo, lead)**, ventana EXPANDIENTE (solo targets anteriores → tampoco
  mira el futuro). Model ids determinísticos: `gfs_seamless`/`ecmwf_ifs025`/`icon_seamless` (OJO:
  `gfs025` da 0 en previous-runs). **[ASUNCION]** `s2` = spread modelado, NO ensemble real.
  Nuevo `scripts/accumulate_ensemble.py`: junta el ensemble REAL forward (ids `gfs025`/`ecmwf_ifs025`/
  `icon_seamless_eps`) para validar en ~90 días si el s2 modelado ≈ spread real (correr a diario).

- **`download_polymarket.py`** (v2, reescrito): ✅. El 0-mercados era endpoint+filtro equivocados
  (`/markets?closed=true`+substring ordena por más ANTIGUOS = mercados 2020). Ahora enumera por
  `GET /events?tag_id=104596` (tag `highest-temperature`), troceando por mes con `end_date_min/max`
  (evita el offset-cap ~2000 → 422). Cada evento = un día/ciudad; buckets desde `groupItemTitle`;
  `clobTokenIds[0]`=token YES; salta volume=0. Precios: `clob.polymarket.com/prices-history` **con
  `startTs`/`endTs` explícitos** (CRÍTICO: `interval=max` devuelve `[]`). Concurrente (ThreadPool).
  Ciudades confirmadas: nyc/chicago/london/paris/tokyo/seoul. `hs`=0.02 asumido (no hay book histórico).

- **2 bugs de plataforma corregidos** (habrían frenado `--data real`, dejarlos documentados):
  8. **`run_backtest.py` escribía el reporte con cp1252** (Windows) y reventaba con `≥` en el md.
     Fix: `open(..., encoding="utf-8")`.
  9. **Timestamps tz-aware vs tz-naive mezclados**: `markets.csv` (close_t con "Z") venía tz-aware,
     `prices.csv`/`forecasts.csv` naive → pandas no los resta. Fix: `download_polymarket.py`
     normaliza TODO a naive-UTC (`_to_naive_utc`). **Patrón:** cualquier fuente nueva debe emitir
     tiempos en el MISMO convenio (naive-UTC) que el resto del mundo, o el motor explota al restar.

**VEREDICTO REAL (6 meses) — leído con los 3 tests de rigor, no por el ROI:**

El backtest crudo dio ROI **23-31x** = señal de fuga, no de edge. Se diagnosticó en capas:

- **Placebo (forecasts desalineados +14 días):** el ROI cae a 13-19x pero NO colapsa → la mayor
  parte del "profit" NO viene del pronóstico. Descarta look-ahead en los downloaders (el forecast
  correcto vence al desalineado: brier 0.22 vs 0.34), y prueba que había un **artefacto estructural**.
- **Artefacto identificado: fills ficticios sub-cent.** El motor compraba $100 de payout a ~0.1¢ en
  buckets muertos (35% del PnL en px≤0.005), asumiendo liquidez inexistente. **Fix: `MIN_ENTRY_PRICE=0.03`
  en `config.py`**, gate en `evaluate_market()` sobre el mid del token (mode-independiente). Con eso:
  ROI 23.7x→12.1x (taker), hit 33%→56%, 7/7 tests siguen pasando.
- **La "sobre-confianza ×2" era en gran parte el mismo artefacto.** Reliability sobre los trades que
  sobreviven al guard: la brecha (p_dice − gana_real) baja de ~0.25-0.40 a ~0.12.
Tres chequeos de rigor sobre por qué "12-17x" no es creíble (hechos tras la crítica del outside-view):

- **z-check sobre el σ CALIBRADO** (el que usa `evaluate_market`, de `predict()` = c+d·s2+e·lead —
  NO el mixture crudo, ese primer chequeo validaba el σ equivocado): std(z_cal)=1.02 (dispersión
  central OK) **PERO colas gordas: |z|>3 = 2.0% vs 0.3% Gaussiano, kurtosis 3.23** e **inestabilidad
  temporal: H1 std(z)=1.32 (σ ~24% muy angosto, EMOS overfit con ~60d de train), H2 std=0.91.**
  Matiz clave: el **s2 CRUDO está bien** (std(z_raw)=0.74, generoso), pero el modelo de varianza de
  EMOS lo afila hasta colas pesadas + inestable. → NO tocar el s2 crudo, PERO el σ calibrado
  (Gaussiano NGR) sí necesita colas pesadas (t-Student) y/o más estabilidad temporal.
- **Colas gordas vs curse:** la kurtosis 3.23 confirma que "el faltante crece con el edge aparente"
  (0.06→0.09→0.15→0.26) es MEZCLA de dos causas: optimizer's curse (selección) Y colas reales más
  pesadas que la Normal. La segunda exige tocar el MODELO (no solo el sizing) y subestima el riesgo
  de cola → los drawdowns del backtest (maxDD −16%) están subestimados.
- **Shrink OUT-OF-SAMPLE (H1/H2):** el ratio realizado/aparente = 0.61 estimado en H1 predice el
  realizado de H2 casi exacto (+0.178 vs +0.179); edge realizado **+0.18/share, positivo y estable
  en ambas mitades** (PnL 17k/16k, hit 58%/57%). El edge AGREGADO es real y OOS-robusto; el shrink
  no es ruido — PERO no basta aplicarlo solo (las colas y los fills quedan).

**HALLAZGO DOMINANTE (2ª sesión, sobre 18 meses ene-2025→jun-2026): la fuente de RESOLUCIÓN está
mal y fabrica casi todo el "edge".** Al extender a 18 meses (ver §6.1) el ROI SUBIÓ a 87x — señal
de que más data amplificaba un artefacto, no que había más edge. Se rastreó a que **el backtest
resuelve con obs de IEM (estación de aeropuerto) pero Polymarket resuelve por Weather Underground
en estaciones que NO siempre son las mismas.** Test: comparar el bucket que el mercado resolvió
(precio final ≥0.9) vs dónde cae obs_IEM. Acuerdo por estación (data limpia): RJTT 100%, LFPB 87%,
KORD 84%, KLGA 77%, RKSI 74%, **EGLL 46%** — global 73.5%. Dos capas:
  - **Bug de UNIDAD (el grande):** London cambió de °F (2025) a °C (2026); KLGA/KORD son °F; el resto
    °C. El downloader ignora el °F/°C del `groupItemTitle` y asume unidad fija por estación → los
    2.387 mercados London-2025 (°F) se resolvían contra obs °C (delta ~−40) → edge 100% fantasma.
    Filtrarlos bajó el ROI 87x→38x.
  - **Estación equivocada — RESUELTO (2026-07-08, lo destapó Santiago):** las reglas del mercado de
    London citan TEXTUAL "London **City** Airport Station... wunderground.com/history/daily/gb/london/
    **EGLC**". El proyecto usaba **EGLL (Heathrow)** — estación equivocada, a ~30 km, 0.5-1.1°C más
    fría. Renombrado EGLL→EGLC en los 10 .py + coords de City (51.5050, 0.0553) + red IEM (GB__ASOS
    tiene EGLC) + re-bajados obs.csv y forecasts.csv de EGLC + re-rotulado markets.csv. **Resultado:
    en la era °C limpia (2026), London pasó de 46% a 99% de acuerdo (163/164).** El 6% de 2025 es SOLO
    el bug de unidad 0a (buckets °F comparados contra obs °C). Verificado por año. Las OTRAS 5 estaciones
    coinciden con sus reglas (LaGuardia, O'Hare, Le Bourget, Haneda, Incheon ✓). **RKSI queda en ~78%
    con la estación CORRECTA (Incheon) → su gap NO es identidad, es otra cosa (redondeo °C / borde de
    día UTC+9) — residual de 0b para Seúl.** 62% de los desacuerdos son off-by-1
    bucket = micro-diferencias IEM-vs-WU-station. Esto es EXACTAMENTE el riesgo T2 (§6), ahora
    cuantificado: los deltas IEM-vs-WU son suficientes para fabricar la mayor parte del edge aparente.
  - **PRUEBA LIMPIA:** backtest solo-RJTT (Tokio, 100% de acuerdo obs=resolución): ROI **colapsa a
    +33% taker / +27% mid** en 18 meses (112 trades, hit 60%, Sharpe 3.8, maxDD −6.5%). Bootstrap
    del PnL/trade: CI90 **[+7%, +58%] taker (t=2.12, P≤0=1.8%)**, [+2%, +52%] mid (t=1.77) — positivo
    pero DÉBILMENTE significativo, muestra chica. NO tratar +33% como "el edge"; es "positivo,
    probablemente, con incertidumbre enorme (~breakeven a +58%)".
  - **EL OFF-BY-1 NO ES TOLERABLE (refuta "es ruido de oráculo aceptable"):** control París-solo
    (LFPB, misma dificultad que Tokio σ_med~1.1, PERO 87% de acuerdo = off-by-1) → ROI **+357%**, ~10×
    el de Tokio. Mecanismo: el modelo predice IEM (se calibró sobre IEM), el mercado cotiza sobre WU;
    cuando IEM y WU difieren 1 bucket, el modelo "le gana" por exactamente ese delta y el backtest
    (que resuelve sobre IEM) lo cuenta como ganancia. **El edge aparente ES el delta IEM-vs-WU
    monetizado ficticiamente.** Off-by-1 en 13% de los días → +324% de edge falso. Solo acuerdo
    ~100% (Tokio, donde IEM-Haneda=WU) da un número confiable. **El ROI escala inversamente con el
    acuerdo, y NO con la dificultad** (esto resuelve el confound): RJTT 100%→+33% (σ_med 1.08),
    LFPB 87%→+357% (1.09), KORD 84%→+452% (2.39), KLGA 77%→+2096% (2.43). Las estaciones DIFÍCILES
    (KLGA/KORD, σ_med 2.4) dan MÁS edge, no menos → el edge lo maneja el delta IEM-WU, no la
    calibración. Tokio da poco porque IEM=WU ahí, no (solo) porque sea fácil. Desagregación del desacuerdo por
    estación: RJTT 0% off, KLGA/KORD/LFPB ~13-23% off-by-1 (fabrica edge), RKSI 11% off≥3, EGLL
    46%-acuerdo (estación mal). CONFOUND: Tokio es además de las MÁS FÁCILES (σ_med 1.08 vs 2.4 de
    NY/Chicago), así que su +33% mezcla resolución-limpia + estación-fácil — no se sabe cuánto
    generaliza a las 5 restantes hasta tenerlas limpias.

**RESOLUCIÓN ARREGLADA — modo market-settled (3ª iteración; CORRIGE la conclusión de arriba).** Se
agregó `engine.run_backtest(resolve="market")`: paga contra la resolución REAL del mercado (columna
`resolved` de markets.csv, derivada de `outcomePrices` de Gamma = ["1","0"] en el bucket ganador,
cobertura ~100% de resueltos). CRPS/Brier/reliability SIGUEN contra obs física (market-settled es
SOLO para el payout — medir "predigo el clima" ≠ "predigo lo que el mercado creerá"). Validación
(null-test): obs vs market-settled, taker:
  - RJTT +29% → **+29%** (idéntico: IEM-Haneda = WU, cero delta). Prueba que el modo es correcto.
  - LFPB +357% → **+106%**  | KLGA +2099% → **+604%**.
El delta IEM-vs-WU inflaba ~2-3× (NO ~99% como decía el párrafo anterior — ese número salió de un
fallback roto: el precio corta ~12h antes de resolver, ~90% de mercados no convergen en la data, por
eso NO sirve resolver por convergencia de precio; sí sirve `outcomePrices` de Gamma).

**Conclusión honesta (corregida, 3ª iteración):** bajo pago REAL (market-settled) SOBREVIVE un edge
consistente de **~10-14% por trade** en las 4 estaciones de resolución limpia, **repartido en TODOS
los bins de precio** (no en longshots: px≤0.1 = 2% del PnL; no en cola: top-10 = 2%). Las diferencias
de ROI (Tokio +29% vs NY +604%) son TURNOVER (NY: 3470 trades vs 111; mismo ~10%/trade recirculado
54× sobre $2000), NO calidad de edge. Bootstrap Tokio (limpio): CI90 [+7%,+58%], t=2.12. Es más
positivo de lo que parecía: hay un edge real-de-pago, sistemático, no-artefacto-de-fills-ni-colas.
**VERIFICACIÓN DEL EDGE (4 checks, market-settled, solo 4 estaciones limpias — RKSI/EGLL afuera):**
  - KORD confirma: +196% ROI, edge_re +0.095/share (ya no es generalización desde 3).
  - **El shrink bajo pago real NO es 0.61 — es 0.17-0.55 y heterogéneo** (RJTT 0.55, KORD 0.35, LFPB
    0.31, KLGA 0.17). El 0.61 medido bajo obs-IEM estaba contaminado por el artefacto de resolución.
    Patrón clave: el shrink EMPEORA cuando el edge aparente crece (ap 0.20→sh 0.55; ap 0.34→sh 0.17)
    = firma del optimizer's curse. **Kelly con edge aparente sobre-apuesta ~3-6×.**
  - Bootstrap por BLOQUES SEMANALES (71 bloques, 4703 trades — clima autocorrelado, N efectivo <<
    N nominal; el CI por bloques da ~2× más ancho que iid): edge_re medio **+0.063/share, CI90
    [+0.032,+0.096], P(≤0)=0.01%**; PnL CI90 [$4.7k,$30.9k], P(≤0)=0.8%.
  - H1/H2 bajo market-settled: edge_re +0.061 vs +0.064 (estable); shrink H1→H2 generaliza (predice
    +0.051 vs +0.064 realizado, conservador).
**El edge real es ~+6¢/share** (el "10-14%" anterior era ROI por bin, sesgado alto): chico, positivo,
OOS-estable, block-significativo.
**STRESS DE FILLS (1er paso, half-spread):** hs 0.02→0.03→0.05 (taker, pool limpio): edge_re
+0.063→+0.056→**+0.042/share**, PnL +17.4k→+13.9k→+8.1k. Sobrevive spread ×2.5 — cae menos que la
aritmética ingenua porque EDGE_MIN_NET filtra los marginales (n 4921→4383). Falta el 2º paso:
market-impact / tope por profundidad real del book (requiere book data o supuesto de impacto).
**SIZING — hallazgos de la ronda de validación (importante, corrige la hipótesis "Kelly sobre-apuesta"):**
  - Los shrinks POR ESTACIÓN (0.17-0.55) **fallan su OOS individual** (KORD H1 0.38→H2 0.08, RJTT
    0.89→0.28) — ruido con forma de señal, mismo patrón que d,e de EMOS. Solo el POOLED es estable
    (H1/H2 = 0.17/0.22). Se agregó `EDGE_SHRINK=0.17` (config, cota conservadora, solo sizing, no gate).
  - **PERO el shrink resultó INERTE en el backtest: el cap fijo de $40/mercado domina a Kelly** (con
    equity ≥$2k hasta el Kelly shrinkeado supera $40 → el cap corta siempre). Kelly NO es el mecanismo
    operativo de sizing en este régimen; los CAPS lo son. ROI/maxDD idénticos con y sin shrink.
  - **Anatomía del maxDD −44%** (equity market-settled ✓, corrida CONJUNTA con GROUP_CAP activo ✓,
    KLGA+KORD comparten US_E ✓): goteo de 47 días (31-jul→16-sep-2025), peor día −5.5%, solo 7 días
    ≤−5% → **el kill-switch diario NO frena este patrón** (solo bloquea entradas del día del −5%).
    Composición: 594 trades TODOS KLGA (2025 = mono-estación en el pool limpio), 12.6 trades/día
    (múltiples buckets simultáneos), hit 41% vs 50% global, edge_re −0.011 — 6 semanas de edge
    genuinamente negativo concentrado en una estación. **En 2026 (4 estaciones) el maxDD es −14.9%**
    → gran parte del −44% es artefacto de cobertura histórica; en vivo (6 ciudades) mejor aún.
  - **Curva fina aparente→realizado: NO monótona, sin techo limpio** (los bins altos 0.4-1.0 son
    positivos +0.09/+0.14). Bolsón NEGATIVO identificado: edge_ap 0.3-0.4 × token YES × px<0.5
    (edge_re −0.03, PnL −$1.6k) = "discutirle fuerte a un precio barato" — candidato a refinar la
    ENTRADA a futuro, pero cambiar selección requiere su propia validación OOS; NO horneado.
  - **FRENO TRAILING implementado (3 iteraciones — las 2 fallas documentadas a propósito):** el
    mecanismo real del DD es "régimen sostenido de error negativo", no "concentración" (el peor DD
    2026 fue mono-GRUPO: KLGA y KORD perdiendo JUNTAS con hit 39-43% mientras EU/ASIA ganaban —
    correlación sinóptica dentro de US_E; un cap de concurrencia por estación NO lo habría frenado).
    Umbrales por REGLA EXTERNA (múltiplos del kill diario, no tuning sobre el DD observado):
      v1 ventana 30d ×0.5 → FALLA mecánica: el goteo real (47d) > ventana; el pico sale de la
         ventana a mitad de la sangría y el freno se suelta solo. maxDD −41.5% (casi nada).
      v2 ATH con stop total a −20% → FALLA: estado ABSORBENTE. Con cero entradas el equity no puede
         recuperarse; el bot quedó muerto 1 año (n 4960→323). Un stop sin re-entrada no es un freno.
      **v3 ATH GEOMÉTRICO (final): cada −10% adicional desde el ATH divide el tamaño por 2** (−10%:
         ×0.5, −20%: ×0.25, ...), sin escalón de cero. Resultado: maxDD 2025 −44.1%→−31.3%
         (−$2,000 desde pico $6.4k), 2026 −14.9%→−14.4% (−$2,718 desde pico $18.8k), ROI +865%→+766%
         (costo modesto), sharpe 2.53→2.77 (mejora: corta exposición exactamente en rachas malas).
         Aplica al COSTO FINAL post-caps (si multiplicara solo a Kelly, el cap $40 lo dejaría inerte).
  - **VALIDACIÓN DEL FRENO (post-implementación, exigida y pasada):** split por régimen con-vs-sin:
    2025 (el régimen que lo motivó): ret +430%→+332%, maxDD −44%→−31%, sharpe 2.45→2.57. **2026 (el
    régimen que NO lo motivó): ret +82%→+100% y sharpe 3.61→3.79 — MEJORA ambos.** La forma funcional
    generaliza entre regímenes; no es un parche ajustado a la racha 2025.
  - **GROUP_CAP_FRAC es una palanca MUERTA a este capital (no tocar, no "optimizar"):** en el pico
    del DD correlacionado 2026, la exposición real de US_E fue máx 10.8% / media 5.4% — nunca cerca
    del cap 30%. El cap $40/mercado × pocos mercados simultáneos lo domina (mismo mecanismo que dejó
    inerte al EDGE_SHRINK). Bajarlo a 0.10-0.15 no cambiaría nada observable.
  - Interacción freno×shrink: SIN patología — en el peor tramo frenado el tamaño medio fue $14.9
    (p10 $8.5), viable; las fees son proporcionales al tamaño, no hay erosión por posición chica.
  - La concurrencia por estación/día se DESCARTÓ como fix principal (atacaba el artefacto 2025 del
    dataset, no el mecanismo — confirmado con la descomposición del DD 2026, que fue mono-GRUPO).

## 6.1 Ventana de datos real disponible (importante para el próximo que baje data)
Polymarket weather por TAG (`104596`) solo cubre feb-2026→ porque ese tag es reciente. La historia
2025 existe pero tageada distinto (tag 84 'weather') → se recupera enumerando por SERIE por ciudad
(`CITY_SERIES` en `download_polymarket.py`: nyc 10005, chicago 10726, london 10006, paris 11168,
tokyo 10740, seoul 10742). Cobertura real: **NYC y London llegan a ene-2025 (~15 meses); Chicago/
Paris/Tokyo/Seoul son casi solo 2026**. El downloader ya usa enumeración por serie. obs (IEM) y
forecasts (Previous Runs) cubren años sin problema; el límite es el mercado.

## 7. Qué falta (en orden de prioridad real)

0. **[HECHO para el payout] Resolución market-settled.** `resolve="market"` paga contra la resolución
   real (Gamma `outcomePrices` → columna `resolved` de markets.csv). Esto ELUDE el delta IEM-vs-WU
   para el PnL sin tener que cazar las 6 estaciones WU. Corrida real: `python run_backtest.py --data
   real --resolve market`. Pendiente menor: el bug de UNIDAD (London °F 2025/°C 2026) y la estación
   equivocada ya NO afectan el payout (usa Gamma), pero SÍ afectan las métricas de CALIBRACIÓN
   (CRPS/Brier/reliability, que siguen contra obs IEM). Si se quiere calibración correcta para London:
   0a parsear °F/°C del `groupItemTitle` y normalizar obs↔bucket; 0b bajar obs de la estación WU real
   por ciudad (London no es Heathrow). No bloquea el veredicto de PnL.
-3. **CALIBRADOR V2 ADOPTADO (2026-07-09, por el LAB de experimentación `scripts/calib_lab.py`).**
   6 variantes evaluadas en walk-forward 60d (2026-05-10→07-08, lead 2, fits mensuales, sin lead-1):
   V0 base 39.6% hit / MAE 1.13 → **V2 (= V0 + sesgo ROLLING 60d por estación) 42.8% / 1.04** ✓.
   V3 (entrenar solo 90d) EMPEORA (36.4%) — expanding + corrección reciente le gana a re-entrenar
   corto. Mejoras por estación: KLGA 31→47%, KORD 51→63%, RCSS 18→33%, RKSI 23→32%. Mecánica:
   `data/station_bias.json` (media de pred−real últimos 60d por estación; lo refresca calib_lab
   SEMANALMENTE vía run_check.ps1) y lo restan `accumulate_predictions` y el dashboard en vivo
   (cableado verificado numéricamente por workflow: ZBAA/RCSS delta ≈ |bias| exacto).
   **CAVEATS del verificador adversarial (3×CONFIRMED):** el RANKING V2>V0 es robusto (las fugas
   conocidas — bug #5 en las m, ld=1.5 — son common-mode entre variantes), pero el NIVEL absoluto
   (43%) no transfiere a live: winner's curse de elegir 1 de 6 (~1-2pp), frescura inflada de
   previous-runs, y el deploy refresca el bias semanal (el lab lo evaluó diario). El margen real
   lo dirá la validación forward (check_predictions ya scorea el modelo corregido desde mañana).
   NO adoptado (probado y perdió o rechazado): ventana-90 de training, slope local (V5 40.8%),
   día-de-semana/feriados (sin efecto físico en tmax — carnada de overfit, rechazado por diseño).
   PRÓXIMO experimento: +5 modelos disponibles en Previous-Runs (meteofrance/gem/ukmo/jma/knmi).
   **SWEEP DE VENTANA DEL BIAS (2026-07-12, pedido de Santiago "probá 90/60/30/7 días"):**
   `scripts/lab_bias_window.py`, 12 variantes (media 7/14/30/45/60/90/expanding, EWMA 7/15/30,
   mediana 30/60) en el harness walk-forward de lab_v7, eval 05-10→07-11, verificación adversarial
   de 4 agentes (look-ahead LIMPIO: 756/756 filas reproducidas; scoring floor 12/12 a mano).
   **Veredicto: W60 (=V2) QUEDA.** Exacto lead 2: W7 32.3% / W30 32.7% / W60 33.1% / W90 32.7% —
   la ventana 60 ya era la óptima de la familia. MED60 (mediana 60d) dio +1.6pp pero es EXACTAMENTE
   E[max de 12 variantes | hipótesis nula] (+1.60pp; p ajustado por selección 0.44; MED30 pierde →
   sin dosis-respuesta). Mismo patrón que mató a V6/V7/V8. **Rescate: SOMBRA pre-registrada** —
   regla fijada ANTES de ver datos nuevos (header de lab_bias_window.py): adoptar MED60 solo si con
   targets ≥2026-07-12 y n≥45 días, delta>0 y P(≤0)<0.05 en bootstrap por bloques de día; una sola
   mirada al llegar a n=45, sin peeking. run_check.ps1 la acumula semanalmente.
   **FIXES OPERATIVOS (misma sesión, cierran el pendiente del refresh no-op):** calib_lab.py D1
   DINÁMICO (min(ayer, cobertura de backfill_check.csv)) + invalidación del cache lab_m.csv si no
   cubre D1; backfill_check.py `--extend` (continúa hasta ayer con solape de 3 días para rellenar
   labels tardíos de Gamma/IEM, coalesce que nunca pisa un label resuelto con None, train-until
   automático) y guard anti-pisado sin `--append`; run_check.ps1 encadena extend→lab→sombra.
   Track vivo re-scoreado 07-08→07-11 (n=69, todas las predicciones acumuladas): calibrado 36%
   exacto vs crudo 26%, pwin 0.250 vs 0.180, CRPS 0.740 vs 0.993 — la calibración aporta en vivo.
   **ESTUDIO POR CIUDAD DE COMBINACIONES DE MODELOS (2026-07-12, pedido "combiná todos los modelos,
   90 días, ciudad por ciudad")**: `scripts/lab_city_models.py` sobre `data/lab_m8.csv` (8 modelos
   Previous-Runs point-in-time 02-10→07-11, `fetch_lab_m8.py`) + `data/nbm_backfill.csv` (NBM 13z
   D-1, 91 días KLGA/KORD, avail real S3). 14 variantes/estación, todas con bias60+sigma rolling
   propios, walk-forward, eval 04-12→07-11 (n=1080 pooled lead 2). RESULTADO:
   - Por estación NADA sobrevive la corrección por selección (p_adj≥0.05; "S_jma en LEMD" = curse,
     jma es el peor modelo global −8pp). NO hacer selección de modelos por estación (= V8, otra vez).
   - Pooled: TODA la familia multi-modelo es positiva vs V2 (MED8 +2.0pp p_dia=0.050, W8 +1.8pp,
     ALL8 +1.2pp) y TODOS los singles negativos → estructura con mecanismo, no sorteo. En DÉBILES:
     E3 (crudo 3 modelos + bias, sin EMOS) +2.9pp p=0.054 → el shrink del EMOS lastima a las débiles.
   - MAE por modelo (60d): **gefs ROTO en RKSI (5.74° vs 1.47 icon)**; en ZSPD los mejores son
     ukmo/knmi/gefs (el bot usa icon/ecmwf que ahí son los peores); EDDM: gefs 2.51 vs icon 0.97.
   - NBM decepciona para exacto (KLGA −7.8pp; MAE buena 2.05 pero el floor exacto no mejora).
   **3 SOMBRAS PRE-REGISTRADAS (regla en header de lab_city_models.py, targets ≥07-12, n≥45d, UNA
   mirada)**: H1 MED8 pooled (α=.05), H2 W8 pooled (solo si H1 falla, α=.025), H3 E3-en-débiles
   (α=.05). run_check.ps1 las acumula semanal (refresca lab_m8 + nbm_backfill, reanudables).
   **TWEETS DE TRADERS (2026-07-13, 16 PDFs aportados por Santiago) — auditados y testeados:**
   la mayoría es marketing con referidos (Hermes agent, "$240→$82K", weatherscan_bot; el "24W-4L"
   es la MISMA serie sin precios que FASE 0 ya descartó). Las 2 ideas técnicas (AlterEgo) se
   testearon con la vara de siempre: (a) EWMA bias α=0.1-0.3 (≈half-life 2-5d) → EW2/EW3/EW5 en
   lab_bias_window: 33.3/33.2/32.7% vs W60 33.1% = ruido, y PEOR en los días vivos (28.6%); aplica
   a quien usa modelo crudo, nuestro EMOS+bias60 ya lo cubre. (b) pesos por accuracy 30d (W830) en
   lab_city_models: +1.39pp p=0.16, PEOR que el W8-60d ya en sombra. NADA se adopta. Lo rescatable
   REAL de los perfiles (tenkiyoho 80% WR comprando colas 1-2¢ vivas; vip68/cmcbrown = China soft):
   flag LONGSHOT VIVO en playbook.py (bucket ≤10¢ con pbot ≥ max(0.15, 3×precio), informativo,
   size chico) + refuerza la prioridad QWeather/CWA y el scout de ciudades chinas. También se
   corrigieron chars no-cp1252 preexistentes en playbook.py (≤, ⚠, ✓ reventaban la consola).
   **LAB ML (2026-07-13, pedido "ciencia de datos profesional"):** `scripts/lab_ml.py` — gradient
   boosting (HistGB, 2 losses fijados a priori) sobre 18 MESES de forecasts.csv + lab_m8, features:
   anomalías de 8 modelos, s2, spread, armónicos estacionales, errores rolling 15/60d, estación
   categórica; pooled 12 estaciones en °C, refit mensual walk-forward. RESULTADO (n=1080 pareadas
   vs V2): GBMm −0.28pp PIERDE; GBMq (mediana) +1.57pp p=0.171 pero INESTABLE (H1 +4.8/H2 −1.7,
   LIVE 29% vs 44%) → NO adoptado, queda como **SOMBRA H4** (targets ≥07-13, n≥45d, α=0.025, una
   mirada; run_check.ps1 lo corre semanal tras lab_city_models). Patrón clave (3ª evidencia
   independiente tras E3-débiles y W8): el ML gana SOLO en las débiles (RCSS 15→27, KLGA 36→43,
   LEMD 31→43) y pierde donde V2 es bueno → **el techo del exacto está en las FUENTES, no en el
   calibrador**. Techo estructural: con MAE ~1.0° a lead 2, el máximo teórico de exacto en buckets
   de 1°C es ~32-38%; para 60% exacto haría falta MAE ~0.5°, que ningún centro global logra a este
   lead. El 44% vivo ya está en/sobre el techo (mezcla °F de 2 grados + racha favorable).
   **AUDITORÍA WU-vs-IEM (2026-07-13, pregunta de Santiago "estás comparando contra WU?"):**
   `scripts/lab_wu_ground_truth.py`. NO hace falta scrapear WU: la resolución de WU YA está en Gamma
   (win_mkt = lo que pagó = lo que WU dijo), y backfill_check tiene win_mkt (WU) junto a max_real
   (IEM, sobre lo que calibramos). RESULTADO por estación: **10 de 12 coinciden 98-100% (todas las
   °C), delta=0.00 — IEM≡WU, scrapear WU no ganaría NADA ahí.** El gap existe SOLO en las °F: KLGA
   63% acuerdo (delta −0.5°F, WU corre bajo IEM), KORD 84% (delta −0.5 pero std 6.4 = inestable).
   Y el test walk-forward de corrección: aplicar el offset −0.5°F a KLGA/KORD NO mejora el hit contra
   la resolución real (KLGA −2pp, KORD 0) → el gap IEM-WU no es un sesgo corregible, es ruido de
   borde de bucket. **Cierra el riesgo T2: el ground-truth ya es correcto para 10/12; en NY/Chicago
   el delta es chico y no-corregible.** (Sombra H5 registrada por si cambia con más N, α=0.05.)
   **TIMING DE ENTRADA con PRECIOS REALES (2026-07-13, hipótesis de Santiago "entrar temprano/
   madrugada porque más incertidumbre = precio blando = mejor %"):** `scripts/lab_entry_timing.py`
   sobre data/prices.csv (18m, 5 estaciones, orderbook real) + picks del bot reconstruidos (EMOS
   leads 2 Y 3 walk-forward). **v2 AVAIL-HONESTA (la v1 usaba el pick lead-2 a 72-48h, cuando aún
   no existía — look-ahead auto-detectado y corregido)**: cada bin usa SOLO el pick disponible
   (53-31h→lead-3; ≤31h→lead-2). PnL/share TOP-1 taker: **53-31h(ld3) +2.2¢ · 31-24h(ld2) +2.4¢ ·
   24-12h +0.3¢ · 12-6h −0.1¢ · 6-0h −0.9¢** (maker: +4.2/+4.4/+2.3/+1.9/+1.1¢). SANTIAGO TIENE
   RAZÓN: entrar temprano (tras la llegada de la corrida, ≥24h) le gana a entrar tarde por ~3¢/share
   — el ganador cotiza 0.32-0.34 temprano vs 0.37 al final, y el hit apenas cae (36-38% vs 38%).
   El bin exacto 31-24 vs 24-12 está borroso por la frescura residual de previous-runs (bug#5:
   el "lead-2" reconstruido puede contener la 18Z); lo robusto es TEMPRANO>TARDE. **Top-2 par:
   +EV solo temprano y maker (+2.9¢); top-3 SIEMPRE diluye (3er bucket −7¢) → concentrar top-1/2.**
   Por estación: el efecto vive en KORD (+9.8¢ madrugada) y KLGA (+5.4¢); LFPB mejor aún antes
   (ld3 +1.9¢); RJTT/RKSI NEGATIVAS en todo bin (Asia = nowcast, sin edge de timing). **BOOKS
   REALES (books_forward, 544 snapshots): $40 fillea el 100% de las veces a cualquier hora, y el
   hs efectivo es MÁS BARATO temprano (mediana 2.4¢ a 2 días, 3.0¢ víspera, 5.9-6.9¢ mismo día)**
   → precio Y fricción favorecen la entrada temprana. Horneado en playbook.py (tag de ventana +
   reglas). CAVEAT: nivel absoluto optimista por bug#5 (common-mode); la validación fina la dará
   el forward (books_forward acumulando). Próximo: re-bajar prices.csv en el VPS (corta 06-30).
   **CAMBIOS 2026-07-13 tarde (pedidos de Santiago):** (a) **FREEZE MOVIDO a las 04:30 HORA LOCAL
   del target** (antes pico−45min) — `freeze_utc()` en dashboard.py; coincide con la hora en que
   Santiago abre los trades y con la ventana temprana validada por lab_entry_timing → el pick
   fijado ES el pick operado. Efecto esperado: el % exacto medido puede bajar 1-3pp (lead más
   largo — el pick ya no ve la 00Z del mismo día en Europa ni la obs de la mañana); es el precio
   de la coherencia pick-medido = pick-tradeado. (b) **Timeline con marcador 🔒**: payload `frz`
   + clamp server-side del mu post-freeze + etiqueta "FIJADO desde HH:MM AR" / "se fija HH:MM";
   el audit ya NO grababa revisiones post-freeze, esto lo hace VERIFICABLE a ojo. (c) **Botonera
   4 botones** (live-combo / orderbook / sync / limpiar-alertas) — regen/cache/forecasts/models/
   calib/stats/leaderboard/export quedaron dentro de live y sync; ids viejos siguen por URL.
   (d) **Limpiar alertas SERVER-SIDE** (`/action?do=alerts_clear` vacía alerts.json items,
   conserva base) → limpia en todos los dispositivos; el ✕ del panel solo oculta por navegador.
   **COHERENCIA RESUELTA (2026-07-12):** stats_page/leaderboard ahora scorean primero el payload
   inmutable `froze.mu/sg` de forecast_audit.json. Para filas legacy sin payload usan la última
   revisión pre-deadline del audit; filas sin ninguna evidencia congelada se excluyen del KPI
   oficial (predictions_forward queda solo como fallback técnico, no scoreable). La selección vive
   en la función pura `wxbt.forward_scoring.frozen_forecast` y tiene tests propios.
   **AUDITORÍA FORWARD + SOMBRA HONESTA (2026-07-12):** `validate_sources.py` tenía dos look-ahead
   operativos: comparaba la fuente local más fresca (aunque fuera posterior al freeze) contra el
   snapshot más fresco del bot. Ahora ambos lados se cortan al freeze por estación; al corregirlo,
   CWA/JMA/QWeather todavía tienen 0 targets resueltos elegibles (la evidencia previa no contaba).
   Para probar MED8/W8 sin la ambigüedad retrospectiva de Previous-Runs se agregó
   `accumulate_models_forward.py`: captura los 8 modelos en vivo para las 29 estaciones mediante
   16 requests multi-location (232/232 pares verificados) y `check_accumulation.py` controla huecos.
   `score_model_shadows.py` toma una captura coherente anterior al freeze, aplica bias60 histórico
   a MED8 y la compara pareada contra V2 congelado/Gamma. Gate inmutable: una sola evaluación con
   >=45 días, delta exacto >0 y bootstrap por día P(delta<=0)<0.05. Integrado en `run_daily.ps1`.
   **AUTOMATIZACIÓN ACTIVADA (2026-07-13):** tarea Windows `wxbt-accumulate`, diaria 12:00 hora
   local, acción `run_daily.ps1`, modo interactivo (corre con sesión iniciada o bloqueada; no con
   sesión cerrada), verificada mediante corrida manual: Last Result=0. La corrida capturó 232/232
   pares de los 8 modelos para las 29 estaciones; `check_accumulation --through 2026-07-13` OK.
   Durante la prueba `stats_page.py` resultó cuello de botella por pedir IEM serialmente por cada
   mercado: ahora usa primero obs.csv y sólo completa faltantes en paralelo; la tarea completa.
   **GATE HISTÓRICO MULTIVENTANA vs GAMMA (2026-07-13):** `lab_gate_windows.py` une los mu de
   `lab_city_models_detail.csv` con el bucket ganador oficial `win_mkt` de Gamma y compara MED8/V2
   pareado: 90d (63 días efectivos/754 mercados) 36.3→37.5%, +1.2pp p_boot=.224; 60d 36.9→37.9%,
   +1.0pp p=.269; 30d 33.5→34.1%, +0.6pp p=.404; 15d 33.3→35.6%, +2.2pp p=.217; 7d
   33.3→40.5%, +7.1pp p=.032 sin corrección. MED8 gana nominalmente las cinco ventanas, señal
   direccional consistente, pero ninguna pasa inferencia familiar: el 7d deja p≈.16 con Bonferroni
   por cinco miradas y sólo tiene 7 bloques diarios. NO adoptar aún. Este test valida el ganador
   Gamma/WU, pero los forecasts retrospectivos mantienen bug #5; la sombra forward sigue mandando.
   **SCOUT + VOLUMEN (city_scout.csv, backtest 60d + vol 30d):** candidatas que SUPERAN a la
   mayoría de las 12 actuales: **Wellington NZWN 55% exacto/83% top2/MAE 0.57/$110k · Miami KMIA
   51%/81%/$73k · Ankara LTAC 52%/72%/$75k** (tier-1); Singapore 45%/$84k, KL 43%/$76k, Shenzhen
   42%/$116k (tier-2). HK $259k (el MAYOR volumen) pero resuelve con HKO a 1 decimal sin WU →
   NO operar hasta resolver eso. Los soft-markets chinos de los tweets (Chengdu 22%, Chongqing
   28%, Guangzhou 22%) son donde NUESTROS modelos son peores — sin fuente local no ir. Gate de
   alta (sin cambios): acuerdo IEM-vs-Gamma en mercados resueltos + PEAK_HOUR medido de METAR.
   **FIX TIMELINE mercados pasados (2026-07-13):** el timeline anclaba la ventana de 24h a AHORA
   → en un mercado ya resuelto no hay trades recientes y la columna de precios salía TODA vacía
   ("—"). Fix en `build_timeline`: fetch amplio (5 días) de cada token, `end_ts = min(ahora,
   último trade real)`, grilla de 24h que TERMINA en ese ancla → se ve el movimiento de precios de
   las 24h ANTES de resolver (lo que Santiago pidió: "cómo se movió el precio y cuál fue el
   resultado"). JS anchor-aware: extremo del slider = "cierre" (no "AHORA"), "Xh antes del cierre",
   header "Δ→cierre", nota "mercado ya resuelto". Verificado en vivo: RJTT 11/07 muestra 11/11
   buckets, ganador 30°C→1.00, perdedores→0.00.
   **FIX CACHE-BUST (2026-07-13, causa raíz de "se rompió el timeline"):** el navegador cacheaba
   `wxbt.js`/`wxbt.css` y servía JS viejo tras un cambio de código (`tlOpen no definida`, timeline
   con lógica vieja). Ahora los links se versionan con el mtime del asset (`wxbt.js?v=<mtime>`) →
   el browser SIEMPRE baja fresco tras un cambio. **RECORDATORIO OPERATIVO:** los watchers
   duplicados siguen siendo la trampa #1 — un `--watch` que hace auto-reload a mitad de una edición
   de código deja un hijo con código intermedio que pisa el `wxbt.js` bueno. Antes de arrancar:
   matar TODOS los python de dashboard, confirmar 0, borrar el lock, y arrancar UNO.
   **ALTA DE 6 CIUDADES (2026-07-13, pedido Santiago "integralas para que corran desde ahora"):**
   Wellington NZWN, Ankara LTAC, Miami KMIA, Singapore WSSS, Kuala Lumpur WMKK, Shenzhen ZGSZ.
   Verificadas por workflow (6 agentes, Gamma+IEM): las 6 GO — mercado VIGENTE (july-13/14 abiertos),
   ICAO de resolución en la description COINCIDE con la estación esperada (sin trampa tipo-London),
   unidad correcta, obs IEM presente, PEAK_HOUR medido de 30d METAR (NZWN 12/ruidoso invierno austral,
   LTAC 15, KMIA 13, WSSS 13.5, WMKK 14, ZGSZ 12). Series Gamma: 10902/10900/10728/11314/11510/11366.
   Metadata en show_live (STATIONS/PEAK_HOUR/DST/CITY_SERIES/CITY_STATION), download_openmeteo,
   dashboard (STATION_META + CONT_ORDER con Oceania), check_predictions (NETWORKS), wxbt/config
   (STATIONS con grupos sinópticos NUEVOS: OCE/US_SE/SEA/S_CHINA + clim synth). DST: Miami→US,
   Wellington→NZ HEMISFERIO SUR (nuevo `_NZ_DST`, verano austral cruza el año). **Onboarding
   QUIRÚRGICO** (`scripts/onboard_cities.py`): baja obs (2024-06→, 771 días c/u) + forecasts
   (2025-01→, 25010 filas) SOLO de las 6 y las APPENDEA — las 12 quedaron BYTE-EXACTAS (backup es
   prefijo exacto). Data limpia (0 NaN/s2≤0), EMOS calibra las 6 (n=556-1114), 7/7 tests, ya
   producen predicción (Miami 92°F, Wellington 15°C, Singapur 31.8°C...) y acumulan en
   predictions_forward desde hoy. CAVEAT: WSSS/WMKK tropicales → EMOS con b bajo (0.3-0.6, clima
   casi constante → shrink fuerte a clim); NZWN timing ruidoso (~1/3 días tmax nocturno). Gate de
   OPERACIÓN sigue vigente: acumular n≥15-20 días forward antes de tradearlas; el leaderboard las
   marcará cuando resuelvan sus primeros mercados.
   **+11 CIUDADES MÁS (2026-07-13 tarde, pedido Santiago "testealas antes de agregar"):** de 12
   candidatas, **11 GO + 1 NO_GO**. Backtest con nuestros modelos (`scripts/scout_test12.py`, lead-2
   + sesgo 60d, floor, eval 05-13→07-11; baseline mediana 12 actuales = 37% exacto) + análisis por
   ciudad (workflow 12 agentes: Gamma+estación+unidad+peak+factores). **Hong Kong = NO_GO**: resuelve
   por HK Observatory a **1 DECIMAL** (weather.gov.hk), no por WU/aeropuerto → rompe la regla floor;
   además HKO no tiene METAR (obs solo archive). Las 11 integradas, tieradas por backtest:
   - **TIER 1 (baten baseline claro):** Austin KAUS 47.5%/top3 92% · Dallas KDAL 47.5% · Houston
     KHOU 45.8% (las 3 texanas = clima continental seco, máxima muy predecible) · Mexico City MMMX
     40.7% (pero top2 bajo 54%).
   - **TIER 2 (~baseline):** Toronto CYYZ 37.3% (MAE 0.82) · São Paulo SBGR 36.7% · Helsinki EFHK 36.7%.
   - **TIER 3 (DÉBILES, solo acumular NO operar):** LA KLAX 32.2% · SF KSFO 30.5% (MAE 2.55 = peor,
     microclima costero/marine-layer) · Buenos Aires SAEZ 30.0% (invierno, buckets angostos) ·
     Atlanta KATL 25.4% (convección vespertina).
   PEAK HOURS medidos DST-aware: costeros pican temprano (KSFO 13.5, KLAX 13, KHOU 14), inland ~15.5
   (KDAL/KATL/KAUS), MMMX 14 (altura+conveccion, NO DST), EFHK 16 (60°N día largo, EEST), SBGR/SAEZ
   invierno austral. Onboarding QUIRÚRGICO (onboard_cities.py, +11): 18 anteriores BYTE-EXACTAS,
   +55035 forecasts/+8481 obs limpias (0 NaN), EMOS calibra las 11, **7/7 tests con 29 estaciones**,
   ya acumulan. Grupos sinópticos: US_W/US_TX/US_SE/US_E/S_AMER/MEX. Series Gamma verificadas.
   Gate de OPERAR sigue: n≥15-20d forward; el leaderboard las marca al resolver.
   **VERDAD sobre el % exacto (Santiago preguntó "mejoraste el exacto?"):** NO — el exacto VIVO
   sigue en ~40% (n=47, top2 66%, top3 85%, MAE 0.93), que es el techo estructural (MAE ~1° en
   buckets de 1° → máximo teórico ~32-38%; el 40-44% incluye °F de 2° más anchos). Lo que se arregló
   fue la MEDICIÓN (gating resolved-only) y se SUMARON ciudades más fáciles (texanas 46-48%) que
   suben el promedio del PORTFOLIO, pero el modelo por-ciudad no mejoró. La palanca real sigue siendo
   fuentes locales (débiles) + timing de entrada temprana + selección de apuesta.
   **LEADERBOARD por-ciudad + timestamp (2026-07-13, pedido Santiago):** cada fila del leaderboard
   es CLICKEABLE → despliega un GAMELOG estilo app de apuestas (fecha | ganó WU | pick bot |
   resultado ✅ EXACTO / ✅ TOP-2 / 🔶 TOP-3 / ❌ PÉRDIDA), gamelog embebido inline (sin server),
   + barra "🕒 Tabla actualizada: DD/MM HH:MM (hora Argentina)". Verificado en browser.
   **FIXES DASHBOARD (mismo día, pedidos de Santiago):** (a) stats/leaderboard/cards solo cuentan
   mercados RESUELTOS (closed/umaResolutionStatus — antes un bucket cotizando ≥0.99 EN CURSO
   contaba como ganador e inflaba stats); (b) freeze INMUTABLE: post-deadline el top-1/2/3 se
   deriva del mu congelado con el MISMO ranking pick-first del timeline, sin re-sugerir sobre
   la marcha (los muertos se tachan conservando color); si el watcher no corría al bloqueo, el
   freeze se captura con la última revisión del audit ANTERIOR al deadline; (c) colores unificados
   pick=verde/top-2=amarillo/top-3=naranja en card+timeline+stats; (d) botón "limpiar todas" en
   alertas.
-2. **SIMULACIÓN DE ESTRATEGIA (2026-07-09, 60 días backfill, 310 mercados con precio real).** La
   pregunta de Santiago: "compramos 3 buckets para ganar <10% pero una pérdida come 4-5 aciertos".
   TENÍA RAZÓN — P&L por trade (precio+fee 2¢):
   | estrategia | PnL/trade | total | hit-mkt |
   |---|---|---|---|
   | A) top-3 buckets del bot, TODAS las estaciones (la que usa) | **−0.055** | −$51 | 70% |
   | B) top-3 solo estaciones buenas | −0.037 | −$17 | 80% |
   | C) top-1 del bot, todas | −0.125 | −$39 | 25% |
   | **D) top-1 con edge≥10¢, SOLO estaciones buenas (concentrada)** | **+0.017** | +$0.7 | 9% |
   | E) todos los buckets con edge≥10¢, buenas | +0.002 | +$0.5 | — |
   | F) favorito del mercado (control) | −0.126 | −$39 | 56% |
   **Conclusiones:** (1) la estrategia de 3 buckets es −EV: ganás 70% de los mercados pero perdés
   plata (comprar cerca del favorito = pagar lo que el mercado ya sabe + fricción). (2) La ÚNICA
   config +EV es la CONCENTRADA (D): 1 bucket, edge≥10¢, solo estaciones validadas — pero es +1.7¢/
   trade, ~45 trades en 60 días, y NO corregido por bug #5 (lead-2 tiene fuga de frescura) → el
   realista es ~0. (3) Seguir al mercado también pierde (F). **NO existe hoy una estrategia de alto
   rendimiento; el edge no le gana al mercado neto de fricción.** La asimetría que Santiago notó ES
   la razón. Sim: scratchpad/strategy_sim.py.
-1. **AMPLIACIÓN A 12 CIUDADES (2026-07-08, pedido de Santiago por top-volumen).** Nuevas: Shanghai
   ZSPD (serie 10741), Madrid LEMD (11345), Beijing ZBAA (11363), Munich EDDM (11272), Taipei RCSS
   (11346), Milan LIMC (11343) — las 6 verificadas contra las REGLAS de Gamma (estación + °C entero;
   dos trampas tipo-London confirmadas OK: Milan resuelve en MALPENSA no Linate, Taipei en SONGSHAN
   no Taoyuan). IEM las tiene todas (CN/ES/DE/TW/IT__ASOS, ICAO completo). Historia bajada (obs 2021→,
   forecasts 2025→: 24k/52k filas), EMOS calibra las 12, y los 3 acumuladores forward las cubren
   (23 predicciones, 101 books, 108 ensembles el día 1). Grupos sinópticos CONSERVADORES (toda Asia
   junta, toda Europa junta → GROUP_CAP muerde más). NOTA DST: Madrid/Munich/Milan usan utc_off
   estándar +1 (verano real +2) → pico local corrido 1h, igual que LFPB desde siempre; aceptado.
   **REDONDEO WU (pregunta de Santiago "creo que redondea a menos") — verificado empírico:** para
   mercados °C NO hay redondeo en juego (la obs METAR ya es entera; EGLC/RJTT dan 99% bajo cualquier
   regla). Para °F (KLGA/KORD) el test directo es NULO (obs.csv no tiene décimas) pero la evidencia
   INDIRECTA apunta a FLOOR: los desacuerdos obs-IEM-vs-ganador-WU son casi TODOS +1°F por encima
   (KLGA 143/146, KORD 28/28, moda exactamente +1, tasa ~17-28% ≈ la predicha por floor-vs-halfup).
   No separable de un sesgo de muestreo de la fuente (IEM captura picos entre METARs que WU no ve).
   PENDIENTE decisivo: bajar METAR crudo con T-group (décimas) de KLGA/KORD. HALLAZGO COLATERAL:
   **LFPB da 81% de acuerdo siendo °C** (debería dar ~99% como EGLC/RJTT) → hay un problema de
   fuente/ventana en París además de todo lo anterior; misma familia que RKSI 78%.
   **TIMING (convergencia de precios, 18 meses):** el precio del ganador está PLANO 72h→24h antes del
   cierre (0.30→0.33) y acelera dentro de las últimas 24h; en KLGA los repricings coinciden con las
   corridas 00Z (05-08 UTC) y 12Z (18-19 UTC); en Asia domina el nowcast del pico. Guardado en
   data/timing_analysis.json (el dashboard lo muestra). Es microestructura, NO evidencia de edge.
   **HIPÓTESIS DE ESTRATEGIA (Santiago, 2026-07-08 — a testear con data forward, NO implementadas):**
   (a) **Deadline de decisión:** fijar una hora local límite (p.ej. ~2h después de que llega la
   corrida 00Z, ≈08-09 local para US/EU) en la que el bot DECIDE y no re-decide más: después de esa
   hora la información nueva es el termómetro en vivo, donde el mercado tiene el mismo acceso (en
   Asia el nowcast domina los saltos) → nuestro edge, si existe, vive en la ventana entre corrida
   nueva y repricing del mercado, no en perseguir el día. Consistente con la curva de convergencia
   y con REENTRY_COOLDOWN (anti-churn). (b) **Compra escalonada:** entrar el bucket central del bot
   y, si el precio confirma (sube), agregar los adyacentes que quedaron baratos al moverse la masa
   — hipótesis de microestructura para capturar la re-distribución; requiere books intradía para
   testear. Ambas se evaluarán con predictions_forward + books_forward + la curva de convergencia
   cuando haya muestra (≥30d).
   **BACKFILL WALK-FORWARD 30d (2026-06-08→07-08, entrenado ≤06-07, 12 estaciones, lead 2):**
   hit calibrado 35% vs crudo 28%; prob-al-ganador 0.224 vs 0.182; CRPS 0.820 vs 0.937 (n=961).
   La calibración APORTA sobre el crudo de forma consistente. Por estación: LEMD 68% (21/31),
   RJTT 45%, EGLC/LFPB 42% — vs **RKSI 10% (3/31)**, RCSS 19%, ZBAA 23%: Seúl sigue roto (datos),
   y Taipei/Beijing muestran sesgo frío del modelo (~−1.5°C) — en observación antes de habilitarlas
   para cualquier cosa. Lead-1 EXCLUIDO de conclusiones (nowcast, bug #5).
0. **BUG #5 (2026-07-08) — FUGA DE NOWCAST EN LEAD-1: EL VEREDICTO DE EDGE QUEDA INVALIDADO
   PENDIENTE DE VALIDACIÓN FORWARD.** Encontrado por verificación adversarial (workflow de 5
   agentes) al construir el backfill de predicciones. La API Previous-Runs ancla las columnas al
   VALID TIME: para fechas pasadas, `temperature_2m` (lead 1) devuelve por cada hora ~la corrida
   más reciente/análisis (day0 = "predicted 0h before valid time") → el tmax "lead 1" de
   `forecasts.csv` es un NOWCAST del propio día (verificado: MAE 1.3°F con aciertos exactos vs
   4.1°F del lead 2), pero su `avail` declarado era init+lag (~05-07 UTC) → **violación del
   invariante #2**: el backtest sirvió esa m horas antes de que su contenido existiera. Los leads
   2-3 tienen una versión menor del mismo problema (~14-17h de frescura no declarada: previous_day1
   a la hora del pico = corrida 18Z del día anterior, no la 00Z).
   **Cuantificación (pool limpio, market-settled, taker, freno+shrink):** baseline ROI +766% /
   edge_re +0.060 → sin lead-1: **+49% / +0.028** → sin lead-1 y avail ld2/3 corrido +17h:
   **−5.6% / +0.014**. La mayor parte del edge validado era la fuga. Los 4 checks + bootstrap + OOS
   eran correctos SOBRE los datos; los datos mentían el avail.
   **Qué sigue en pie:** resolución market-settled, freno geométrico (mecánica), infra de books/
   fills, y — ahora central — los acumuladores forward (`predictions_forward.csv` ES point-in-time
   de verdad: se fetchea en el momento y las horas futuras son forecast genuino). **Qué NO:** el
   +6¢/share, el ROI, el shrink 0.17-0.22 y todo número derivado del backtest con lead-1.
   **Caminos:** (i) esperar los 90 días de forward (el plan ya vigente — la validación forward pasa
   de "confirmar" a "medir por primera vez el edge honesto"); (ii) re-derivar historia init-anclada
   desde archivos GEFS/ECMWF crudos (AWS Open Data) — proyecto grande; (iii) operar el backtest
   solo con ld2/3 y avail+17h como cota inferior (edge_re +0.014 — hoy no cubre fricciones).
   Fixes aplicados: `backfill_check.py` etiqueta lead-1 como nowcast; half-up explícito en
   `check_predictions.py`/`download_iem_obs.py` (round() de Python es banker's y en buckets de 1°C
   cambia el ganador); comentarios de leads corregidos en config/show_live.
1. **Fills — ENVOLVENTE PARAMÉTRICA hecha (paso 1), da un umbral duro.** Stress de `hs` (proxy de
   fricción total spread+impacto) sobre engine actual (freno+shrink), market-settled:
   | hs | POOL ROI | KLGA ROI | edge_re/share |
   |----|----------|----------|---------------|
   | 0.02 | +766% | +514% | +0.060 |
   | 0.05 | +183% | +56%  | +0.031 |
   | 0.08 | −13%  | −12%  | +0.013 |
   | 0.10 | −29%  | −29%  | +0.001 |
   **Break-even ≈ half-spread 6-7¢** (idéntico pool y KLGA — KLGA es más frágil en ROI pero mismo
   punto de muerte). Con `hs`=0.02 actual hay ~4-5¢ de headroom para spread-real-extra + impacto +
   slippage. edge_re/share cruza 0 en hs≈0.09. **La pregunta ya no es "sobrevive fills?" sino "el
   half-spread efectivo real (incl. impacto para $20-40) está bajo 6¢?"**. Paso 2 (books en vivo):
   muestrear CLOB /book de mercados NYC en la ventana de entrada (24-72h), ponderado a KLGA (concentra
   turnover). CAVEAT temporal fuerte [ASUNCION]: la liquidez se densificó recién feb-2026, el book de
   HOY sobre-estima la profundidad de 2025 (donde vive el edge mono-KLGA) → el impacto real 2025 pudo
   ser peor; tratar el book actual como cota OPTIMISTA, no como la verdad histórica.
   **Spot-check de book en vivo (paso 2, PRELIMINAR — N=1 mercado, hoy):** único NYC vivo en ventana
   (July-9, 33h a cierre). Half-spread top-of-book mediano 0.7¢. Half-spread EFECTIVO para orden de
   $40 caminando el book (impacto incl.): buckets líquidos ATM ~1¢ (82-83→1.2¢, 84-85→0.8¢), bucket
   fino 86-87→5.2¢ (solo 5 shares en el mejor ask). Lectura: en los buckets LÍQUIDOS la fricción real
   (~1-2¢) está MUY debajo del break-even (6-7¢) → el edge plausiblemente sobrevive fills realistas
   CON MARGEN; los buckets finos rozan el break-even (el cap $40 + MIN_ENTRY_PRICE ya limitan ahí).
   PERO: N=1 mercado, liquidez 2026 (no 2025). Validación real = acumular snapshots de book FORWARD en
   la ventana de entrada, muchos mercados, ponderado a KLGA (no se puede reconstruir el book 2025).
2. **De-sesgar el sizing.** El shrink RE-medido bajo market-settled es **0.17-0.55 por estación**
   (el 0.61 anterior murió con el artefacto que lo generó) y empeora con el edge aparente → Kelly
   sobre-apuesta ~3-6×. Opciones: shrink por estación, shrink en función del edge aparente, o subir
   EDGE_MIN_NET. Aplicar antes de Kelly, re-validar H1/H2.
3. σ/colas: **NO tocar** — con 18 meses el σ calibrado quedó bien (std(z)=0.93, exc_kurt 1.16). La
   inestabilidad/colas de la 1ª sesión eran overfit de d,e con 6 meses, no la familia Normal.
4. **Validación forward — CORRIENDO desde 2026-07-08, gatea FASE 5** (bajar los 2 `[ASUNCION]`
   load-bearing a `[VERIFICADO]`/`[FALSADO]`). Los DOS scripts escribieron su 1er snapshot:
   - `scripts/accumulate_ensemble.py` — s2 modelado vs varianza real entre miembros.
   - `scripts/accumulate_books.py` (NUEVO) — book real en la ventana de entrada; half-spread EFECTIVO
     caminando el book para $40. Día 1: hs_eff mediano 1.3¢ en 6 ciudades (<<6¢ break-even). Diseño:
     NO captura todos los leads en una corrida; la cadencia diaria arma el corte por time-to-close.
   Ambos `--date`, append-only, idempotentes, correr **1×/día**. Criterio de éxito ~90 días
   (2026-10-06): hs_eff ≤ ~2¢ ponderado a KLGA; s2_real ~ s2 sin sesgo. FALTA: agendarlos (Task #4).
5. **FASE 5 correctamente separada (2026-07-08): acumular ≠ paper-trading.** Los `[ASUNCION]` se validan
   ACUMULANDO con el mercado corriendo, NO operando (paper-trading los CONSUME: si pierde 12 semanas no
   se distingue `[ASUNCION]`-malo de edge-modesto). Paper-trading GATEADO tras los 90 días. Capital: NO
   es binario −31%/−$620; el edge es per-share → $500 cobra la misma señal (peor caso −$155); decidir
   DESPUÉS. Prerequisito adicional: `RUNBOOK.md` (modos de falla) con los HALT como chequeos automáticos
   (Task #5). Si al validar el book real da hs_eff > 6¢ → edge real pero no tradeable a esta liquidez;
   frenar sin conectar.

### Auditoría honesta de modelos init-anclados (2026-07-13)

- `scripts/backfill_single_runs.py` usa Single Runs con inicialización explícita y una latencia
  conservadora anterior al freeze. Resultado: 19.197 pronósticos, 90 targets, 29 estaciones y
  8 modelos globales, sin violaciones `avail > freeze`.
- Validación temporal anidada: desarrollo 10/05-20/06, holdout intacto 21/06-11/07. La receta global
  única falló (32,5% -> 31,7%). El selector congelado por ciudad pasó el gate: **32,4% -> 39,6%**
  exacto (+7,2pp; bootstrap por día p=0,0085; CI90 +2,1 a +12,4pp) y top-2 sin cambio (64,8%).
- Se backfillearon además modelos regionales de alta resolución. Su selector DEV perdió exactitud
  en holdout (**39,4% -> 37,3%**, p=0,7603); no se promueve. La investigación conserva resultados
  negativos para evitar repetir variantes.
- Open-Meteo gratuito es no comercial. Si esto se usa para trading real corresponde plan comercial
  o self-host de las fuentes abiertas. Gamma continúa siendo el oráculo autoritativo del payout.
- Prueba posterior de **MOS físico multivariable** en KLGA/KORD/LEMD/EGLC: corridas exactas de
  HRRR/NBM/UKV/ICON-EU/ARPEGE-EU, 560 filas y 19 features (curva térmica, humedad, nubes,
  radiación, precipitación, viento). Algoritmo elegido únicamente en validación: ExtraTrees D2.
  Test final 28/06-11/07: CITYX1 53,6% vs MOS 48,2% exacto (−5,4pp, p=0,9559); top-2 igual
  80,4%; MAE mejora 1,141 -> 1,057. Resultado: señal para error continuo, **no para bucket exacto**.
- Clasificador directo del offset de bucket, expanding walk-forward (367 mercados): 43,9% CITYX1
  vs 43,3% directo; rechazado. Optimizar explícitamente 0/1 no venció al selector simple.
- Consenso histórico CLOB+CITYX1 a freeze−3h: 42,6% -> 46,8% (+4,3pp) en test, pero p=0,1088;
  política por estación +2,1pp, p=0,235. Señal positiva insuficiente. `MKTWX1-20260713` queda en
  sombra forward desde 14/07 con mezcla fija 50/50 y gate de 45 días, sin tocar producción.
- Expansión CITYX2 a las 17 estaciones nuevas, usando un holdout Gamma que no había sido inspeccionado:
  1.515 labels oficiales; DEV 10/05-20/06, TEST 21/06-11/07. Exacto 31,6% -> **41,8%** (+10,2pp,
  p=0,0001), top-2 63,8% -> 67,8%. Agregado independiente de las dos cohortes: 604 mercados,
  29 ciudades, baseline 32,0% -> **CITYX2 40,9%** (+8,9pp, bootstrap p≈0), top-2 66,6%.
  Se promueve a sombra `CITYX2-20260713`, no a V2 productivo; offsets nuevos usan Gamma+IEM.
  La mejora es estable por semana (+10,4pp, +11,3pp y +5,0pp) y al excluir una ciudad por vez
  (delta mínimo +7,7pp, máximo +9,6pp). Hubo 129 aciertos exclusivos de CITYX2 contra 75 del
  baseline. `scripts/playbook.py` muestra la predicción CITYX2 y si coincide con V2, pero la marca
  explícitamente como SOMBRA y no modifica ninguna acción hasta completar el gate forward.
- **Corrección del ground truth °F (2026-07-13):** `lab_metar_precision.py` comparó 746 mercados
  Gamma de KLGA/KORD/KMIA/KSFO/KLAX/KDAL/KATL/KHOU/KAUS contra METAR crudo. IEM `daily.py`
  coincidía sólo 59,6-83,9%; el máximo horario `tmpf` coincidió **98,4-100%**. El T-group en décimas
  con half-up produjo el mismo resultado. La aparente mediana −0,5°F del lab WU anterior era en parte
  geometría del centro de buckets de 2°F, no un sesgo que debiera restarse al forecast.
  Se centralizó la lectura en `wxbt/observations.py`: ASOS horario y timezone local para °F; daily
  permanece para °C. Backfill, evaluación, downloaders y dashboard usan esa ruta. La reselección DEV
  con verdad corregida cambió 7/29 recetas pero **empeoró** el holdout abierto (39,6% vs 40,6% con
  recetas CITYX2; en °F 34,9% vs baseline 36,0%); no se crea CITYX3 ni se altera CITYX2 durante su gate.
- Se repitió el MOS físico sin cambiar features, grilla ni split, reemplazando únicamente las
  etiquetas KLGA/KORD por METAR horario. El algoritmo elegido en validación pasó de ET_D2 a RF_D2,
  pero en test el exacto cayó **53,6% -> 37,5%** (−16,1pp; p=0,9944), aunque MAE mejoró
  1,138 -> 0,947 y top-2 agregado quedó igual. Resultado: rechazo definitivo para exact-first;
  Open-Meteo Professional no se justifica por este MOS. Reproducible con
  `python scripts/lab_physical_mos.py --oracle-truth`.
- **CITYCONF1 exact-first (2026-07-13):** familia de abstención predefinida sobre CITYX2 usando
  únicamente dispersión/acuerdo de los ocho modelos, con cobertura DEV mínima 40%. H6a (voto
  >=50%) no alcanzó cobertura; H6b añadió umbrales sólo desde la distribución de features DEV,
  antes de mirar hits del holdout. Ganó `spread <= 1,1 buckets`: DEV 55,4% exacto con 51,5% de
  cobertura. En el holdout histórico ya abierto, CITYX2 all 40,9% vs seleccionados **45,8%**
  (+4,9pp; cobertura 43,0%; top-2 71,2%; bootstrap por día p=0,007, CI90 +1,6..+8,2pp).
  No se adopta desde ese holdout: `CITYCONF1-20260713` acumula forward desde target 14/07.
  Gate 45 días: cobertura >=35%, exacto seleccionado >=45%, delta vs CITYX-all >0 y p<0,05.
  Las primeras 76 capturas point-in-time contienen 23 selecciones; no cambia V2/CITYX ni acciones.

### NOAA LAMP/LAV exact-first (2026-07-13)

- Challenger independiente para las nueve estaciones Fahrenheit, obtenido del archivo MOS de IEM
  con runtime y forecast-time explícitos. Se elige la última corrida cuya publicación conservadora
  (`runtime + lag`) precede el freeze local: 810 station-days, 90 por ciudad.
- La familia LAV/mezcla 50-50 con CITYX y correcciones RAW/B60/X60 fue fijada antes de abrir el
  holdout. Ganó globalmente `BLEND50|X60`. Con offsets congelados al inicio del test: **39,2% →
  46,6% exacto**, top-2 67,7% → 74,6%, pero p=0,0270 no supera el gate Bonferroni p<0,025.
- Robustez con latencia +2h: 45,5% exacto, top-2 74,6%, p=0,0454. La política por ciudad tampoco
  pasa. Resultado: señal prometedora, **no promovida ni conectada a apuestas**.
- `LAMPX1-20260713` acumula una muestra forward desde target 14/07. Política inmutable:
  `BLEND50|X60`, lag de publicación LAV +2h, offsets y sigma por estación entrenados sólo con
  labels hasta 11/07. Cada fila conserva runtime/availability/freeze LAV y el snapshot CITYX2 padre;
  el auditor rechaza cualquier timestamp tardío o cambio de fórmula. Gate único a 45 días:
  exacto >39,6%, delta vs CITYX2 >0, top-2 no baja y bootstrap por día p<0,05. La sombra no modifica
  el playbook ni las acciones. Reproducible con `backfill_lamp.py`, `lab_lamp.py --frozen-test`,
  `accumulate_lamp_shadow.py` y `score_lamp_shadow.py`. Fuentes:
  NOAA MDL LAMP (`vlab.noaa.gov/web/mdl/lamp`) e IEM MOS archive
  (`mesonet.agron.iastate.edu/cgi-bin/request/mos.py?help=`).

### MOS de estación GFS/NAM/NBM (2026-07-13)

- IEM conserva runtimes explícitos de GFS/MAV, NAM/MET, MEX, NBS y NBE. Se descargaron 4.050
  forecasts nativos (9 estaciones × 5 productos × 90 días), con cobertura 90/90 y cero fallos.
  Se usó el máximo nativo `n_x`/`txn`, fecha local y un lag uniforme conservador de +4h; ninguna
  corrida elegida excede el freeze. NBS/NBE corresponden al régimen NBM v5 iniciado el 05/05/2026.
- Familia cerrada antes de mirar hits: cinco productos, mediana MOS, promedio NBM, stack con LAMP
  y mezclas 50/50 con CITYX; sólo RAW/X60. Ganador DEV global: `STACKCITY50|RAW` (mediana de los
  cinco MOS + LAMP, mezclada 50/50 con CITYX). En el test descriptivo ya abierto: **39,2% → 45,0%**
  exacto (+5,8pp), top-2 67,7% → 74,1%, MAE 1,679 → 1,445, p=0,0581.
- No supera LAMPX (45,5-46,6% según sensibilidad de lag) ni alcanza p<0,05. Resultado:
  **rechazado como challenger adicional**, sin nueva sombra ni cambios de acciones. El test es
  exploratorio porque las etiquetas ya habían sido vistas; reproducible con
  `backfill_station_mos.py` y `lab_station_mos.py`.

### Kernel de confusión exacta por modelo (2026-07-13)

- Se probó una transformación discreta distinta al clasificador directo previo: cada uno de los
  ocho modelos aporta la distribución rolling de su desplazamiento respecto del bucket Gamma
  pagado. Candidatos congelados: local 30d, local 60d y 60d con shrink hacia un prior global por
  modelo equivalente a 15 observaciones. Las 29 ciudades de cada target se predicen antes de que
  ese día actualice historiales, evitando leakage transversal.
- Ganó `SHRINK60` en DEV. En el test descriptivo de 604 mercados: CITYX2 **40,9%** vs kernel
  **39,9% exacto** (-1,0pp, p=0,6839); top-2 66,6% → 68,5%. Usar CITYX-top1 y kernel-top1 como
  las dos selecciones cubre sólo 55,6%, peor que el top-2 probabilístico de CITYX.
- Resultado: **rechazado para exacto y para top-2 combinado**. No se abre sombra. Reproducible con
  `lab_confusion_kernel.py`.

## 8. Invariantes que no se negocian (si un cambio los rompe, el cambio está mal)

- `evaluate_market()` es una función pura (snapshot→orden) y es la MISMA en backtest/paper/live.
  No meter I/O ni estado oculto adentro.
- Anti-look-ahead vive enteramente en la columna `avail` (cuándo el forecast fue público de
  verdad). Cualquier fuente de datos que no respete un `avail` real, miente en el backtest.
- `validate_world()` corre antes de cada backtest, sin excepciones. Si tira issues, se frena.
- `test_null_market` debe seguir dando ROI≤0 siempre. Si algún cambio lo pone positivo, el motor
  está roto — no operar hasta encontrar por qué (ver bugs #1-4 arriba como sospechosos típicos).
- La calibración EMOS es sobre anomalías, no sobre nivel (bug #4). No "simplificar" sacando la resta.

## 9. Riesgos aceptados, no resueltos por diseño

- **Oráculo:** sensor tampering y fallos de resolución (casos reales documentados en §3). Sin
  cobertura de ninguna regla de trading. Mitigación parcial: diversificar estaciones/fuentes.
- **Bloqueo geográfico de Argentina** a Polymarket (fallo judicial, ENACOM bloqueó ISPs, caso
  gatillado por trading sospechoso en el mercado de inflación INDEC). El VPS en Dublín resuelve
  esto; usar VPN en cambio violaría los ToS de Polymarket.
- **Degradación del edge con el tiempo** si el retail aprende a mirar la estación específica en
  vez de la ciudad en general — no hay forma de prevenir esto, solo remedir periódicamente.
