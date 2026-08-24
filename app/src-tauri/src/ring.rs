//! Ring control (interim): drive BLE ops by shelling out to the proven
//! `open_oura` CLI. The history drain is Rust-only in `open_oura` anyway, and
//! the CLI is what Phase 0 validated on hardware.
//!
//! Guarded like `sync/pipeline.sh`: a printed success marker (`Battery` /
//! `Done:`) counts as success even if the btleplug client then hangs on
//! disconnect — we've got what we need, so we reap the process and move on.
//! A watchdog kills any op that exceeds its timeout (connect-hang).
//!
//! Phase B replaces this with one long-lived, self-healing `CBCentralManager`
//! embedded in-process (see DESKTOP_APP_SPEC §2).

use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{AppHandle, Emitter};

use crate::paths;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct SyncProgress {
    phase: String, // connecting | battery | syncing | done | error
    message: String,
    battery: Option<u8>,
    running: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncOutcome {
    battery: Option<u8>,
    received: u32,
    new_rows: u32,
}

fn emit(app: &AppHandle, phase: &str, message: &str, battery: Option<u8>, running: bool) {
    let _ = app.emit(
        "sync-progress",
        SyncProgress {
            phase: phase.into(),
            message: message.into(),
            battery,
            running,
        },
    );
}

/// Run one `oura` subcommand, streaming its stdout as progress. Returns the
/// captured stdout if `marker` was seen (success), else an error. A background
/// watchdog kills the child after `timeout_s` so a hung connect can't stall us.
fn run_oura(
    app: &AppHandle,
    bin: &Path,
    args: &[String],
    marker: &str,
    phase: &str,
    battery: Option<u8>,
    timeout_s: u64,
) -> Result<String, String> {
    let mut child = Command::new(bin)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit()) // ring/tracing logs go to the dev console
        .spawn()
        .map_err(|e| format!("launch {}: {e}", bin.display()))?;

    let stdout = child.stdout.take().ok_or("no stdout from oura")?;
    let child = Arc::new(Mutex::new(child));
    let finished = Arc::new(AtomicBool::new(false));

    // watchdog: kill if it runs past the timeout
    {
        let child = child.clone();
        let finished = finished.clone();
        std::thread::spawn(move || {
            let mut waited = 0u64;
            while waited < timeout_s * 1000 {
                if finished.load(Ordering::Relaxed) {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
                waited += 200;
            }
            if let Ok(mut c) = child.lock() {
                let _ = c.kill();
            }
        });
    }

    let mut captured = String::new();
    let mut saw_marker = false;
    for line in BufReader::new(stdout).lines() {
        let Ok(line) = line else { break };
        captured.push_str(&line);
        captured.push('\n');
        emit(app, phase, &line, battery, true);
        if line.contains(marker) {
            saw_marker = true;
            // Got what we need; don't wait on a possibly-hanging disconnect.
            finished.store(true, Ordering::Relaxed);
            if let Ok(mut c) = child.lock() {
                let _ = c.kill();
            }
            break;
        }
    }
    finished.store(true, Ordering::Relaxed);
    if let Ok(mut c) = child.lock() {
        let _ = c.wait();
    }

    if saw_marker {
        Ok(captured)
    } else {
        Err(format!(
            "the ring didn't respond ({}). Make sure it's on the charger; if it persists, toggle macOS Bluetooth off/on.",
            phase
        ))
    }
}

fn parse_battery(s: &str) -> Option<u8> {
    for line in s.lines() {
        if line.contains("Battery") {
            let digits: String = line.chars().filter(|c| c.is_ascii_digit()).collect();
            if let Ok(n) = digits.parse::<u32>() {
                if n <= 100 {
                    return Some(n as u8);
                }
            }
        }
    }
    None
}

fn parse_sync(s: &str) -> (u32, u32) {
    for line in s.lines() {
        if line.starts_with("Done:") {
            let nums: Vec<u32> = line
                .split(|c: char| !c.is_ascii_digit())
                .filter(|t| !t.is_empty())
                .filter_map(|t| t.parse().ok())
                .collect();
            return (nums.first().copied().unwrap_or(0), nums.get(1).copied().unwrap_or(0));
        }
    }
    (0, 0)
}

fn sync_now_blocking(app: AppHandle) -> Result<SyncOutcome, String> {
    let p = paths::resolve();
    let bin = paths::oura_bin().ok_or("couldn't locate the oura binary")?;
    if !bin.exists() {
        return Err(format!("oura binary not found at {}", bin.display()));
    }
    let key = paths::key_file().ok_or("couldn't find the ring's auth-key file")?;
    let key_s = key.to_string_lossy().to_string();
    let db_s = p.raw_db.to_string_lossy().to_string();

    // 1) info → battery (quick; the first op is the reliable one)
    emit(&app, "connecting", "Connecting to the ring…", None, true);
    let battery = run_oura(
        &app,
        &bin,
        &["--key-file".into(), key_s.clone(), "info".into()],
        "Battery",
        "connecting",
        None,
        75,
    )
    .ok()
    .and_then(|s| parse_battery(&s));
    if let Some(b) = battery {
        emit(&app, "battery", &format!("Battery {b}%"), Some(b), true);
    }

    // 2) sync → drain history
    emit(&app, "syncing", "Syncing history…", battery, true);
    let out = run_oura(
        &app,
        &bin,
        &[
            "--key-file".into(),
            key_s,
            "--db".into(),
            db_s,
            "sync".into(),
            // Keep the ring clock+timezone aligned to local time on every sync — the
            // firmware schedules time-of-day features (incl. the CVA overnight PPG
            // sampler) by this offset. The CLI auto-detects local TZ from the host.
            "--sync-time".into(),
        ],
        "Done:",
        "syncing",
        battery,
        420,
    )?;
    let (received, new_rows) = parse_sync(&out);

    stage_sleep(&app, &p, battery);
    run_interpreters(&app, &p);

    emit(
        &app,
        "done",
        &format!(
            "Synced {new_rows} new {} · {received} received",
            if new_rows == 1 { "row" } else { "rows" }
        ),
        battery,
        false,
    );
    Ok(SyncOutcome {
        battery,
        received,
        new_rows,
    })
}

/// Re-stage sleep from the current DB via the repo-root SleepECG venv
/// (best-effort — silently skipped if the venv/script is absent).
fn stage_sleep(app: &AppHandle, p: &paths::DataPaths, battery: Option<u8>) {
    let Some(repo) = paths::repo_root() else {
        return;
    };
    let venv = repo.join(".venv").join("bin").join("python");
    let script = repo.join("interpret").join("sleep_stage.py");
    if venv.exists() && script.exists() {
        emit(app, "staging", "Staging last night's sleep…", battery, true);
        let _ = run_capture(
            app,
            &venv,
            &[
                script.to_string_lossy().to_string(),
                "--db".into(),
                p.raw_db.to_string_lossy().to_string(),
                "--metrics-db".into(),
                p.derived_db.to_string_lossy().to_string(),
                "--age".into(),
                "26".into(),
                "--sex".into(),
                "M".into(),
            ],
            "staging",
            180,
        );
    }
}

/// GOAL 2/3 — after staging, run the local interpreters on the freshly-synced
/// data: sleep staging, the per-day metric history + personal
/// baselines (centered-Gaussian method), steps, workout detection, and
/// cardiovascular age. Order matters (sleep feeds the daily history; history
/// feeds baselines). Each runs only if its script is present, so new
/// interpreters start running automatically once they land. All are cheap and
/// safe to run every sync; ones lacking data print a status and exit 0.
fn run_interpreters(app: &AppHandle, p: &paths::DataPaths) {
    let Some(repo) = paths::repo_root() else {
        return;
    };
    let venv = repo.join(".venv").join("bin").join("python");
    if !venv.exists() {
        return;
    }
    let raw = p.raw_db.to_string_lossy().to_string();
    let der = p.derived_db.to_string_lossy().to_string();
    let jobs: [(&str, &[&str], &str); 9] = [
        ("run_sleepnet.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Staging sleep…"),
        // Fills a night the ring sensed but never finalized into a bedtime_period
        // (e.g. after a restless night) — detects the window + stages it.
        // No-ops when the ring already staged a real night, so it never clobbers one.
        ("stage_last_night.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Staging last night…"),
        ("build_daily.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Building your daily history…"),
        ("baselines.py", &["--metrics-db", der.as_str()], "Updating your personal baselines…"),
        ("run_step_counter.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Counting your steps…"),
        ("run_activity.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Detecting workouts…"),
        ("run_cva.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Cardiovascular age…"),
        // Runs last: needs activity_sessions (run_activity) + baselines + re-decodes
        // gait itself → HR/motion/gait-driven active energy into energy_daily/_hourly.
        ("run_energy_expenditure.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Energy burn…"),
        // Decodes the ring's everyday-HR buffer (ehr_trace 0x73) → daytime HR.
        ("run_ehr.py", &["--db", raw.as_str(), "--metrics-db", der.as_str()], "Daytime heart rate…"),
    ];
    for (script, args, msg) in jobs {
        let s = repo.join("interpret").join(script);
        if !s.exists() {
            continue;
        }
        emit(app, "interpret", msg, None, true);
        let mut full: Vec<String> = vec![s.to_string_lossy().to_string()];
        full.extend(args.iter().map(|a| a.to_string()));
        let _ = run_capture(app, &venv, &full, "interpret", 180);
    }
}

fn analyze_sleep_blocking(app: AppHandle) -> Result<SyncOutcome, String> {
    let p = paths::resolve();
    let bin = paths::oura_bin().ok_or("couldn't locate the oura binary")?;
    if !bin.exists() {
        return Err(format!("oura binary not found at {}", bin.display()));
    }
    let key = paths::key_file().ok_or("couldn't find the ring's auth-key file")?;
    let key_s = key.to_string_lossy().to_string();
    let db_s = p.raw_db.to_string_lossy().to_string();

    // 1) ask the ring to run its own sleep analysis → it emits sleep_phase (4-stage
    //    hypnogram) + sleep_summary events (bedtime, durations, lowest HR).
    emit(&app, "analyzing", "Asking the ring to analyze last night…", None, true);
    let _ = run_oura(
        &app,
        &bin,
        &["--key-file".into(), key_s.clone(), "sleep-analyze".into()],
        "Sleep analysis triggered",
        "analyzing",
        None,
        90,
    );
    // give the ring time to postprocess before we fetch (the CLI advises this)
    std::thread::sleep(std::time::Duration::from_secs(8));

    // 2) sync to drain the freshly-emitted sleep events
    emit(&app, "syncing", "Fetching the ring's sleep data…", None, true);
    let out = run_oura(
        &app,
        &bin,
        &["--key-file".into(), key_s, "--db".into(), db_s, "sync".into()],
        "Done:",
        "syncing",
        None,
        300,
    )?;
    let (received, new_rows) = parse_sync(&out);

    // 3) re-stage (our SleepECG fallback / cross-check)
    stage_sleep(&app, &p, None);
    run_interpreters(&app, &p);

    emit(
        &app,
        "done",
        &format!("Fetched the ring's sleep · {new_rows} new rows"),
        None,
        false,
    );
    Ok(SyncOutcome {
        battery: None,
        received,
        new_rows,
    })
}

/// Trigger the ring's native sleep analysis, then sync + re-stage.
#[tauri::command]
pub async fn analyze_sleep(app: AppHandle) -> Result<SyncOutcome, String> {
    tauri::async_runtime::spawn_blocking(move || analyze_sleep_blocking(app))
        .await
        .map_err(|e| e.to_string())?
}

/// Read battery + drain history, streaming `sync-progress` events. Interim:
/// shells out to the CLI (see module docs).
#[tauri::command]
pub async fn sync_now(app: AppHandle) -> Result<SyncOutcome, String> {
    let res = tauri::async_runtime::spawn_blocking(move || sync_now_blocking(app))
        .await
        .map_err(|e| e.to_string())?;
    if let Err(ref e) = res {
        // best-effort error toast is emitted by the caller via the returned Err
        let _ = e;
    }
    res
}

// ---- airplane mode (Dock & Silence) --------------------------------------

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArmOutcome {
    silent: bool,
    message: String,
}

/// Run a program to completion, streaming BOTH stdout and stderr as progress and
/// capturing them. A watchdog kills it past `timeout_s`. Used for the Python
/// bench (bleak), which exits cleanly (unlike the btleplug sync).
fn run_capture(
    app: &AppHandle,
    program: &Path,
    args: &[String],
    phase: &str,
    timeout_s: u64,
) -> (String, bool) {
    let mut child = match Command::new(program)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => return (format!("launch {}: {e}", program.display()), false),
    };
    let out_pipe = child.stdout.take();
    let err_pipe = child.stderr.take();
    let child = Arc::new(Mutex::new(child));
    let finished = Arc::new(AtomicBool::new(false));
    let combined = Arc::new(Mutex::new(String::new()));

    let err_thread = err_pipe.map(|err| {
        let combined = combined.clone();
        let app = app.clone();
        let phase = phase.to_string();
        std::thread::spawn(move || {
            for line in BufReader::new(err).lines().map_while(Result::ok) {
                emit(&app, &phase, &line, None, true);
                if let Ok(mut c) = combined.lock() {
                    c.push_str(&line);
                    c.push('\n');
                }
            }
        })
    });

    {
        let child = child.clone();
        let finished = finished.clone();
        std::thread::spawn(move || {
            let mut waited = 0u64;
            while waited < timeout_s * 1000 {
                if finished.load(Ordering::Relaxed) {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
                waited += 200;
            }
            if let Ok(mut c) = child.lock() {
                let _ = c.kill();
            }
        });
    }

    if let Some(out) = out_pipe {
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            emit(app, phase, &line, None, true);
            if let Ok(mut c) = combined.lock() {
                c.push_str(&line);
                c.push('\n');
            }
        }
    }
    if let Some(t) = err_thread {
        let _ = t.join();
    }
    finished.store(true, Ordering::Relaxed);
    let ok = child
        .lock()
        .ok()
        .and_then(|mut c| c.wait().ok())
        .map(|s| s.success())
        .unwrap_or(false);
    let captured = combined.lock().map(|c| c.clone()).unwrap_or_default();
    (captured, ok)
}

