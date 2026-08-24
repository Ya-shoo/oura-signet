# `sync/` — airplane mode + auto-sync-on-dock (GOAL 1)

The radio-silent core of oura-local. The ring's resting state is **silent**
(flight mode): it records HR / temp / motion / SpO2 with its Bluetooth radio off
while worn, so it never advertises. To get the data off, you **dock it** — the
radio wakes, we sync, then we re-arm silence. This module implements both halves.

## Files
| File | Role |
|------|------|
| `benchlib.py` | Shared foundation: paths to the vetted bench + our auth key, a bleak `scan_for_ring()` (the dock detector **and** the silence verifier), and the bench invoker. |
| `flight_mode.py` | Arm airplane mode (opcode **`0x26`**) with a confirm prompt + a **silence proof** (scans after arming to show the ring stopped advertising). |
| `dock_watch.py` | Poll loop: detect dock → run the pipeline → re-arm + verify silent. Edge-triggered with a cooldown so it can't spin in a resync loop. |
| `pipeline.sh` | The on-dock action: **sync (resume-to-completion)** → interpret. Loops the guarded drain until the ring reports fully drained, resuming from the saved cursor after any mid-drain drop. Forward-compatible with GOAL 2's `interpret/build_daily.py`. |
| `status.py` | Read-only: the sync cursor (resume point), last-sync time + staleness, event counts, and (`--scan`) whether the ring is reachable now. Safe anytime. |

## Usage
```bash
PY=open_oura/.venv/bin/python          # the bleak/pycryptodome bench venv

# Arm airplane mode (ring on charger). Confirms, sends 0x26, proves silence:
$PY sync/flight_mode.py arm
$PY sync/flight_mode.py arm --dry-run  # print the exact bench cmd, touch no BLE

# Watch for a dock and auto-sync (+ re-arm) forever, or once:
$PY sync/dock_watch.py                 # poll every 20s
$PY sync/dock_watch.py --once          # handle one dock, then exit (launchd-friendly)
$PY sync/dock_watch.py --dry-run --once  # report only; no sync, no BLE writes

# The pipeline on its own (ring on charger) — syncs to completion + interprets:
bash sync/pipeline.sh

# See sync state / decide whether to resume (read-only, works anytime):
$PY sync/status.py            # cursor, last-sync, staleness, counts
$PY sync/status.py --scan     # + is the ring reachable right now?
```

## Resuming a sync
Sync is **incremental and resume-safe by construction**: `open_oura`'s drain loops
to `bytes_left == 0` and **persists the cursor after every batch**, so a dropped
link never loses or re-fetches data — the next run continues from exactly where it
stopped. To resume, you don't need a special command: **`bash sync/pipeline.sh`**
loops the guarded drain until the ring reports fully drained (retrying a mid-drain
drop from the saved cursor; `MAX_SYNC_ATTEMPTS=4` by default). `dock_watch` uses the
same pipeline, so an interrupted on-dock sync self-completes. If it stops with a
**connect hang** (btleplug wedged), toggle Mac Bluetooth off/on and run it again —
progress is already saved. Check where things stand anytime with `status.py`.

## ✅ Danger op — verified on hardware (2026-08-05)
`0x26` (`enable_flight_mode`) is classed **danger** and *was* unverified upstream.
**It's now verified end-to-end on our ring** — the full arm→silence→wake cycle plus
the unattended loop. Results are in **Findings** below; the checklist that produced
them is kept for reproducibility (re-run after a firmware change). Always do it with
the **ring on the charger** (charger LED blinks blue = radio ready).

1. **Baseline.** With the ring docked, confirm it's advertising:
   `$PY sync/dock_watch.py --once --dry-run` → expect `DOCK detected`.
2. **Arm.** `$PY sync/flight_mode.py arm` → type `arm` at the prompt. Watch for
   the bench's `write command=enable_flight_mode … hex=26026027` and any
   notification response.
3. **Prove silence.** The tool then scans ~15 s and should report
   **`✅ PROVEN SILENT`**. Cross-check with an independent BLE scanner (e.g. the
   macOS *Bluetooth Explorer*, or `open_oura/target/release/oura scan`): the
   ring should be **gone**.
4. **Recovery / auto-clear.** Lift the ring off and re-dock it. Confirm the
   radio wakes on its own (flight mode auto-clears on the charger) and it
   advertises again — `oura scan` or `dock_watch --once --dry-run` sees it.
5. **Full loop.** `$PY sync/dock_watch.py` running, then dock the ring →
   expect: `DOCK detected` → `sync ok` → interpret → `arming flight mode` →
   `PROVEN SILENT`.

### Findings (2026-08-05)
- **Auth:** authenticate first (owned ring) → `auth_state=success`, then `0x26` is
  accepted. (Didn't test `--no-auth`; auth-first is the reliable path.)
- **Response:** the ring ACKs `0x26` with **`270100`** = tag `0x27`, len 1, payload
  `00` → **status OK**. Trust this ACK *and* the silence scan (belt + suspenders).
- **Timing:** goes **silent immediately**, even while still on the charger (two
  scans found nothing → `PROVEN SILENT`).
- **Auto-clear:** does **not** clear from sitting on the charger (still silent after
  2+ min). It **wakes on the dock *event*** (undock→redock) — so `dock_watch`'s
  edge-trigger is correct: arm-while-docked stays silent; a real redock re-syncs.
- **Payload `6027`** — still undecoded (duration? flags?); raw exchange saved to
  `captures/flight_mode.jsonl` for later.
- **btleplug reliability:** `oura sync` (Rust) wedges after repeated one-shot ops —
  a **disconnect-hang** (prints `Done:` then never exits) and a **connect-hang**
  (needs a Mac Bluetooth toggle). `pipeline.sh` guards both (rc0/rc1/rc2). The **app
  must** avoid this with one long-lived, self-healing BLE central — see
  `DESKTOP_APP_SPEC.md` §2.

## Safety
- The danger opcode is only ever sent by the **vetted bench**
  (`open_oura/tools/oura_protocol.py`), gated behind `--include-danger`. This
  module just wraps it with a confirm + a silence proof; `flight_mode.py`
  refuses to arm non-interactively without `--yes`.
- Local-only, no network. The auth key stays in `open_oura/oura-*.key` (0600,
  gitignored). Nothing here sends factory-reset / DFU.

## Later (GOAL 5)
`dock_watch` is the prototype of what becomes the Tauri app's Rust core (BLE
presence → sync → `0x26`). Run it under **launchd** for hands-off syncing:
`launchctl` a `KeepAlive` agent calling `dock_watch.py` (poll mode), or a
watch-triggered `--once`.
