#!/usr/bin/env python3
"""interpret/ring_clock.py — the ONE correct device-deciseconds → absolute-unix map.

Every event carries a `ring_timestamp` in device **deciseconds** (10 ds = 1 s).
To place an event on the wall clock we need the ring's epoch: the real unix time
at ds == 0.

**Why the old anchor was wrong.** The rest of the pipeline used to anchor the
newest event's `captured_unix` (the moment we *wrote* it to SQLite during a sync)
to its ds, then extrapolate linearly. `captured_unix` is the SYNC time, not the
RECORD time — a whole night can be drained hours after it happened — and, worse,
the ds clock only advances while the ring is recording. Across a recording gap
(e.g. the ring going dormant in flight mode) ds barely moves while real days pass,
so a single global "newest event" anchor mis-dates everything before the gap by
days. Observed on 2026-08-14: the global anchor's implied epoch was ~4.5 days off,
which stamped week-old nights onto the last few calendar days with bogus clocks.

**The fix.** The ring periodically emits a tag-66 marker whose payload includes an
absolute `unix_time` — its own RTC reading at that ds. Each marker gives an exact
anchor `epoch = unix_time - ring_timestamp/10`. At sync moments the ring RTC matches
the host clock to within seconds, and the implied epoch is stable to a few seconds
across days, so `ds/10 + epoch` is the ring's true wall clock. We use the median of
all tag-66 epochs (robust to the small per-marker jitter/drift).

Fallback: if a database has no tag-66 markers yet, we degrade to the legacy
newest-`captured_unix` anchor so nothing crashes — but that path is known-lossy and
should only bite a brand-new DB captured before any time marker was pulled.
"""
from __future__ import annotations

import json
import sqlite3
from statistics import median
from typing import Callable, Iterable, Optional

TAG_TIME_MARKER = 66  # 0x42 — carries an absolute `unix_time` (the ring's RTC)


def _epoch_from_markers(conn: sqlite3.Connection) -> Optional[float]:
    """Median ring epoch (unix seconds at ds==0) from tag-66 markers, or None."""
    epochs = []
    for ds, dj in conn.execute(
        "SELECT ring_timestamp, decoded_json FROM events "
        "WHERE tag=? AND decoded_json IS NOT NULL",
        (TAG_TIME_MARKER,),
    ):
        try:
            ut = json.loads(dj).get("unix_time")
        except (TypeError, json.JSONDecodeError):
            ut = None
        if ut:
            epochs.append(ut - ds / 10.0)
    return median(epochs) if epochs else None


def _legacy_epoch(conn: sqlite3.Connection) -> Optional[float]:
    """Old scheme: newest event's captured_unix anchored to its ds. Lossy; last resort."""
    row = conn.execute(
        "SELECT ring_timestamp, captured_unix FROM events "
        "ORDER BY ring_timestamp DESC LIMIT 1"
    ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return None
    max_ds, anchor_unix = row[0], row[1]
    return anchor_unix - max_ds / 10.0


def ring_epoch(conn: sqlite3.Connection) -> Optional[float]:
    """Unix seconds corresponding to ds==0. Prefers tag-66 markers; falls back to legacy."""
    return _epoch_from_markers(conn) or _legacy_epoch(conn)


def ds_to_unix_fn(conn: sqlite3.Connection) -> Callable[[float], float]:
    """A ds→absolute-unix-seconds function anchored to the ring's own clock.

    Raises if the DB has neither time markers nor any events (nothing to anchor to).
    """
    epoch = ring_epoch(conn)
    if epoch is None:
        raise ValueError("ring_clock: no tag-66 markers and no events to anchor to")

    def ds_to_unix(ds: float) -> float:
        return ds / 10.0 + epoch

    return ds_to_unix


def epoch_from_rows(rows: Iterable[sqlite3.Row]) -> Optional[float]:
    """Same as `ring_epoch` but from already-fetched rows (need tag + decoded_json).

    Lets callers that already SELECT the full event stream avoid a second query.
    Returns None if no usable tag-66 markers are present (caller should fall back).
    """
    epochs = []
    for r in rows:
        if r["tag"] != TAG_TIME_MARKER or r["decoded_json"] is None:
            continue
        try:
            ut = json.loads(r["decoded_json"]).get("unix_time")
        except (TypeError, json.JSONDecodeError):
            ut = None
        if ut:
            epochs.append(ut - r["ring_timestamp"] / 10.0)
    return median(epochs) if epochs else None
