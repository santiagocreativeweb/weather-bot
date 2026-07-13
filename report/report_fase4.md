# FASE 4 — Backtest y validación (motor + walk-forward)

**Fuente de datos:** REAL desde data_KORD/
**Capital inicial:** $2000 · Reglas FASE 3: edge≥10% neto, ¼-Kelly,
cap 2%/mercado, cap 30%/grupo sinóptico, kill-switch -5%/día,
salida por forecast-changed ≥15%. Walk-forward: entrena solo con targets previos al bloque (30d), mínimo 60d.

## ADVERTENCIA (leer primero)
Los números de abajo salen de un MUNDO SINTÉTICO con sesgos plantados. **Validan que la máquina
funciona (contabilidad, anti look-ahead, fees, calibración), NO prueban edge real en Polymarket.**
El veredicto real requiere correr los downloaders (scripts/) en tu VPS y re-ejecutar con --data real.

## Resultados (bracketing de ejecución)
| modo | ROI | PnL $ | trades | hit rate | EV/trade $ | maxDD | Sharpe* | Brier(entradas) |
|---|---|---|---|---|---|---|---|---|
| taker (pesimista: cruza spread + fee 0.05·p(1-p)) | +452.2% | +9045 | 713 | 66% | +12.69 | -24.9% | 4.50 | 0.170 |
| mid (optimista: maker llenado, sin fee) | +620.0% | +12401 | 823 | 67% | +15.07 | -16.1% | 5.46 | 0.169 |
| mid SIN calibrar (baseline raw) | +372.7% | +7455 | 645 | 59% | +11.56 | -9.0% | 4.40 | 0.177 |

*Sharpe anualizado √365 sobre retornos diarios — muestra corta, tomar como orden de magnitud.

## Calibración (claim central de la tesis FASE 2)
- CRPS crudo → calibrado: **1.612 → 1.335** (17% mejora)
- Brier por bucket crudo → calibrado: **0.0700 → 0.0641**
- Consistente con la literatura EMOS local (FASE 1: mejoras CRPS ~34–44%). En el mundo sintético
  la calibración es la diferencia entre baseline y estrategia (ver tabla).

## Null test — el chequeo anti-fuga
Mundo EFICIENTE (el mercado ya sabe todo lo que sabe el modelo): el motor NO debe ganar.
| seed | modo | ROI | trades |
|---|---|---|---|

Interpretación: negativo = sin ganancia fantasma → sin look-ahead ni contabilidad rota
(invariante Σpnl≈Δequity verificado). Hallazgo del diagnóstico: contra un mercado YA calibrado
la estrategia no queda en 0, PIERDE por churn (ruido propio del modelo dispara entradas y la
salida forecast-changed realiza pérdidas en whipsaw). Lección operativa: el edge de calibración
es carga-portante — si el paper trading (FASE 5) muestra que el mercado real ya está calibrado
contra nuestra señal, NO se pasa a dinero real. El cooldown de 12h post-stop-out mitiga el
churn pero no lo elimina.

## Chequeos automáticos incluidos
- validate_world(): schemas, avail≥init, varianzas>0, mids∈(0,1), outliers |z|>6, colas de buckets.
- Tripwire anti look-ahead en tests (envenenar forecasts futuros no cambia decisiones).
- Suite: `python3 -m pytest tests/ -q` (7 tests, incluye null test multi-seed).

## Modo paper trading (puente a FASE 5)
El núcleo de decisión `engine.evaluate_market()` es función pura (snapshot→orden). FASE 5 la
conecta a feeds vivos (Gamma/CLOB + Open-Meteo) sin tocar la lógica: mismo código decide en
backtest, paper y live. Paper = ejecutar decisiones contra el book real SIN mandar órdenes.

## Etiquetas de confianza
- fee weather rate=0.05, pico $1.25/100sh@50¢: [VERIFICAR-VIVO] en docs.polymarket.com antes de real.
- Estaciones (KLGA, LFPB, …): placeholder; la real sale de las rules DE CADA mercado. [VERIFICAR-VIVO]
- Redondeo entero half-up en resolución: [ASUNCION] — confirmar contra WU history por estación.
- Fills: bracketing taker/mid; el maker real con fill parcial vive entre ambos. [ASUNCION]
- 60 días mínimos de entrenamiento por (estación, lead): [ASUNCION FASE 3].
