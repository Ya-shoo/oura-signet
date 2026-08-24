#!/usr/bin/env python3
"""Arm airplane mode ("Silence") on the owned Oura ring — GOAL 1, piece 1.

Sends flight-mode opcode 0x26 (bench command `enable_flight_mode`, frame
`26026027`) and then PROVES the radio went silent by scanning for the ring and
asserting it no longer advertises the Oura service.

    python sync/flight_mode.py arm            # confirm, arm, verify silence
    python sync/flight_mode.py arm --yes      # no prompt (for automation)
    python sync/flight_mode.py arm --dry-run  # print exact bench cmd, touch no BLE

⚠️  DANGER OP, UNVERIFIED UPSTREAM. The ring must be on the charger (radio only
    connects when docked). Flight mode is expected to *auto-clear when docked*,
    so docking is the recovery path — but we have not confirmed the ring's exact
    behavior, so the first live run is deliberately gated behind a confirmation
    and a silence proof. Only ever run against our own ring.

Exit codes: 0 armed + PROVEN SILENT · 2 armed but STILL ADVERTISING ·
            3 aborted by user/preflight · 4 send failed.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running as `python sync/flight_mode.py` or importing as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchlib as bl  # noqa: E402

# Outcome strings returned by arm().
DRY_RUN = "dry-run"
SILENT = "silent"
STILL_ADVERTISING = "still-advertising"
SEND_FAILED = "send-failed"


def arm(*,
        auth: bool = True,
        verify: bool = True,
        verify_timeout: float = 15.0,
        settle: float = 3.0,
        name: str = bl.DEFAULT_NAME,
        dry_run: bool = False) -> str:
    """Arm flight mode and (optionally) verify the radio went silent.

    Returns one of: DRY_RUN, SILENT, STILL_ADVERTISING, SEND_FAILED.
    Assumes the caller has already confirmed intent (the CLI handles the prompt).
    """
    capture = bl.CAPTURE_DIR / "flight_mode.jsonl"
    cmd = bl.build_bench_cmd(
        ["enable_flight_mode"],
        auth=auth,
        include_danger=True,
        name_contains=name,
        capture=capture,
    )

    if dry_run:
        bl.log("DRY RUN — would send flight-mode opcode 0x26 (enable_flight_mode):")
        print("    " + bl.shlex_join(cmd))
        if verify:
            bl.log(f"then settle {settle:.0f}s and scan {verify_timeout:.0f}s to prove the "
                   "ring stopped advertising the Oura service.")
        return DRY_RUN

    bl.log("arming flight mode: auth + enable_flight_mode (0x26) via the bench…")
    result = bl.run_cmd(cmd)
    if result.returncode != 0:
        bl.log(f"bench exited {result.returncode} — send may have failed. "
               "If BLE is flaky, toggle Mac Bluetooth off/on and retry.")
        return SEND_FAILED

    if not verify:
        bl.log("sent. (verification skipped)")
        return SILENT  # optimistic; caller opted out of proof

    bl.log(f"sent. Settling {settle:.0f}s for the radio to shut down…")
    time.sleep(settle)
    bl.log(f"verifying silence: scanning {verify_timeout:.0f}s for the Oura service…")
    # name_contains=None => match ANY Oura-service advertiser (conservative).
    sighting = bl.scan_for_ring(timeout=verify_timeout, name_contains=None)
    if sighting is None:
        bl.log("✅ PROVEN SILENT — ring is no longer advertising. Airplane mode is on.")
        return SILENT
    bl.log(f"⚠️  STILL ADVERTISING — found {sighting.name!r} rssi={sighting.rssi}. "
           "Flight mode may not have taken (or another Oura ring is nearby).")
    return STILL_ADVERTISING


def _confirm() -> bool:
    if not sys.stdin.isatty():
        bl.log("refusing to arm a danger op non-interactively without --yes.")
        return False
    print(
        "\n  About to send flight-mode opcode 0x26 to the ring (DANGER, unverified).\n"
        "  • The ring must be ON THE CHARGER.\n"
        "  • Docking is expected to auto-clear flight mode (the recovery path).\n"
        "  • We will then scan to PROVE the radio went silent.\n"
    )
    try:
        return input("  Type 'arm' to proceed: ").strip().lower() == "arm"
    except (EOFError, KeyboardInterrupt):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Arm airplane mode on the Oura ring.")
    parser.add_argument("action", choices=["arm"], help="what to do")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the exact bench command; touch no Bluetooth")
    parser.add_argument("--no-auth", action="store_true",
                        help="do not authenticate before arming (upstream default)")
    parser.add_argument("--no-verify", action="store_true",
                        help="do not scan to prove silence after arming")
    parser.add_argument("--verify-timeout", type=float, default=15.0,
                        help="seconds to scan when proving silence (default 15)")
    parser.add_argument("--settle", type=float, default=3.0,
                        help="seconds to wait after arming before verifying (default 3)")
    parser.add_argument("--name", default=bl.DEFAULT_NAME,
                        help="device-name substring to match (default 'Oura')")
    args = parser.parse_args()

    problems = bl.preflight()
    if problems:
        for problem in problems:
            bl.log(f"preflight: {problem}")
        return 3

    if not args.dry_run and not args.yes and not _confirm():
        bl.log("aborted.")
        return 3

    outcome = arm(
        auth=not args.no_auth,
        verify=not args.no_verify,
        verify_timeout=args.verify_timeout,
        settle=args.settle,
        name=args.name,
        dry_run=args.dry_run,
    )
    return {DRY_RUN: 0, SILENT: 0, STILL_ADVERTISING: 2, SEND_FAILED: 4}[outcome]


if __name__ == "__main__":
    raise SystemExit(main())
