#!/usr/bin/env python3
"""Introspect an open_oura SQLite DB — the *confirmed* raw data model.

open_oura stores everything in ONE generic `events` table (tag/name + raw body +
decoded_json) plus `device`, `readings`, and `sync_state`. Physiological data
lives in `events.decoded_json`, keyed by `name` (hrv_event, temp_event,
ibi_and_amplitude_event, spo2_r_pi_event, sleep_*, activity_information, …) —
NOT in typed tables. Every Phase-1 interpreter reads from here, so this prints
ground truth: table shapes, event-type distribution, and the decoded_json field
shape per event type.

A factory-fresh ring emits only debug/system events; **re-run this after the
first worn sync** to see the physiological event shapes.

Usage: python interpret/introspect.py [path/to/oura.db]   (default: oura.db)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _ts(unix: int) -> str:
    try:
        return datetime.fromtimestamp(unix, timezone.utc).isoformat()
    except Exception:
        return str(unix)


def main() -> None:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("oura.db")
    if not db.exists():
        sys.exit(f"no such db: {db}  (create it with: oura --key-file <key> --db {db} sync)")

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print(f"# open_oura DB — {db}\n")
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print("tables:", ", ".join(tables), "\n")

    if "device" in tables:
        for row in cur.execute("SELECT * FROM device"):
            print("## device")
            for k in row.keys():
                print(f"  {k}: {row[k]}")
            print()

    if "sync_state" in tables:
        for row in cur.execute("SELECT * FROM sync_state"):
            print("## sync_state")
            for k in row.keys():
                print(f"  {k}: {_ts(row[k]) if k.endswith('_unix') else row[k]}")
            print()

    if "events" in tables:
        total = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        print(f"## events  ({total} rows)")
        if total:
            tmin, tmax = cur.execute(
                "SELECT MIN(ring_timestamp), MAX(ring_timestamp) FROM events").fetchone()
            print(f"  ring_timestamp span: {tmin} … {tmax}")
            by_name = cur.execute(
                "SELECT name, tag, COUNT(*) c FROM events GROUP BY name, tag ORDER BY c DESC").fetchall()
            print("  event types (name / tag → count):")
            for r in by_name:
                print(f"    {r['name']:26} tag={r['tag']:<4} {r['c']}")
            print("\n  decoded_json field shape per event type "
                  "(field(count-seen); _status tally):")
            for r in by_name:
                rows = cur.execute(
                    "SELECT decoded_json FROM events WHERE name=? AND decoded_json IS NOT NULL LIMIT 500",
                    (r["name"],)).fetchall()
                keys: Counter = Counter()
                statuses: Counter = Counter()
                sample = None
                for (dj,) in rows:
                    try:
                        obj = json.loads(dj)
                    except Exception:
                        continue
                    if sample is None:
                        sample = obj
                    statuses[obj.get("_status", "?")] += 1
                    keys.update(k for k in obj if not k.startswith("_"))
                klist = ", ".join(f"{k}({keys[k]})" for k in sorted(keys))
                stat = ", ".join(f"{k}={v}" for k, v in statuses.items())
                print(f"    {r['name']}: status[{stat}] keys[{klist}]")
                if sample:
                    print(f"        e.g. {json.dumps(sample, default=str)[:200]}")
        print()

    if "readings" in tables:
        total_r = cur.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        print(f"## readings  ({total_r} rows)")
        for r in cur.execute(
                "SELECT kind, unit, COUNT(*) c, MIN(value) lo, AVG(value) av, MAX(value) hi "
                "FROM readings GROUP BY kind, unit ORDER BY c DESC"):
            print(f"  {r['kind']:16} [{r['unit'] or ''}] n={r['c']} "
                  f"min={r['lo']:.2f} avg={r['av']:.2f} max={r['hi']:.2f}")

    con.close()


if __name__ == "__main__":
    main()
