//! oura-local — Rust core (phase A: read-only viewer).
//!
//! A narrow, allow-listed, typed IPC surface. The webview can only call these
//! specific commands; it never touches SQL or BLE directly. The raw store is
//! opened read-only. BLE / ring control (phase B) lands as more commands here.

mod db;
mod metrics;
mod paths;
mod ring;

use serde::Serialize;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DataStatus {
    raw_db_path: String,
    raw_db_exists: bool,
    derived_db_path: String,
    derived_db_exists: bool,
}

/// Which stores the app resolved, and whether they exist — powers the
/// empty-state / "no data yet" UI and the Settings/Ring provenance readouts.
#[tauri::command]
fn get_data_status() -> DataStatus {
    let p = paths::resolve();
    DataStatus {
        raw_db_exists: p.raw_db.exists(),
        derived_db_exists: p.derived_db.exists(),
        raw_db_path: p.raw_db.display().to_string(),
        derived_db_path: p.derived_db.display().to_string(),
    }
}

#[tauri::command]
fn get_device() -> Result<Option<db::DeviceInfo>, String> {
    let p = paths::resolve();
    let conn = db::open_ro(&p.raw_db)?;
    db::device(&conn)
}

#[tauri::command]
fn get_sync_status() -> Result<db::SyncStatus, String> {
    let p = paths::resolve();
    let conn = db::open_ro(&p.raw_db)?;
    db::sync_status(&conn)
}

#[tauri::command]
fn get_event_counts() -> Result<Vec<db::EventCount>, String> {
    let p = paths::resolve();
    let conn = db::open_ro(&p.raw_db)?;
    db::event_counts(&conn)
}

#[tauri::command]
fn get_home_channels(date: Option<String>) -> Result<Vec<metrics::Channel>, String> {
    let p = paths::resolve();
    let conn = db::open_ro(&p.raw_db)?;
    metrics::home_channels(&conn, &p.derived_db, date.as_deref())
}

#[tauri::command]
fn get_available_days() -> Result<Vec<String>, String> {
    let p = paths::resolve();
    let conn = db::open_ro(&p.raw_db)?;
    Ok(metrics::available_days(&conn, &p.derived_db))
}

#[tauri::command]
fn get_vo2_history() -> Result<Vec<f64>, String> {
    let p = paths::resolve();
    metrics::vo2_history(&p.derived_db)
}

#[tauri::command]
fn get_vo2_trend() -> Result<Vec<metrics::Vo2Point>, String> {
    let p = paths::resolve();
    metrics::vo2_trend(&p.derived_db)
}

#[tauri::command]
fn get_activity_sessions(date: Option<String>) -> Result<Vec<metrics::ActivitySession>, String> {
    let p = paths::resolve();
    let conn = db::open_ro(&p.raw_db)?;
    let weight = metrics::read_profile(&p.derived_db).weight_kg;
    // Prefer Oura's real AAD model output; fall back to the MET heuristic.
    metrics::activity_sessions(&conn, &p.derived_db, weight, 5, date.as_deref())
}

#[tauri::command]
fn get_profile() -> metrics::Profile {
    let p = paths::resolve();
    metrics::read_profile(&p.derived_db)
}

#[tauri::command]
fn set_profile(profile: metrics::ProfileInput) -> Result<metrics::Profile, String> {
    let p = paths::resolve();
    metrics::write_profile(&p.derived_db, &profile)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            get_data_status,
            get_device,
            get_sync_status,
            get_event_counts,
            get_home_channels,
            get_available_days,
            get_vo2_history,
            get_vo2_trend,
            get_activity_sessions,
            get_profile,
            set_profile,
            ring::sync_now,
            ring::ring_battery,
            ring::arm_airplane,
            ring::analyze_sleep,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
