//! Read-only queries against the raw open_oura store (`oura.db`).
//!
//! The raw store is immutable truth — we open it **read-only** so the app can
//! never corrupt the event log. Every function here is a plain SQL read that
//! returns typed rows serialized to the webview (camelCase).

use rusqlite::{Connection, OpenFlags, OptionalExtension};
use serde::Serialize;

pub type Result<T> = std::result::Result<T, String>;

/// Open the raw store read-only. Errors (missing file, etc.) surface to the UI.
pub fn open_ro(path: &std::path::Path) -> Result<Connection> {
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )
    .map_err(|e| format!("open {} read-only: {e}", path.display()))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceInfo {
    pub serial: String,
    pub firmware: Option<String>,
    pub hardware_id: Option<String>,
    pub api_version: Option<String>,
    pub mac: Option<String>,
    pub updated_unix: i64,
}

pub fn device(conn: &Connection) -> Result<Option<DeviceInfo>> {
    conn.query_row(
        "SELECT serial, firmware, hardware_id, api_version, mac, updated_unix \
         FROM device ORDER BY updated_unix DESC LIMIT 1",
        [],
        |r| {
            let non_empty = |s: Option<String>| s.filter(|v| !v.is_empty());
            Ok(DeviceInfo {
                serial: r.get(0)?,
                firmware: non_empty(r.get(1)?),
                hardware_id: non_empty(r.get(2)?),
                api_version: non_empty(r.get(3)?),
                mac: non_empty(r.get(4)?),
                updated_unix: r.get(5)?,
            })
        },
    )
    .optional()
    .map_err(|e| e.to_string())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncStatus {
    pub serial: Option<String>,
    pub next_cursor: i64,
    pub last_sync_unix: Option<i64>,
    pub total_events: i64,
    pub decoded_events: i64,
    pub first_captured_unix: Option<i64>,
    pub last_captured_unix: Option<i64>,
}

pub fn sync_status(conn: &Connection) -> Result<SyncStatus> {
    let (serial, next_cursor, last_sync_unix) = conn
        .query_row(
            "SELECT serial, next_cursor, last_sync_unix FROM sync_state LIMIT 1",
            [],
            |r| {
                Ok((
                    r.get::<_, Option<String>>(0)?,
                    r.get::<_, Option<i64>>(1)?.unwrap_or(0),
                    r.get::<_, Option<i64>>(2)?,
                ))
            },
        )
        .optional()
        .map_err(|e| e.to_string())?
        .unwrap_or((None, 0, None));

    let (total_events, first_captured_unix, last_captured_unix) = conn
        .query_row(
            "SELECT COUNT(*), MIN(captured_unix), MAX(captured_unix) FROM events",
            [],
            |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, Option<i64>>(1)?,
                    r.get::<_, Option<i64>>(2)?,
                ))
            },
        )
        .map_err(|e| e.to_string())?;

    let decoded_events: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM events WHERE decoded_json IS NOT NULL",
            [],
            |r| r.get(0),
        )
        .map_err(|e| e.to_string())?;

    Ok(SyncStatus {
        serial,
        next_cursor,
        last_sync_unix,
        total_events,
        decoded_events,
        first_captured_unix,
        last_captured_unix,
    })
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EventCount {
    pub name: String,
    pub count: i64,
}

pub fn event_counts(conn: &Connection) -> Result<Vec<EventCount>> {
    let mut stmt = conn
        .prepare("SELECT name, COUNT(*) FROM events GROUP BY name ORDER BY COUNT(*) DESC")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |r| {
            Ok(EventCount {
                name: r.get(0)?,
                count: r.get(1)?,
            })
        })
        .map_err(|e| e.to_string())?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|e| e.to_string())
}
