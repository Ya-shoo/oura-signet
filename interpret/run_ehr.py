"""Daytime heart rate from the ring's `ehr_trace` stream (reverse-engineered).

The ring runs an "everyday heart rate" sampler whenever it's worn and AWAKE, and
buffers the result as `ehr_trace_event` (tag 0x73) records. open_oura pulls those
bytes on every sync but never decoded them, so the app only ever showed OVERNIGHT
heart rate (from the sleep IBI stream) — the entire waking day was invisible. This
runner decodes that buffer into the per-day daytime-HR summary the Home "Daytime
HR" card reads.

Wire format (reverse-engineered + validated — see below)
--------------------------------------------------------
Body = seq(1) + flag(1) + N × triplet(3).  Each triplet = (a, b, count):
  * (a, b) are the low/high edges of a heart-rate bin (bpm); the bin's HR is the
    midpoint (a≈b, within a few bpm, for a confident read).
  * count is how often that HR occurred in the record's window. Within a record
    the triplets are sorted by count DESCENDING (verified: 100% of multi-bin
    records) — i.e. each record is a little HR HISTOGRAM, not a time series. So we
    treat every (hr, count) as a weighted sample and never need intraday clock
    placement (which the ring makes unreliable by batch-dumping these records).

`flag` splits the stream into two interleaved channels. Only flag==1 is heart
rate; flag==0 is some other envelope (it moves the WRONG way vs activity). Proof:
  * grouped by concurrent MET, flag==1 HR rises monotonically with exertion
    (light 96 → moderate 100 → vigorous 107 bpm) while flag==0 falls (129→124→123)
  * flag==1's resting floor (p5≈52) matches the clean green-IBI HR floor exactly,
    so it's in true bpm (not scaled)
  * flag==1's distribution is bimodal (resting 50-79 + activity ~100-109 + an
    exercise tail) — the shape awake HR actually has.
Low-confidence bins (|a-b|>6 — the few dropout <45 and saturated >210 reads) are
dropped; they're ~1.5% of weight.

We report per LOCAL DAY (the global event anchor lands each record on the right
day even though sub-day timing is unreliable): resting = 5th pct, typical =
median, peak = 98th pct (robust; 98% of the tail is confident), plus the share of
waking readings in each HR zone. Shares are RELATIVE (fractions of the day's
readings), so they don't depend on the unknown sample→seconds rate.

Usage:  python interpret/run_ehr.py --db oura.db --metrics-db db/metrics.db
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path

# HR zones (bpm) — interpretable daytime bands, cool→warm. z1..z5 in the table.
ZONES: list[tuple[int, int, str]] = [
    (0, 60, "resting"),    # calm / sitting
    (60, 100, "light"),    # up and about
    (100, 130, "active"),  # brisk / light exercise
    (130, 160, "cardio"),  # sustained aerobic effort
    (160, 1000, "peak"),   # hard / near-max
]
MIN_WEIGHT = 400  # per-day floor (~a few min of readings) before we summarize a day


def _wpct(pairs: list[tuple[float, float]], p: float) -> float | None:
    """Weighted percentile p (0..100) over (value, weight) pairs."""
    if not pairs:
        return None
    s = sorted(pairs)
    total = sum(w for _, w in s)
    if total <= 0:
        return None
    target = (p / 100.0) * total
    acc = 0.0
    for v, w in s:
        acc += w
        if acc >= target:
            return v
    return s[-1][0]


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    tw = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw > 0 else None


def decode(db_path: Path) -> dict[str, list[tuple[float, int]]]:
    """Return per-local-date list of (hr_bpm, count) daytime-HR samples (flag==1)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=8000")
    con.row_factory = sqlite3.Row
    from ring_clock import ring_epoch  # ring's own tag-66 clock (see ring_clock.py)
    epoch = ring_epoch(con)  # unix seconds at ds == 0
    if epoch is None:
        return {}

    def local_date(ds: int) -> str:
        return dt.datetime.fromtimestamp(ds / 10.0 + epoch).strftime("%Y-%m-%d")

    rows = con.execute(
        "SELECT ring_timestamp, body FROM events WHERE name='ehr_trace_event' ORDER BY ring_timestamp"
    ).fetchall()
    con.close()

    by_day: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for r in rows:
        b = bytes(r["body"])
        if len(b) < 2 or b[1] != 1:  # flag==1 is the heart-rate channel
            continue
        payload = b[2:]
        if len(payload) % 3:
            continue
        d = local_date(r["ring_timestamp"])
        for i in range(0, len(payload), 3):
            a, bb, count = payload[i], payload[i + 1], payload[i + 2]
            if count == 0 or abs(a - bb) > 6:  # empty / low-confidence (wide) bin
                continue
            hr = (a + bb) / 2.0
            if hr < 45 or hr > 210:
                continue
            by_day[d].append((hr, count))
    return by_day


