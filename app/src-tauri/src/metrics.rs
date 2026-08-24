//! First-pass derived metrics, computed **on demand** from the raw event log.
//!
//! This is deliberately honest, not fake: each channel is tagged
//!   • `real`    — a value we can compute robustly from what's in `oura.db` today
//!   • `preview` — real sensor data, but uncalibrated / not yet baseline-normalized
//!   • `pending` — needs work that isn't done yet (baselines = GOAL 2, sleep
//!                 staging = GOAL 3, step decode, respiration derivation)
//!
//! When GOAL 2's `build_daily.py` / `baselines.py` land, these move into a
//! persisted `daily_metrics` + `baselines` table in the derived store and this
//! command reads from there instead — the IPC contract below stays identical.

use rusqlite::Connection;
use serde::Serialize;

use crate::db::Result;

#[derive(Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct Channel {
    pub id: String,
    /// "real" | "preview" | "pending"
    pub status: String,
    pub value: Option<f64>,
    pub unit: String,
    pub lo: Option<f64>,
    pub hi: Option<f64>,
    pub mean: Option<f64>,
    pub series: Vec<f64>,
    pub sample_count: usize,
    /// short factual subtitle, e.g. "median of 84 overnight readings"
    pub sub: Option<String>,
    /// why it's preview/pending (empty for real)
    pub note: String,
    /// provenance tag for cards that have more than one possible data source
    /// (e.g. sleep: "oura-native" from the ring vs "local" from our SleepECG model)
    pub source: Option<String>,
    /// sleep-card stage minutes, indexed [wake, light, rem, deep] (empty otherwise)
    pub stage_min: Vec<f64>,
    /// For intraday sparklines: wall-clock time (minutes since local midnight) of
    /// `series[0]`, and minutes between consecutive downsampled points. Lets the UI
    /// label each hovered point with its clock time. None for non-temporal series.
    pub series_start_min: Option<f64>,
    pub series_step_min: Option<f64>,
}

impl Channel {
    fn pending(id: &str, unit: &str, note: &str) -> Self {
        Channel {
            id: id.into(),
            status: "pending".into(),
            unit: unit.into(),
            note: note.into(),
            ..Default::default()
        }
    }
}

/// Unix seconds at ring decisecond 0 — the ring's true RTC epoch, taken from its
/// own periodic tag-66 `unix_time` markers (median; they're stable to a few seconds).
///
/// This replaces the old "newest event's captured_unix" anchor, which used the
/// SYNC-write time as if it were the record time. The ring's ds clock only advances
/// while it is recording, so across a recording gap (e.g. going dormant in flight
/// mode) ds barely moves while real days pass — a single global sync-time anchor then
/// mis-dates everything before the gap by days. The tag-66 marker is the ring's own
/// clock reading at a known ds, so `ds/10 + epoch` is the true wall clock. Mirrors
/// interpret/ring_clock.py. Falls back to the legacy anchor only if the DB has no
/// markers yet.
fn ring_epoch(conn: &Connection) -> Option<f64> {
    let mut epochs: Vec<f64> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT ring_timestamp, decoded_json FROM events \
         WHERE tag = 66 AND decoded_json IS NOT NULL",
    ) {
        if let Ok(rows) =
            stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
        {
            for (ds, js) in rows.flatten() {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) {
                    if let Some(ut) = v.get("unix_time").and_then(|x| x.as_f64()) {
                        epochs.push(ut - ds as f64 / 10.0);
                    }
                }
            }
        }
    }
    if !epochs.is_empty() {
        epochs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        return Some(epochs[epochs.len() / 2]); // median
    }
    // legacy fallback: newest event's captured_unix ↔ its ds
    conn.query_row(
        "SELECT ring_timestamp, captured_unix FROM events \
         ORDER BY ring_timestamp DESC LIMIT 1",
        [],
        |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)),
    )
    .ok()
    .map(|(ds, cu)| cu as f64 - ds as f64 / 10.0)
}

/// The most recent local calendar date present in the raw log — the default
/// ("today") day the Home view opens on. Uses the ring's own clock, so it reflects
/// when data was actually RECORDED, not when it was synced. None if the log is empty.
fn latest_local_date(conn: &Connection) -> Option<String> {
    let epoch = ring_epoch(conn)?;
    let max_ds: i64 = conn
        .query_row("SELECT MAX(ring_timestamp) FROM events", [], |r| r.get(0))
        .ok()?;
    let unix = (max_ds as f64 / 10.0 + epoch).round() as i64;
    conn.query_row("SELECT date(?1,'unixepoch','localtime')", [unix], |r| r.get(0))
        .ok()
}

/// Ring-timestamp `[start, end)` bounds for a local calendar day (`YYYY-MM-DD`),
/// derived from the ring's own RTC (`ring_epoch`). Every raw stream is filtered to
/// this window so a card shows exactly the selected day — this is the per-day scoping
/// the whole pipeline lacked.
fn day_window(conn: &Connection, date: &str) -> Option<(i64, i64)> {
    let epoch = ring_epoch(conn)?;
    // local-midnight of `date` in unix seconds ('utc' modifier: treat the string as
    // local time and convert to UTC epoch), then the next midnight.
    let day_start: i64 = conn
        .query_row(
            "SELECT CAST(strftime('%s', ?1 || ' 00:00:00', 'utc') AS INTEGER)",
            [date],
            |r| r.get(0),
        )
        .ok()?;
    let day_end = day_start + 86_400;
    // unix = ds/10 + epoch  ⇒  ds = (unix - epoch) * 10
    let start_rts = ((day_start as f64 - epoch) * 10.0) as i64;
    let end_rts = ((day_end as f64 - epoch) * 10.0) as i64;
    Some((start_rts, end_rts))
}

/// The distinct local dates that have data (union of the raw activity stream and
/// the historized sleep nights), newest first — bounds the Home date stepper.
pub fn available_days(conn: &Connection, derived_db: &std::path::Path) -> Vec<String> {
    use std::collections::BTreeSet;
    let mut days: BTreeSet<String> = BTreeSet::new();
    // days present in the raw log — by each event's RECORD date (ring clock), not
    // its sync-write date, so the stepper doesn't offer days the ring never recorded.
    let epoch = ring_epoch(conn).unwrap_or(0.0);
    if let Ok(mut stmt) = conn.prepare(
        "SELECT DISTINCT date(CAST(ring_timestamp/10.0 + ?1 AS INTEGER),'unixepoch','localtime') \
         FROM events",
    ) {
        if let Ok(rows) = stmt.query_map([epoch], |r| r.get::<_, String>(0)) {
            days.extend(rows.flatten());
        }
    }
    // days we have a staged night for
    if let Ok(conn) = rusqlite::Connection::open(derived_db) {
        if let Ok(mut stmt) = conn.prepare("SELECT DISTINCT date FROM daily_metrics") {
            if let Ok(rows) = stmt.query_map([], |r| r.get::<_, String>(0)) {
                days.extend(rows.flatten());
            }
        }
    }
    days.into_iter().rev().collect()
}

