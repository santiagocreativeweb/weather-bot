# RUNBOOK — operación WXBT (prerequisito de FASE 5)

Este documento define QUÉ hacer ante fallos ANTES de conectar al mercado. Un bot que corre 24/7 no
puede descubrir sus modos de falla en producción. Regla madre: **ante cualquier duda de integridad de
datos, el default es NO operar** (flat), no "operar con lo que haya". Perder una oportunidad es gratis;
operar sobre datos corruptos no.

Estado: **borrador pre-FASE-5**. Cada fila con acción "HALT" necesita estar implementada como chequeo
automático (no confiar en vigilancia humana) antes de arriesgar dinero real.

---

## 0. Precondición de arranque (cada día, antes de habilitar entradas)

| chequeo | fuente | si falla |
|---|---|---|
| `validate_world()` sin issues | invariante #3 CLAUDE.md | HALT total del día |
| `test_null_market` ROI≤0 en el último build | invariante #4 | HALT — el motor está roto, no operar |
| ≥3 modelos frescos por (estación, target) | `MIN_MODELS_ENTRY` | esa estación NO opera (las demás sí) |
| forecasts con `avail` ≤ ahora (anti look-ahead) | invariante #2 | HALT esa corrida — nunca usar forecast con avail futuro |
| clock del host sincronizado (NTP) | — | HALT: `avail`/`close_t` mal comparados = look-ahead o entradas tardías |

`validate_world()` y `test_null_market` son **gates duros**: si cualquiera falla, el sistema arranca en
modo flat y alerta, no "degradado".

---

## 1. Fallos de datos de entrada (forecasts / obs)

### 1a. Downloader de forecasts falla un día (Open-Meteo caído / rate-limit / timeout)
- **Detección:** 0 filas nuevas en `forecasts.csv` para una corrida esperada, o `<3` modelos.
- **Respuesta:** operar SOLO con los forecasts ya disponibles y frescos. Si una estación queda con
  `<3` modelos, esa estación no entra hoy (regla existente). **No re-usar el forecast de ayer como si
  fuera de hoy** — viola el anti-look-ahead conceptual (el `avail` sería viejo, el edge fantasma).
- **HALT?** No global; degrada por estación. Alertar si >1 día seguido (posible cambio de API).

### 1b. Obs de IEM llega tarde o con huecos
- **Regla de fuente (auditada 2026-07-13):** para mercados °F NO usar el endpoint `daily.py`.
  Usar ASOS horario `asos.py?data=tmpf`, agrupado por fecha local DST-aware. En 746 mercados de
  nueve estaciones °F reprodujo Gamma/WU en 98,4-100%; `daily.py` sólo en 59,6-83,9%.
  Para mercados °C se mantiene `daily.py` (acuerdo histórico 98-100%).
- **Impacto:** la obs alimenta CALIBRACIÓN (EMOS sobre anomalías) y, en backtest, resolución. En VIVO la
  resolución de PnL es por mercado (Gamma), así que un hueco de obs NO afecta el pago — solo atrasa el
  re-fit de d,e. **La calibración usa una ventana; un día faltante no la rompe.**
- **Respuesta:** seguir con los coeficientes calibrados vigentes; rellenar la obs cuando aparezca.
- **HALT?** No.

### 1c. Sensor tampering / obs físicamente imposible (riesgo de oráculo, YA VISTO)
- **Contexto:** ya apareció un caso de lectura de sensor manipulada/anómala. Ningún freno de sizing
  cubre esto — es riesgo de oráculo puro.
- **Detección:** salto físicamente imposible (Δ>15°C intra-día, o fuera de récord histórico de la
  estación), o divergencia grande obs-IEM vs los 3 modelos a la vez.
- **Respuesta:** marcar esa (estación, día) como sospechosa → NO operar ese mercado; si ya hay posición,
  mantener (el mercado igual resuelve por WU, no por nuestra obs) pero no agregar.
- **HALT?** Por mercado, no global.

---

## 2. Fallos del lado Polymarket

### 2a. Cambio de mecánica de fees (YA PASÓ: Fee Structure V2, mar-2026)
- **Contexto:** `FEE_RATE_WEATHER=0.05` está `[VERIFICAR-VIVO]`. Un cambio de fee mueve el break-even
  del edge (la envolvente mostró que el edge muere en fricción ~6¢; el fee es parte de esa fricción).
- **Detección:** el fee efectivo cobrado en un fill ≠ el modelado. **Chequear el fee real del primer
  fill de cada día contra `entry_cost_per_share`.**