fn arm_airplane_blocking(app: AppHandle) -> Result<ArmOutcome, String> {
    let repo = paths::repo_root().ok_or("couldn't locate the repo (open_oura bench)")?;
    let venv = repo
        .join("open_oura")
        .join(".venv")
        .join("bin")
        .join("python");
    let flight = repo.join("sync").join("flight_mode.py");
    if !venv.exists() {
        return Err(format!("bench python missing at {}", venv.display()));
    }
    if !flight.exists() {
        return Err(format!("flight_mode.py missing at {}", flight.display()));
    }

    // `flight_mode.py arm --yes`: authenticates, sends 0x26, then scans to PROVE
    // the radio went silent. Data is safe on-ring (~7-day buffer), so we don't
    // block arming on a fresh sync.
    emit(&app, "arming", "Arming airplane mode (0x26)…", None, true);
    let (out, _ok) = run_capture(
        &app,
        &venv,
        &[
            flight.to_string_lossy().to_string(),
            "arm".into(),
            "--yes".into(),
        ],
        "arming",
        150,
    );

    if out.contains("PROVEN SILENT") {
        let m = "Confirmed silent — safe to undock and wear.".to_string();
        emit(&app, "silent", &m, None, false);
        Ok(ArmOutcome {
            silent: true,
            message: m,
        })
    } else if out.contains("STILL ADVERTISING") {
        let m = "Sent 0x26, but the ring still shows a radio — re-dock and retry.".to_string();
        emit(&app, "error", &m, None, false);
        Err(m)
    } else {
        let m = "Couldn't arm airplane mode. Make sure the ring is on the charger; if Bluetooth is stuck, toggle it off/on and retry.".to_string();
        emit(&app, "error", &m, None, false);
        Err(m)
    }
}

/// Dock & Silence — arm flight mode (0x26) and prove the radio went silent.
#[tauri::command]
pub async fn arm_airplane(app: AppHandle) -> Result<ArmOutcome, String> {
    tauri::async_runtime::spawn_blocking(move || arm_airplane_blocking(app))
        .await
        .map_err(|e| e.to_string())?
}

/// Just read the battery (a single, quick BLE op).
#[tauri::command]
pub async fn ring_battery(app: AppHandle) -> Result<Option<u8>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let bin = paths::oura_bin().ok_or("couldn't locate the oura binary")?;
        let key = paths::key_file().ok_or("couldn't find the ring's auth-key file")?;
        let out = run_oura(
            &app,
            &bin,
            &[
                "--key-file".into(),
                key.to_string_lossy().to_string(),
                "info".into(),
            ],
            "Battery",
            "battery",
            None,
            75,
        )?;
        Ok(parse_battery(&out))
    })
    .await
    .map_err(|e| e.to_string())?
}