/// Every home channel, in a stable order, for the day `date` (None = the latest
/// day with data). The frontend owns presentation (family, accent, icon,
/// explanation) keyed by `id`; we own the data.
pub fn home_channels(
    conn: &Connection,
    derived_db: &std::path::Path,
    date: Option<&str>,
) -> Result<Vec<Channel>> {
    // Resolve the day being viewed + its ring-timestamp window. Default is the
    // latest local date; `is_latest` gates the "today only" bits (persisting the
    // snapshot, baseline deviation, readiness).
    let latest = latest_local_date(conn);
    let target: Option<String> = date.map(|d| d.to_string()).or_else(|| latest.clone());
    let is_latest = target.as_deref() == latest.as_deref();
    let win = target.as_deref().and_then(|d| day_window(conn, d));
    // --- gather the raw streams once ---
    // HR from the green-LED IBI stream, keeping only the ring's good-quality
    // beats (its own per-beat quality flag, 0–3). Good quality ⇒ solid skin
    // contact ⇒ actually worn, so this filters out non-worn / charger time
    // automatically. The raw `ibi_and_amplitude` stream isn't quality-gated and
    // reads far too low (missed beats double the interval → half the HR), so we
    // never use it for HR. Prefer quality≥3; widen to ≥2 if a sync is sparse.
    let mut hr = quality_hr(conn, 3, win)?;
    if hr.len() < 120 {
        let wider = quality_hr(conn, 2, win)?;
        if wider.len() > hr.len() {
            hr = wider;
        }
    }
    let rmssd = collect_array(conn, "hrv_event", "rmssd_ms", win)?
        .into_iter()
        .filter(|&v| (5.0..=300.0).contains(&v))
        .collect::<Vec<_>>();
    let spo2 = collect_array(conn, "spo2_event", "spo2_percent", win)?
        .into_iter()
        .filter(|&v| (70.0..=100.0).contains(&v)) // drop >100 raw artifacts
        .collect::<Vec<_>>();
    // per-period skin temp (single value per event) is the cleanest temp stream
    let mut temp_src = "temp_period";
    let mut temp = collect_array(conn, "temp_period", "temps_c", win)?;
    if temp.is_empty() {
        temp_src = "sleep_temp_event";
        temp = collect_array(conn, "sleep_temp_event", "temps_c", win)?;
    }
    temp.retain(|&v| (20.0..=42.0).contains(&v));
    let met = collect_array(conn, "activity_information", "met", win)?
        .into_iter()
        .filter(|&v| (0.0..=25.0).contains(&v))
        .collect::<Vec<_>>();

    let mut out = Vec::new();

    // Resting HR — the low end of tonight's worn, good-quality HR. An accurate
    // measurement; the personal "normal range" context builds over ~1 week.
    out.push(match percentile(&hr, 10.0) {
        Some(rhr) => {
            let series = downsample(&hr, 40);
            let (start, step) = split_time_range(stream_time_range(
                conn,
                "green_ibi_quality_event",
                series.len(),
                win,
            ));
            Channel {
                id: "rhr".into(),
                status: "real".into(),
                value: Some(rhr.round()),
                unit: "bpm".into(),
                lo: min(&hr),
                hi: max(&hr),
                mean: mean(&hr),
                series,
                sample_count: hr.len(),
                sub: Some(format!("resting · {} good beats tonight", hr.len())),
                note: String::new(),
                source: None,
                stage_min: Vec::new(),
                series_start_min: start,
                series_step_min: step,
            }
        }
        None => Channel::pending("rhr", "bpm", "no good-quality HR beats in this sync"),
    });

    // HRV rMSSD — preview. Median of the overnight rMSSD readings; a clean
    // nightly HRV wants the same sleep-windowing as resting HR (GOAL 2).
    out.push(match median(&rmssd) {
        Some(m) => {
            let (start, step) =
                split_time_range(stream_time_range(conn, "hrv_event", rmssd.len(), win));
            Channel {
                id: "hrv".into(),
                status: "real".into(),
                value: Some(m.round()),
                unit: "ms".into(),
                lo: min(&rmssd),
                hi: max(&rmssd),
                mean: mean(&rmssd),
                series: rmssd.clone(),
                sample_count: rmssd.len(),
                sub: Some(format!("median · {} reads tonight", rmssd.len())),
                note: String::new(),
                source: None,
                stage_min: Vec::new(),
                series_start_min: start,
                series_step_min: step,
            }
        }
        None => Channel::pending("hrv", "ms", "no HRV readings in this sync"),
    });

    // Overnight heart-rate curve — real. The showcase hero until sleep staging
    // (GOAL 3) can take the top slot.
    out.push(if hr.is_empty() {
        Channel::pending("hr", "bpm", "no heart-rate stream in this sync")
    } else {
        let series = downsample(&hr, 64);
        let (start, step) = split_time_range(stream_time_range(
            conn,
            "green_ibi_quality_event",
            series.len(),
            win,
        ));
        Channel {
            id: "hr".into(),
            status: "real".into(),
            value: mean(&hr).map(|v| v.round()),
            unit: "bpm".into(),
            lo: min(&hr),
            hi: max(&hr),
            mean: mean(&hr),
            series,
            sample_count: hr.len(),
            sub: Some(format!("{} good-quality beats · single night", hr.len())),
            note: String::new(),
            source: None,
            stage_min: Vec::new(),
            series_start_min: start,
            series_step_min: step,
        }
    });

    // Movement / MET — real, from the on-ring activity stream.
    out.push(if met.is_empty() {
        Channel::pending("met", "MET", "no activity samples in this sync")
    } else {
        let active = met.iter().filter(|&&v| v >= 1.5).count();
        // peak-preserving so a brief burst (a sprint's MET spike) stays visible
        // instead of being averaged into the surrounding walk.
        let series = downsample_peak(&met, 48);
        let (start, step) =
            split_time_range(stream_time_range(conn, "activity_information", series.len(), win));
        Channel {
            id: "met".into(),
            status: "real".into(),
            value: mean(&met).map(|v| (v * 100.0).round() / 100.0),
            unit: "MET".into(),
            lo: min(&met),
            hi: max(&met),
            mean: mean(&met),
            series,
            sample_count: met.len(),
            sub: Some(format!(
                "peak {:.1} · {} active samples",
                max(&met).unwrap_or(0.0),
                active
            )),
            note: String::new(),
            source: None,
            stage_min: Vec::new(),
            series_start_min: start,
            series_step_min: step,
        }
    });

    // Skin temperature — preview. Real absolute skin temp; *deviation* (the
    // number that matters) needs a rolling baseline → GOAL 2.
    out.push(if temp.is_empty() {
        Channel::pending("temp", "°F", "no skin-temp readings in this sync")
    } else {
        // Reported in °F for now; a °C/°F preference will move the conversion
        // up to the UI (prefs.units) later.
        let temp_f: Vec<f64> = temp.iter().map(|&c| c_to_f(c)).collect();
        let series = downsample(&temp_f, 40);
        let (start, step) =
            split_time_range(stream_time_range(conn, temp_src, series.len(), win));
        Channel {
            id: "temp".into(),
            status: "preview".into(),
            value: temp_f.last().copied().map(|v| (v * 10.0).round() / 10.0),
            unit: "°F".into(),
            lo: min(&temp_f),
            hi: max(&temp_f),
            mean: mean(&temp_f),
            series,
            sample_count: temp_f.len(),
            sub: Some(format!("absolute skin temp · {} periods", temp.len())),
            note: "deviation vs your baseline builds over ~1 week".into(),
            source: None,
            stage_min: Vec::new(),
            series_start_min: start,
            series_step_min: step,
        }
    });

    // SpO₂ — preview. Raw sensor percentages read >100 (uncalibrated); the
    // R-ratio → SpO₂ calibration is GOAL 2.
    out.push(if spo2.is_empty() {
        Channel::pending("spo2", "%", "no SpO₂ readings in this sync")
    } else {
        let series = downsample(&spo2, 40);
        let (start, step) =
            split_time_range(stream_time_range(conn, "spo2_event", series.len(), win));
        Channel {
            id: "spo2".into(),
            status: "preview".into(),
            value: median(&spo2).map(|v| v.round()),
            unit: "%".into(),
            lo: min(&spo2),
            hi: max(&spo2),
            mean: mean(&spo2),
            series,
            sample_count: spo2.len(),
            sub: Some(format!("raw median of {} readings", spo2.len())),
            note: "R-ratio → SpO₂ calibration pending (GOAL 2)".into(),
            source: None,
            stage_min: Vec::new(),
            series_start_min: start,
            series_step_min: step,
        }
    });

    // --- honestly pending (no fake numbers) ---
    // Sleep: prefer the ring's own native 4-stage hypnogram (bedtime_period +
    // sleep_phase_data) when present; fall back to our local SleepECG staging,
    // then to a pending card.
    out.push(
        read_native_sleep(conn, target.as_deref())
            .or_else(|| read_sleep_night(derived_db, target.as_deref()))
            // On the default (latest) view, if the newest day has no complete night —
            // e.g. the ring stopped recording mid-night — fall back to the most recent
            // real staged night instead of a pending card, so "last night" shows your
            // last actual night rather than nothing.
            .or_else(|| if is_latest { read_sleep_night(derived_db, None) } else { None })
            .unwrap_or_else(|| {
                Channel::pending(
                    "sleep",
                    "",
                    "no staged night for this day yet — the ring finalizes each night's sleep a few hours after you wake; Sync (or Analyze sleep) to pull it once it's ready",
                )
            }),
    );
    // Breathing rate — real. Derived from the overnight beat-to-beat interval
    // series (respiratory sinus arrhythmia) by interpret/build_daily.py, the same
    // signal Oura uses; windowed to the sleep bout and stored per night. Pending
    // only until a staged night exists to window against.
    out.push(
        read_resp(derived_db, target.as_deref()).unwrap_or_else(|| {
            Channel::pending(
                "resp",
                "br/min",
                "breathing rate computes from your overnight heart-rate variation once a night is staged",
            )
        }),
    );
    // Steps — real. Oura's own on-ring step models (steps_motion_decoder +
    // step_counter) run locally by interpret/run_step_counter.py over
    // the ring's buffered gait-feature events (0x7e/0x7f) + motion (0x47) — the
    // 28→27 field mapping was solved and validated by a ~1.7 Hz decoded stride
    // cadence. Pending only until the first sync computes it.
    out.push(read_steps(derived_db, target.as_deref()).unwrap_or_else(|| {
        Channel::pending(
            "steps",
            "steps",
            "step count computes from the ring's gait model on your next sync",
        )
    }));

    // Cardiovascular age (CVA) — Oura's on-device cva_2_1_0 model, run locally by
    // interpret/run_cva.py from the ring's raw-PPG bursts (0x81). Those bursts only
    // arrive on a dock+sync, so this stays pending until one is captured.
    out.push(read_cva(derived_db).unwrap_or_else(|| {
        Channel::pending(
            "cva",
            "yr",
            "cardiovascular age needs a raw-PPG burst — dock the ring and sync to capture one",
        )
    }));

    // Daytime (awake) heart rate — decoded from the ring's everyday-HR buffer
    // (ehr_trace) by interpret/run_ehr.py. Complements the overnight `hr` card,
    // which is sleep-only. Pending until the ring buffers a worn-awake stretch.
    out.push(read_dayhr(derived_db, target.as_deref()).unwrap_or_else(|| {
        Channel::pending(
            "dayhr",
            "bpm",
            "daytime heart rate builds from the ring's everyday-HR sampler — wear it through the day, then sync",
        )
    }));

    // Cardio fitness + metabolism — ecore-ported anthropometric formulas
    // (oura-analysis::metabolic). The population estimate is deterministic from
    // your profile; a logged walk/step test (vo2_tests) supersedes it as YOUR
    // measured value and seeds the trend line.
    let demo = read_demographics(derived_db);
    let est = vo2max_jackson(demo.age, demo.female, demo.weight_kg);
    let measured = latest_vo2_test(derived_db);
    let shown = measured.as_ref().map(|m| m.vo2).unwrap_or(est);
    let (zones, category) = vo2max_rating(shown, demo.age, demo.female);
    let (status, sub, note) = match &measured {
        Some(m) => (
            "real",
            format!("{category} · measured from your walk"),
            format!("{} — {}", m.method, m.note),
        ),
        None => (
            "preview",
            format!(
                "{category} for {}{}",
                demo.age as i32,
                if demo.female { "F" } else { "M" }
            ),
            "estimate from age/sex/weight — a walk/step test personalizes it".into(),
        ),
    };
    out.push(Channel {
        id: "vo2max".into(),
        status: status.into(),
        value: Some(shown.round()),
        unit: "ml/kg".into(),
        lo: Some(28.0), // fitness-bar scale min
        hi: Some(62.0), // fitness-bar scale max
        series: zones,  // zone upper-bounds (very-poor|poor|fair|good|excellent)
        // when a test supersedes the estimate, carry the population estimate as a
        // faint "typical for your age/sex" marker for comparison on the zone bar.
        mean: measured.as_ref().map(|_| est.round()),
        sub: Some(sub),
        note,
        ..Default::default()
    });
    let bmr_day = bmr_mifflin(demo.age, demo.female, demo.weight_kg, demo.height_cm);
    // Pro-rate the full-day basal rate to the elapsed part of the local day, so
    // "resting today" reflects calories actually burned SO FAR — not a whole day's
    // BMR shown at 1 AM. Completed/past days use the full BMR (fraction 1.0).
    let frac = target
        .as_deref()
        .map(|d| day_elapsed_fraction(conn, d))
        .unwrap_or(1.0);
    let bmr = bmr_day * frac;
    // Active burn: prefer the HR/motion/gait-driven `energy_expenditure` model
    // (interpret/run_energy_expenditure.py → energy_daily/energy_hourly); fall back
    // to the live MET-only estimate when the model hasn't run for this day yet.
    let (burn_by_hour, active, active_src) = match target
        .as_deref()
        .and_then(|d| read_energy(derived_db, d))
    {
        Some((a, hourly)) => (hourly, a, "HR/motion-modeled"),
        None => {
            let (h, a) = intraday_active_kcal(conn, demo.weight_kg, target.as_deref());
            (h, a, "MET-based")
        }
    };
    out.push(Channel {
        id: "bmr".into(),
        status: "real".into(),
        value: Some((bmr + active).round()), // burned so far today (resting-so-far + active)
        unit: "kcal".into(),
        lo: Some(bmr.round()),    // resting burned so far (for the split bar)
        hi: Some(active.round()), // active portion (today)
        series: burn_by_hour,     // per-local-hour active kcal → intraday burn curve
        sub: Some(format!(
            "resting {} + active {} so far · BMR ~{}/day · {}",
            bmr.round() as i64,
            active.round() as i64,
            bmr_day.round() as i64,
            active_src
        )),
        ..Default::default()
    });

    // "Today only": accrue tonight's metrics into the derived store, annotate
    // deviation-vs-baseline, and score readiness. Past-day views are read-only and
    // don't carry today's deviation/readiness (which are "as of now").
    if is_latest {
        persist_daily(conn, derived_db, &out);
        annotate_baselines(derived_db, &mut out);
        out.push(compute_readiness(derived_db));
    } else {
        out.push(Channel::pending(
            "readiness",
            "",
            "readiness is scored for today",
        ));
    }

    Ok(out)
}

