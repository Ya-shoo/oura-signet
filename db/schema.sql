-- oura-local derived store (db/metrics.db) — schema. SQLite.
-- Applied idempotently (every statement is IF NOT EXISTS) on each connect, so
-- the tools work with zero setup.
--
-- Scope today: the `labels` table (GOAL 4 — self-labeling). The `daily_metrics`
-- and `baselines` tables are specified in SPEC.md "Data model" and belong here
-- too, but they're added when build_daily.py / baselines.py land (GOAL 2) — kept
-- out for now so their column shape isn't frozen prematurely.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- My own ground-truth labels — what I did / how I felt — time-aligned (unix
-- seconds, UTC) to the raw ring stream, so any window of ring data joins to
-- whatever label overlaps it. This is the private training set that unblocks
-- finger-worn workout detection later. Full design: LABELING.md.
CREATE TABLE IF NOT EXISTS labels (
  id            INTEGER PRIMARY KEY,
  start_ts      INTEGER NOT NULL,                 -- unix sec (UTC): window start / point-in-time
  end_ts        INTEGER,                          -- unix sec (UTC): NULL = a point-in-time note
  kind          TEXT    NOT NULL DEFAULT 'note'
                  CHECK (kind IN ('activity','context','symptom','note','correction')),
  category      TEXT,                             -- 'cycling'|'run'|'lift'|'coffee'|'alcohol'|'sick'|'stress'|…
  text          TEXT,                             -- the raw thing I typed / said
  tags          TEXT    NOT NULL DEFAULT '[]',    -- JSON array, freeform
  intensity     TEXT,                             -- 'easy'|'moderate'|'hard' or '1'..'10'
  source        TEXT    NOT NULL DEFAULT 'typed'
                  CHECK (source IN ('typed','quick-add','question','auto')),
  confidence    REAL    DEFAULT 1.0,              -- 0..1 (human entry = 1.0; LLM parse < 1)
  created_unix  INTEGER NOT NULL
);

-- Time-window joins ("what label overlaps this ring segment?") hit start_ts hard.
CREATE INDEX IF NOT EXISTS idx_labels_start ON labels(start_ts);
CREATE INDEX IF NOT EXISTS idx_labels_kind  ON labels(kind);

-- GOAL 2 — per-day metric history. One robust row per local date, written by
-- interpret/build_daily.py (backfill from oura.db) and app persist_daily (live).
-- Values match app metrics.rs formulas: rhr=10th-pct quality HR, hrv=median rMSSD,
-- temp=median skin temp, spo2=median, met_avg=mean, resp=RSA breaths/min. This is
-- the substrate baselines trend over.
CREATE TABLE IF NOT EXISTS daily_metrics (
  date          TEXT PRIMARY KEY,   -- local date (YYYY-MM-DD)
  rhr           REAL,               -- resting HR, bpm
  hrv           REAL,               -- overnight rMSSD, ms
  temp_c        REAL,               -- skin temp median, °C
  temp_f        REAL,               -- same, °F (UI convenience)
  spo2          REAL,               -- SpO₂ median, %
  resp          REAL,               -- respiratory rate, breaths/min
  met_avg       REAL,               -- mean MET
  sleep_min     REAL,               -- total sleep, minutes
  sleep_eff     REAL,               -- sleep efficiency, %
  vo2max        REAL,
  updated_unix  INTEGER
);

-- GOAL 2 — personal baselines, one row per metric, written by interpret/baselines.py.
-- `baseline` is Oura's centered-Gaussian-weighted value (std=N/2.5) over a 5–21 day
-- buffer; `median`+`mad` are the robust pair for deviation z=(x−median)/(1.4826·mad).
-- Faithful to Oura's daily_short_term_baselines model. No row until ≥5 days exist.
CREATE TABLE IF NOT EXISTS baselines (
  metric        TEXT PRIMARY KEY,   -- rhr | hrv | temp_c | spo2 | resp | met_avg | sleep_min | sleep_eff
  baseline      REAL,               -- centered-Gaussian-weighted baseline
  median        REAL,               -- robust median over the window
  mad           REAL,               -- median absolute deviation (robust spread)
  n_days        INTEGER,            -- observations in the window (5..21)
  window_days   INTEGER,            -- window cap (21)
  method        TEXT,               -- 'centered_gaussian_std_n_2.5'
  window_start  TEXT,
  window_end    TEXT,
  updated_unix  INTEGER
);
