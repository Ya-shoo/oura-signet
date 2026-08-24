#!/usr/bin/env python3
"""Tier 1 — independent hypnogram from the ring's overnight IBI, via SleepECG.

SleepECG ships GRU classifiers pretrained on thousands of MESA records. We feed
them the ring's reconstructed beat series (HRV features + circadian clock + age/sex)
and get a per-30s hypnogram with ZERO training:

  ws-gru-mesa   -> WAKE / SLEEP        (2-class)
  wrn-gru-mesa  -> WAKE / REM / NREM   (3-class)   <- default

NOTE the ceiling: the pretrained models do NOT split NREM into light/deep. A full
4-stage (deep/light/REM/wake) hypnogram means training a custom SleepECG classifier
on MESA/SHHS N1/N2/N3 labels — the Tier-2 "improve" step, not this file.

Usage:
  python interpret/sleep_stage.py --synthetic              # prove the pipeline now
  python interpret/sleep_stage.py --db oura.db --age 30 --sex M
  python interpret/sleep_stage.py --db oura.db --classifier ws-gru-mesa

Requires the project venv:  .venv/bin/python interpret/sleep_stage.py ...
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import numpy as np

from oura_sleep_io import EPOCH_S, load_night, make_synthetic_night

GLYPH = {"WAKE": "W", "REM": "R", "NREM": "N", "SLEEP": "-", "N1": "L", "N2": "L",
         "N3": "D", "DEEP": "D", "LIGHT": "L", "UNDEFINED": "?"}
WAKE_LABELS = {"WAKE"}


def _fmt_clock(unix_s: float, tz: float) -> str:
    return dt.datetime.fromtimestamp(unix_s + tz * 3600, dt.timezone.utc).strftime("%H:%M")


def _date_str(unix_s: float, tz: float) -> str:
    return dt.datetime.fromtimestamp(unix_s + tz * 3600, dt.timezone.utc).strftime("%Y-%m-%d")


def summarize(stages: list[str], start_unix: float, tz: float) -> dict:
    """Durations, efficiency, latency, WASO from a per-epoch stage list."""
    n = len(stages)
    tib = n * EPOCH_S / 60.0
    mins = {}
    for s in stages:
        mins[s] = mins.get(s, 0.0) + EPOCH_S / 60.0
    asleep_epochs = [i for i, s in enumerate(stages) if s not in WAKE_LABELS]
    tst = len(asleep_epochs) * EPOCH_S / 60.0
    onset = asleep_epochs[0] if asleep_epochs else None
    latency = (onset * EPOCH_S / 60.0) if onset is not None else float("nan")
    if onset is not None:
        waso = sum(1 for s in stages[onset:] if s in WAKE_LABELS) * EPOCH_S / 60.0
    else:
        waso = float("nan")
    transitions = sum(1 for a, b in zip(stages, stages[1:]) if a != b)
    return {
        "time_in_bed_min": round(tib, 1),
        "total_sleep_min": round(tst, 1),
        "sleep_efficiency_pct": round(100 * tst / tib, 1) if tib else 0.0,
        "sleep_latency_min": round(latency, 1),
        "waso_min": round(waso, 1),
        "n_transitions": transitions,
        "stage_min": {k: round(v, 1) for k, v in mins.items()},
        "start": _fmt_clock(start_unix, tz),
        "end": _fmt_clock(start_unix + n * EPOCH_S, tz),
    }


def plausibility(stages: list[str], summ: dict, coverage: float) -> list[tuple[str, str, str]]:
    """No-ground-truth sanity checks. Returns (level, name, detail)."""
    out = []
    tst = summ["total_sleep_min"] or 1
    stage_min = summ["stage_min"]
    rem = stage_min.get("REM", 0)
    rem_pct = 100 * rem / tst
    eff = summ["sleep_efficiency_pct"]
    hours = summ["time_in_bed_min"] / 60.0
    trans_per_h = summ["n_transitions"] / max(hours, 1e-9)

    def chk(ok, name, detail):
        out.append(("PASS" if ok else "WARN", name, detail))

    chk(coverage >= 0.80, "beat coverage",
        f"{coverage*100:.0f}% of epochs have a valid beat (want >=80%)")
    chk(50 <= eff <= 100, "sleep efficiency", f"{eff:.0f}% (physiologic 70–98%)")
    if "REM" in stage_min:
        chk(8 <= rem_pct <= 35, "REM fraction", f"{rem_pct:.0f}% of TST (typical 18–25%)")
    chk(summ["sleep_latency_min"] <= 60 or np.isnan(summ["sleep_latency_min"]),
        "sleep latency", f"{summ['sleep_latency_min']} min")
    chk(trans_per_h <= 40, "fragmentation", f"{trans_per_h:.0f} stage transitions/hour")
    # REM should not dominate the first ~20 min of sleep
    first = stages[: int(20 * 60 / EPOCH_S)]
    rem_early = first.count("REM") / max(len(first), 1)
    chk(rem_early < 0.5, "REM latency", f"{rem_early*100:.0f}% REM in first 20 min (want low)")
    return out


def compact_hypnogram(stages: list[str], start_unix: float, tz: float, per_char_min=10) -> str:
    step = int(per_char_min * 60 / EPOCH_S)
    line = []
    for i in range(0, len(stages), step):
        blk = stages[i:i + step]
        maj = max(set(blk), key=blk.count)
        line.append(GLYPH.get(maj, "?"))
    return (f"  {_fmt_clock(start_unix, tz)} " + "".join(line)
            + f" {_fmt_clock(start_unix + len(stages)*EPOCH_S, tz)}"
            + f"   (~{per_char_min}min/char)")


def write_outputs(out_dir: Path, night, stages, conf, summ):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _fmt_clock(night.start_unix, night.tz_offset_hours).replace(":", "")
    date = _date_str(night.start_unix, night.tz_offset_hours)
    csv_path = out_dir / f"hypnogram_{date}_{stamp}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "clock", "stage", "confidence"])
        for i, (s, c) in enumerate(zip(stages, conf)):
            w.writerow([i, _fmt_clock(night.start_unix + i * EPOCH_S, night.tz_offset_hours),
                        s, f"{c:.3f}"])
    return csv_path


def _find_main_bout(mask, min_h=2.0):
    """Longest contiguous True run in `mask`; (start, end) indices, or None if the
    best run is shorter than `min_h` hours."""
    n = len(mask)
    best, best_len, i = None, 0, 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i > best_len:
                best_len, best = j - i, (i, j)
            i = j
        else:
            i += 1
    if best is None or best_len * EPOCH_S < min_h * 3600:
        return None
    return best


def refine_window(night):
    """No ring bedtime_period → the loader fell back to the full beat span (often
    12h+). Find the main sleep bout from actigraphy — Cole-Kripke on binned motion,
    with a scale-robust relative-motion fallback — and trim the window to it, so
    efficiency/latency reflect the actual night."""
    from sleep_features import _epoch_grid, motion_features, cole_kripke
    n, centers = _epoch_grid(night)
    if n < 40 or night.motion_unix.size < 10:
        return night
    counts = motion_features(night, centers)
    k = max(1, int(round(600 / EPOCH_S)))  # ~10-min smoothing
    kern = np.ones(k) / k
    # (1) Cole-Kripke wake/sleep, majority-smoothed
    ck = np.array([s == "SLEEP" for s in cole_kripke(counts)], dtype=float)
    bout = _find_main_bout(np.convolve(ck, kern, mode="same") >= 0.5)
    # (2) fallback if CK's absolute threshold is miscalibrated for this ring:
    #     longest window quiet relative to the night's own motion distribution.
    if bout is None:
        sm = np.convolve(counts, kern, mode="same")
        bout = _find_main_bout(sm <= max(sm.mean() * 0.5, float(np.percentile(sm, 30))))
    if bout is None:
        return night
    a, b = bout
    night.start_unix = float(centers[a] - EPOCH_S / 2)
    night.end_unix = float(centers[min(b, n - 1)] + EPOCH_S / 2)
    return night


def upsert_metrics_db(db_path: Path, date: str, summ: dict, classifier: str, stages: list[str]):
    import json
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS sleep_night(
        date TEXT PRIMARY KEY, classifier TEXT, time_in_bed_min REAL, total_sleep_min REAL,
        sleep_efficiency_pct REAL, sleep_latency_min REAL, waso_min REAL,
        wake_min REAL, rem_min REAL, nrem_min REAL, n_transitions INT,
        start_clock TEXT, end_clock TEXT, hypnogram TEXT)""")
    for col in ("start_clock TEXT", "end_clock TEXT", "hypnogram TEXT"):
        try:
            con.execute(f"ALTER TABLE sleep_night ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    # downsample the per-epoch stages to ~48 bars (majority per bin) for the UI
    nbars = 48
    step = max(1, len(stages) // nbars)
    bars = [max(set(stages[i:i + step]), key=stages[i:i + step].count)
            for i in range(0, len(stages), step)]
    sm = summ["stage_min"]
    con.execute("""INSERT OR REPLACE INTO sleep_night
        (date, classifier, time_in_bed_min, total_sleep_min, sleep_efficiency_pct,
         sleep_latency_min, waso_min, wake_min, rem_min, nrem_min, n_transitions,
         start_clock, end_clock, hypnogram)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        date, classifier, summ["time_in_bed_min"], summ["total_sleep_min"],
        summ["sleep_efficiency_pct"], summ["sleep_latency_min"], summ["waso_min"],
        sm.get("WAKE", 0), sm.get("REM", 0), sm.get("NREM", sm.get("SLEEP", 0)),
        summ["n_transitions"], summ["start"], summ["end"], json.dumps(bars)))
    con.commit()
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="oura.db")
    ap.add_argument("--synthetic", action="store_true", help="generate a plausible night to test the pipeline")
    ap.add_argument("--classifier", default="wrn-gru-mesa",
                    choices=["wrn-gru-mesa", "wrn-gru-mesa-weighted", "ws-gru-mesa"])
    ap.add_argument("--age", type=int, default=30)
    ap.add_argument("--sex", default="M", choices=["M", "F"])
    ap.add_argument("--tz", type=float, default=None, help="UTC offset hours (default: system local)")
    ap.add_argument("--out", default="interpret/out")
    ap.add_argument("--metrics-db", default=None, help="also upsert the night summary into this sqlite file")
    args = ap.parse_args()

    # import sleepecg lazily so --help / errors don't pay the TF import cost
    from sleepecg import Gender, SleepRecord, SubjectData, load_classifier, stage

    if args.synthetic:
        night = make_synthetic_night(tz_offset_hours=args.tz or 0.0)
        print("### SYNTHETIC night (pipeline check — not real data)")
    else:
        night = load_night(args.db, tz_offset_hours=args.tz)
        if night.duration_h > 11:  # no ring bedtime → trim to the actigraphy bout
            night = refine_window(night)
        print(f"### {args.db}")

    hbt = night.heartbeat_times_rel()
    print(f"window {_fmt_clock(night.start_unix, night.tz_offset_hours)}–"
          f"{_fmt_clock(night.end_unix, night.tz_offset_hours)} "
          f"({night.duration_h:.1f}h)  valid_beats={hbt.size}  "
          f"coverage={night.coverage()*100:.0f}%  epochs={night.n_epochs}")
    if hbt.size < 100:
        sys.exit("too few valid beats to stage — check IBI coverage.")

    rec = SleepRecord(
        sleep_stage_duration=int(EPOCH_S),
        recording_start_time=night.start_time_local,
        heartbeat_times=hbt,
        subject_data=SubjectData(gender=Gender.MALE if args.sex == "M" else Gender.FEMALE,
                                 age=args.age),
    )
    clf = load_classifier(args.classifier, "SleepECG")
    stages = [str(s) for s in stage(clf, rec, return_mode="str")]
    probs = stage(clf, rec, return_mode="prob")
    conf = np.nanmax(probs, axis=1) if probs.ndim == 2 else np.full(len(stages), np.nan)
    # drop any UNDEFINED padding epochs at the tail
    while stages and stages[-1] == "UNDEFINED":
        stages.pop()
        conf = conf[:-1]

    summ = summarize(stages, night.start_unix, night.tz_offset_hours)

    print(f"\nClassifier: {args.classifier} ({clf.stages_mode})")
    print(f"In bed {summ['time_in_bed_min']:.0f} min · asleep {summ['total_sleep_min']:.0f} min "
          f"· efficiency {summ['sleep_efficiency_pct']:.0f}% · latency {summ['sleep_latency_min']} min "
          f"· WASO {summ['waso_min']} min")
    for k, v in summ["stage_min"].items():
        pct = 100 * v / (summ["total_sleep_min"] or 1) if k not in WAKE_LABELS else 0
        print(f"   {k:<6} {v:>6.0f} min" + (f"  ({pct:3.0f}% of sleep)" if k not in WAKE_LABELS else ""))
    print("\n" + compact_hypnogram(stages, night.start_unix, night.tz_offset_hours))

    print("\nPlausibility (no ground truth):")
    for level, name, detail in plausibility(stages, summ, night.coverage()):
        mark = "✓" if level == "PASS" else "!"
        print(f"   [{mark}] {name:<16} {detail}")

    # synthetic self-check (plumbing only — NOT accuracy validation)
    if night.truth_stage is not None:
        truth = list(night.truth_stage[:len(stages)])
        pred3 = ["NREM" if s in ("N1", "N2", "N3", "NREM", "SLEEP") else s for s in stages]
        agree = np.mean([p == t for p, t in zip(pred3, truth)])
        print(f"\n   synthetic self-check: {agree*100:.0f}% epoch agreement vs injected truth "
              "(sanity only — synthetic HRV is crude, real validation needs EEG)")

    out_dir = Path(args.out)
    csv_path = write_outputs(out_dir, night, stages, conf, summ)
    date = _date_str(night.start_unix, night.tz_offset_hours)
    print(f"\nwrote {csv_path}")
    if args.metrics_db:
        upsert_metrics_db(Path(args.metrics_db), date, summ, args.classifier, stages)
        print(f"upserted sleep_night row for {date} -> {args.metrics_db}")


if __name__ == "__main__":
    main()