/// Deviation-vs-your-own-baseline readout ("how does today compare"), using the
/// personal baselines built by interpret/baselines.py — a robust rolling method
/// (a centered-Gaussian-weighted baseline over a 5–21 day buffer, plus a robust
/// median/MAD pair). We report a robust z = (today − median) / (1.4826·MAD).
/// The `baselines` table only has a row once ≥5 days exist, so before that these
/// channels keep their factual subtitle and temp stays "preview" — no fake norm.
fn annotate_baselines(derived_db: &std::path::Path, out: &mut [Channel]) {
    let Ok(conn) = rusqlite::Connection::open(derived_db) else {
        return;
    };
    // (channel id, daily_metrics col, baselines.metric, is_temp)
    for (id, col, metric, is_temp) in [
        ("rhr", "rhr", "rhr", false),
        ("hrv", "hrv", "hrv", false),
        ("temp", "temp_c", "temp_c", true),
        ("spo2", "spo2", "spo2", false),
        ("resp", "resp", "resp", false),
    ] {
        // Robust rolling baseline (median + MAD) from baselines.py.
        let base: Option<(f64, f64, i64)> = conn
            .query_row(
                "SELECT median, mad, n_days FROM baselines WHERE metric=?1",
                [metric],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .ok();
        let Some((median, mad, n)) = base else {
            continue; // still warming up (<5 days) — leave the honest "building" copy
        };
        // Today's per-day value (same statistic the baseline is built from).
        let today: Option<f64> = conn
            .query_row(
                &format!(
                    "SELECT {col} FROM daily_metrics WHERE {col} IS NOT NULL ORDER BY date DESC LIMIT 1"
                ),
                [],
                |r| r.get(0),
            )
            .ok();
        let Some(today) = today else {
            continue;
        };
        let spread = 1.4826 * mad; // MAD → robust σ estimate
        let z = if spread > 1e-6 { (today - median) / spread } else { 0.0 };
        let arrow = if z.abs() < 0.3 {
            "•"
        } else if z > 0.0 {
            "▲"
        } else {
            "▼"
        };
        if let Some(c) = out.iter_mut().find(|c| c.id == id && c.status != "pending") {
            if is_temp {
                // Skin-temp deviation is the number that matters — promote to real.
                let dev_f = (today - median) * 1.8; // Δ°C → Δ°F
                c.status = "real".into();
                c.note = String::new();
                c.sub = Some(format!("{arrow} {dev_f:+.1}°F vs your {n}-day baseline"));
            } else {
                c.sub = Some(format!("{arrow} {z:+.1}σ vs your {n}-day baseline"));
            }
        }
    }
}

/// Nights before readiness is shown at all (below this, the baseline is too thin
/// to say anything honest — calm placeholder). Oura itself shows a score within a
/// couple of nights; we ask for a few so the contributors have something to lean on.
const READINESS_FLOOR: usize = 3;
/// Nights before readiness graduates from `preview` (asterisked — "still sharpening
/// as your baseline fills in") to `real`. ≈ Oura's ~2-week baseline; until then the
/// score is shown but flagged, per the honesty rule.
const READINESS_FULL: usize = 14;

/// Snapshot the current metric values into `daily_metrics`, keyed by the night
/// the data represents (the staged sleep date, else the newest captured day).
fn persist_daily(conn_raw: &Connection, derived_db: &std::path::Path, out: &[Channel]) {
    let Ok(conn) = rusqlite::Connection::open(derived_db) else {
        return;
    };
    let _ = conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_metrics(date TEXT PRIMARY KEY, rhr REAL, hrv REAL, \
         temp_f REAL, spo2 REAL, met_avg REAL, sleep_min REAL, sleep_eff REAL, vo2max REAL, \
         updated_unix INTEGER)",
        [],
    );
    let _ = conn.execute("ALTER TABLE daily_metrics ADD COLUMN vo2max REAL", []);
    let sleep: Option<(String, f64, f64)> = conn
        .query_row(
            "SELECT date, total_sleep_min, sleep_efficiency_pct FROM sleep_night \
             WHERE hypnogram IS NOT NULL ORDER BY date DESC LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .ok();
    let date = match &sleep {
        Some((d, _, _)) => d.clone(),
        None => match latest_local_date(conn_raw) {
            // ring-clock latest RECORD date (not sync date) when no night is staged
            Some(d) => d,
            None => return,
        },
    };
    let v = |id: &str| {
        out.iter()
            .find(|c| c.id == id && c.status != "pending")
            .and_then(|c| c.value)
    };
    let _ = conn.execute(
        "INSERT INTO daily_metrics(date,rhr,hrv,temp_f,spo2,met_avg,sleep_min,sleep_eff,vo2max,updated_unix) \
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,strftime('%s','now')) \
         ON CONFLICT(date) DO UPDATE SET rhr=?2,hrv=?3,temp_f=?4,spo2=?5,met_avg=?6,sleep_min=?7,\
         sleep_eff=?8,vo2max=?9,updated_unix=strftime('%s','now')",
        rusqlite::params![
            date,
            v("rhr"),
            v("hrv"),
            v("temp"),
            v("spo2"),
            v("met"),
            sleep.as_ref().map(|s| s.1),
            sleep.as_ref().map(|s| s.2),
            v("vo2max"),
        ],
    );
}

/// A logged VO₂max field test (walk/step). This is YOUR measured value; it
/// supersedes the population estimate on the card and drives the trend line.
struct Vo2Test {
    vo2: f64,
    method: String,
    note: String,
    #[allow(dead_code)]
    date: String,
}

fn vo2_tests_schema(conn: &Connection) {
    let _ = conn.execute(
        "CREATE TABLE IF NOT EXISTS vo2_tests(date TEXT, vo2max REAL, method TEXT, \
         note TEXT, created_unix INTEGER)",
        [],
    );
}

/// The most recent logged VO₂max test, if any.
fn latest_vo2_test(derived_db: &std::path::Path) -> Option<Vo2Test> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    vo2_tests_schema(&conn);
    conn.query_row(
        "SELECT vo2max, method, note, date FROM vo2_tests \
         ORDER BY date DESC, created_unix DESC LIMIT 1",
        [],
        |r| {
            Ok(Vo2Test {
                vo2: r.get(0)?,
                method: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                note: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                date: r.get(3)?,
            })
        },
    )
    .ok()
}

/// VO₂max values over time for the card's trend line. Prefers your logged field
/// tests (the meaningful trend); before any test is logged, falls back to the
/// deduped formula history (a flat estimate = one point).
pub fn vo2_history(derived_db: &std::path::Path) -> Result<Vec<f64>> {
    let conn = rusqlite::Connection::open(derived_db).map_err(|e| e.to_string())?;
    vo2_tests_schema(&conn);
    let tests: Vec<f64> = conn
        .prepare("SELECT vo2max FROM vo2_tests ORDER BY date, created_unix")
        .and_then(|mut s| s.query_map([], |r| r.get(0)).and_then(|m| m.collect()))
        .map_err(|e| e.to_string())?;
    if !tests.is_empty() {
        return Ok(tests);
    }
    let mut stmt = conn
        .prepare("SELECT vo2max FROM daily_metrics WHERE vo2max IS NOT NULL ORDER BY date")
        .map_err(|e| e.to_string())?;
    let vals: Vec<f64> = stmt
        .query_map([], |r| r.get(0))
        .and_then(|m| m.collect())
        .map_err(|e| e.to_string())?;
    let mut out: Vec<f64> = Vec::new();
    for v in vals {
        if out.last() != Some(&v) {
            out.push(v);
        }
    }
    Ok(out)
}

/// A dated VO₂max data point for the card's history strip (peak + trend).
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Vo2Point {
    pub date: String,
    pub vo2: f64,
}

/// Dated VO₂max history — logged field tests over time (peak, 6-month window and
/// trend are computed on the frontend). Empty until the first test is logged.
pub fn vo2_trend(derived_db: &std::path::Path) -> Result<Vec<Vo2Point>> {
    let conn = rusqlite::Connection::open(derived_db).map_err(|e| e.to_string())?;
    vo2_tests_schema(&conn);
    let mut stmt = conn
        .prepare("SELECT date, vo2max FROM vo2_tests WHERE vo2max IS NOT NULL ORDER BY date, created_unix")
        .map_err(|e| e.to_string())?;
    let pts: Vec<Vo2Point> = stmt
        .query_map([], |r| Ok(Vo2Point { date: r.get(0)?, vo2: r.get(1)? }))
        .and_then(|m| m.collect())
        .map_err(|e| e.to_string())?;
    Ok(pts)
}

fn mean_sd(vals: &[f64]) -> Option<(f64, f64)> {
    if vals.len() < 2 {
        return None;
    }
    let mean = vals.iter().sum::<f64>() / vals.len() as f64;
    let var = vals.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / vals.len() as f64;
    Some((mean, var.sqrt()))
}

