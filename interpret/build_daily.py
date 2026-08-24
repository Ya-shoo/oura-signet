"""GOAL 2 — build the per-day metric history that baselines trend over.

Reads the raw ring event buffer (oura.db) and writes one robust row per local
date into db/metrics.db `daily_metrics`. This is the substrate the baseline layer
(baselines.py) needs: without a *history* there is nothing to compare "today"
against, so every deviation/readiness readout stays `pending`.

Design notes
------------
* Statistics MATCH app/src-tauri/src/metrics.rs so a day backfilled here is
  comparable to a day the live Rust path writes via persist_daily():
    - rhr      = 10th percentile of quality-gated HR (green_ibi_quality, q>=3)
    - hrv      = median rMSSD (hrv_event.rmssd_ms, 5..300 ms)
    - temp_c   = median skin temp (temp_period / sleep_temp_event, 20..42 C)
    - spo2     = median spo2_event.spo2_percent (70..100)
    - met_avg  = mean activity_information.met (0..25)
    - resp     = median breaths/min derived from overnight IBI (see resp_rate)
  sleep_min/sleep_eff are pulled from the already-computed `sleep_night` table.
* Device time is deciseconds on a free-running clock; anchored to wall-clock via
  the newest event's captured_unix (same scheme as oura_sleep_io / run_sleep_model).
* Physiology (rhr/hrv/temp/spo2/resp) is bucketed to the local date it was
  recorded; sleep is keyed to the night's date in sleep_night. With only a few
  days buffered the history is short — that's expected; baselines warm up as more
  days sync (Oura itself needs ~1 week).

Usage:  python interpret/build_daily.py --db oura.db --metrics-db db/metrics.db
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

TAG_IBI_IR = 0x60        # ibi_and_amplitude   (overnight IR)
TAG_IBI_GREEN = 0x80     # green_ibi_quality   (quality-gated HR)
TAG_HRV = 0x5d           # hrv_event           (rmssd_ms)
TAG_TEMP_PERIOD = 0x69   # temp_period
TAG_TEMP_SLEEP = 0x75    # sleep_temp_event
TAG_TEMP_EVENT = 0x46    # temp_event
TAG_SPO2 = 0x6f          # spo2_event
TAG_ACTIVITY = 0x50      # activity_information (met[])


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    rank = (p / 100.0) * (len(s) - 1)
    lo = int(rank)
    frac = rank - lo
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * frac


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _clock_window(date: str, start_clock, end_clock) -> tuple[float, float] | None:
    """Resolve a sleep_night's HH:MM start/end clocks (end is on `date`) into a
    ``(start_unix, end_unix)`` pair, handling a bout that crosses midnight."""
    if not start_clock or not end_clock:
        return None
    try:
        d = dt.date.fromisoformat(date)
        sh, sm = (int(x) for x in str(start_clock).split(":")[:2])
        eh, em = (int(x) for x in str(end_clock).split(":")[:2])
    except (ValueError, TypeError):
        return None
    start = dt.datetime(d.year, d.month, d.day, sh, sm)
    end = dt.datetime(d.year, d.month, d.day, eh, em)
    if end <= start:  # went to bed the previous evening
        start -= dt.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def resp_rate(beat_unix: list[float], ibi_ms: list[float]) -> tuple[float | None, float | None]:
    """Overnight breathing rate (breaths/min) from respiratory sinus arrhythmia.

    Oura derives breathing rate from the beat-to-beat interval series the same
    way (IBI resampled to 4 Hz → band-pass → dominant frequency; the app's
    ``breathing_rate_calculate_averages`` is an IBI→RR@4Hz→IIR pipeline). Rather
    than counting zero-crossings over the whole night — which undercounts badly
    across sync gaps (it was reporting an impossible 5-8 br/min) — we take the
    PSD-peak frequency in the respiratory band over short sliding windows and
    report the median across the night, which is robust to gaps and artifacts.

    Returns ``(rate, variability)`` where variability is the std of the
    per-window rates (a steadiness readout). ``(None, None)`` if too sparse.
    """
    if len(ibi_ms) < 120:
        return None, None
    import numpy as np
    from scipy import signal

    fs, win_s, step_s = 4.0, 120.0, 30.0  # 2-min windows, 30 s hop
    b = np.asarray(beat_unix, float)
    x = np.asarray(ibi_ms, float)
    order = np.argsort(b)
    b, x = b[order], x[order]
    if b[-1] - b[0] < 300:  # need at least ~5 min of span
        return None, None
    try:
        sos = signal.butter(2, [0.13, 0.40], btype="band", fs=fs, output="sos")
    except ValueError:
        return None, None
    rates: list[float] = []
    c = b[0] + win_s / 2
    while c + win_s / 2 <= b[-1]:
        s = np.searchsorted(b, c - win_s / 2)
        e = np.searchsorted(b, c + win_s / 2)
        c += step_s
        if e - s < 20:  # too few beats in this window
            continue
        t, y = b[s:e], x[s:e]
        grid = np.arange(t[0], t[-1], 1.0 / fs)
        if grid.size < 32:
            continue
        yi = np.interp(grid, t, y)          # uniform 4 Hz IBI
        yi = signal.detrend(yi)
        yf = signal.sosfiltfilt(sos, yi)    # isolate the respiratory band
        f, p = signal.welch(yf, fs=fs, nperseg=min(len(yf), 256))
        band = (f >= 0.13) & (f <= 0.40)    # ≈ 8–24 br/min
        if band.any() and p[band].max() > 0:
            rates.append(float(f[band][np.argmax(p[band])] * 60.0))
    if len(rates) < 5:
        return None, None
    arr = np.asarray(rates)
    return round(float(np.median(arr)), 1), round(float(arr.std()), 2)


def build(db_path: Path, metrics_db: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ring_timestamp, tag, decoded_json, captured_unix FROM events "
        "WHERE decoded_json IS NOT NULL ORDER BY ring_timestamp"
    ).fetchall()
    if not rows:
        raise SystemExit(f"{db_path}: no decoded events — sync the ring first.")

    # Anchor to the ring's OWN clock (tag-66 unix_time markers), not the newest
    # event's sync-write time — the latter mis-dates nights by days across a
    # recording gap. See interpret/ring_clock.py.
    from ring_clock import epoch_from_rows
    epoch = epoch_from_rows(rows)
    if epoch is None:  # legacy fallback: no time markers in this DB yet
        max_ds, anchor_unix = max(
            ((r["ring_timestamp"], r["captured_unix"]) for r in rows), key=lambda x: x[0]
        )
        epoch = anchor_unix - max_ds / 10.0

    def ds_to_unix(ds: int) -> float:
        return ds / 10.0 + epoch

    def local_date(unix: float) -> str:
        return dt.datetime.fromtimestamp(unix).strftime("%Y-%m-%d")

    # per-date accumulators
    hr_q: dict[str, list[float]] = defaultdict(list)
    rmssd: dict[str, list[float]] = defaultdict(list)
    temp_c: dict[str, list[float]] = defaultdict(list)
    spo2: dict[str, list[float]] = defaultdict(list)
    met: dict[str, list[float]] = defaultdict(list)
    # IR beat timeline is kept global (unbinned) so breathing rate can be
    # windowed to the actual overnight sleep bout instead of a calendar day.
    beats_all: list[float] = []
    ibi_all: list[float] = []

    for r in rows:
        tag = r["tag"]
        d = local_date(ds_to_unix(r["ring_timestamp"]))
        try:
            v = json.loads(r["decoded_json"])
        except Exception:
            continue
        if tag == TAG_IBI_GREEN and v.get("ibi_ms"):
            q = v.get("quality", [])
            for i, ibi in enumerate(v["ibi_ms"]):
                if i < len(q) and q[i] >= 3 and ibi and 300 <= ibi <= 2000:
                    hr_q[d].append(60000.0 / ibi)
        elif tag == TAG_HRV and v.get("rmssd_ms"):
            for x in v["rmssd_ms"]:
                if 5.0 <= x <= 300.0:
                    rmssd[d].append(float(x))
        elif tag in (TAG_TEMP_PERIOD, TAG_TEMP_SLEEP, TAG_TEMP_EVENT) and v.get("temps_c"):
            for c in v["temps_c"]:
                if 20.0 <= c <= 42.0:
                    temp_c[d].append(float(c))
        elif tag == TAG_SPO2 and v.get("spo2_percent"):
            for s in v["spo2_percent"]:
                if 70.0 <= s <= 100.0:
                    spo2[d].append(float(s))
        elif tag == TAG_ACTIVITY and v.get("met"):
            for m in v["met"]:
                if 0.0 <= m <= 25.0:
                    met[d].append(float(m))
        elif tag == TAG_IBI_IR and v.get("ibi_ms"):
            t0 = ds_to_unix(r["ring_timestamp"])
            acc = 0.0
            for ibi in v["ibi_ms"]:
                if ibi and 300 <= ibi <= 2000:
                    acc += ibi / 1000.0
                    beats_all.append(t0 + acc)
                    ibi_all.append(float(ibi))
    con.close()

    # sleep from the derived store (already computed by sleep_stage/native).
    # start_clock/end_clock give the night's bout, which we use to window the
    # breathing-rate estimate to actual overnight sleep (Oura reports resp as an
    # overnight average — daytime activity would otherwise pollute it).
    sleep = {}
    windows: dict[str, tuple[float, float]] = {}
    if metrics_db.exists():
        sc = sqlite3.connect(f"file:{metrics_db}?mode=ro", uri=True)
        try:
            for date, tsm, eff, s_clk, e_clk in sc.execute(
                "SELECT date, total_sleep_min, sleep_efficiency_pct, start_clock, end_clock "
                "FROM sleep_night"
            ):
                sleep[date] = (tsm, eff)
                w = _clock_window(date, s_clk, e_clk)
                if w is not None:
                    windows[date] = w
        except sqlite3.OperationalError:
            pass
        sc.close()

    # Breathing rate per night, from the IR beat timeline windowed to the bout.
    import numpy as np
    ball = np.asarray(beats_all, float)
    iall = np.asarray(ibi_all, float)
    order = np.argsort(ball)
    ball, iall = ball[order], iall[order]
    resp_by_date: dict[str, tuple[float | None, float | None]] = {}
    for date, (su, eu) in windows.items():
        s = int(np.searchsorted(ball, su))
        e = int(np.searchsorted(ball, eu))
        resp_by_date[date] = resp_rate(ball[s:e].tolist(), iall[s:e].tolist())

    dates = sorted(set(hr_q) | set(rmssd) | set(temp_c) | set(spo2) | set(met) | set(sleep))
    out = []
    for d in dates:
        tsm, eff = sleep.get(d, (None, None))
        resp, resp_var = resp_by_date.get(d, (None, None))
        out.append(
            {
                "date": d,
                "rhr": round(v, 1) if (v := _pct(hr_q[d], 10.0)) is not None else None,
                "hrv": round(v) if (v := _median(rmssd[d])) is not None else None,
                "temp_c": round(v, 2) if (v := _median(temp_c[d])) is not None else None,
                "spo2": round(v) if (v := _median(spo2[d])) is not None else None,
                "resp": resp,
                "resp_var": resp_var,
                "met_avg": round(v, 2) if (v := _mean(met[d])) is not None else None,
                "sleep_min": tsm,
                "sleep_eff": eff,
            }
        )
    return out


def upsert(metrics_db: Path, days: list[dict]) -> None:
    con = sqlite3.connect(metrics_db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS daily_metrics("
        "date TEXT PRIMARY KEY, rhr REAL, hrv REAL, temp_c REAL, temp_f REAL, "
        "spo2 REAL, resp REAL, resp_var REAL, met_avg REAL, sleep_min REAL, sleep_eff REAL, "
        "vo2max REAL, updated_unix INTEGER)"
    )
    # tolerate an older daily_metrics that predates temp_c/resp/resp_var
    for col in ("temp_c", "resp", "resp_var"):
        try:
            con.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass
    now = int(dt.datetime.now().timestamp())
    for r in days:
        temp_f = round(r["temp_c"] * 9 / 5 + 32, 1) if r["temp_c"] is not None else None
        con.execute(
            "INSERT INTO daily_metrics(date,rhr,hrv,temp_c,temp_f,spo2,resp,resp_var,met_avg,sleep_min,sleep_eff,updated_unix) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
            "rhr=COALESCE(excluded.rhr,daily_metrics.rhr), hrv=COALESCE(excluded.hrv,daily_metrics.hrv), "
            "temp_c=COALESCE(excluded.temp_c,daily_metrics.temp_c), temp_f=COALESCE(excluded.temp_f,daily_metrics.temp_f), "
            "spo2=COALESCE(excluded.spo2,daily_metrics.spo2), resp=COALESCE(excluded.resp,daily_metrics.resp), "
            "resp_var=COALESCE(excluded.resp_var,daily_metrics.resp_var), "
            "met_avg=COALESCE(excluded.met_avg,daily_metrics.met_avg), "
            "sleep_min=COALESCE(excluded.sleep_min,daily_metrics.sleep_min), "
            "sleep_eff=COALESCE(excluded.sleep_eff,daily_metrics.sleep_eff), updated_unix=excluded.updated_unix",
            (r["date"], r["rhr"], r["hrv"], r["temp_c"], temp_f, r["spo2"], r["resp"], r["resp_var"],
             r["met_avg"], r["sleep_min"], r["sleep_eff"], now),
        )
    con.commit()
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build per-day metric history for baselines (GOAL 2).")
    ap.add_argument("--db", default="oura.db")
    ap.add_argument("--metrics-db", default="db/metrics.db")
    ap.add_argument("--dry-run", action="store_true", help="print rows, don't write")
    a = ap.parse_args()
    days = build(Path(a.db), Path(a.metrics_db))
    hdr = f"{'date':12} {'rhr':>5} {'hrv':>5} {'temp_c':>7} {'spo2':>5} {'resp':>5} {'met':>5} {'sleep':>6} {'eff':>4}"
    print(hdr)
    print("-" * len(hdr))
    for r in days:
        def f(x, w, p=""):
            return f"{x:>{w}{p}}" if x is not None else f"{'—':>{w}}"
        print(f"{r['date']:12} {f(r['rhr'],5,'.0f')} {f(r['hrv'],5,'.0f')} "
              f"{f(r['temp_c'],7,'.2f')} {f(r['spo2'],5,'.0f')} {f(r['resp'],5,'.1f')} "
              f"{f(r['met_avg'],5,'.2f')} {f(r['sleep_min'],6,'.0f')} {f(r['sleep_eff'],4,'.0f')}")
    if not a.dry_run:
        upsert(Path(a.metrics_db), days)
        print(f"\nwrote {len(days)} day(s) to {a.metrics_db} daily_metrics")


if __name__ == "__main__":
    main()
