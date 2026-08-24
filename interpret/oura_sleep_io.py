"""Shared I/O for the local sleep-staging interpreters (Tier 1 + Tier 2).

Loads one overnight window of raw ring signals from an open_oura SQLite DB and
turns them into the arrays the stagers need:

  - beats       : absolute beat instants (UTC epoch seconds) reconstructed from IBI
  - motion      : per-event activity magnitude (actigraphy proxy)
  - temp        : skin-temperature samples
  - bedtime     : (start_unix, end_unix) from bedtime_period, if present

open_oura stores everything in one generic `events` table; physiology lives in
`decoded_json`, keyed by tag. Device time is in "deciseconds" (ds, 0.1 s) on a
free-running ring clock, so we anchor it to wall-clock via the newest event's
`captured_unix` — exactly the scheme tools/run_sleep_model.py uses.

Also provides `make_synthetic_night()` so the whole pipeline can be exercised
end-to-end before a real overnight sync exists.

Relevant tags (hex):
  0x60 (96)  ibi_and_amplitude    overnight IR HR  -> ibi_ms[], amplitude[]
  0x80 (128) green_ibi_quality    daytime green HR -> ibi_ms[], quality[]
  0x47 (71)  motion_event         -> motion_seconds, low/high_intensity, avg_x/y/z
  0x46 (70)  temp_event           -> temps_c[]
  0x76 (118) bedtime_period       -> bedtime_start_ds, bedtime_end_ds
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TAG_IBI_IR = 0x60      # ibi_and_amplitude  (overnight, infrared)
TAG_IBI_GREEN = 0x80   # green_ibi_quality  (daytime, green LED)
TAG_MOTION = 0x47      # motion_event
TAG_TEMP = 0x46        # temp_event
TAG_BEDTIME = 0x76     # bedtime_period

VALID_IBI_MS = (300.0, 2000.0)   # 30–200 bpm; anything else can't advance the beat clock
EPOCH_S = 30.0


@dataclass
class Night:
    """One night of ring signals on an absolute (UTC epoch seconds) time axis."""
    beats_unix: np.ndarray     # (N,) absolute beat instants, seconds
    ibi_ms: np.ndarray         # (N,) interbeat interval feeding each beat
    amplitude: np.ndarray      # (N,) PPG pulse amplitude (0 if unknown)
    valid: np.ndarray          # (N,) bool: VALID_IBI_MS range
    motion_unix: np.ndarray    # (M,) motion event instants
    motion_mag: np.ndarray     # (M,) activity magnitude (seconds-of-motion proxy)
    temp_unix: np.ndarray      # (K,)
    temp_c: np.ndarray         # (K,)
    start_unix: float          # bedtime window start
    end_unix: float            # bedtime window end
    tz_offset_hours: float     # local offset used to derive clock time
    source: str = "db"
    truth_stage: np.ndarray | None = None   # synthetic only: per-epoch WAKE/REM/NREM
    notes: list[str] = field(default_factory=list)

    @property
    def start_time_local(self) -> _dt.time:
        """Clock time of lights-out (circadian feature for SleepECG)."""
        local = _dt.datetime.fromtimestamp(
            self.start_unix + self.tz_offset_hours * 3600, _dt.timezone.utc)
        return local.time()

    @property
    def duration_h(self) -> float:
        return (self.end_unix - self.start_unix) / 3600.0

    @property
    def n_epochs(self) -> int:
        return int(np.ceil((self.end_unix - self.start_unix) / EPOCH_S))

    def heartbeat_times_rel(self) -> np.ndarray:
        """Valid beats as strictly-increasing seconds from `start_unix`
        (the exact input SleepECG's SleepRecord.heartbeat_times wants)."""
        t = self.beats_unix[self.valid] - self.start_unix
        t = t[(t >= 0) & (t <= self.end_unix - self.start_unix)]
        t = np.unique(np.sort(t))          # strictly increasing, de-duped
        return t

    def coverage(self) -> float:
        """Fraction of 30-s epochs that contain >=1 valid beat."""
        if self.n_epochs == 0:
            return 0.0
        rel = self.beats_unix[self.valid] - self.start_unix
        idx = np.floor(rel[(rel >= 0)] / EPOCH_S).astype(int)
        idx = idx[idx < self.n_epochs]
        return len(np.unique(idx)) / self.n_epochs


# ---------------------------------------------------------------------------
# DB loading
# ---------------------------------------------------------------------------
def _anchor(rows) -> tuple[int, int]:
    """(max_ds, anchor_unix): device deciseconds are anchored to the newest
    event's captured_unix, matching tools/run_sleep_model.py."""
    max_ds, anchor_unix = max(((r["ring_timestamp"], r["captured_unix"]) for r in rows),
                              key=lambda x: x[0])
    return max_ds, anchor_unix


def load_night(db_path: str | Path,
               start_unix: float | None = None,
               end_unix: float | None = None,
               tz_offset_hours: float | None = None) -> Night:
    """Load the most recent bedtime window (or an explicit [start,end]) from an
    open_oura DB. Raises with a clear message if there is no overnight data."""
    db_path = Path(db_path)
    if tz_offset_hours is None:
        # system local offset — good enough for the circadian clock feature
        tz_offset_hours = _dt.datetime.now().astimezone().utcoffset().total_seconds() / 3600

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ring_timestamp, tag, decoded_json, captured_unix FROM events "
        "WHERE decoded_json IS NOT NULL ORDER BY ring_timestamp").fetchall()
    if not rows:
        raise SystemExit(f"{db_path}: no decoded events — sync the ring first.")

    from ring_clock import epoch_from_rows  # ring's own tag-66 clock (see ring_clock.py)
    epoch = epoch_from_rows(rows)
    if epoch is None:  # legacy fallback: no time markers in this DB yet
        max_ds, anchor_unix = _anchor(rows)
        epoch = anchor_unix - max_ds / 10.0

    def ds_to_unix(ds: int) -> float:
        return ds / 10.0 + epoch

    # bedtime window
    if start_unix is None or end_unix is None:
        bt = con.execute(
            "SELECT decoded_json FROM events WHERE tag=? ORDER BY ring_timestamp DESC LIMIT 1",
            (TAG_BEDTIME,)).fetchone()
        if bt is not None:
            v = json.loads(bt["decoded_json"])
            start_unix = ds_to_unix(v["bedtime_start_ds"])
            end_unix = ds_to_unix(v["bedtime_end_ds"])

    beats_u, ibis, amps, valids = [], [], [], []
    mot_u, mot_m, tmp_u, tmp_c = [], [], [], []
    for r in rows:
        tag = r["tag"]
        v = json.loads(r["decoded_json"])
        if tag in (TAG_IBI_IR, TAG_IBI_GREEN) and v.get("ibi_ms"):
            t0 = ds_to_unix(r["ring_timestamp"])
            ibi = v["ibi_ms"]
            amp = v.get("amplitude", [0] * len(ibi))
            acc = 0.0
            for i, x in enumerate(ibi):
                if x <= 0:
                    continue
                acc += x / 1000.0
                beats_u.append(t0 + acc)
                ibis.append(float(x))
                amps.append(float(amp[i]) if i < len(amp) else 0.0)
                valids.append(VALID_IBI_MS[0] <= x <= VALID_IBI_MS[1])
        elif tag == TAG_MOTION:
            # actigraphy proxy: seconds-of-motion, fall back to intensity counts
            mag = v.get("motion_seconds")
            if mag is None:
                mag = (v.get("low_intensity", 0) or 0) + 3 * (v.get("high_intensity", 0) or 0)
            mot_u.append(ds_to_unix(r["ring_timestamp"]))
            mot_m.append(float(mag))
        elif tag == TAG_TEMP and v.get("temps_c"):
            tmp_u.append(ds_to_unix(r["ring_timestamp"]))
            tmp_c.append(float(np.median(v["temps_c"])))
    con.close()

    beats_u = np.asarray(beats_u, float)
    if start_unix is None or end_unix is None:      # no bedtime_period → use beat span
        if beats_u.size == 0:
            raise SystemExit(
                f"{db_path}: no IBI beats and no bedtime_period.\n"
                "This DB has no overnight data yet — wear the ring overnight, `oura sync`, "
                "then re-run. (Use --synthetic to exercise the pipeline meanwhile.)")
        start_unix, end_unix = float(beats_u.min()), float(beats_u.max())

    night = Night(
        beats_unix=beats_u,
        ibi_ms=np.asarray(ibis, float),
        amplitude=np.asarray(amps, float),
        valid=np.asarray(valids, bool),
        motion_unix=np.asarray(mot_u, float),
        motion_mag=np.asarray(mot_m, float),
        temp_unix=np.asarray(tmp_u, float),
        temp_c=np.asarray(tmp_c, float),
        start_unix=float(start_unix),
        end_unix=float(end_unix),
        tz_offset_hours=float(tz_offset_hours),
        source=str(db_path),
    )
    if night.valid.sum() == 0:
        raise SystemExit(f"{db_path}: found events but no valid IBI in the window "
                         "(this looks like a daytime-only sync).")
    return night