/// A transparent, Oura-faithful readiness score built from per-contributor
/// sub-scores (0-100), each comparing today against your own baseline, then
/// weighted-averaged (Sleep leads). Mirrors Oura's contributor model — Resting
/// HR, HRV balance, Body temperature, Sleep, Breathing — which is likewise an
/// app-side formula over contributors, not a single model. Contributor scores are
/// packed into `series` in this fixed order for the card body:
///   [0] Sleep · [1] Resting HR · [2] HRV balance · [3] Body temp · [4] Breathing
/// (a `-1` slot means that contributor is still calibrating). Pending below
/// READINESS_FLOOR nights; `preview` (asterisked) until READINESS_FULL, then `real`.
fn compute_readiness(derived_db: &std::path::Path) -> Channel {
    let pending = |n: usize| {
        Channel::pending(
            "readiness",
            "",
            &format!("a recovery score builds from your own baseline · {n}/{READINESS_FLOOR} nights"),
        )
    };
    let Ok(conn) = rusqlite::Connection::open(derived_db) else {
        return pending(0);
    };
    // Full history, newest first — row 0 is "today".
    let mut stmt = match conn.prepare(
        "SELECT rhr, hrv, temp_c, resp, sleep_min, sleep_eff FROM daily_metrics ORDER BY date DESC",
    ) {
        Ok(s) => s,
        Err(_) => return pending(0),
    };
    let rows: Vec<[Option<f64>; 6]> = stmt
        .query_map([], |r| {
            Ok([
                r.get(0)?,
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get(4)?,
                r.get(5)?,
            ])
        })
        .and_then(|m| m.collect())
        .unwrap_or_default();
    let n = rows.len();
    if n < READINESS_FLOOR {
        return pending(n);
    }
    let today = &rows[0];

    // Center + spread for a metric: prefer the Oura-style robust baseline
    // (median + MAD→σ) from the baselines table; fall back to history mean/sd.
    let baseline = |metric: &str, col: usize| -> Option<(f64, f64)> {
        if let Ok((median, mad)) = conn.query_row(
            "SELECT median, mad FROM baselines WHERE metric=?1",
            [metric],
            |r| Ok((r.get::<_, f64>(0)?, r.get::<_, f64>(1)?)),
        ) {
            return Some((median, (1.4826 * mad).max(1e-6)));
        }
        let hist: Vec<f64> = rows.iter().filter_map(|r| r[col]).collect();
        let (m, sd) = mean_sd(&hist)?;
        Some((m, sd.max(1e-6)))
    };
    // z of today vs baseline, clamped so a thin/degenerate baseline (few nights,
    // tiny spread) can't swing a contributor to an extreme on trivial noise.
    let zof = |metric: &str, col: usize| -> Option<f64> {
        let (c, s) = baseline(metric, col)?;
        Some(((today[col]? - c) / s).clamp(-2.5, 2.5))
    };

    // --- contributor sub-scores (0-100; ~80 ≈ a normal day at your baseline) ---
    let mut scores: Vec<f64> = vec![-1.0; 5];
    // Sleep — absolute (no baseline needed): efficiency + duration of last night.
    if let Some(eff) = today[5] {
        let dur = today[4].unwrap_or(0.0);
        let dur_score = (100.0 - (dur - 465.0).abs() / 4.0).clamp(30.0, 100.0); // ~7.75 h ideal
        let eff_score = ((eff - 55.0) / (93.0 - 55.0) * 100.0).clamp(0.0, 100.0); // 93%+ → 100
        scores[0] = 0.55 * eff_score + 0.45 * dur_score;
    }
    // Resting HR — lower vs baseline is better.
    if let Some(z) = zof("rhr", 0) {
        scores[1] = (82.0 - 14.0 * z).clamp(10.0, 100.0);
    }
    // HRV balance — higher vs baseline is better.
    if let Some(z) = zof("hrv", 1) {
        scores[2] = (78.0 + 14.0 * z).clamp(10.0, 100.0);
    }
    // Body temperature — any deviation dings; an elevated (fever) side dings harder.
    if let Some(z) = zof("temp_c", 2) {
        scores[3] = (90.0 - 16.0 * z.abs() - 6.0 * z.max(0.0)).clamp(10.0, 100.0);
    }
    // Breathing rate — an *elevated* overnight rate is the recovery flag (strain
    // or oncoming illness); a lower-than-usual rate is not a negative, so the low
    // side is only lightly dinged for the deviation itself.
    if let Some(z) = zof("resp", 3) {
        scores[4] = (86.0 - 12.0 * z.max(0.0) - 4.0 * (-z).max(0.0)).clamp(10.0, 100.0);
    }

    // weighted average over the available contributors (Sleep leads, like Oura).
    let weights = [2.0, 1.5, 1.5, 1.0, 0.6];
    let (mut acc, mut wsum) = (0.0, 0.0);
    for (i, &sc) in scores.iter().enumerate() {
        if sc >= 0.0 {
            acc += sc * weights[i];
            wsum += weights[i];
        }
    }
    if wsum <= 0.0 {
        return pending(n);
    }
    let score = (acc / wsum).clamp(1.0, 99.0).round();
    let label = if score >= 85.0 {
        "primed"
    } else if score >= 70.0 {
        "balanced"
    } else if score >= 60.0 {
        "take it easy"
    } else {
        "pay attention"
    };
    // Honest graduation: show the number now, but flag it as `preview` (asterisked)
    // until ~2 weeks of baseline accrue, per the "no unearned numbers" rule.
    let (status, sub, note) = if n >= READINESS_FULL {
        (
            "real",
            format!("{label} · your {n}-night baseline"),
            String::new(),
        )
    } else {
        (
            "preview",
            format!("{label} · sharpening ({n}/{READINESS_FULL} nights)"),
            "readiness sharpens as ~2 weeks of nights accrue".into(),
        )
    };
    Channel {
        id: "readiness".into(),
        status: status.into(),
        value: Some(score),
        unit: "".into(),
        series: scores,       // per-contributor sub-scores (order in the doc comment)
        mean: Some(n as f64), // nights of baseline
        sub: Some(sub),
        note,
        ..Default::default()
    }
}

// ---- demographics + metabolic (ecore-ported) -----------------------------

struct Demographics {
    age: f64,
    female: bool,
    weight_kg: f64,
    height_cm: f64,
}

/// Ensure the `prefs` table exists and is seeded with sane defaults, returning an
/// open read-write connection. Shared by every profile read/write so the schema +
/// defaults live in exactly one place.
fn prefs_conn(derived_db: &std::path::Path) -> Option<Connection> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS prefs(key TEXT PRIMARY KEY, value TEXT)",
        [],
    )
    .ok()?;
    for (k, v) in [
        ("age", "26"),
        ("sex", "M"),
        ("weight_kg", "65.77"),
        ("height_cm", "170.18"),
        ("ring_size", "9"), // US ring size — feeds the CVA model's demographics
        ("units", "imperial"),
    ] {
        let _ = conn.execute("INSERT OR IGNORE INTO prefs(key,value) VALUES (?1,?2)", [k, v]);
    }
    Some(conn)
}

fn pref_str(conn: &Connection, key: &str) -> Option<String> {
    conn.query_row("SELECT value FROM prefs WHERE key=?1", [key], |r| r.get(0))
        .ok()
}

/// Read the user profile from the derived store's `prefs` table, seeding sane
/// defaults on first run (the Settings screen edits these).
fn read_demographics(derived_db: &std::path::Path) -> Demographics {
    let mut d = Demographics {
        age: 26.0,
        female: false,
        weight_kg: 65.77,
        height_cm: 170.18,
    };
    if let Some(conn) = prefs_conn(derived_db) {
        if let Some(a) = pref_str(&conn, "age").and_then(|s| s.parse().ok()) {
            d.age = a;
        }
        if let Some(w) = pref_str(&conn, "weight_kg").and_then(|s| s.parse().ok()) {
            d.weight_kg = w;
        }
        if let Some(h) = pref_str(&conn, "height_cm").and_then(|s| s.parse().ok()) {
            d.height_cm = h;
        }
        if let Some(s) = pref_str(&conn, "sex") {
            d.female = s.eq_ignore_ascii_case("F");
        }
    }
    d
}

/// The editable profile plus the estimates it drives — so the Settings screen is a
/// transparent preview of exactly what the VO₂max / Calories cards compute. Height
/// isn't in the Jackson/Schofield formulas (it's kept for BMI + future models).
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub age: f64,
    /// "M" | "F"
    pub sex: String,
    pub weight_kg: f64,
    pub height_cm: f64,
    /// "metric" | "imperial" — input-display preference only; values stored metric.
    pub units: String,
    pub vo2max: f64,
    pub vo2max_category: String,
    pub bmr: f64,
    pub bmi: Option<f64>,
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileInput {
    pub age: f64,
    pub sex: String,
    pub weight_kg: f64,
    pub height_cm: f64,
    pub units: String,
}

fn build_profile(d: &Demographics, height_cm: f64, units: String) -> Profile {
    let vo2 = vo2max_jackson(d.age, d.female, d.weight_kg);
    let (_, category) = vo2max_rating(vo2, d.age, d.female);
    let bmr = bmr_mifflin(d.age, d.female, d.weight_kg, height_cm);
    let bmi = (height_cm > 0.0).then(|| d.weight_kg / (height_cm / 100.0).powi(2));
    Profile {
        age: d.age,
        sex: if d.female { "F" } else { "M" }.into(),
        weight_kg: d.weight_kg,
        height_cm,
        units,
        vo2max: vo2.round(),
        vo2max_category: category,
        bmr: bmr.round(),
        bmi: bmi.map(|b| (b * 10.0).round() / 10.0),
    }
}

/// The full profile + derived estimates for the Settings screen.
pub fn read_profile(derived_db: &std::path::Path) -> Profile {
    let d = read_demographics(derived_db);
    let (height_cm, units) = prefs_conn(derived_db)
        .map(|c| {
            (
                pref_str(&c, "height_cm")
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(170.18),
                pref_str(&c, "units").unwrap_or_else(|| "imperial".into()),
            )
        })
        .unwrap_or((170.18, "imperial".into()));
    build_profile(&d, height_cm, units)
}

/// Persist an edited profile (clamped to physiologic ranges), then return the
/// recomputed profile so the caller re-renders with the new estimates.
pub fn write_profile(derived_db: &std::path::Path, input: &ProfileInput) -> Result<Profile> {
    let conn = prefs_conn(derived_db).ok_or_else(|| "could not open prefs store".to_string())?;
    let age = input.age.clamp(1.0, 120.0);
    let weight_kg = input.weight_kg.clamp(20.0, 400.0);
    let height_cm = input.height_cm.clamp(80.0, 260.0);
    let sex = if input.sex.eq_ignore_ascii_case("F") { "F" } else { "M" };
    let units = if input.units.eq_ignore_ascii_case("metric") {
        "metric"
    } else {
        "imperial"
    };
    let set = |k: &str, v: String| -> Result<()> {
        conn.execute(
            "INSERT INTO prefs(key,value) VALUES (?1,?2) \
             ON CONFLICT(key) DO UPDATE SET value=?2",
            rusqlite::params![k, v],
        )
        .map(|_| ())
        .map_err(|e| e.to_string())
    };
    set("age", format!("{age}"))?;
    set("sex", sex.into())?;
    set("weight_kg", format!("{weight_kg}"))?;
    set("height_cm", format!("{height_cm}"))?;
    set("units", units.into())?;
    Ok(read_profile(derived_db))
}

/// The latest daily step total from Oura's on-ring step models (run locally by
/// interpret/run_step_counter.py → daily_metrics.steps). None until a sync has
/// computed one → the caller shows a pending card.
/// Model-driven active energy for `date` from interpret/run_energy_expenditure.py:
/// `energy_daily.active_ee_kcal` is the HR/motion/gait-driven burn inside detected
/// workouts (Oura's `energy_expenditure` model) blended with the MET estimate
/// elsewhere; `energy_hourly` is the intraday curve. Returns (active_kcal, hours[24]).
/// None when the tables/day are absent → the caller falls back to the MET-only calc.
fn read_energy(derived_db: &std::path::Path, date: &str) -> Option<(f64, Vec<f64>)> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    let active: f64 = conn
        .query_row(
            "SELECT active_ee_kcal FROM energy_daily WHERE date=?1",
            [date],
            |r| r.get(0),
        )
        .ok()?;
    let mut hours = vec![0.0f64; 24];
    if let Ok(mut stmt) = conn.prepare("SELECT hour, active_kcal FROM energy_hourly WHERE date=?1")
    {
        if let Ok(rows) = stmt.query_map([date], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, f64>(1)?)))
        {
            for (h, k) in rows.flatten() {
                if (0..24).contains(&h) {
                    hours[h as usize] = k;
                }
            }
        }
    }
    Some((active, hours))
}

/// Oura's Cardiovascular Age (vascular age, in years) from the ring's raw PPG —
/// the on-device `cva_2_1_0` model, run locally by interpret/run_cva.py into the
/// `cva_daily` table. Raw-PPG bursts (0x81) only arrive on a dock+sync, so this
/// returns None (→ a pending card) until one lands. Value = vascular age; pulse-wave
/// velocity rides along in the subtitle as a second cardiovascular read.
fn read_cva(derived_db: &std::path::Path) -> Option<Channel> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    let (date, cva, pwv): (String, f64, Option<f64>) = conn
        .query_row(
            "SELECT date, cva, pwv FROM cva_daily WHERE cva IS NOT NULL ORDER BY date DESC LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .ok()?;
    // The CVA only means something next to your real age (younger/older than you
    // are) — carry chronological age in `mean` and arterial stiffness (PWV) in `lo`
    // so the card can render the age comparison + a stiffness readout.
    let age = read_demographics(derived_db).age;
    Some(Channel {
        id: "cva".into(),
        status: "real".into(),
        value: Some(cva.round()),
        unit: "yr".into(),
        mean: Some(age),
        lo: pwv,
        sub: Some(format!("Oura CVA model · {date}")),
        ..Default::default()
    })
}

