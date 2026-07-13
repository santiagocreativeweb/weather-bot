# run_backtest.py — Produce report/report_fase4.md + PNGs + trades.csv
# Uso: python3 run_backtest.py            (sintético, valida la máquina)
#      python3 run_backtest.py --data real --dir data/   (cuando tengas CSVs reales del VPS)
import argparse, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from wxbt.synth import gen_world
from wxbt.engine import run_backtest, metrics, reliability_bins
from wxbt.checks import validate_world
from wxbt import config as C

P = argparse.ArgumentParser()
P.add_argument("--data", default="synth", choices=["synth", "real"])
P.add_argument("--dir", default="data/")
P.add_argument("--days", type=int, default=420)
P.add_argument("--seed", type=int, default=42)
P.add_argument("--resolve", default="obs", choices=["obs", "market"],
               help="obs=paga contra obs_IEM (temp fisica); market=paga contra la resolucion real "
                    "del mercado (columna resolved de markets.csv, de outcomePrices de Gamma). "
                    "market elude el delta IEM-vs-WU; CRPS/Brier/reliability siguen contra obs.")
a = P.parse_args()
import os; os.makedirs("report", exist_ok=True)


def load_real(d):
    """Contratos de schema en README. Mismo motor, cero cambios de código."""
    obs = pd.read_csv(f"{d}/obs.csv", parse_dates=["date"]); obs["date"] = obs["date"].dt.date
    fc = pd.read_csv(f"{d}/forecasts.csv", parse_dates=["init", "avail", "target"])
    fc["target"] = fc["target"].dt.date
    mk = pd.read_csv(f"{d}/markets.csv", parse_dates=["open_t", "close_t", "target"])
    mk["target"] = mk["target"].dt.date
    px = pd.read_csv(f"{d}/prices.csv", parse_dates=["t", "target"]); px["target"] = px["target"].dt.date
    for df in (mk, px):
        for c in ("lo", "hi"):
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    return dict(obs=obs, forecasts=fc, markets=mk, prices=px)


if a.data == "synth":
    world = gen_world(n_days=a.days, seed=a.seed, regime="inefficient")
    fuente = f"SINTÉTICO régimen ineficiente (seed={a.seed}, {a.days} días)"
else:
    world = load_real(a.dir)
    fuente = f"REAL desde {a.dir}"

issues = validate_world(world)
if issues:
    print("SANITY CHECKS FALLARON:\n - " + "\n - ".join(issues)); sys.exit(1)
print("sanity checks: OK")

res, mets = {}, {}
for mode in ("taker", "mid"):
    res[mode] = run_backtest(world, mode=mode, use_calibration=True, resolve=a.resolve)
    mets[mode] = metrics(res[mode])
    print(f"[{mode}] {mets[mode]}")
res_raw = run_backtest(world, mode="mid", use_calibration=False)
mets_raw = metrics(res_raw)
print(f"[mid/RAW sin calibrar] {mets_raw}")

# --- Null test (solo synth): mercado eficiente, sin ganancia fantasma ---
null_rows = []
if a.data == "synth":
    for seed in (1, 2, 3):
        wn = gen_world(n_days=200, seed=seed, regime="efficient")
        for mode in ("taker", "mid"):
            m = metrics(run_backtest(wn, mode=mode))
            null_rows.append((seed, mode, m["roi"], m["n_trades"]))
            print(f"[null seed={seed} {mode}] roi={m['roi']:.4f} trades={m['n_trades']}")

# --- Plots ---
fig, ax = plt.subplots(figsize=(9, 4.2))
for mode, col in (("taker", "#c0392b"), ("mid", "#2471a3")):
    eq = res[mode]["equity"]["equity"].resample("1D").last().dropna()
    ax.plot(eq.index, eq.values, label=f"{mode} (cota {'pesimista' if mode=='taker' else 'optimista'})", color=col)
ax.axhline(C.CAPITAL_INICIAL, ls="--", c="gray", lw=0.8)
ax.set_title("Equity — walk-forward, mundo sintético ineficiente (valida la máquina, NO el edge real)")
ax.legend(); ax.set_ylabel("USD"); fig.tight_layout(); fig.savefig("report/equity.png", dpi=130)

