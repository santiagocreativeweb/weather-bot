# WXBT — Handoff a Claude Code

Bot de trading de mercados de **temperatura máxima diaria** en Polymarket (multi-ciudad).
Este doc es el contexto completo para seguir en Claude Code. Entorno del usuario: **Windows,
Python 3.14**, ejecutable = `python` (NO `python3`). Repo en `C:\Users\Admin\Downloads\wxbt_fase4`.

---

## Estado en una línea
Motor de backtest FASE 4 **construido y validado con datos sintéticos (7/7 tests pasan)**. Los
downloaders de datos reales **fallan en 2 de 3** — ese es el trabajo inmediato. El edge real
todavía NO está probado; solo se prueba cuando el backtest corra con data real limpia.

---

## Qué pasó en la última corrida (hechos, no interpretación)

```
python -m pytest tests/ -q                 → 7 passed (68s)                    ✅
python scripts/download_iem_obs.py ...     → escrito data/obs.csv: 12048 filas ⚠️ (ver T2)
python scripts/download_openmeteo.py ...   → [ABORT] 400 Bad Request           ❌ (ver T1)
python scripts/download_polymarket.py      → markets_raw: 0  prices_raw: 0     ❌ (ver T3)
python run_backtest.py --data real ...     → FileNotFoundError forecasts.csv   ❌ (cascada de T1)
```

- `python3` no existe en Windows → usar `python`. (No es bug del código, es del comando.)
- El `FileNotFoundError` es **esperado**: `forecasts.csv` no existe porque el downloader abortó.
  No es un bug nuevo; es la cascada. Se resuelve arreglando T1.

---

## Tareas priorizadas por riesgo (P(mal) × costo). Atacar en este orden.

### T1 — [CRÍTICA] `download_openmeteo.py` da 400, y aunque no lo diera, filtra look-ahead
**Dos problemas, el segundo es el que importa:**

1. **El 400** (superficial): la llamada es a `ensemble-api.open-meteo.com/v1/ensemble` con
   `models=gfs_seamless`, `start_date=2025-07-01`, `end_date=2026-07-01`.
   - Hipótesis primaria (inferida, ~70%): el endpoint `/v1/ensemble` es de **forecast**, no de
     archivo — no sirve un año de historia. El free tier retiene una ventana corta hacia atrás.
     Un rango de >1 año → 400. **Verificar** en https://open-meteo.com/en/docs/ensemble-api.
   - Hipótesis secundaria (~30%): nombre de modelo inválido para el endpoint ensemble
     (`gfs_seamless` puede ser solo determinístico; los ensemble usan otros ids). **Verificar**
     la lista de `models` válidos en la doc.
   - Test barato para separarlas: una sola llamada con rango de 7 días recientes y un solo modelo.
     Si pasa → era el rango (problema de archivo). Si sigue 400 → era el nombre del modelo.

2. **El look-ahead** (profundo, load-bearing): incluso con el 400 resuelto, `/v1/ensemble`
   devuelve la corrida **más reciente** por fecha, NO la que estaba disponible en el momento de
   decidir. El motor garantiza anti-look-ahead con la columna `avail`, pero si el downloader
   pone un `avail` aproximado sobre una corrida que en realidad es del futuro, **la garantía se
   rompe en la fuente**. El backtest daría un edge fantasma imposible de reproducir en vivo.
   - **Fix correcto:** sacar forecasts **point-in-time** (la corrida tal como existía a `init`).
     Candidatos a investigar: Previous-Runs API de Open-Meteo con soporte ensemble, o el
     Historical Forecast archive. Si ninguno da ensembles históricos reales por corrida, el
     backtest de forecasts pierde validez y hay que decir eso explícitamente antes de operar.
   - **Claim que debe cumplir el fix:** para todo target `t`, cada fila de forecast tiene un
     `avail < close(t)` que es el instante real de publicación de ESA corrida. Falsable: elegir
     un target, mirar si el `m` coincide con la corrida vieja (no con la observación final).

**Contrato de salida** (`data/forecasts.csv`): `station,target,model,init,avail,lead_h,m,s2`
donde `m`=media entre miembros del ensemble, `s2`=varianza entre miembros (>0, nunca vacío).
El script ya aborta si <5 miembros — mantener esa disciplina de fail-loud.

---

### T2 — [ALTA] `obs.csv` escribió 12048 filas — verificar que no sean basura silenciosa
12048 = 6 estaciones × ~2008 días (rango pide 2007 días). O sea **las 6 estaciones
devolvieron cobertura completa**, incluidas EGLL/LFPB/RJTT/RKSI — que yo había marcado como el
punto más flojo (no confirmé que esos códigos de red IEM existan tal cual). Que haya filas NO
significa que sean correctas. **Verificar antes de confiar:**
- Abrir `data/obs.csv`, mirar 5 filas por estación. ¿`tmax` de RJTT (Tokio) tiene rango físico
  plausible en °C? ¿O quedó en °F sin convertir? (el script convierte solo si `not code.startswith("K")`).
- Claim a falsar: `tmax_int` de una estación europea en julio debería estar ~20-35°C, no ~70-95.
  Si ves 70-95 en Londres, la conversión °F→°C no corrió para esa red.
- Riesgo de resolución (documentado, no resuelto): el mercado lo resuelve **Weather Underground**,
  no IEM. Validar obs IEM vs WU history en ≥30 días por estación antes de dinero real. Deltas = riesgo.

---