/// Daytime (awake) heart rate for a day, decoded from the ring's everyday-HR
/// buffer (`ehr_trace`) by interpret/run_ehr.py into `dayhr_daily`. This is the
/// waking-hours counterpart to the overnight `hr` card (which is sleep-only): a
/// resting→typical→peak readout plus the share of the day spent in each HR zone.
/// `series` carries the five zone weights (resting|light|active|cardio|peak) for
/// the card's time-in-zone bar; the frontend normalizes them to proportions.
/// Returns None (→ no card) until the ring has buffered a worn-awake stretch.
fn read_dayhr(derived_db: &std::path::Path, date: Option<&str>) -> Option<Channel> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    let cols = "date, resting, typical, active, peak, z_resting, z_light, z_active, z_cardio, z_peak";
    let row = |r: &rusqlite::Row| -> rusqlite::Result<(String, f64, f64, f64, f64, [f64; 5])> {
        Ok((
            r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?,
            [r.get(5)?, r.get(6)?, r.get(7)?, r.get(8)?, r.get(9)?],
        ))
    };
    let (day, resting, typical, active, peak, zones): (String, f64, f64, f64, f64, [f64; 5]) =
        match date {
            Some(d) => conn
                .query_row(
                    &format!("SELECT {cols} FROM dayhr_daily WHERE date = ?1"),
                    [d],
                    row,
                )
                .ok()?,
            None => conn
                .query_row(
                    &format!("SELECT {cols} FROM dayhr_daily ORDER BY date DESC LIMIT 1"),
                    [],
                    row,
                )
                .ok()?,
        };
    Some(Channel {
        id: "dayhr".into(),
        status: "real".into(),
        value: Some(typical.round()),
        unit: "bpm".into(),
        lo: Some(resting.round()), // daytime resting HR (awake floor)
        hi: Some(peak.round()),    // robust peak (98th pct)
        mean: Some(active.round()), // 90th pct — "active" spells
        series: zones.to_vec(),    // zone weights → time-in-zone bar
        sub: Some(format!("resting {} · typical {} · peak {} · {day}", resting.round(), typical.round(), peak.round())),
        source: Some("ehr-trace".into()),
        ..Default::default()
    })
}

/// Overnight breathing rate for a day, from the RSA-derived estimate written by
/// interpret/build_daily.py (`daily_metrics.resp` breaths/min, `resp_var` the
/// per-window std = steadiness). Keyed to the night's wake date, like sleep. Oura
/// derives breathing rate the same way — from the beat-to-beat interval series
/// (respiratory sinus arrhythmia), so this is a faithful local reproduction.
fn read_resp(derived_db: &std::path::Path, date: Option<&str>) -> Option<Channel> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    let (day, resp, var): (String, f64, Option<f64>) = match date {
        Some(d) => conn
            .query_row(
                "SELECT date, resp, resp_var FROM daily_metrics \
                 WHERE date = ?1 AND resp IS NOT NULL",
                [d],
                |r| Ok((r.get(0)?, r.get(1)?, r.get::<_, Option<f64>>(2)?)),
            )
            .ok()?,
        None => conn
            .query_row(
                "SELECT date, resp, resp_var FROM daily_metrics \
                 WHERE resp IS NOT NULL ORDER BY date DESC LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get::<_, Option<f64>>(2)?)),
            )
            .ok()?,
    };
    // Steadiness read from the per-window variability (std of the nightly PSD-peak
    // rates). A tight spread reads as steady breathing; a wide one as restless.
    let steady = match var {
        Some(v) if v < 1.5 => "very steady",
        Some(v) if v < 2.2 => "steady",
        Some(_) => "variable",
        None => "overnight average",
    };
    // Recent nightly breathing rates (oldest→newest, up to and including the viewed
    // day) for the trend sparkline — night-to-night stability is the key signal.
    let mut trend: Vec<f64> = conn
        .prepare(
            "SELECT resp FROM daily_metrics WHERE resp IS NOT NULL AND date <= ?1 \
             ORDER BY date DESC LIMIT 14",
        )
        .and_then(|mut s| s.query_map([&day], |r| r.get::<_, f64>(0)).and_then(|m| m.collect()))
        .unwrap_or_default();
    trend.reverse();
    Some(Channel {
        id: "resp".into(),
        status: "real".into(),
        value: Some(resp.round()),
        unit: "br/min".into(),
        mean: var,     // per-window variability → steadiness readout
        series: trend, // recent nightly rates → trend sparkline
        sub: Some(format!("overnight average · {steady} · {day}")),
        ..Default::default()
    })
}

fn read_steps(derived_db: &std::path::Path, date: Option<&str>) -> Option<Channel> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    let (day, steps): (String, f64) = match date {
        Some(d) => (
            d.to_string(),
            conn.query_row(
                "SELECT steps FROM daily_metrics WHERE date = ?1 AND steps IS NOT NULL",
                [d],
                |r| r.get(0),
            )
            .ok()?,
        ),
        None => conn
            .query_row(
                "SELECT date, steps FROM daily_metrics WHERE steps IS NOT NULL ORDER BY date DESC LIMIT 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .ok()?,
    };
    // Per-hour step buckets → the iOS-Health-style bar chart. Written by
    // interpret/run_step_counter.py (`steps_hourly`); absent → empty series (card
    // shows just the total). series[h] = steps taken in local hour h (0..23).
    let mut hourly = vec![0.0f64; 24];
    let mut found = false;
    if let Ok(mut stmt) = conn.prepare("SELECT hour, steps FROM steps_hourly WHERE date = ?1") {
        if let Ok(rows) =
            stmt.query_map([&day], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, f64>(1)?)))
        {
            for (h, s) in rows.flatten() {
                if (0..24).contains(&h) {
                    hourly[h as usize] = s;
                    found = true;
                }
            }
        }
    }
    Some(Channel {
        id: "steps".into(),
        status: "real".into(),
        value: Some(steps.round()),
        unit: "steps".into(),
        series: if found { hourly } else { Vec::new() },
        sub: Some(format!("Oura step model · {day}")),
        sample_count: steps.max(0.0) as usize,
        ..Default::default()
    })
}

/// The most recent night from the `sleep_night` table, as a sleep channel. Now
/// prefers the ring's native 4-stage staging (`oura-sleepnet`) when present
/// — status "real" with a deep/light split derived from the hypnogram — and
/// falls back to the SleepECG staging (`local`, 3-class) as "preview". The
/// hypnogram is encoded in `series` (WAKE=0, LIGHT=1, REM=2, DEEP=3). Returns
/// None if no night has been staged (→ the caller shows a pending card).
fn read_sleep_night(derived_db: &std::path::Path, date: Option<&str>) -> Option<Channel> {
    let conn = rusqlite::Connection::open(derived_db).ok()?;
    // Scope to the requested night; None → the most recent staged night.
    let (where_date, params): (&str, Vec<String>) = match date {
        Some(d) => ("AND date = ?1", vec![d.to_string()]),
        None => ("", Vec::new()),
    };
    let sql = format!(
        "SELECT total_sleep_min, sleep_efficiency_pct, wake_min, nrem_min, rem_min, \
         start_clock, end_clock, hypnogram, time_in_bed_min, classifier, date FROM sleep_night \
         WHERE hypnogram IS NOT NULL AND total_sleep_min >= 60 {where_date} ORDER BY date DESC LIMIT 1"
    );
    let (tst, eff, wake, nrem, rem, start_c, end_c, hyp_json, tib, classifier, night_date): (
        f64,
        f64,
        f64,
        f64,
        f64,
        Option<String>,
        Option<String>,
        Option<String>,
        f64,
        Option<String>,
        String,
    ) = conn
        .query_row(
            &sql,
            rusqlite::params_from_iter(params.iter()),
            |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    r.get(4)?,
                    r.get(5)?,
                    r.get(6)?,
                    r.get(7)?,
                    r.get(8)?,
                    r.get(9)?,
                    r.get(10)?,
                ))
            },
        )
        .ok()?;
    let bars: Vec<String> = serde_json::from_str(&hyp_json?).ok()?;
    // window start (minutes since local midnight) + minutes per bar → per-cell times
    let start_min = start_c.as_deref().and_then(parse_hhmm);
    let per_bar = (!bars.is_empty()).then(|| tib / bars.len() as f64);
    let code = |s: &str| -> f64 {
        match s {
            "WAKE" => 0.0,
            "REM" => 2.0,
            "DEEP" | "N3" => 3.0,
            _ => 1.0,
        }
    };
    let series: Vec<f64> = bars.iter().map(|s| code(s)).collect();
    // The ring's native staging is a true 4-stage classifier — split deep from light off
    // the hypnogram (the table only stores nrem = deep+light). SleepECG can't.
    let is_oura = classifier.as_deref() == Some("oura-sleepnet");
    let deep_min = per_bar
        .map(|pb| bars.iter().filter(|s| matches!(s.as_str(), "DEEP" | "N3")).count() as f64 * pb)
        .unwrap_or(0.0);
    let light_min = (nrem - deep_min).max(0.0);
    let hm = |m: f64| -> String {
        let h = (m / 60.0) as i64;
        let mn = (m % 60.0).round() as i64;
        if h > 0 {
            format!("{h}h{mn:02}m")
        } else {
            format!("{mn}m")
        }
    };
    let has_deep = is_oura && deep_min > 0.0;
    let parts = if has_deep {
        let mut p = vec![
            format!("Deep {}", hm(deep_min)),
            format!("Light {}", hm(light_min)),
        ];
        if rem > 0.0 {
            p.push(format!("REM {}", hm(rem)));
        }
        p.push(format!("Awake {}", hm(wake)));
        p
    } else {
        let mut p = vec![format!("Awake {}", hm(wake)), format!("NREM {}", hm(nrem))];
        if rem > 0.0 {
            p.push(format!("REM {}", hm(rem)));
        }
        p
    };
    // `sub` = the night's date + bedtime window; efficiency rides in `hi` so the card
    // can render it as a prominent, quality-coloured score of its own. The date matters
    // because the "last night" header is static: when the newest night isn't literally
    // last night (the ring missed a night), this is what tells you which night it is.
    let md = fmt_month_day(&night_date);
    let sub = match (
        start_c.as_deref().and_then(parse_hhmm),
        end_c.as_deref().and_then(parse_hhmm),
    ) {
        (Some(s), Some(e)) => Some(format!("{md} · {} – {}", fmt_12h(s), fmt_12h(e))),
        _ => Some(md),
    };
    Some(Channel {
        id: "sleep".into(),
        status: if is_oura { "real".into() } else { "preview".into() },
        value: Some(tst),
        unit: "min".into(),
        // reused for the hypnogram: `mean` = window-start min, `lo` = min/bar
        mean: start_min,
        lo: per_bar,
        // sleep efficiency (%) — rendered as the card's efficiency badge
        hi: Some(eff),
        series,
        sample_count: bars.len(),
        sub,
        note: parts.join(" · "),
        source: Some(if is_oura { "oura-sleepnet".into() } else { "local".into() }),
        // 4-stage [wake, light, rem, deep] when native; 3-class [wake, nrem, rem, 0] else.
        stage_min: if has_deep {
            vec![wake.round(), light_min.round(), rem.round(), deep_min.round()]
        } else {
            vec![wake.round(), nrem.round(), rem.round(), 0.0]
        },
        ..Default::default()
    })
}

