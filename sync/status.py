#!/usr/bin/env python3
"""sync/status.py — sync state at a glance, so you can resume deliberately.

Reads the raw store (oura.db): the incremental cursor, when we last synced, and
what's stored. Sync is resume-safe (open_oura persists the cursor per batch), so
"resume" is just running the sync again — this shows you whether that's worth
doing and whether the ring is reachable right now. Read-only; safe anytime.

  python sync/status.py            # cursor + last sync + staleness + event counts
  python sync/status.py --scan     # also: is the ring docked / reachable now?
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchlib as bl  # noqa: E402

DEFAULT_DB = bl.REPO_ROOT / "oura.db"


def ago(unix: int) -> str:
    secs = int(datetime.now().timestamp()) - int(unix)
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86_400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86_400}d ago"


def main() -> int:
    ap = argparse.ArgumentParser(description="Show ring sync status.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--scan", action="store_true",
                    help="also scan to see if the ring is reachable right now")
    ap.add_argument("--scan-timeout", type=float, default=10.0)
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"  no database yet at {db} — nothing synced. Dock the ring and run 'bash sync/pipeline.sh'.")
        return 0

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT serial, next_cursor, last_sync_unix FROM sync_state"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    latest = conn.execute("SELECT MAX(captured_unix) FROM events").fetchone()[0]
    conn.close()

    print("  ── ring sync status ─────────────────────────────")
    if not rows:
        print("  no sync yet.")
    for r in rows:
        when = datetime.fromtimestamp(r["last_sync_unix"]).strftime("%Y-%m-%d %H:%M")
        print(f"  ring {r['serial']}")
        print(f"    last sync : {when}  ({ago(r['last_sync_unix'])})")
        print(f"    cursor    : {r['next_cursor']}   (resume point — sync continues from here)")
    print(f"    events    : {total:,} stored" + (f"  (latest {ago(latest)})" if latest else ""))

    if args.scan:
        print(f"  scanning {args.scan_timeout:.0f}s for the ring…")
        sighting = bl.scan_for_ring(timeout=args.scan_timeout)
        if sighting:
            print(f"    ✅ reachable now — {sighting.name!r} rssi={sighting.rssi}. "
                  "Run 'bash sync/pipeline.sh' to sync/resume.")
        else:
            print("    🌙 not advertising — silent (airplane mode) or undocked. "
                  "Dock it (undock→redock) to wake, then sync.")
    else:
        print("  → to sync / resume: dock the ring, then 'bash sync/pipeline.sh' "
              "(drains to completion, resuming from the cursor).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
