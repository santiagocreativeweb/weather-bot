#!/usr/bin/env python3
# scripts/fix_forward_dups.py — parche quirurgico UNA VEZ (2026-07-15, lo cazo check_accumulation):
#   1. exact_selector_forward.csv / cityx_confidence_forward.csv: filas DUPLICADAS identicas por
#      (station, target, capture_utc, version) — corridas CONCURRENTES del acumulador (task 12:00 +
#      run_daily manual) sin lock. Se dedupea conservando la PRIMERA aparicion (son identicas).
#      El lock que evita que se repita quedo en los acumuladores (fix del mismo dia).
#   2. lamp_shadow_forward.csv: agrega la columna de provenance `lav_match_utc` (vacia en filas
#      viejas — esa provenance no se persistio en su momento y NO se fabrica retroactivamente).
# Backups: <archivo>.bak-dedup0715. Idempotente: re-correr no cambia nada.
import csv
import os
import shutil
import sys

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
KEY = ("station", "target", "capture_utc", "version")


def dedupe(path):
    if not os.path.exists(path):
        print(f"[skip] {os.path.basename(path)} no existe")
        return
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    seen, out = set(), []
    for r in rows:
        k = tuple(r.get(c, "") for c in KEY)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    if len(out) == len(rows):
        print(f"[ok]   {os.path.basename(path)}: sin duplicados ({len(rows)} filas)")
        return
    bak = path + ".bak-dedup0715"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    os.replace(tmp, path)
    print(f"[FIX]  {os.path.basename(path)}: {len(rows)} -> {len(out)} filas "
          f"(-{len(rows) - len(out)} dup) · backup {os.path.basename(bak)}")


def add_column(path, col, after):
    if not os.path.exists(path):
        print(f"[skip] {os.path.basename(path)} no existe")
        return
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if col in fields:
        print(f"[ok]   {os.path.basename(path)}: ya tiene {col}")
        return
    bak = path + ".bak-dedup0715"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    i = fields.index(after) + 1 if after in fields else len(fields)
    fields.insert(i, col)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)   # filas viejas: col vacia (provenance no persistida, no se inventa)
    os.replace(tmp, path)
    print(f"[FIX]  {os.path.basename(path)}: +columna {col} (vacia en {len(rows)} filas viejas)")


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else D
    dedupe(os.path.join(base, "exact_selector_forward.csv"))
    dedupe(os.path.join(base, "cityx_confidence_forward.csv"))
    add_column(os.path.join(base, "lamp_shadow_forward.csv"), "lav_match_utc", "lav_at_obs")
