#!/usr/bin/env python3
"""Tier 2 — the feature layer + an independent actigraphy wake/sleep baseline.

Walch and Zhai aren't "run-on-your-ring" tools; they're training codebases. Their
*ideas*, reimplemented on the ring's signals, are the useful part and the substrate
for training your own 4-stage (deep/light/REM/wake) model later:

  per-30s-epoch features
    HR      : mean / min / max / std
    HRV     : RMSSD, SDNN, pNN50   (±90 s window)
    resp    : respiration rate + variability, derived from RRI (RSA / EDR)
    motion  : activity count       (actigraphy)
    temp    : skin temperature
    clock   : minutes-since-onset + circadian cosine   (Walch clock proxy)

Plus a Cole-Kripke-style actigraphy wake/sleep classifier — an independent,
transparent cross-check on SleepECG's wake/sleep, and the strongest single cue
for the wake vs sleep boundary.

Outputs a tidy per-epoch feature table (CSV) — feed it to LightGBM/an LSTM once
you have EEG-labeled nights (see SLEEP_STAGING.md, "training your own 4-class").

Usage:
  .venv/bin/python interpret/sleep_features.py --synthetic
  .venv/bin/python interpret/sleep_features.py --db oura.db
  .venv/bin/python interpret/sleep_features.py --synthetic --compare interpret/out/hypnogram_*.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
from pathlib import Path

import numpy as np
from scipy import signal

from oura_sleep_io import EPOCH_S, load_night, make_synthetic_night

# Cole-Kripke (Webster rescored) weights, newest→oldest neighbourhood.
# Calibrated for actigraph counts; on ring motion units treat SCALE as tunable.
_CK_W = np.array([0.0033]) * np.array([1.06, 0.54, 0.58, 0.76, 2.30, 0.74, 0.67])
_CK_OFFSETS = np.array([-4, -3, -2, -1, 0, 1, 2])
# CK's threshold assumes actigraph "activity counts" (0–300/epoch). The ring emits
# motion-seconds (0–30), so counts are ~15× smaller — scale up to compensate. This
# is a STARTING value: calibrate it on nights you know you were awake vs asleep.
_CK_SCALE = 15.0


def _epoch_grid(night):
    n = night.n_epochs
    centers = night.start_unix + (np.arange(n) + 0.5) * EPOCH_S
    return n, centers


def hr_hrv_features(night, centers, half_window_s=90.0):
    """Per-epoch HR + time-domain HRV over a ±half_window centred on each epoch."""
    b = night.beats_unix[night.valid]
    rri = night.ibi_ms[night.valid]  # ms
    order = np.argsort(b)
    b, rri = b[order], rri[order]
    n = len(centers)
    hr_mean = np.full(n, np.nan); hr_min = np.full(n, np.nan)
    hr_max = np.full(n, np.nan); hr_std = np.full(n, np.nan)
    rmssd = np.full(n, np.nan); sdnn = np.full(n, np.nan); pnn50 = np.full(n, np.nan)
    lo = np.searchsorted(b, centers - half_window_s)
    hi = np.searchsorted(b, centers + half_window_s)
    for i in range(n):
        seg = rri[lo[i]:hi[i]]
        if seg.size >= 3:
            hr = 60000.0 / seg
            hr_mean[i], hr_min[i], hr_max[i], hr_std[i] = hr.mean(), hr.min(), hr.max(), hr.std()
            d = np.diff(seg)
            rmssd[i] = np.sqrt(np.mean(d * d))
            sdnn[i] = seg.std()
            pnn50[i] = 100.0 * np.mean(np.abs(d) > 50)
    return dict(hr_mean=hr_mean, hr_min=hr_min, hr_max=hr_max, hr_std=hr_std,
                rmssd=rmssd, sdnn=sdnn, pnn50=pnn50)


def respiration_features(night, centers, half_window_s=90.0, fs=4.0):
    """Respiration rate + variability derived from RRI (RSA / ECG-derived respiration):
    resample RRI to `fs` Hz, band-pass 0.1–0.4 Hz (6–24 breaths/min), take the PSD peak."""
    b = night.beats_unix[night.valid]
    rri = night.ibi_ms[night.valid]
    order = np.argsort(b); b, rri = b[order], rri[order]
    n = len(centers)
    resp_rate = np.full(n, np.nan)
    try:
        sos = signal.butter(2, [0.1, 0.4], btype="band", fs=fs, output="sos")
    except ValueError:
        sos = None
    lo = np.searchsorted(b, centers - half_window_s)
    hi = np.searchsorted(b, centers + half_window_s)
    for i in range(n):
        s, e = lo[i], hi[i]
        if e - s < 8 or sos is None:
            continue
        t = b[s:e]; y = rri[s:e]
        grid = np.arange(t[0], t[-1], 1.0 / fs)
        if grid.size < 16:
            continue
        yi = np.interp(grid, t, y)
        yi = signal.detrend(yi)
        yf = signal.sosfiltfilt(sos, yi)
        f, p = signal.welch(yf, fs=fs, nperseg=min(len(yf), 128))
        band = (f >= 0.1) & (f <= 0.4)
        if band.any() and p[band].max() > 0:
            resp_rate[i] = f[band][np.argmax(p[band])] * 60.0
    # variability = rolling std of respiration rate over ±5 epochs
    resp_var = np.full(n, np.nan)
    for i in range(n):
        w = resp_rate[max(0, i - 5):i + 6]
        w = w[~np.isnan(w)]
        if w.size >= 3:
            resp_var[i] = w.std()
    return dict(resp_rate=resp_rate, resp_var=resp_var)


def motion_features(night, centers):
    """Per-epoch activity count (sum of motion magnitude falling in the epoch)."""
    n = len(centers)
    counts = np.zeros(n)
    if night.motion_unix.size:
        idx = np.floor((night.motion_unix - night.start_unix) / EPOCH_S).astype(int)
        for k, mag in zip(idx, night.motion_mag):
            if 0 <= k < n:
                counts[k] += mag
    return counts


def cole_kripke(counts):
    """Cole-Kripke actigraphy wake/sleep. Returns array of 'WAKE'/'SLEEP' per epoch.
    D = scale * Σ w_k · A_{i+off_k};  wake if D >= 1. Weights are the Webster rescoring;
    on ring motion units the absolute threshold needs per-device calibration."""
    a = counts * _CK_SCALE
    n = len(a)
    D = np.zeros(n)
    for w, off in zip(_CK_W, _CK_OFFSETS):
        shifted = np.zeros(n)
        if off < 0:
            shifted[-off:] = a[:n + off]
        elif off > 0:
            shifted[:n - off] = a[off:]
        else:
            shifted = a
        D += w * shifted
    return np.where(D >= 1.0, "WAKE", "SLEEP")


def temp_features(night, centers):
    n = len(centers)
    if night.temp_unix.size == 0:
        return np.full(n, np.nan)
    return np.interp(centers, night.temp_unix, night.temp_c,
                     left=night.temp_c[0], right=night.temp_c[-1])


def clock_features(night, centers, wake_sleep):
    """Walch-style clock proxy: minutes since sleep onset + circadian cosine
    (peaks ~04:00, the nadir of core temp / peak sleep propensity)."""
    n = len(centers)
    onset = next((i for i, s in enumerate(wake_sleep) if s == "SLEEP"), 0)
    min_since_onset = (np.arange(n) - onset) * EPOCH_S / 60.0
    local = (centers + night.tz_offset_hours * 3600)
    hour = (local / 3600.0) % 24.0
    circadian_cos = np.cos(2 * np.pi * (hour - 4.0) / 24.0)
    return min_since_onset, circadian_cos


def cohen_kappa(a, b):
    labels = sorted(set(a) | set(b))
    idx = {l: i for i, l in enumerate(labels)}
    k = len(labels)
    m = np.zeros((k, k))
    for x, y in zip(a, b):
        m[idx[x], idx[y]] += 1
    tot = m.sum()
    if tot == 0:
        return float("nan")
    po = np.trace(m) / tot
    pe = sum((m[i, :].sum() / tot) * (m[:, i].sum() / tot) for i in range(k))
    return (po - pe) / (1 - pe) if pe != 1 else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="oura.db")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--tz", type=float, default=None)
    ap.add_argument("--out", default="interpret/out")
    ap.add_argument("--compare", default=None,
                    help="glob to a Tier-1 hypnogram CSV; reports wake/sleep agreement (kappa)")
    args = ap.parse_args()

    night = (make_synthetic_night(tz_offset_hours=args.tz or 0.0) if args.synthetic
             else load_night(args.db, tz_offset_hours=args.tz))
    tag = "SYNTHETIC" if args.synthetic else args.db
    n, centers = _epoch_grid(night)
    print(f"### Tier-2 features — {tag}  ({n} epochs, {night.duration_h:.1f}h, "
          f"coverage {night.coverage()*100:.0f}%)")

    feats = {}
    feats.update(hr_hrv_features(night, centers))
    feats.update(respiration_features(night, centers))
    counts = motion_features(night, centers)
    feats["activity_count"] = counts
    feats["temp_c"] = temp_features(night, centers)
    ws = cole_kripke(counts)
    feats["min_since_onset"], feats["circadian_cos"] = clock_features(night, centers, ws)
    feats["actigraphy_ws"] = ws

    # summary
    def _m(x):
        x = np.asarray(x, float); x = x[~np.isnan(x)]
        return f"{x.mean():.1f}" if x.size else "n/a"
    wake_pct = 100 * np.mean(ws == "WAKE")
    print(f"  HR {_m(feats['hr_mean'])} bpm · RMSSD {_m(feats['rmssd'])} ms · "
          f"resp {_m(feats['resp_rate'])} br/min · actigraphy wake {wake_pct:.0f}%")

    # cross-check vs a Tier-1 hypnogram if given
    if args.compare:
        matches = sorted(glob.glob(args.compare))
        if matches:
            with open(matches[-1]) as f:
                se = [row["stage"] for row in csv.DictReader(f)]
            se_ws = ["WAKE" if s == "WAKE" else "SLEEP" for s in se]
            m = min(len(se_ws), len(ws))
            kappa = cohen_kappa(list(ws[:m]), se_ws[:m])
            agree = np.mean([a == b for a, b in zip(ws[:m], se_ws[:m])])
            print(f"  vs SleepECG wake/sleep ({Path(matches[-1]).name}): "
                  f"{agree*100:.0f}% agreement, Cohen κ={kappa:.2f}")
        else:
            print(f"  --compare: no CSV matched {args.compare}")

    # write feature table
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    date = dt.datetime.fromtimestamp(
        night.start_unix + night.tz_offset_hours * 3600, dt.timezone.utc).strftime("%Y-%m-%d")
    cols = ["hr_mean", "hr_min", "hr_max", "hr_std", "rmssd", "sdnn", "pnn50",
            "resp_rate", "resp_var", "activity_count", "temp_c",
            "min_since_onset", "circadian_cos", "actigraphy_ws"]
    path = out_dir / f"features_{date}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "clock"] + cols)
        for i in range(n):
            clock = dt.datetime.fromtimestamp(
                centers[i] - EPOCH_S / 2 + night.tz_offset_hours * 3600,
                dt.timezone.utc).strftime("%H:%M")
            row = [i, clock]
            for c in cols:
                v = feats[c][i]
                row.append(v if isinstance(v, str) else ("" if v != v else round(float(v), 3)))
            w.writerow(row)
    print(f"wrote {path}  ({len(cols)} features/epoch)")


if __name__ == "__main__":
    main()