# ---------------------------------------------------------------------------
# Synthetic night — realistic enough to drive the whole pipeline
# ---------------------------------------------------------------------------
# latent stages used to *generate* the night (collapsed to WAKE/REM/NREM as truth).
# lf_amp = amplitude of a ~0.1 Hz RR oscillation (sympathetic / LF power): high in
# REM+WAKE, low in deep — this is what lets an HRV model separate REM from NREM.
_SYN = {  # stage: (mean_hr, hr_drift_sd, beat_noise_sd, motion_rate)
    "WAKE":  (66, 3.0, 0.045, 0.8),
    "REM":   (61, 2.5, 0.055, 0.15),   # REM: elevated + irregular (high beat noise)
    "LIGHT": (58, 1.5, 0.030, 0.10),
    "DEEP":  (53, 1.0, 0.015, 0.03),   # deep: low + metronomic (low beat noise)
}
_COLLAPSE = {"WAKE": "WAKE", "REM": "REM", "LIGHT": "NREM", "DEEP": "NREM"}


def _synthetic_hypnogram(n_epochs: int, rng: np.random.Generator) -> list[str]:
    """A plausible night: sleep-onset latency, ~90-min cycles, deep front-loaded,
    REM back-loaded, a couple of brief awakenings."""
    stages: list[str] = []
    onset = int(10 * 60 / EPOCH_S)                    # ~10 min to fall asleep
    stages += ["WAKE"] * onset
    cycle_len = int(90 * 60 / EPOCH_S)
    e = onset
    cyc = 0
    while len(stages) < n_epochs:
        deep_frac = max(0.05, 0.45 - 0.10 * cyc)      # deep shrinks each cycle
        rem_frac = min(0.35, 0.08 + 0.07 * cyc)       # REM grows each cycle
        nd = int(cycle_len * deep_frac)
        nr = int(cycle_len * rem_frac)
        nl = max(0, cycle_len - nd - nr)
        block = (["LIGHT"] * (nl // 2) + ["DEEP"] * nd
                 + ["LIGHT"] * (nl - nl // 2) + ["REM"] * nr)
        if cyc > 0 and rng.random() < 0.5:            # occasional brief awakening
            block[0:2] = ["WAKE", "WAKE"]
        stages += block
        cyc += 1
    return stages[:n_epochs]


def make_synthetic_night(hours: float = 8.0,
                         start_hour_local: int = 23,
                         start_min_local: int = 30,
                         tz_offset_hours: float = 0.0,
                         seed: int = 7) -> Night:
    """Generate beats + motion + temp + bedtime for a plausible night, plus the
    ground-truth per-epoch stage (WAKE/REM/NREM) used only for a plumbing self-check."""
    rng = np.random.default_rng(seed)
    # anchor synthetic start to a fixed wall-clock date, treated as UTC so the
    # displayed clock == start_hour_local regardless of the host machine's zone
    start_unix = _dt.datetime(2026, 1, 15, start_hour_local, start_min_local,
                              tzinfo=_dt.timezone.utc).timestamp()
    n_epochs = int(hours * 3600 / EPOCH_S)
    latent = _synthetic_hypnogram(n_epochs, rng)

    beats_u, ibis, amps, valids = [], [], [], []
    mot_u, mot_m = [], []
    for ei, st in enumerate(latent):
        hr0, drift_sd, noise_sd, mrate = _SYN[st]
        ep_start = start_unix + ei * EPOCH_S
        hr = hr0 + rng.normal(0, drift_sd)
        t = ep_start
        while t < ep_start + EPOCH_S:
            rr = 60.0 / max(hr, 40) * (1 + rng.normal(0, noise_sd))
            rr = float(np.clip(rr, 0.3, 2.0))
            t += rr
            beats_u.append(t)
            ibis.append(rr * 1000.0)
            amps.append(1.0 + rng.normal(0, 0.05))
            valids.append(True)
        # one motion sample per epoch, magnitude ~ motion rate of the stage
        mot_u.append(ep_start + EPOCH_S / 2)
        mot_m.append(float(max(0, rng.poisson(mrate * 6) + rng.normal(0, 0.3))))

    end_unix = start_unix + n_epochs * EPOCH_S
    temp_u = np.arange(start_unix, end_unix, 300.0)      # a temp sample every 5 min
    temp_c = 34.6 - 0.4 * np.sin(np.linspace(0, np.pi, len(temp_u))) + rng.normal(0, 0.05, len(temp_u))

    return Night(
        beats_unix=np.asarray(beats_u, float),
        ibi_ms=np.asarray(ibis, float),
        amplitude=np.asarray(amps, float),
        valid=np.asarray(valids, bool),
        motion_unix=np.asarray(mot_u, float),
        motion_mag=np.asarray(mot_m, float),
        temp_unix=temp_u,
        temp_c=temp_c,
        start_unix=start_unix,
        end_unix=end_unix,
        tz_offset_hours=tz_offset_hours,
        source="synthetic",
        truth_stage=np.array([_COLLAPSE[s] for s in latent]),
        notes=["SYNTHETIC night — for pipeline validation only, not real data."],
    )