/// Map a device deciseconds timestamp to minutes-since-local-midnight, using the
/// ring's own RTC (`ring_epoch`) so hover labels read the true time of day.
fn ds_to_local_min(conn: &Connection, ds: i64) -> Option<f64> {
    let epoch = ring_epoch(conn)?;
    let unix = (ds as f64 / 10.0 + epoch).round() as i64;
    conn.query_row(
        "SELECT CAST(strftime('%H', ?1, 'unixepoch', 'localtime') AS REAL) * 60 \
         + CAST(strftime('%M', ?1, 'unixepoch', 'localtime') AS REAL)",
        [unix],
        |r| r.get(0),
    )
    .ok()
}

/// The ring's OWN 4-stage hypnogram, straight from the device: `sleep_phase_data`
/// (per-30s awake/light/deep/rem, chunked by `header`) windowed to `bedtime_period`.
/// This is Oura's native staging — not our SleepECG estimate — so it takes the top
/// slot when present. Returns None if the ring hasn't emitted a night yet.
fn read_native_sleep(conn: &Connection, date: Option<&str>) -> Option<Channel> {
    // Anchor to the ring's own RTC so each candidate night can be date-labelled
    // by when it was RECORDED, not when it was synced (see `ring_epoch`).
    let epoch = ring_epoch(conn)?;
    // Pick the "main sleep" for the target day: the LONGEST bedtime_period whose
    // local wake (end) date matches `date`. With date=None (the live view) take the
    // single longest night in the recent buffer. The ring also buffers short daytime
    // dozes/fragments (e.g. a 0.4h nap) that sort *newest* by id, so length — not
    // recency — is what identifies the real night. Each night's phase batch is
    // emitted in the same sync as its bedtime_period (same captured_unix, within
    // seconds), so we key the hypnogram off that timestamp.
    let mut stmt = conn
        .prepare(
            "SELECT captured_unix, decoded_json FROM events \
             WHERE name = 'bedtime_period' AND decoded_json IS NOT NULL \
             ORDER BY captured_unix DESC LIMIT 30",
        )
        .ok()?;
    let cand = stmt
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
        .ok()?;
    // Collect candidate nights with their local wake (end) date, then choose one.
    struct Cand {
        captured: i64,
        json: String,
        dur: f64,
        wake: Option<String>,
    }
    let mut cands: Vec<Cand> = Vec::new();
    for row in cand.flatten() {
        let (cap, js) = row;
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) else {
            continue;
        };
        let dur = v
            .get("duration_hours")
            .and_then(|x| x.as_f64())
            .unwrap_or(0.0);
        let wake = if date.is_some() {
            let end_ds = v.get("bedtime_end_ds").and_then(|x| x.as_i64()).unwrap_or(0);
            let end_unix = (end_ds as f64 / 10.0 + epoch).round() as i64;
            conn.query_row("SELECT date(?1,'unixepoch','localtime')", [end_unix], |r| {
                r.get::<_, String>(0)
            })
            .ok()
        } else {
            None
        };
        cands.push(Cand {
            captured: cap,
            json: js,
            dur,
            wake,
        });
    }
    // A "real night" is ≥ 3h — the ring also logs 0.2–2h daytime dozes/fragments
    // that must never be shown as the night. Each day shows only ITS OWN real night
    // (the one it woke from); a day with none — because the ring wasn't worn
    // overnight (e.g. left charging on the dock) — returns None so the caller shows
    // an honest "no sleep recorded" card rather than borrowing another day's night.
    const MIN_REAL: f64 = 3.0;
    let chosen: Option<&Cand> = match date {
        // Live/no specific day: the single longest recent night.
        None => cands.iter().max_by(|a, b| a.dur.total_cmp(&b.dur)),
        // A specific day: only that day's own real night (waking on D).
        Some(d) => cands
            .iter()
            .filter(|c| c.wake.as_deref() == Some(d) && c.dur >= MIN_REAL)
            .max_by(|a, b| a.dur.total_cmp(&b.dur)),
    };
    let chosen = chosen?;
    let (bt_captured, bt) = (chosen.captured, chosen.json.clone());

    // concat phases across that night's chunks in header order (headers restart at
    // 0 each night, so scope to the chosen night's sync batch by captured_unix).
    let mut stmt = conn
        .prepare(
            "SELECT decoded_json FROM events \
             WHERE name = 'sleep_phase_data' AND decoded_json IS NOT NULL \
               AND captured_unix BETWEEN ?1 - 180 AND ?1 + 180 \
             ORDER BY CAST(json_extract(decoded_json,'$.header') AS INTEGER)",
        )
        .ok()?;
    let rows = stmt.query_map([bt_captured], |r| r.get::<_, String>(0)).ok()?;
    let mut phases: Vec<String> = Vec::new();
    for row in rows {
        let js = row.ok()?;
        let v: serde_json::Value = serde_json::from_str(&js).ok()?;
        if let Some(arr) = v.get("phases").and_then(|p| p.as_array()) {
            phases.extend(arr.iter().filter_map(|p| p.as_str().map(str::to_string)));
        }
    }
    if phases.len() < 10 {
        return None;
    }

    let btv: serde_json::Value = serde_json::from_str(&bt).ok()?;
    let start_ds = btv.get("bedtime_start_ds")?.as_i64()?;
    let end_ds = btv.get("bedtime_end_ds")?.as_i64()?;
    let tib_min = btv
        .get("duration_hours")
        .and_then(|x| x.as_f64())
        .map(|h| h * 60.0)
        .unwrap_or((end_ds - start_ds) as f64 / 600.0);

    let n = phases.len();
    let per_epoch = tib_min / n as f64;
    // stage codes: 0 awake · 1 light · 2 rem · 3 deep (matches the UI's HYP_* maps)
    let code = |s: &str| -> usize {
        match s {
            "awake" => 0,
            "rem" => 2,
            "deep" => 3,
            _ => 1,
        }
    };
    let mut mins = [0.0f64; 4];
    for p in &phases {
        mins[code(p)] += per_epoch;
    }
    let tst = mins[1] + mins[2] + mins[3];
    let eff = if tib_min > 0.0 { 100.0 * tst / tib_min } else { 0.0 };

    // majority-downsample the per-epoch codes to ~48 bars for the card
    let nbars = 48usize;
    let codes: Vec<usize> = phases.iter().map(|p| code(p)).collect();
    let bars: Vec<f64> = (0..nbars)
        .map(|i| {
            let a = i * n / nbars;
            let b = (((i + 1) * n / nbars).max(a + 1)).min(n);
            let mut cnt = [0usize; 4];
            for &c in &codes[a..b] {
                cnt[c] += 1;
            }
            let mut best = 1usize;
            for k in 0..4 {
                if cnt[k] > cnt[best] {
                    best = k;
                }
            }
            best as f64
        })
        .collect();

    let start_min = ds_to_local_min(conn, start_ds);
    let end_min = ds_to_local_min(conn, end_ds);
    let per_bar = tib_min / nbars as f64;
    let hm = |m: f64| -> String {
        let h = (m / 60.0) as i64;
        let mn = (m % 60.0).round() as i64;
        if h > 0 {
            format!("{h}h{mn:02}m")
        } else {
            format!("{mn}m")
        }
    };
    // `sub` = the bedtime window; efficiency rides in `hi` for the badge (see
    // read_sleep_night for the rationale).
    let sub = match (start_min, end_min) {
        (Some(s), Some(e)) => Some(format!("{} – {}", fmt_12h(s), fmt_12h(e))),
        _ => None,
    };
    Some(Channel {
        id: "sleep".into(),
        status: "real".into(),
        value: Some(tst.round()),
        unit: "min".into(),
        mean: start_min,
        lo: Some(per_bar),
        hi: Some(eff),
        series: bars,
        sample_count: n,
        sub,
        note: format!(
            "Deep {} · Light {} · REM {} · Awake {}",
            hm(mins[3]),
            hm(mins[1]),
            hm(mins[2]),
            hm(mins[0])
        ),
        source: Some("oura-native".into()),
        // [wake, light, rem, deep] exact minutes for the card's stage legend
        stage_min: vec![
            mins[0].round(),
            mins[1].round(),
            mins[2].round(),
            mins[3].round(),
        ],
        ..Default::default()
    })
}

/// Parse "HH:MM" into minutes since midnight.
fn parse_hhmm(s: &str) -> Option<f64> {
    let (h, m) = s.split_once(':')?;
    Some(h.trim().parse::<f64>().ok()? * 60.0 + m.trim().parse::<f64>().ok()?)
}

/// `Option<(start, step)>` → the pair of `Option`s the Channel fields want.
fn split_time_range(r: Option<(f64, f64)>) -> (Option<f64>, Option<f64>) {
    match r {
        Some((s, step)) if step > 0.0 => (Some(s), Some(step)),
        _ => (None, None),
    }
}

/// Wallclock range of an event stream → (start-of-day minutes, minutes per
/// downsampled point) for per-point clock times on hover of its sparkline.
/// `ring_timestamp` is device deciseconds; anchor it to the newest event's
/// `captured_unix`. `n_points` is the length of the (downsampled) series the
/// times are being spread across.
fn stream_time_range(
    conn: &Connection,
    name: &str,
    n_points: usize,
    win: Option<(i64, i64)>,
) -> Option<(f64, f64)> {
    let epoch = ring_epoch(conn)?;
    let win_sql = match win {
        Some((s, e)) => format!(" AND ring_timestamp >= {s} AND ring_timestamp < {e}"),
        None => String::new(),
    };
    let (min_ts, max_ts): (i64, i64) = conn
        .query_row(
            &format!(
                "SELECT MIN(ring_timestamp), MAX(ring_timestamp) FROM events \
                 WHERE name = ?1{win_sql}"
            ),
            [name],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .ok()?;
    let start_unix = (min_ts as f64 / 10.0 + epoch).round() as i64;
    let end_unix = (max_ts as f64 / 10.0 + epoch).round() as i64;
    let start_min: f64 = conn
        .query_row(
            "SELECT CAST(strftime('%H', ?1, 'unixepoch', 'localtime') AS REAL) * 60 \
             + CAST(strftime('%M', ?1, 'unixepoch', 'localtime') AS REAL)",
            [start_unix],
            |r| r.get(0),
        )
        .ok()?;
    let span_min = ((end_unix - start_unix) as f64 / 60.0).max(1.0);
    Some((start_min, span_min / n_points.max(1) as f64))
}

/// A detected bout of sustained movement — a fast MET-threshold detector. Oura's
/// an on-device activity model (`automatic_activity_detection`) now also runs
/// locally via interpret/run_activity.py → the `activity_sessions` table (solid
/// detection + a workout probability; sport-type is weak without the gait input).
/// This heuristic stays as a lightweight always-on complement.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActivitySession {
    pub date: String,
    /// minutes since local midnight
    pub start_min: f64,
    pub end_min: f64,
    pub duration_min: i64,
    pub avg_met: f64,
    pub peak_met: f64,
    pub active_kcal: f64,
    /// AAD model's **is_workout** probability (0..1) — the trustworthy detection
    /// signal — when this session came from Oura's model; None for the heuristic.
    pub workout_prob: Option<f64>,
    /// The model's tentative sport-type guess. Weak: the gait/`stepmotion` input is
    /// stubbed, so this is a low-confidence hint, never paired with `workout_prob`.
    /// None for the MET heuristic (which never guesses a sport).
    pub sport: Option<String>,
    /// "aad" = Oura's Automatic Activity Detection model (`activity_sessions`
    /// table); "met" = our lightweight MET-stream heuristic fallback.
    pub source: String,
}

