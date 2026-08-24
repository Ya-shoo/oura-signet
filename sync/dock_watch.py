#!/usr/bin/env python3
"""Auto-detect-when-docked → auto-sync → re-silence — GOAL 1, piece 2.

The ring's resting state is *silent* (flight mode): it collects data with the
radio off while worn, so it never advertises. Placing it on the charger wakes
the radio (flight mode auto-clears) and it starts advertising the Oura service.
This watcher notices that rising edge and runs the pipeline:

    dock detected  ->  sync (drain history)  ->  interpret  ->  re-arm flight mode

    python sync/dock_watch.py                 # poll forever
    python sync/dock_watch.py --once          # one scan; handle a dock then exit
    python sync/dock_watch.py --dry-run       # report what it WOULD do; no BLE writes
    python sync/dock_watch.py --no-rearm      # sync only, leave the radio awake

State machine (edge-triggered): a sync fires only on a clean absent->present
transition, and never more than once per --cooldown window — so if flight mode
auto-clears while the ring is still docked, we don't spin in a resync loop.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchlib as bl  # noqa: E402
import flight_mode  # noqa: E402

DEFAULT_PIPELINE = bl.REPO_ROOT / "sync" / "pipeline.sh"


def run_pipeline(pipeline: Path, dry_run: bool) -> bool:
    """Run the on-dock pipeline (sync + interpret). Returns True on success."""
    if dry_run:
        bl.log(f"DRY RUN — would run pipeline: bash {pipeline}")
        return True
    if not pipeline.exists():
        bl.log(f"pipeline script missing: {pipeline}")
        return False
    bl.log(f"running pipeline: bash {pipeline}")
    result = subprocess.run(["bash", str(pipeline)], cwd=bl.REPO_ROOT)
    ok = result.returncode == 0
    bl.log(f"pipeline {'ok' if ok else f'FAILED (exit {result.returncode})'}")
    return ok


def handle_dock(sighting: bl.Sighting, *, pipeline: Path, rearm: bool,
                dry_run: bool, verify_timeout: float) -> None:
    bl.log(f"📥 DOCK detected: {sighting.name!r} rssi={sighting.rssi}")

    ok = run_pipeline(pipeline, dry_run)
    if not ok:
        bl.log("pipeline failed — leaving the radio AWAKE so you can retry "
               "(not re-arming flight mode over unsynced data).")
        return

    if not rearm:
        bl.log("--no-rearm: sync done, leaving the radio awake.")
        return

    outcome = flight_mode.arm(
        auth=True,
        verify=True,
        verify_timeout=verify_timeout,
        dry_run=dry_run,
    )
    if outcome == flight_mode.STILL_ADVERTISING:
        bl.log("note: still advertising after re-arm. If flight mode only takes "
               "effect once undocked, this is expected while on the charger.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch for the ring being docked and auto-sync.")
    parser.add_argument("--once", action="store_true",
                        help="scan once; if docked, handle it, then exit")
    parser.add_argument("--interval", type=float, default=20.0,
                        help="seconds between scans when polling (default 20)")
    parser.add_argument("--scan-timeout", type=float, default=12.0,
                        help="seconds per scan (default 12)")
    parser.add_argument("--cooldown", type=float, default=120.0,
                        help="min seconds between two dock handlings (default 120)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what it would do; never sync or write BLE")
    parser.add_argument("--no-rearm", action="store_true",
                        help="sync only; do not re-arm flight mode")
    parser.add_argument("--pipeline", default=str(DEFAULT_PIPELINE),
                        help="path to the on-dock pipeline script")
    parser.add_argument("--name", default=bl.DEFAULT_NAME,
                        help="device-name substring to match (default 'Oura')")
    parser.add_argument("--verify-timeout", type=float, default=15.0,
                        help="seconds to scan when proving silence after re-arm")
    args = parser.parse_args()

    problems = bl.preflight()
    if problems:
        for problem in problems:
            bl.log(f"preflight: {problem}")
        return 3
    pipeline = Path(args.pipeline)

    mode = "once" if args.once else f"poll every {args.interval:.0f}s"
    bl.log(f"dock-watch starting ({mode}"
           f"{', DRY RUN' if args.dry_run else ''}"
           f"{', no re-arm' if args.no_rearm else ''}). Ctrl-C to stop.")

    present_last = False
    last_handled = float("-inf")
    try:
        while True:
            sighting = bl.scan_for_ring(timeout=args.scan_timeout, name_contains=args.name)
            present = sighting is not None
            now = time.monotonic()

            if present and not present_last and (now - last_handled) >= args.cooldown:
                handle_dock(sighting, pipeline=pipeline, rearm=not args.no_rearm,
                            dry_run=args.dry_run, verify_timeout=args.verify_timeout)
                last_handled = time.monotonic()
                if args.once:
                    return 0
            elif not present:
                bl.log("…ring silent / not docked.")

            present_last = present
            if args.once:
                bl.log("--once: nothing docked, exiting.")
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        bl.log("stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
