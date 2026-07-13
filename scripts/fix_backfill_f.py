#!/usr/bin/env python3
# scripts/fix_backfill_f.py — companion de fix_obs_f.py: realinea max_real (y sus derivados
# crps_cal/crps_raw) de data/backfill_check.csv para las 9 estaciones Fahrenheit contra el
# obs.csv YA PARCHEADO (maximo horario METAR = verdad de resolucion WU/Gamma).
#
# Por que hace falta: backfill_check.py --extend hace coalesce que NUNCA pisa un label resuelto
# (proteccion correcta contra pisadas accidentales), asi que los max_real de la era daily quedan
# congelados para siempre sin este parche. hit_cal/hit_raw/pwin/win_mkt NO se tocan (van contra
# Gamma, que siempre fue la verdad de pago). El sesgo rolling de calib_lab (station_bias.json)
# se recalcula corriendo calib_lab despues de esto.
import csv
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

D = os.path.join(os.path.dirname(__file__), "..", "data")
F_ST = {"KLGA", "KORD", "KMIA", "KSFO", "KLAX", "KDAL", "KATL", "KHOU", "KAUS"}


def crps_normal(x, mu, sg):
    if sg is None or sg <= 0:
        return None
    z = (x - mu) / sg
    pdf = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    cdf = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return sg * (z * (2 * cdf - 1) + 2 * pdf - 1 / math.sqrt(math.pi))


def main():
    obs = {}
    with open(os.path.join(D, "obs.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["station"] in F_ST:
                obs[(r["station"], r["date"])] = float(r["tmax"])
    path = os.path.join(D, "backfill_check.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fields = fh.name and rows and list(rows[0].keys())
    bak = path + ".bak-fixF"
    if not os.path.exists(bak):
        import shutil
        shutil.copyfile(path, bak)
    changed = 0
    for r in rows:
        if r["station"] not in F_ST or not r.get("max_real"):
            continue
        v = obs.get((r["station"], r["target"]))
        if v is None:
            continue
        old = float(r["max_real"])
        new = round(v, 1)
        if abs(old - new) < 1e-9:
            continue
        changed += 1
        r["max_real"] = f"{new}"
        for col, mu_c, sg_c in [("crps_cal", "mu_cal", "sigma_cal"),
                                ("crps_raw", "mu_raw", "sigma_raw")]:
            try:
                c = crps_normal(new, float(r[mu_c]), float(r[sg_c]))
                r[col] = f"{round(c, 3)}" if c is not None else r[col]
            except (KeyError, TypeError, ValueError):
                pass
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"backfill_check.csv: {changed} max_real F realineados (backup {os.path.basename(bak)})."
          f" Ahora correr calib_lab.py para refrescar station_bias.json.")


if __name__ == "__main__":
    main()