/// Average MET, peak MET, and active-kcal over a wall-clock window `[start_unix,
/// end_unix)`, computed from *our own* per-minute MET stream (the same source as
/// the Movement sparkline). Used to give AAD sessions intensity numbers that stay
/// consistent with the rest of the card, rather than the model's internal MET.
fn met_window_stats(
    conn: &Connection,
    start_unix: i64,
    end_unix: i64,
    weight_kg: f64,
) -> (f64, f64, f64) {
    let epoch = match ring_epoch(conn) {
        Some(e) => e,
        None => return (0.0, 0.0, 0.0),
    };
    let mut samples: Vec<f64> = Vec::new();
    let mut stmt = match conn.prepare(
        "SELECT ring_timestamp, decoded_json FROM events \
         WHERE name = 'activity_information' AND decoded_json IS NOT NULL ORDER BY id",
    ) {
        Ok(s) => s,
        Err(_) => return (0.0, 0.0, 0.0),
    };
    let rows = match stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))) {
        Ok(r) => r,
        Err(_) => return (0.0, 0.0, 0.0),
    };
    for row in rows.flatten() {
        let (rts, js) = row;
        let base_wall = (rts as f64 / 10.0 + epoch).round() as i64;
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) {
            if let Some(arr) = v.get("met").and_then(|m| m.as_array()) {
                for (i, m) in arr.iter().filter_map(|x| x.as_f64()).enumerate() {
                    let wall = base_wall + i as i64 * 60; // MET samples are ~1 min apart
                    if wall >= start_unix && wall < end_unix {
                        samples.push(m);
                    }
                }
            }
        }
    }
    let avg = mean(&samples).unwrap_or(0.0);
    let peak = max(&samples).unwrap_or(0.0);
    let kcal: f64 = samples
        .iter()
        .map(|&m| ((m - 1.0).max(0.0) * 3.5 * weight_kg) / 200.0)
        .sum();
    (avg, peak, kcal)
}

/// Read Oura's real **Automatic Activity Detection** output — the `activity_sessions`
/// table written by `interpret/run_activity.py`.
/// The model gives us the *window* + the *is_workout probability* + a tentative sport
/// guess; we recompute intensity/kcal from our own MET stream over that window so the
/// numbers line up with the sparkline. Returns None if the table is absent or empty
/// (→ caller falls back to the MET heuristic).
fn read_aad_sessions(
    derived_db: &std::path::Path,
    raw: &Connection,
    weight_kg: f64,
    limit: usize,
    date: Option<&str>,
) -> Option<Vec<ActivitySession>> {
    let dconn = rusqlite::Connection::open(derived_db).ok()?;
    // Optionally scope to one day; `limit` is a usize (safe to inline).
    let sql = format!(
        "SELECT date, start_ts, end_ts, sport, met_mean, confidence \
         FROM activity_sessions WHERE 1=1{} ORDER BY start_ts DESC LIMIT {}",
        if date.is_some() { " AND date = ?1" } else { "" },
        limit
    );
    let mut stmt = dconn.prepare(&sql).ok()?;
    let row_map = |r: &rusqlite::Row| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, i64>(1)?,
            r.get::<_, i64>(2)?,
            r.get::<_, Option<String>>(3)?,
            r.get::<_, Option<f64>>(4)?,
            r.get::<_, Option<f64>>(5)?,
        ))
    };
    let collected: Vec<(String, i64, i64, Option<String>, Option<f64>, Option<f64>)> =
        match date {
            Some(d) => stmt.query_map([d], row_map).ok()?.flatten().collect(),
            None => stmt.query_map([], row_map).ok()?.flatten().collect(),
        };
    let mut out = Vec::new();
    for row in collected {
        let (date, start_ts, end_ts, sport, met_mean, conf) = row;
        let (start_min, end_min): (f64, f64) = raw
            .query_row(
                "SELECT CAST(strftime('%H',?1,'unixepoch','localtime') AS REAL)*60 + CAST(strftime('%M',?1,'unixepoch','localtime') AS REAL), \
                 CAST(strftime('%H',?2,'unixepoch','localtime') AS REAL)*60 + CAST(strftime('%M',?2,'unixepoch','localtime') AS REAL)",
                rusqlite::params![start_ts, end_ts],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap_or((0.0, 0.0));
        let (avg, peak, kcal) = met_window_stats(raw, start_ts, end_ts, weight_kg);
        let avg_met = if avg > 0.0 { avg } else { met_mean.unwrap_or(0.0) };
        out.push(ActivitySession {
            date,
            start_min,
            end_min,
            duration_min: (end_ts - start_ts) / 60,
            avg_met: (avg_met * 10.0).round() / 10.0,
            peak_met: (peak * 10.0).round() / 10.0,
            active_kcal: kcal.round(),
            workout_prob: conf,
            sport,
            source: "aad".into(),
        });
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// The activity-sessions the UI shows: prefer Oura's real AAD model output
/// (`activity_sessions` in the derived store); fall back to the always-on MET
/// heuristic when the model hasn't produced any (e.g. before the first sync that
/// runs the interpreters, or a day with no detected workout).
pub fn activity_sessions(
    raw: &Connection,
    derived_db: &std::path::Path,
    weight_kg: f64,
    limit: usize,
    date: Option<&str>,
) -> Result<Vec<ActivitySession>> {
    // Default (no explicit day) = the latest day with data, so the Home "Today"
    // view shows only today's sessions — consistent with home_channels' windowing.
    let latest = latest_local_date(raw);
    let target: Option<&str> = date.or(latest.as_deref());
    if let Some(aad) = read_aad_sessions(derived_db, raw, weight_kg, limit, target) {
        return Ok(aad);
    }
    // MET-heuristic fallback: detect over the buffer, then scope to the day.
    let mut heur = detect_sessions(raw, weight_kg, limit.saturating_mul(4).max(limit))?;
    if let Some(d) = target {
        heur.retain(|s| s.date == d);
    }
    heur.truncate(limit);
    Ok(heur)
}

/// Detect sustained-movement sessions from the per-minute MET stream: contiguous
/// runs of median-MET ≥ 2.5 (walking+), bridging gaps ≤ 3 min, lasting ≥ 8 min.
/// Returns the most recent `limit`, newest first. Honest heuristic on real data.
pub fn detect_sessions(conn: &Connection, weight_kg: f64, limit: usize) -> Result<Vec<ActivitySession>> {
    // anchor device-clock → wall time (same trick as elsewhere)
    let epoch =
        ring_epoch(conn).ok_or_else(|| "no events to anchor the ring clock".to_string())?;

    // per-minute (absolute unix-minute) MET samples
    use std::collections::BTreeMap;
    let mut per_min: BTreeMap<i64, Vec<f64>> = BTreeMap::new();
    let mut stmt = conn
        .prepare(
            "SELECT ring_timestamp, decoded_json FROM events \
             WHERE name = 'activity_information' AND decoded_json IS NOT NULL ORDER BY id",
        )
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
        .map_err(|e| e.to_string())?;
    for row in rows.flatten() {
        let (rts, js) = row;
        let wall = (rts as f64 / 10.0 + epoch).round() as i64;
        let minute = wall / 60;
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) {
            if let Some(arr) = v.get("met").and_then(|m| m.as_array()) {
                for (i, m) in arr.iter().filter_map(|x| x.as_f64()).enumerate() {
                    per_min.entry(minute + i as i64).or_default().push(m);
                }
            }
        }
    }

    // minutes whose median MET marks them "active"
    let active: Vec<i64> = per_min
        .iter()
        .filter(|(_, v)| median(v).map(|md| md >= 2.5).unwrap_or(false))
        .map(|(&k, _)| k)
        .collect();

    // group into contiguous runs (gap ≤ 3 min)
    let mut sessions: Vec<ActivitySession> = Vec::new();
    let mut run: Vec<i64> = Vec::new();
    let flush = |run: &Vec<i64>, out: &mut Vec<ActivitySession>| {
        if run.len() < 8 {
            return;
        }
        let (a, b) = (run[0], *run.last().unwrap());
        let samples: Vec<f64> = run
            .iter()
            .flat_map(|m| per_min.get(m).cloned().unwrap_or_default())
            .collect();
        let avg = mean(&samples).unwrap_or(0.0);
        let peak = max(&samples).unwrap_or(0.0);
        let kcal: f64 = samples
            .iter()
            .map(|&m| ((m - 1.0).max(0.0) * 3.5 * weight_kg) / 200.0)
            .sum();
        let start_unix = a * 60;
        let end_unix = (b + 1) * 60;
        let fields: Option<(String, f64, f64)> = conn
            .query_row(
                "SELECT date(?1,'unixepoch','localtime'), \
                 CAST(strftime('%H',?1,'unixepoch','localtime') AS REAL)*60 + CAST(strftime('%M',?1,'unixepoch','localtime') AS REAL), \
                 CAST(strftime('%H',?2,'unixepoch','localtime') AS REAL)*60 + CAST(strftime('%M',?2,'unixepoch','localtime') AS REAL)",
                rusqlite::params![start_unix, end_unix],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .ok();
        if let Some((date, start_min, end_min)) = fields {
            out.push(ActivitySession {
                date,
                start_min,
                end_min,
                duration_min: b - a + 1,
                avg_met: (avg * 10.0).round() / 10.0,
                peak_met: (peak * 10.0).round() / 10.0,
                active_kcal: kcal.round(),
                workout_prob: None,
                sport: None,
                source: "met".into(),
            });
        }
    };
    for m in active {
        if run.last().map(|&last| m - last <= 3).unwrap_or(true) {
            run.push(m);
        } else {
            flush(&run, &mut sessions);
            run = vec![m];
        }
    }
    flush(&run, &mut sessions);

    sessions.reverse(); // newest first
    sessions.truncate(limit);
    Ok(sessions)
}

/// Active kcal above rest per LOCAL hour (0..23) for today, from the MET stream,
/// plus today's active total. Each ~per-minute MET sample burns ((MET−1)·3.5·kg)/200
/// kcal above rest; we bucket it by the local hour it happened, scoped to today —
/// so the card shows *when* you were active, and "active" is genuinely today's, not
/// the whole sync buffer.
fn intraday_active_kcal(
    conn: &Connection,
    weight_kg: f64,
    target: Option<&str>,
) -> (Vec<f64>, f64) {
    let mut hours = vec![0.0f64; 24];
    let today: String = match target
        .map(|s| s.to_string())
        .or_else(|| latest_local_date(conn))
    {
        Some(d) => d,
        None => return (hours, 0.0),
    };
    let epoch = match ring_epoch(conn) {
        Some(e) => e,
        None => return (hours, 0.0),
    };
    let mut stmt = match conn.prepare(
        "SELECT ring_timestamp, decoded_json FROM events \
         WHERE name = 'activity_information' AND decoded_json IS NOT NULL ORDER BY id",
    ) {
        Ok(s) => s,
        Err(_) => return (hours, 0.0),
    };
    let rows = match stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))) {
        Ok(r) => r,
        Err(_) => return (hours, 0.0),
    };
    for (rts, js) in rows.flatten() {
        let wall_unix = (rts as f64 / 10.0 + epoch).round() as i64;
        let (date, hour): (String, i64) = match conn.query_row(
            "SELECT date(?1,'unixepoch','localtime'), \
             CAST(strftime('%H', ?1, 'unixepoch', 'localtime') AS INTEGER)",
            [wall_unix],
            |r| Ok((r.get(0)?, r.get(1)?)),
        ) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if date != today {
            continue;
        }
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) else {
            continue;
        };
        if let Some(arr) = v.get("met").and_then(|m| m.as_array()) {
            let kcal: f64 = arr
                .iter()
                .filter_map(|x| x.as_f64())
                .map(|m| ((m - 1.0).max(0.0) * 3.5 * weight_kg) / 200.0)
                .sum();
            hours[hour.clamp(0, 23) as usize] += kcal;
        }
    }
    let total: f64 = hours.iter().sum();
    (hours, total)
}

