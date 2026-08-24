"""Shared foundation for the sync/ tooling (GOAL 1).

Two jobs:
  1. Locate the vetted Python bench (open_oura/tools/oura_protocol.py) + our auth
     key, and build/run it as a subprocess. The bench is the ONE audited place
     BLE opcodes — including danger ops like flight mode (0x26) — are ever sent.
  2. BLE presence detection via bleak. A ring that advertises the Oura GATT
     service has its radio awake (docked on the charger, or worn-not-silent);
     a silent / flight-mode ring does not advertise it. So the same scan is both
     the *dock detector* (dock_watch) and the *silence verifier* (flight_mode).

Everything is anchored to REPO_ROOT so these scripts work from any CWD.
"""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# The Oura BLE GATT service — the exact UUID oura_protocol.py filters on. A
# device advertising this == an Oura ring with an awake radio. This is the
# signal we key the whole dock/silence logic off of.
OURA_SERVICE = "98ed0001-a541-11e4-b6a0-0002a5d5c51b"

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_PY = REPO_ROOT / "open_oura" / ".venv" / "bin" / "python"
BENCH_SCRIPT = REPO_ROOT / "open_oura" / "tools" / "oura_protocol.py"
KEY_FILE = REPO_ROOT / "open_oura" / "oura-XXXXXXXXXXXXXX.key"
OURA_BIN = REPO_ROOT / "open_oura" / "target" / "release" / "oura"
CAPTURE_DIR = REPO_ROOT / "captures"

DEFAULT_NAME = "Oura"


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def ts() -> str:
    """Local timestamp for log lines."""
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def preflight() -> list[str]:
    """Return human-readable problems (missing bench/key). Empty list == OK."""
    problems: list[str] = []
    if not BENCH_PY.exists():
        problems.append(f"bench python missing: {BENCH_PY}")
    if not BENCH_SCRIPT.exists():
        problems.append(f"bench script missing: {BENCH_SCRIPT}")
    if not KEY_FILE.exists():
        problems.append(f"auth key missing: {KEY_FILE}")
    return problems


# --------------------------------------------------------------------------- #
# BLE presence detection (bleak) — dock detector + silence verifier
# --------------------------------------------------------------------------- #
@dataclass
class Sighting:
    address: str
    name: str
    rssi: int


async def _ascan(timeout: float, name_contains: Optional[str]) -> Optional[Sighting]:
    # Imported lazily so `--help` / `--dry-run` never need a Bluetooth adapter.
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    best: Optional[Sighting] = None
    for device, adv in devices.values():
        service_uuids = [u.lower() for u in (adv.service_uuids or [])]
        if OURA_SERVICE not in service_uuids:
            continue
        name = device.name or adv.local_name or ""
        # name_contains=None => match on service only (used for silence proofs,
        # where "any Oura-service advertiser is present" is the conservative test;
        # docked rings advertise named, so detection passes "Oura").
        if name_contains and name_contains.lower() not in name.lower():
            continue
        sighting = Sighting(device.address, name, adv.rssi)
        if best is None or sighting.rssi > best.rssi:
            best = sighting
    return best


def scan_for_ring(timeout: float = 12.0,
                  name_contains: Optional[str] = DEFAULT_NAME) -> Optional[Sighting]:
    """Blocking BLE scan. Returns the strongest matching ring, or None if the
    ring's radio is silent (flight mode) / out of range."""
    return asyncio.run(_ascan(timeout, name_contains))


# --------------------------------------------------------------------------- #
# Invoking the vetted bench (open_oura/tools/oura_protocol.py)
# --------------------------------------------------------------------------- #
def build_bench_cmd(commands: list[str],
                    *,
                    auth: bool = False,
                    include_danger: bool = False,
                    name_contains: str = DEFAULT_NAME,
                    capture: Optional[Path] = None,
                    scan_timeout: float = 20.0,
                    listen_seconds: float = 2.0) -> list[str]:
    """Build an argv for oura_protocol.py. `auth=True` prepends the `auth`
    pseudo-command so the ring is authenticated (with our key) before the real
    command runs — required for state/danger ops on an owned, onboarded ring."""
    cmd: list[str] = [str(BENCH_PY), str(BENCH_SCRIPT)]
    if auth:
        cmd.append("auth")
    cmd += list(commands)
    cmd += [
        "--auth-key-file", str(KEY_FILE),
        "--name-contains", name_contains,
        "--scan-timeout", str(scan_timeout),
        "--listen-seconds", str(listen_seconds),
    ]
    if include_danger:
        cmd.append("--include-danger")
    if capture:
        cmd += ["--capture", str(capture)]
    return cmd


def run_cmd(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess from REPO_ROOT, streaming its output to our stdout."""
    return subprocess.run(cmd, cwd=REPO_ROOT, check=check)


def shlex_join(cmd: list[str]) -> str:
    """Human-readable, copy-pasteable rendering of an argv (for --dry-run)."""
    import shlex
    return " ".join(shlex.quote(part) for part in cmd)
