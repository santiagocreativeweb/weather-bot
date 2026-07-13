# wxbt — Backtest FASE 4 (bot clima Polymarket)

Motor de backtest walk-forward + calibración EMOS-lite sobre anomalías. **Validado con datos
sintéticos: prueba la MÁQUINA, no el edge real.** El veredicto real sale de correr esto con
datos reales en tu VPS.

## Correr
```bash
python3 -m pytest tests/ -q          # 7 tests (fee, buckets, anti-lookahead, null, calibración)
python3 run_backtest.py              # sintético -> report/report_fase4.md + PNGs
python3 run_backtest.py --data real --dir data/   # con CSVs reales
```

## Flujo en el VPS (Dublín)
1. `scripts/download_iem_obs.py --start 2021-01-01 --end 2026-07-01` → `data/obs.csv`
   (≥3 años: la climatología kernel lo necesita; con 1 año anda, con 3+ mejor).
2. `scripts/download_openmeteo.py --start 2025-07-01 --end 2026-07-01` → `data/forecasts.csv`
   (para `s2` real usar la Ensemble API de Open-Meteo: media y varianza por corrida).
3. `scripts/download_polymarket.py` → `data/markets_raw.csv` + `data/prices_raw.csv`,
   luego mapeo manual slug→(station,target) al schema final.
4. `python3 run_backtest.py --data real --dir data/`

Los 3 scripts están **[NO EJECUTADOS AQUÍ]** (red restringida en este entorno): endpoints y
parámetros salen de la investigación FASE 1 — verificar contra docs vigentes al primer uso.

## Schemas (contratos del motor)
| archivo | columnas |
|---|---|
| obs.csv | station, date, tmax, tmax_int |
| forecasts.csv | station, target, model, init, avail, lead_h, m, s2 |
| markets.csv | station, target, bucket, lo, hi, open_t, close_t (lo/hi vacíos = cola abierta) |
| prices.csv | t, station, target, bucket, lo, hi, mid, hs |

Fechas ISO 8601; timestamps UTC. `avail` = cuándo el forecast fue PÚBLICO (init + lag del
modelo): es la columna que garantiza el anti look-ahead — si la inventás mal, el backtest miente.

## Qué probaron los tests (mundo sintético)
- Fee taker exacta ($1.25/100sh @50¢, rate 0.05) y simetría p/(1-p).
- Buckets con redondeo entero particionan a prob 1; colas abiertas nan-safe.
- Tripwire anti look-ahead: envenenar forecasts futuros no cambia ninguna decisión.
- **Null test**: contra un mercado que ya sabe todo (posterior bayesiano exacto), el motor NO
  gana (6/6 corridas negativas). Cazó 3 bugs de construcción del propio mundo sintético antes
  de pasar — es el test más valioso del repo.
- Régimen ineficiente: calibración baja CRPS y Brier vs crudo y el motor encuentra EV>0.

## Etiquetas de confianza
- fee weather rate=0.05: [VERIFICAR-VIVO] docs.polymarket.com (Fee Structure V2).
- Estaciones/redes IEM/coords: [VERIFICAR-VIVO] contra las rules de CADA mercado.
- Redondeo entero half-up y cadena °F→°C de WU: [ASUNCION] validar obs vs WU history ≥30 días.
- Fills: bracketing taker (pesimista) / mid (optimista); realidad maker entre ambos: [ASUNCION].
- SIGMA_STRESS=1.3, cooldown 12h, cap $40/mercado y payout $100: reglas FASE 3/4, revisar en paper.
- Riesgo de oráculo (sensor tampering, resolución errada): NO cubierto por ninguna regla.

## Puente a FASE 5
`engine.evaluate_market()` es el núcleo puro de decisión (snapshot→orden). Paper/live usan
exactamente esa función con feeds vivos; el backtest queda como test de regresión permanente.