/// Jackson NES anthropometric VO2max (ecore `0x12bc78`).
fn vo2max_jackson(age: f64, female: bool, weight_kg: f64) -> f64 {
    let weight_lb = weight_kg * 2.20462;
    (79.9 - 0.39 * age - 13.7 * (female as i32 as f64) - 0.127 * weight_lb).max(0.0)
}

/// ACSM-style VO2max fitness zones (upper bounds: very-poor|poor|fair|good|
/// excellent — above the last is "superior"), for a young adult and shifted for
/// sex + age. Returns (zone bounds, the value's category).
fn vo2max_rating(v: f64, age: f64, female: bool) -> (Vec<f64>, String) {
    let base: [f64; 5] = if female {
        [27.0, 32.0, 37.0, 42.0, 48.0]
    } else {
        [32.0, 38.0, 44.0, 50.0, 56.0]
    };
    let adj = (age - 27.0).max(0.0) * 0.3; // norms drift down ~0.3/yr past ~27
    let zones: Vec<f64> = base.iter().map(|b| (b - adj).max(0.0)).collect();
    let cats = ["Very poor", "Poor", "Fair", "Good", "Excellent", "Superior"];
    let idx = zones.iter().position(|&z| v <= z).unwrap_or(5);
    (zones, cats[idx].to_string())
}

/// Fraction of the local day elapsed for `date` (YYYY-MM-DD, local time). Returns
/// 1.0 for any completed (past) day; for the current local day, elapsed-seconds/86400.
/// Lets the Calories card pro-rate the full-day BMR into "resting burned SO FAR
/// today" — otherwise a whole day's basal burn shows up at, e.g., 1 AM.
fn day_elapsed_fraction(conn: &Connection, date: &str) -> f64 {
    let res: rusqlite::Result<(String, i64)> = conn.query_row(
        "SELECT date('now','localtime'), \
         CAST(strftime('%H','now','localtime') AS INTEGER) * 3600 \
         + CAST(strftime('%M','now','localtime') AS INTEGER) * 60 \
         + CAST(strftime('%S','now','localtime') AS INTEGER)",
        [],
        |r| Ok((r.get(0)?, r.get(1)?)),
    );
    match res {
        Ok((today, secs)) if today == date => (secs as f64 / 86_400.0).clamp(0.0, 1.0),
        _ => 1.0,
    }
}

/// Mifflin–St Jeor basal metabolic rate, kcal/day. Uses HEIGHT (which Schofield
/// ignores) and is the most accurate general-population predictive equation, so it's
/// our default. NOTE: Oura's own `energy_expenditure` model uses Schofield — Mifflin
/// is chosen here for accuracy over strict Oura-parity. (With a body-fat % you'd
/// enter, Katch–McArdle would beat both; the ring can't measure body composition.)
fn bmr_mifflin(age: f64, female: bool, weight_kg: f64, height_cm: f64) -> f64 {
    let base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age;
    (base + if female { -161.0 } else { 5.0 }).max(0.0)
}

/// Schofield basal metabolic rate, kcal/day (ecore `0x10d7e4`). Kept as the
/// Oura-parity alternative to [`bmr_mifflin`] (Oura's energy model uses this).
#[allow(dead_code)]
fn bmr_schofield(age: f64, female: bool, weight_kg: f64) -> f64 {
    let bands: &[(f64, f64, f64)] = if female {
        &[
            (3.0, 58.317, -31.1),
            (10.0, 20.315, 485.9),
            (18.0, 13.384, 692.6),
            (30.0, 14.818, 486.6),
            (60.0, 8.126, 845.6),
            (f64::INFINITY, 9.082, 658.5),
        ]
    } else {
        &[
            (3.0, 59.512, -30.4),
            (10.0, 22.706, 504.3),
            (18.0, 17.686, 658.2),
            (30.0, 15.057, 692.2),
            (60.0, 11.472, 873.1),
            (f64::INFINITY, 11.711, 587.7),
        ]
    };
    let &(_, s, i) = bands
        .iter()
        .find(|&&(hi, _, _)| age < hi)
        .unwrap_or(bands.last().unwrap());
    s * weight_kg + i
}

// ---- json array collection ----------------------------------------------

/// Pull every number out of `field` (an array) across all decoded events named
/// `name`, in insertion order (≈ ring-time order within a sync).
fn collect_array(
    conn: &Connection,
    name: &str,
    field: &str,
    win: Option<(i64, i64)>,
) -> Result<Vec<f64>> {
    // Optional ring-timestamp day window (bounds are i64s → safe to inline).
    let win_sql = match win {
        Some((s, e)) => format!(" AND ring_timestamp >= {s} AND ring_timestamp < {e}"),
        None => String::new(),
    };
    let mut stmt = conn
        .prepare(&format!(
            "SELECT decoded_json FROM events \
             WHERE name = ?1 AND decoded_json IS NOT NULL{win_sql} ORDER BY id"
        ))
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([name], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?;

    let mut out = Vec::new();
    for row in rows {
        let js = row.map_err(|e| e.to_string())?;
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) else {
            continue;
        };
        match v.get(field) {
            Some(serde_json::Value::Array(arr)) => {
                out.extend(arr.iter().filter_map(|n| n.as_f64()));
            }
            Some(other) => {
                if let Some(f) = other.as_f64() {
                    out.push(f);
                }
            }
            None => {}
        }
    }
    Ok(out)
}

/// HR (bpm) from the green-LED IBI stream, keeping only beats whose per-beat
/// `quality` flag is ≥ `min_quality` (0–3). Good quality ⇒ solid skin contact ⇒
/// worn, so this excludes non-worn / charger time. HR = 60000 / IBI(ms).
fn quality_hr(conn: &Connection, min_quality: i64, win: Option<(i64, i64)>) -> Result<Vec<f64>> {
    let win_sql = match win {
        Some((s, e)) => format!(" AND ring_timestamp >= {s} AND ring_timestamp < {e}"),
        None => String::new(),
    };
    let mut stmt = conn
        .prepare(&format!(
            "SELECT decoded_json FROM events \
             WHERE name = 'green_ibi_quality_event' AND decoded_json IS NOT NULL{win_sql} ORDER BY id"
        ))
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?;

    let mut out = Vec::new();
    for row in rows {
        let js = row.map_err(|e| e.to_string())?;
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&js) else {
            continue;
        };
        let ibi = v.get("ibi_ms").and_then(|x| x.as_array());
        let qual = v.get("quality").and_then(|x| x.as_array());
        if let (Some(ibi), Some(qual)) = (ibi, qual) {
            for i in 0..ibi.len().min(qual.len()) {
                let q = qual[i].as_i64().unwrap_or(0);
                let ms = ibi[i].as_f64().unwrap_or(0.0);
                if q >= min_quality && ms > 0.0 {
                    let hr = 60000.0 / ms;
                    if (30.0..=200.0).contains(&hr) {
                        out.push(hr);
                    }
                }
            }
        }
    }
    Ok(out)
}

// ---- tiny stats ----------------------------------------------------------

fn sorted(v: &[f64]) -> Vec<f64> {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    s
}
fn median(v: &[f64]) -> Option<f64> {
    if v.is_empty() {
        return None;
    }
    let s = sorted(v);
    let n = s.len();
    Some(if n % 2 == 1 {
        s[n / 2]
    } else {
        (s[n / 2 - 1] + s[n / 2]) / 2.0
    })
}
fn percentile(v: &[f64], p: f64) -> Option<f64> {
    if v.is_empty() {
        return None;
    }
    let s = sorted(v);
    let idx = ((p / 100.0) * (s.len() - 1) as f64).round() as usize;
    Some(s[idx.min(s.len() - 1)])
}
fn mean(v: &[f64]) -> Option<f64> {
    if v.is_empty() {
        return None;
    }
    Some(v.iter().sum::<f64>() / v.len() as f64)
}
fn c_to_f(c: f64) -> f64 {
    c * 9.0 / 5.0 + 32.0
}
/// Minutes-since-local-midnight → "h:MM AM/PM" (12-hour). The minutes are already
/// in the laptop's timezone (computed via SQLite localtime), so this is display-only.
fn fmt_12h(min: f64) -> String {
    let t = (((min as i64) % 1440) + 1440) % 1440;
    let (h24, m) = (t / 60, t % 60);
    let ap = if h24 < 12 { "AM" } else { "PM" };
    let h12 = match h24 % 12 {
        0 => 12,
        h => h,
    };
    format!("{h12}:{m:02} {ap}")
}
/// "2026-08-11" → "Aug 11" (short, unambiguous night label). Falls back to the raw
/// string if it isn't a well-formed ISO date.
fn fmt_month_day(iso: &str) -> String {
    let parts: Vec<&str> = iso.split('-').collect();
    if parts.len() == 3 {
        if let Ok(mo) = parts[1].parse::<usize>() {
            const M: [&str; 12] = [
                "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
            ];
            if (1..=12).contains(&mo) {
                let day = parts[2].trim_start_matches('0');
                return format!("{} {}", M[mo - 1], day);
            }
        }
    }
    iso.to_string()
}
fn min(v: &[f64]) -> Option<f64> {
    v.iter().cloned().fold(None, |acc, x| {
        Some(acc.map_or(x, |a: f64| a.min(x)))
    })
}
fn max(v: &[f64]) -> Option<f64> {
    v.iter().cloned().fold(None, |acc, x| {
        Some(acc.map_or(x, |a: f64| a.max(x)))
    })
}

/// Like `downsample` but keeps each bucket's PEAK — so brief bursts (a sprint's
/// MET spike) stay visible instead of being averaged away. For spiky activity
/// data where the maximum is the point, not the mean.
fn downsample_peak(v: &[f64], target: usize) -> Vec<f64> {
    if v.len() <= target || target == 0 {
        return v.to_vec();
    }
    (0..target)
        .map(|i| {
            let start = i * v.len() / target;
            let end = (((i + 1) * v.len() / target).max(start + 1)).min(v.len());
            v[start..end].iter().cloned().fold(f64::MIN, f64::max)
        })
        .collect()
}

/// Bucket-average down to at most `target` points, preserving overall shape.
fn downsample(v: &[f64], target: usize) -> Vec<f64> {
    if v.len() <= target || target == 0 {
        return v.to_vec();
    }
    (0..target)
        .map(|i| {
            let start = i * v.len() / target;
            let end = (((i + 1) * v.len() / target).max(start + 1)).min(v.len());
            let slice = &v[start..end];
            slice.iter().sum::<f64>() / slice.len() as f64
        })
        .collect()
}