### T3 — [MEDIA] `download_polymarket.py` devolvió 0 mercados
El filtro Gamma (`closed=true` + substring `"highest temperature"` en la question) no matcheó nada.
- Causa probable (inferida): el texto real de las questions de clima no contiene esa frase exacta,
  o el filtrado por substring es demasiado estricto, o el parámetro de paginación/tag está mal.
- **Investigar con datos reales:** pegar en el navegador `https://gamma-api.polymarket.com/markets?closed=true&limit=20`
  y leer cómo vienen realmente las questions de temperatura (slug, tags, `groupItemTitle`). Ajustar
  el matcher a lo que exista. Este script **siempre requirió mapeo manual** slug→(station,target);
  no es automático de punta a punta.
- Contratos de salida: `markets.csv` = `station,target,bucket,lo,hi,open_t,close_t` (lo/hi vacío =
  cola abierta); `prices.csv` = `t,station,target,bucket,lo,hi,mid,hs` (mid=p_yes; hs=half-spread,
  si no hay book histórico asumir 0.02).

---

## Arquitectura del repo (qué mirar, qué no tocar)

```
wxbt/
  config.py       Reglas duras FASE 3 (edge≥10%, ¼-Kelly, caps $40/mercado y 30%/grupo,
                  kill-switch -5%/día, salida forecast∆≥15pts, σ-stress ×1.3). Constantes acá.
  calibration.py  EMOS-lite sobre ANOMALÍAS (no sobre nivel). crps_normal cerrado. NO tocar sin
                  entender por qué es anomalías: OLS sobre serie estacional atenúa (b<1) y sesga.
  market.py       Buckets con redondeo entero half-up, fee taker = rate·p·(1-p), Kelly, fills.
                  _open() trata None y NaN como cola abierta (fix de bug histórico — no romperlo).
  synth.py        Mundo sintético. Régimen 'efficient' = NULL TEST (posterior bayesiano exacto);
                  'inefficient' = sesgos plantados. Solo para validar la máquina, no es data real.
  engine.py       NÚCLEO. run_backtest() walk-forward. evaluate_market() = función PURA de
                  decisión (snapshot→orden) que se reusa idéntica en paper/live (FASE 5).
                  Anti-look-ahead: solo lee forecasts con avail<=t (searchsorted). NO debilitar.
  checks.py       validate_world(): corre SIEMPRE antes de backtestear. Ya chequea NaN explícito
                  en m/s2/mid (un NaN NO lo agarra `<=0` porque NaN<=0 es False — lección aprendida).
tests/test_core.py  7 tests. El más valioso: test_null_market — si el motor "gana" contra un
                  mercado eficiente, hay fuga. Ya cazó 3 bugs. Correr siempre tras tocar engine/synth.
scripts/          Los 3 downloaders. SON EL TRABAJO PENDIENTE (T1-T3). Nunca tocaron API real hasta ahora.
run_backtest.py   Runner. load_real() parsea los 4 CSV. `--data synth` valida máquina; `--data real` es el veredicto.
dashboard_snapshot.json  Estado derivado del backtest para el dashboard React (wxbt_dashboard.jsx, separado).
```

**Invariantes que no se negocian** (si un cambio los rompe, el cambio está mal):
- `evaluate_market()` es pura y es la misma en backtest/paper/live. No meter I/O adentro.
- Anti-look-ahead vive en `avail<=t`. Cualquier fuente de datos que no respete `avail` real, miente.
- `validate_world()` corre antes de cada backtest. Si tira issues, se frena, no se ignora.
- test_null_market debe seguir dando ROI≤0. Si se pone positivo, NO operar: el motor está roto.

---

## Comandos (Windows / `python`)

```powershell
# setup
python -m pip install pandas numpy scipy matplotlib pytest requests

# regresión (debe seguir 7/7 tras cualquier cambio a engine/synth/calibration)
python -m pytest tests/ -q

# pipeline de data real — orden obligatorio, cada paso depende del anterior
python scripts/download_iem_obs.py --start 2021-01-01 --end 2026-07-01     # → data/obs.csv   [T2: verificar]
python scripts/download_openmeteo.py --start 2025-07-01 --end 2026-07-01   # → data/forecasts.csv [T1: roto]
python scripts/download_polymarket.py                                       # → *_raw.csv       [T3: roto + mapeo manual]
# (mapeo manual *_raw.csv → markets.csv / prices.csv, ver README)

# veredicto real
python run_backtest.py --data real --dir data/
```

Regla de barrera: si `run_backtest.py` no imprime `sanity checks: OK` como primera línea, **no
seguir** — validate_world encontró algo. Leer el issue, no saltearlo.

---

## Decisión pendiente (qué se decide con el resultado de T1-T3)
Cuando el backtest corra con data real y limpia, mirar SOLO estas señales (no el ROI absoluto):
- `crps cal < raw` y `brier cal < raw` → la calibración agrega valor. Si no, la tesis de edge muere.
- `pnl_total > 0` neto en modo `taker` (cota pesimista) → hay margen sobre fees. Si solo gana en
  `mid`, el edge se lo come el spread.
- Reliability de entradas cerca de la diagonal → probabilidades calibradas, no sobre-confiadas.

Si esas 3 sobreviven → recién ahí tiene sentido FASE 5 (conexión CLOB + paper trading en vivo,
reusando `evaluate_market`). Si no → frenar acá; conectar al mercado no arregla la falta de edge.

## Riesgo no cubierto (documentado, sin solución en código)
Oráculo: sensor tampering (caso real París CDG abr-2026) y fallos de resolución (18-may-2026).
Ninguna regla de trading lo cubre. Se mitiga diversificando estaciones/fuentes, no con sizing.
