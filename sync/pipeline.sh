#!/usr/bin/env bash
# On-dock pipeline: drain the ring's history into SQLite, then interpret.
# Invoked by sync/dock_watch.py on a dock event; also runnable standalone
# (ring on charger) to sync + interpret in one shot.
#
#   bash sync/pipeline.sh
#
# Exit non-zero if the sync fails, so the watcher won't re-arm flight mode over
# unsynced data.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OURA_BIN="open_oura/target/release/oura"
KEY="${OURA_KEY_FILE:-$(ls open_oura/oura-*.key 2>/dev/null | head -1)}"
DB="oura.db"

# Keep the ring clock aligned to LOCAL time+timezone on every sync. The firmware
# schedules time-of-day features — incl. the CVA overnight PPG sampler — against
# this offset, so a wrong/UTC TZ misaligns "night" with your actual sleep.
# Compute the host's local UTC offset in signed 30-minute units (PDT=-14, EST=-10).
_z="$(date +%z)"; _sign="${_z:0:1}"; _hh=$((10#${_z:1:2})); _mm=$((10#${_z:3:2}))
TZ_HALF=$(( (_hh*60 + _mm) / 30 )); [ "$_sign" = "-" ] && TZ_HALF=$(( -TZ_HALF ))

log() { printf '[pipeline %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# --- 1. Sync: drain history events into SQLite (incremental) ----------------
# VERIFIED on-hardware gotcha (2026-08-05): `oura sync` drains + commits the DB
# fully and prints "Done: N events received, N new rows", then HANGS on BLE
# disconnect (macOS btleplug) instead of exiting. So we can't just wait on it:
# we watch its output, and once "Done:" appears the data is already committed —
# we kill the hung client and call it success. A real drop (no "Done:" within
# MAX_SYNC_SECS) is a genuine failure → exit non-zero so we don't re-silence.
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-45}"   # no "Syncing" line by now => connect hang
MAX_SYNC_SECS="${MAX_SYNC_SECS:-600}"      # backstop for a wedged mid-drain (one
                                           # connection drains the whole buffer, so
                                           # keep this generous — a full night is
                                           # ~2.5 min; big backlogs need headroom).
                                           # The disconnect-hang is caught via "Done:",
                                           # not this cap, so raising it costs nothing.

# Two on-hardware failure modes (both VERIFIED 2026-08-05):
#   • disconnect-hang: prints "Done: …" (DB committed) then never exits.
#   • connect-hang:    never prints "Syncing" — btleplug wedged after repeated
#                      BLE ops; only a Mac Bluetooth off/on toggle clears it.
# Returns 0 = committed, 1 = drop (no Done), 2 = connect hang.
sync_with_disconnect_guard() {
  local out; out="$(mktemp)"
  "$OURA_BIN" --key-file "$KEY" --db "$DB" sync --sync-time --tz-half-hours "$TZ_HALF" >"$out" 2>&1 &
  local pid=$! waited=0 connected=0 rc=1
  while kill -0 "$pid" 2>/dev/null; do
    if grep -q "Done:" "$out"; then                       # drain + DB write done
      kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true
      rc=0; break
    fi
    grep -q "Syncing" "$out" && connected=1
    if [ "$connected" -eq 0 ] && [ "$waited" -ge "$CONNECT_TIMEOUT" ]; then
      kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true
      rc=2; break
    fi
    if [ "$waited" -ge "$MAX_SYNC_SECS" ]; then
      kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true
      grep -q "Done:" "$out" && rc=0 || rc=1; break
    fi
    sleep 1; waited=$((waited + 1))
  done
  grep -q "Done:" "$out" && rc=0                            # clean exit that printed Done
  grep -E "Syncing|Done:" "$out" | sed 's/^/    /'          # surface the sync line(s)
  rm -f "$out"
  return "$rc"
}

# RESUME-TO-COMPLETION. One `oura sync` already drains to bytes_left==0 in a
# single connection and persists the cursor per batch (open_oura drain_events),
# so a dropped link is safe: the next pass resumes from exactly where it stopped.
# We loop the guarded pass until it reports "Done:" (fully drained), retrying a
# mid-drain drop (rc1). A connect-hang (rc2) needs a Bluetooth toggle, so we stop
# and say so rather than churn the adapter. Progress is always durable between passes.
MAX_SYNC_ATTEMPTS="${MAX_SYNC_ATTEMPTS:-4}"

drain_to_completion() {
  local attempt=1
  while [ "$attempt" -le "$MAX_SYNC_ATTEMPTS" ]; do
    log "sync pass ${attempt}/${MAX_SYNC_ATTEMPTS}: draining $DB …"
    sync_with_disconnect_guard; local rc=$?
    case "$rc" in
      0) log "sync ok — fully drained (ring reported nothing left)."; return 0 ;;
      2) log "BLE CONNECT HANG — btleplug wedged. Toggle Mac Bluetooth off/on, then"
         log "run 'bash sync/pipeline.sh' again to RESUME (progress is saved)."; return 2 ;;
      *) log "pass ended before fully drained (link dropped mid-drain); progress"
         log "persisted — resuming from the saved cursor…" ;;
    esac
    attempt=$((attempt + 1))
  done
  log "not fully drained after ${MAX_SYNC_ATTEMPTS} passes; re-run to resume (progress saved)."
  return 1
}

drain_to_completion; rc=$?
[ "$rc" -ne 0 ] && exit 1

# --- 2. Interpret: run the local open-source interpreters over the freshly-synced
# data (the same hooks the app calls in app/src-tauri/src/ring.rs::run_interpreters).
PY="python3"
[ -x "$REPO_ROOT/.venv/bin/python" ] && PY="$REPO_ROOT/.venv/bin/python"
DER="db/metrics.db"

# Each is best-effort (non-fatal) and no-ops cleanly when it lacks data. Only the
# scripts present under interpret/ run, so optional model runners plug in here.
run_interp() {
  local script="$1"; shift
  [ -f "interpret/$script" ] || return 0
  log "interpret: $script …"
  "$PY" "interpret/$script" "$@" || log "  $script failed (non-fatal)"
}
run_interp sleep_stage.py  --db "$DB"                     # SleepECG + Cole-Kripke hypnogram
run_interp build_daily.py  --db "$DB" --metrics-db "$DER" # per-day metric history
run_interp run_ehr.py      --db "$DB" --metrics-db "$DER" # daytime HR from ehr_trace

log "done."
