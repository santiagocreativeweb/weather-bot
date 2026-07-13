#!/usr/bin/env python3
"""Capture LAMPX1 after freeze but before the target local day has ended.

The IEM archive exposes explicit LAV runtimes.  Selection admits only a runtime
whose conservative publication time (runtime + 2 h) preceded the operational
CITYX freeze.  The forecast is a shadow and never changes an action.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backfill_lamp import fetch_station, select_daily  # noqa: E402
from backfill_lamp_nowcast import fetch_asos, select_features  # noqa: E402
from dashboard import freeze_utc  # noqa: E402
from show_live import local_offset  # noqa: E402
from wxbt.lamp_shadow import (AVAIL_LAG_HOURS, NOW_VERSION, OFFSETS_F,  # noqa: E402
    PARENT_VERSION, RECIPE, SHADOW0, VERSION, now_prediction, prediction)

D = os.path.join(os.path.dirname(__file__), "..", "data")
OUT = os.path.join(D, "lamp_shadow_forward.csv")
LOG = os.path.join(D, "accumulator.log")
LOCK = os.path.join(D, ".lamp_shadow.lock")
CORRUPT_LOCK_STALE_SECONDS = 3600


def pid_alive(pid):
    """Return whether a lock owner still exists without signalling it on Windows."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(path=LOCK):
    """Atomically acquire a cross-process lock, recovering only dead owners."""
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(path, encoding="utf-8") as handle:
                    owner = json.load(handle)
                stale = not pid_alive(owner.get("pid"))
            except (OSError, ValueError, AttributeError):
                try:
                    stale = time.time()-os.path.getmtime(path) > CORRUPT_LOCK_STALE_SECONDS
                except OSError:
                    continue
            if not stale:
                return False
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created_utc":
                       dt.datetime.now(dt.timezone.utc).isoformat()}, handle)
            handle.flush(); os.fsync(handle.fileno())
        return True
    return False


def release_lock(path=LOCK):
    try:
        with open(path, encoding="utf-8") as handle:
            owner = json.load(handle)
        if int(owner.get("pid")) == os.getpid():
            os.remove(path)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def local_day_end_utc(station, target):
    midnight = dt.datetime.combine(target + dt.timedelta(days=1), dt.time())
    return midnight - dt.timedelta(hours=local_offset(station, target))


def capture_window_open(station, target, captured):
    """The archived final pre-freeze run is knowable only after freeze, before outcome."""
    naive = captured.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return freeze_utc(station, target) <= naive <= local_day_end_utc(station, target)


def eligible_cityx(frame, station, target):
    x = frame.copy()
    x["target_d"] = pd.to_datetime(x.target).dt.date
    x["capture_utc"] = pd.to_datetime(x.capture_utc, utc=True)
    cutoff = pd.Timestamp(freeze_utc(station, target), tz="UTC")
    x = x[(x.station == station) & (x.target_d == target) &
          (x.version == PARENT_VERSION) & (x.capture_utc <= cutoff)]
    if x.empty:
        return None
    return x.sort_values("capture_utc").iloc[-1]


def build_row(station, target, lav, cityx, captured, nowcast):
    mu_lampx = prediction(station, lav["tmax"], cityx.mu)
    return {
        "capture_utc": captured.isoformat(), "station": station,
        "target": target.isoformat(), "version": VERSION,
        "parent_version": PARENT_VERSION, "recipe": RECIPE, "unit": "F",
        "lav_runtime_utc": lav["runtime_utc"], "lav_avail_utc": lav["avail_utc"],
        "freeze_utc": lav["freeze_utc"], "lav_tmax": round(float(lav["tmax"]), 4),
        "cityx_capture_utc": cityx.capture_utc.isoformat(),
        "mu_cityx": round(float(cityx.mu), 4), "offset_f": OFFSETS_F[station],
        "mu_lampx": round(mu_lampx, 4), "now_version": NOW_VERSION,
        "obs_valid_utc": nowcast["obs_valid_utc"],
        "obs_avail_utc": nowcast["obs_avail_utc"], "n_obs": nowcast["n_obs"],
        "obs_latest": round(nowcast["obs_latest"], 4),
        "lav_at_obs": round(nowcast["lav_at_obs"], 4),
        "innovation": round(nowcast["innovation"], 4),
        "mu_nowx": round(now_prediction(mu_lampx, nowcast["innovation"]), 4),
    }


def log_run(target, status, detail):
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(f"{stamp} | lamp_shadow | {target} | {status} | {detail}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="target local date YYYY-MM-DD")
    args = ap.parse_args()
    target = dt.date.fromisoformat(args.date)
    if target < dt.date.fromisoformat(SHADOW0):
        print(f"{VERSION}: target anterior al inicio forward"); return
    if not acquire_lock():
        print(f"{VERSION}: [SKIP] otra captura LAMP está activa")
        return
    try:
        captured = dt.datetime.now(dt.timezone.utc)
        cityx_path = os.path.join(D, "exact_selector_forward.csv")
        if not os.path.exists(cityx_path):
            log_run(target, "FAIL", "exact_selector_forward.csv missing")
            raise SystemExit("[ABORT] falta CITYX2 forward")
        cityx = pd.read_csv(cityx_path)
        done = set()
        if os.path.exists(OUT):
            old = pd.read_csv(OUT)
            done = set(zip(old.station, old.target.astype(str), old.version))
        rows, failures = [], []
        for station in OFFSETS_F:
            if (station, target.isoformat(), VERSION) in done:
                continue
            if not capture_window_open(station, target, captured):
                failures.append(f"{station}: fuera de ventana freeze..fin del dia local")
                continue
            parent = eligible_cityx(cityx, station, target)
            if parent is None:
                failures.append(f"{station}: sin CITYX2 pre-freeze")
                continue
            try:
                raw = fetch_station(station, target, target)
                selected = select_daily(raw, station, target, target, AVAIL_LAG_HOURS)
                if len(selected) != 1:
                    raise ValueError("sin runtime LAV elegible")
                observed = fetch_asos(station, target, target)
                nowcast = select_features(raw, observed, station, target, target)
                if len(nowcast) != 1:
                    raise ValueError("sin ASOS pre-freeze elegible")
                rows.append(build_row(station, target, selected[0], parent, captured, nowcast[0]))
            except Exception as exc:
                failures.append(f"{station}: {exc}")
        if rows:
            new = not os.path.exists(OUT)
            with open(OUT, "a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                if new:
                    writer.writeheader()
                writer.writerows(rows)
        already = sum((station, target.isoformat(), VERSION) in done for station in OFFSETS_F)
        completed = already+len(rows)
        status = "OK" if completed == len(OFFSETS_F) and not failures else "WARN"
        log_run(target, status, f"rows={len(rows)} completed={completed}/9 failures={len(failures)}")
        print(f"{VERSION}: +{len(rows)} forecasts -> {OUT}")
        for failure in failures:
            print(f"[WARN] {failure}", file=sys.stderr)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