- **Respuesta:** si el fee real > modelado → HALT y re-correr la envolvente con el fee nuevo antes de
  seguir. Un fee más alto puede volver el edge no-tradeable sin ningún síntoma en el agregado.
- **HALT?** Sí, hasta re-validar.

### 2b. Cambio de mecánica de resolución / estación de referencia
- **Contexto:** el mercado resuelve por Weather Underground; ya sabemos que la estación WU no siempre =
  IEM (EGLL 46%, RKSI 74% de acuerdo → tarea 0b, esas dos FUERA hasta arreglar). Si Polymarket cambia la
  estación de referencia de una ciudad, el edge medido se invalida.
- **Detección:** las `rules`/`groupItemTitle` del evento cambian de estación o de unidad (London ya fue
  °F 2025 / °C 2026 — bug de unidad conocido).
- **Respuesta:** re-verificar `[VERIFICAR-VIVO]` mapeo ciudad→estación y unidad ANTES de operar esa
  ciudad. No asumir unidad fija por estación (ese fue el bug de London).
- **HALT?** Por ciudad, hasta re-verificar.

### 2c. Book se seca / spread se abre (liquidez fina)
- **Contexto:** la liquidez se densificó recién feb-2026; buckets de cola pueden tener 5 shares en el
  mejor ask. El acumulador de books (`accumulate_books.py`) mide esto forward.
- **Detección:** half-spread efectivo para $40 > 6¢ (break-even) en el bucket objetivo AL MOMENTO de
  entrar — no un promedio histórico.
- **Respuesta:** no entrar ese bucket (el edge no sobrevive esa fricción). El cap $40 + `MIN_ENTRY_PRICE`
  ya limitan, pero el chequeo de spread-en-vivo debe ser un gate de entrada duro en FASE 5.
- **HALT?** Por bucket.

### 2d. Orden no se llena / se llena parcial / precio se movió
- **Respuesta:** re-evaluar `evaluate_market()` con el book actual; si el edge neto ya no supera
  `EDGE_MIN_NET`, cancelar. Nunca "perseguir" el precio. Respetar `REENTRY_COOLDOWN_H` para no hacer
  churn.
- **HALT?** No.

---

## 3. Fallos de infraestructura

### 3a. Host/VPS caído
- **Impacto:** posiciones ABIERTAS siguen vivas en Polymarket (resuelven solas por WU aunque estemos
  offline) — no hay riesgo de pago. El riesgo es no poder SALIR por forecast-changed ni entrar.
- **Respuesta al volver:** NO re-hidratar ciegamente el estado. Reconstruir posiciones desde el CLOB
  (fuente de verdad de fills), no desde un archivo local que pudo quedar a medio escribir. Correr la
  precondición §0 completa antes de re-habilitar entradas.
- **Prevención:** el estado de posiciones debe ser recuperable desde el exchange, no solo local.
  Escrituras de estado atómicas (temp+rename) para no corromper en un corte a mitad de escritura.
- **HALT?** Sí al volver, hasta pasar §0.

### 3b. Kill-switch / freno trailing: verificar que ACTÚAN en vivo
- **Contexto:** el kill diario (−5%) NO frena goteos multi-día; por eso existe el freno geométrico
  (cada −10% desde el ATH divide el tamaño ×2, sin escalón de cero). Ambos son lógica de `engine.py`
  probada en backtest — en vivo hay que confirmar que el equity que leen es el REAL (mark-to-market
  del CLOB), no uno stale.
- **Detección:** loguear en cada tick el `equity`, `hw_max`, `brake_factor` y compararlos contra el
  valor real de la cuenta.
- **HALT?** Si el equity leído diverge del real → HALT (el freno estaría dimensionando sobre un número
  equivocado).

### 3c. Reloj / timezone
- Todo el anti-look-ahead depende de comparar `avail`/`close_t` en UTC contra "ahora". Un host con
  clock corrido = entradas tardías o look-ahead silencioso. NTP obligatorio; chequear en §0.

---

## 4. Validaciones forward en curso (NO son fallos — son los `[ASUNCION]` load-bearing)

Estos dos corren en paralelo, acumulando desde 2026-07-08, y **gatean FASE 5**:

| script | valida | criterio de éxito (~90 días) |
|---|---|---|
| `scripts/accumulate_books.py` | half-spread EFECTIVO real ≤ break-even (~6¢) | mediana hs_eff_40 ≤ ~2¢ en buckets líquidos, ponderado a KLGA (concentra turnover) |
| `scripts/accumulate_ensemble.py` | s2 modelado ~ varianza real entre miembros | s2_real de `ensemble_forward.csv` ~ s2 de `forecasts.csv` por (modelo, lead); sin sesgo sistemático |
| `scripts/accumulate_predictions.py` | SKILL forward del modelo calibrado | track record OOS: `check_predictions.py` muestra CALIBRADO > CRUDO (CRPS menor, prob-al-ganador mayor) contra el ganador REAL |
| `scripts/accumulate_cityx_confidence.py` | abstención exact-first CITYCONF1 (`spread <=1,1 buckets`) | 45 días; cobertura >=35%, exacto seleccionado >=45%, mejora vs CITYX-all y bootstrap por día p<0,05 |

**Track record de predicciones (skill OOS, complementa el backtest):** `accumulate_predictions.py` guarda
la predicción CALIBRADA del motor (μ,σ vía EMOS/anomalías) + la cruda, por (estación, target D+1..D+3),
forward. Cuando los targets resuelven, `check_predictions.py` compara contra el bucket ganador en DOS
resoluciones separadas — MERCADO (Gamma outcomePrices, lo que paga) y FÍSICA (obs IEM, skill puro) —
con hit / prob-al-ganador / CRPS, calibrado vs crudo. Es la verificación forward de que el edge del
backtest no era un artefacto del período: si en vivo el calibrado no le gana al crudo, hay que dudar.
Correr `python scripts/check_predictions.py` cuando haya targets pasados (hoy no resuelve nada del
mismo día). Anti-look-ahead: params EMOS entrenados sólo con historia ≤ última obs.

Correr **UNA vez por día** cada uno (`--date YYYY-MM-DD`). Ambos son append-only e idempotentes por
snapshot (guard anti doble-corrida). Hasta que estos dos pasen de `[ASUNCION]` a `[VERIFICADO]`, **no
hay FASE 5**.

**Robustez de la acumulación (para que "90 días de data" no sea "60 días con 30 huecos que no viste"):**
- `data/accumulator.log` — una línea por corrida (OK/WARN/SKIP con timestamp), sobrevive reinicios.
- `book_json` en `books_forward.csv` — book CRUDO (top-20 niveles bid/ask con size), no solo el `hs_eff`
  derivado a $40. Si cambia `PER_MARKET_CAP_USD`, se re-camina el book crudo al nuevo tamaño. **El book
  de hoy no se archiva en ningún otro lado — por eso se guarda crudo desde el día 1.**
- `scripts/check_accumulation.py` — completitud (días sin hueco, cobertura 6 ciudades/estaciones,
  rangos plausibles, cruce con el log). Exit≠0 si algo falla. Correr SEMANAL, no a los 90 días.

**Registro en Task Scheduler (Windows).** Wrappers listos (calculan la fecha solos):
```
schtasks /Create /TN "wxbt-accumulate" /SC DAILY  /ST 12:00 /F ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\Admin\Downloads\wxbt_fase4\scripts\run_daily.ps1\""
schtasks /Create /TN "wxbt-check" /SC WEEKLY /D SUN /ST 09:00 /F ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\Admin\Downloads\wxbt_fase4\scripts\run_check.ps1\""
```
**DECISIÓN PREVIA (tuya, load-bearing): ¿en qué máquina corre?** Si se registra en un host que se
suspende/apaga (una laptop), los días con la máquina dormida son huecos silenciosos — justo el riesgo
que el log y el check existen para atrapar, pero mejor no crearlo. Registrar en una máquina **siempre
encendida** (el VPS del §3a, o la de escritorio si no se suspende). El check semanal avisa si se eligió
mal, pero cada hueco es data 2025-equivalente perdida que no se recupera.

---

## 5. Decisiones que NO son del bot (de Santiago, antes de conectar)

- **Cuánto capital en la ventana no-validada.** El edge es +6¢/share (per-share, no per-dólar): la misma
  señal se cobra con $500 que con $2.000. Peor-caso de DD ~−31% → −$155 con $500 vs −$620 con $2.000. La
  decisión honesta es "cuán poco arriesgar mientras los dos `[ASUNCION]` no estén validados", y se toma
  DESPUÉS de los 90 días, no ahora.
- **Estaciones en el portfolio.** RKSI/EGLL FUERA hasta cerrar 0b (obs WU real). Sólo las 4 limpias
  (RJTT/LFPB/KORD/KLGA) tienen edge validado.
- **Umbral de alerta / quién recibe el HALT.** Definir canal (email/push) y quién responde fuera de hora.