fig2, ax2 = plt.subplots(figsize=(5.2, 5))
rb = reliability_bins(res["mid"]["preds"])
if rb:
    xs, ys, ns = zip(*rb)
    ax2.plot([0, 1], [0, 1], "--", c="gray")
    ax2.scatter(xs, ys, s=[max(n / 2, 12) for n in ns], c="#2471a3")
    for x, y, n in rb:
        ax2.annotate(str(n), (x, y), fontsize=7, xytext=(3, 3), textcoords="offset points")
ax2.set_xlabel("prob predicha (entrada)"); ax2.set_ylabel("frecuencia realizada")
ax2.set_title("Reliability diagram — predicciones en entradas (mid)")
fig2.tight_layout(); fig2.savefig("report/reliability.png", dpi=130)

res["mid"]["trades"].to_csv("report/trades_mid.csv", index=False)
res["taker"]["trades"].to_csv("report/trades_taker.csv", index=False)

# --- Reporte ---
def fila(m):
    return (f"| {m['roi']*100:+.1f}% | {m['pnl_total']:+.0f} | {m['n_trades']} | {m['hit_rate']*100:.0f}% "
            f"| {m['ev_por_trade']:+.2f} | {m['max_dd']*100:.1f}% | {m['sharpe']:.2f} | {m['brier_entry']:.3f} |")

crps = res["mid"]["crps"]; bb = res["mid"]["brier_buckets"]
rep = f"""# FASE 4 — Backtest y validación (motor + walk-forward)

**Fuente de datos:** {fuente}
**Capital inicial:** ${C.CAPITAL_INICIAL:.0f} · Reglas FASE 3: edge≥{C.EDGE_MIN_NET:.0%} neto, ¼-Kelly,
cap {C.PER_MARKET_CAP_FRAC:.0%}/mercado, cap {C.GROUP_CAP_FRAC:.0%}/grupo sinóptico, kill-switch {C.DAILY_KILL_SWITCH:.0%}/día,
salida por forecast-changed ≥{C.EXIT_PROB_SHIFT:.0%}. Walk-forward: entrena solo con targets previos al bloque (30d), mínimo {C.MIN_TRAIN_DAYS}d.

## ADVERTENCIA (leer primero)
Los números de abajo salen de un MUNDO SINTÉTICO con sesgos plantados. **Validan que la máquina
funciona (contabilidad, anti look-ahead, fees, calibración), NO prueban edge real en Polymarket.**
El veredicto real requiere correr los downloaders (scripts/) en tu VPS y re-ejecutar con --data real.

## Resultados (bracketing de ejecución)
| modo | ROI | PnL $ | trades | hit rate | EV/trade $ | maxDD | Sharpe* | Brier(entradas) |
|---|---|---|---|---|---|---|---|---|
| taker (pesimista: cruza spread + fee {C.FEE_RATE_WEATHER}·p(1-p)) {fila(mets['taker'])}
| mid (optimista: maker llenado, sin fee) {fila(mets['mid'])}
| mid SIN calibrar (baseline raw) {fila(mets_raw)}

*Sharpe anualizado √365 sobre retornos diarios — muestra corta, tomar como orden de magnitud.

## Calibración (claim central de la tesis FASE 2)
- CRPS crudo → calibrado: **{crps['raw']:.3f} → {crps['cal']:.3f}** ({(1-crps['cal']/crps['raw'])*100:.0f}% mejora)
- Brier por bucket crudo → calibrado: **{bb['raw']:.4f} → {bb['cal']:.4f}**
- Consistente con la literatura EMOS local (FASE 1: mejoras CRPS ~34–44%). En el mundo sintético
  la calibración es la diferencia entre baseline y estrategia (ver tabla).

## Null test — el chequeo anti-fuga
Mundo EFICIENTE (el mercado ya sabe todo lo que sabe el modelo): el motor NO debe ganar.
| seed | modo | ROI | trades |
|---|---|---|---|
"""
for s_, mo_, roi_, nt_ in null_rows:
    rep += f"| {s_} | {mo_} | {roi_*100:+.2f}% | {nt_} |\n"
rep += """
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
"""
open("report/report_fase4.md", "w", encoding="utf-8").write(rep)   # utf-8: el reporte tiene ≥ × ¼ σ
print("\nreporte escrito en report/report_fase4.md")