def summarize(by_day: dict[str, list[tuple[float, int]]]) -> list[dict]:
    out = []
    for d in sorted(by_day):
        pairs = [(hr, float(c)) for hr, c in by_day[d]]
        weight = sum(w for _, w in pairs)
        if weight < MIN_WEIGHT:
            continue
        zw = {label: 0.0 for _, _, label in ZONES}
        for hr, w in pairs:
            for lo, hi, label in ZONES:
                if lo <= hr < hi:
                    zw[label] += w
                    break
        out.append({
            "date": d,
            "resting": round(_wpct(pairs, 5.0)),
            "typical": round(_wpct(pairs, 50.0)),
            "avg": round(_wmean(pairs)),
            "active": round(_wpct(pairs, 90.0)),
            "peak": round(_wpct(pairs, 98.0)),
            "n_read": int(weight),
            "zones": zw,
        })
    return out


def upsert(metrics_db: Path, days: list[dict]) -> None:
    con = sqlite3.connect(metrics_db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS dayhr_daily("
        "date TEXT PRIMARY KEY, resting REAL, typical REAL, avg REAL, active REAL, peak REAL, "
        "n_read INTEGER, z_resting REAL, z_light REAL, z_active REAL, z_cardio REAL, z_peak REAL, "
        "updated_unix INTEGER)"
    )
    now = int(dt.datetime.now().timestamp())
    for r in days:
        z = r["zones"]
        con.execute(
            "INSERT INTO dayhr_daily(date,resting,typical,avg,active,peak,n_read,"
            "z_resting,z_light,z_active,z_cardio,z_peak,updated_unix) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
            "resting=excluded.resting, typical=excluded.typical, avg=excluded.avg, "
            "active=excluded.active, peak=excluded.peak, n_read=excluded.n_read, "
            "z_resting=excluded.z_resting, z_light=excluded.z_light, z_active=excluded.z_active, "
            "z_cardio=excluded.z_cardio, z_peak=excluded.z_peak, updated_unix=excluded.updated_unix",
            (r["date"], r["resting"], r["typical"], r["avg"], r["active"], r["peak"], r["n_read"],
             z["resting"], z["light"], z["active"], z["cardio"], z["peak"], now),
        )
    con.commit()
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Daytime HR from the ring's ehr_trace buffer.")
    ap.add_argument("--db", default="oura.db")
    ap.add_argument("--metrics-db", default="db/metrics.db")
    ap.add_argument("--dry-run", action="store_true", help="print rows, don't write")
    a = ap.parse_args()

    days = summarize(decode(Path(a.db)))
    if not days:
        print("Daytime HR — no ehr_trace coverage yet (the ring's everyday-HR sampler "
              "records while worn & awake; sync after wearing it through the day).")
        return

    print("Daytime HR — Oura everyday-HR (ehr_trace, flag=1) decoded locally\n")
    hdr = f"  {'date':10} {'rest':>4} {'typ':>4} {'act':>4} {'peak':>4}   zone share (rest/light/active/cardio/peak)"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in days:
        z = r["zones"]
        tw = sum(z.values()) or 1
        shares = "  ".join(f"{z[lbl] / tw * 100:2.0f}%" for _, _, lbl in ZONES)
        print(f"  {r['date']:10} {r['resting']:>4} {r['typical']:>4} {r['active']:>4} {r['peak']:>4}   {shares}")

    if not a.dry_run:
        upsert(Path(a.metrics_db), days)
        print(f"\n  wrote {len(days)} day(s) to {a.metrics_db} (table dayhr_daily)")


if __name__ == "__main__":
    main()
