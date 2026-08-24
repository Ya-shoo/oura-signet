//! Where the two stores live.
//!
//! Raw store  = `oura.db` — the lossless open_oura event log. **Read-only truth.**
//! Derived    = `db/metrics.db` — the app's own store (labels today; daily_metrics,
//!              baselines, sync_log, airplane_log as they land).
//!
//! Resolution order (dev-friendly, ships-correct):
//!   1. explicit env override (`OURA_LOCAL_RAW_DB` / `OURA_LOCAL_DERIVED_DB`)
//!   2. dev: walk up from the current dir to the repo root that contains `oura.db`
//!   3. installed: `~/Library/Application Support/oura-local/`

use std::path::PathBuf;

pub struct DataPaths {
    pub raw_db: PathBuf,
    pub derived_db: PathBuf,
}

pub fn resolve() -> DataPaths {
    let repo = find_repo_root();

    let raw_db = std::env::var("OURA_LOCAL_RAW_DB")
        .ok()
        .map(PathBuf::from)
        .unwrap_or_else(|| match &repo {
            Some(r) => r.join("oura.db"),
            None => app_support_dir().join("oura.db"),
        });

    let derived_db = std::env::var("OURA_LOCAL_DERIVED_DB")
        .ok()
        .map(PathBuf::from)
        .unwrap_or_else(|| match &repo {
            Some(r) => r.join("db").join("metrics.db"),
            None => app_support_dir().join("metrics.db"),
        });

    DataPaths { raw_db, derived_db }
}

/// Find the repo root (the dir holding `oura.db`): an explicit `OURA_LOCAL_ROOT`
/// override, else walk up from cwd (dev), else a known install path. The bundled
/// `.app` launches with cwd `/`, so the cwd-walk finds nothing — the fallback is
/// what makes the installed app locate your data, `open_oura`, and the venv.
/// TODO: replace the hardcoded fallback with a first-run config / bundled resources.
fn find_repo_root() -> Option<PathBuf> {
    if let Ok(root) = std::env::var("OURA_LOCAL_ROOT") {
        let p = PathBuf::from(root);
        if p.join("oura.db").exists() {
            return Some(p);
        }
    }
    if let Ok(mut dir) = std::env::current_dir() {
        loop {
            if dir.join("oura.db").exists() {
                return Some(dir);
            }
            if !dir.pop() {
                break;
            }
        }
    }
    for cand in KNOWN_ROOTS {
        let p = PathBuf::from(cand);
        if p.join("oura.db").exists() {
            return Some(p);
        }
    }
    None
}

/// Known install locations, checked only when the cwd-walk fails (the bundled app
/// runs with cwd `/`).
const KNOWN_ROOTS: &[&str] = &[];

fn app_support_dir() -> PathBuf {
    dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("oura-local")
}

/// The built open_oura CLI binary — drives BLE ops (sync, battery) until the
/// link layer is embedded in-process (phase B). Env override: `OURA_LOCAL_BIN`.
pub fn oura_bin() -> Option<PathBuf> {
    if let Ok(b) = std::env::var("OURA_LOCAL_BIN") {
        return Some(PathBuf::from(b));
    }
    find_repo_root().map(|r| {
        r.join("open_oura")
            .join("target")
            .join("release")
            .join("oura")
    })
}

/// The dev repo root (the dir that holds `oura.db`, `open_oura/`, `sync/`).
pub fn repo_root() -> Option<PathBuf> {
    find_repo_root()
}

/// The ring's 16-byte auth-key file. Env override: `OURA_LOCAL_KEY_FILE`.
pub fn key_file() -> Option<PathBuf> {
    if let Ok(k) = std::env::var("OURA_LOCAL_KEY_FILE") {
        return Some(PathBuf::from(k));
    }
    let dir = find_repo_root()?.join("open_oura");
    std::fs::read_dir(&dir)
        .ok()?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .find(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.starts_with("oura-") && n.ends_with(".key"))
                .unwrap_or(false)
        })
}
