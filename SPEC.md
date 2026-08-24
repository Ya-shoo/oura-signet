# Open-Oura Local Health Stack — Spec & Build Doc

> **Goal:** Own my Oura ring's data end-to-end — local, private, radio-silent by default,
> no Oura app and no cloud. Read the ring over BLE,
> store raw signals locally, run open-source interpreters over them, and visualize
> trends + baselines with a UI I control.

**Status:** v0.3 — **Phase 0 acquisition DONE.** Ring owned app-free (our key), HR/SpO2 enabled, `oura sync`→SQLite validated, raw schema confirmed (`interpret/introspect.py`). Remaining Phase 0: wear → first real sync; verify flight-mode `0x26`.
**Owner:** personal project
**Hardware target:** Oura Ring **Gen 3 Horizon** (7-day on-ring storage; `open_oura`-confirmed) — Gen 4 acceptable (same protocol, 7-day storage).

---

## Part 1 — Requirements (v1)

Legend
- **Tier** — where the data comes from: **T1** = raw/on-ring (free, local, no models); **DIY** = compute it myself from T1 signals with open tools; **EST** = rough open-source estimate.
- **Effort** — S / M / L once the pipeline exists.
- **Phase** — when it lands (see roadmap in Part 3).

### Must-have (my stated priorities)

| # | Requirement | Tier | How | Effort | Phase |
|---|---|---|---|---|---|
| 1 | **Heart rate** — live, resting (lowest overnight), daily avg/min/max | T1 | `open_oura` live-HR + IBI→BPM; resting HR = nightly minimum | S | 1 |
| 2 | **Step count** — daily steps + activity intensity | T1 | on-ring MET bins + step counts | S | 1 |
| 3 | **Trends** for everything above | DIY | time-series in DB → charts (Grafana/Datasette) | S | 1 |
| 4 | **Baselines over time + how they evolve** | **DONE** | `interpret/build_daily.py` → `daily_metrics` history; `interpret/baselines.py` → `baselines` table using a **centered-Gaussian-weighted mean** (std=N/2.5, over a 5–21 day buffer) + robust median/MAD; wired into `metrics.rs annotate_baselines` (robust z = (today−median)/(1.4826·MAD)). Warms up at ≥5 days. | M | 1 |
| 5 | **Sleep** — stages (deep/light/REM/awake), duration, efficiency, latency, timing | **DONE** | 4-stage staging runs fully offline: the ring emits its **own native staging** (`sleep_phase_data`) over BLE (used first), with an independent **SleepECG + Cole-Kripke** hypnogram (`interpret/sleep_stage.py`) as the fallback. Efficiency/latency derive from the stages. | M–L | 1–2 |
| 6 | **Sleep score** | DIY | a transparent, documented score computed from the sleep stages | M | 3 |
| 7 | **Temperature** — nightly skin temp + **deviation from baseline** | T1 + DIY | raw temp is T1; deviation = nightly mean − rolling baseline | S | 1 |
| 8 | **Breathing** — respiratory rate | DIY | NeuroKit2 over PPG/IBI (RSA / amplitude modulation) | M | 2 |
| 9 | **VO₂max** — start rough, **evolve to calibrated (walk test)** | EST → **CAL** | Phase 2: non-exercise `15.3 × HRmax/HRrest`. Later: **Rockport 1-mile walk test** (walk time + ending HR + age/sex/weight) or Firstbeat-style HR-vs-pace w/ phone GPS | M → L | 2 → 4 |

### "Comes easily with basic OSS tools" (fold in — low cost, high value)

| # | Requirement | Tier | How | Effort | Phase |
|---|---|---|---|---|---|
| 10 | **HRV** (rMSSD/SDNN/etc., nightly) — the recovery backbone | T1 + DIY | ring gives 5-min RMSSD; NeuroKit2 over raw IBI for full indices | S | 1–2 |
| 11 | **SpO₂** nightly average + **ODI** (desaturation index) | T1 + DIY | raw SpO₂ is T1; ODI = count ≥3–4% dips (simple, standard) | S | 2 |
| 12 | **My own Readiness / Recovery score** | DIY | composite: HRV vs baseline + RHR trend + temp deviation + sleep + prior activity. Oura's Readiness/Sleep/Activity/Stress are computed **on-phone** (`ecore` + on-device PyTorch, not cloud) → reproducible offline; `open_oura` ported several as a reference | M | 3 |
| 13 | **Wear-time / compliance + battery** | T1 | wear-state + battery from ring | S | 1 |

### Visualization & insights (cross-cutting — everything above must be visualized)

| # | Requirement | Tier | How | Effort | Phase |
|---|---|---|---|---|---|
| 14 | **Every metric visualized** — HR, steps, sleep, temp, breathing, HRV, SpO₂, VO₂max, baselines, trends | DIY | start basic (Datasette/Grafana), no metric left as raw SQL | S→L | 1 → ongoing |
| 15 | **My own visualization design** — bespoke UI elements, my design system, my layout | DIY | delivered inside the **local desktop app** (Tauri: Rust core + React); Recharts → visx/D3; owned front to back | L | 3+ |
| 16 | **My own insights layer** — surfaced observations, not just charts ("HRV 1.4σ below baseline 3 nights running") | DIY | rules/z-score engine over `baselines`, later an LLM/MCP over `metrics.db` | M→L | 3+ |
| 17 | **Secure local desktop app (macOS)** — one app to *run the ring* **and** *see everything*: flight-mode on/off, sync/dock, feature toggles, battery/status; + every metric, trend, baseline & insight. **Local-only (no network)**, auth key in the **Keychain** | DIY | **Tauri** — Rust core embeds `open_oura`'s crates (BLE sync, the `0x26` toggle, features, pairing) in-process; React frontend is my design system over `metrics.db`. The vessel #15/#16 live in | L | 3 → ongoing |

**Visualization philosophy:** every metric is visible from day one (even if plain), but the endgame is a
**hand-designed frontend I own** — my own components, my own charts, my own insight cards — not a borrowed
dashboard. Basic tools (Datasette → Grafana) are scaffolding to see data immediately; the real destination is
a **secure, local-only macOS desktop app I own** (see #17) — one surface that both *controls* the ring (flight
mode, sync, features) and *shows* every metric + insight — and it grows continuously past v1.

### Later capabilities — planned, not v1 (see Part 4 for tooling & caveats)
- **Auto workout-type detection** (open HAR over accel+gyro — real tooling exists; ring placement is the catch → Part 4)
- **Calibrated VO₂max** (walk-test protocol replacing the rough estimate → Part 4)
- Illness detection à la Symptom Radar (anomaly detection over RHR + temp + resp rate)
- Vascular / biological age (open PPG "aging-clock" models over raw 250 Hz PPG — research-grade)
- Cycle / ovulation (temperature-shift detection)
- Private "Advisor" (MCP server over my SQLite + my own LLM)
- Native bridges (Apple Health / Health Connect export)
- **Self-labeling + LLM-assisted algorithm loop** — I log what I did (free text, or a few skippable questions at dock time); the app builds a **private labeled dataset** that unblocks workout detection (it's the finger-worn ground truth Part 4.A lacked) and lets an LLM / coding-agent refine or author interpreters. Full design in **[`LABELING.md`](LABELING.md)**.

### Non-negotiable constraints (carried from the decision thread)
- **Radio-silent by default.** Ring lives in flight mode; radio only wakes on the charger, at sync time.
- **No Oura app (app-free key path).** `oura pair` installs *our own* 16-byte key onto a factory-reset ring (opcode `0x24`, no prior auth), replacing the old "extract Oura's key" plan. The Oura app is needed **only if** a used ring is still locked to its previous owner (`not_original_onboarded_device`), for a one-time restricted-mode reset — otherwise never.
- **No cloud, no subscription.** All data stays on my machine.
- **Single source of truth.** One local DB; any number of visual layers on top.
- **Local, secure control surface.** The destination is a **local-only macOS desktop app** (no network calls, ever) that is the single place to arm/disarm flight mode, sync, toggle features, and view all data. The auth key lives in the **macOS Keychain**, not a plaintext file.

---

## Part 2 — What's implementable *now* (and how)

Everything in Phase 1–2 is buildable today with existing tools. The architecture:

```
                          ┌─────────────────────────────────────────┐
   Oura Ring ──BLE──▶     │  open_oura (Rust CLI + Python bench)     │   ACQUISITION
  (flight mode,           │  scan · pair · sync · live-hr · accel    │
   wakes on charger)      └───────────────┬─────────────────────────┘
                                          │ writes
                                          ▼
                          ┌─────────────────────────────────────────┐
                          │  RAW STORE  — open_oura SQLite           │   CANONICAL DATA
                          │  PPG/IBI/accel/gyro/temp/SpO2 +          │   (read-only truth)
                          │  on-ring: hypnogram, MET/steps, HRV      │
                          └───────────────┬─────────────────────────┘
                                          │ read
                                          ▼
                          ┌─────────────────────────────────────────┐
                          │  INTERPRET  — Python jobs                │   OUR GLUE
                          │  NeuroKit2 (HRV, resp) · Walch (sleep)   │
                          │  ODI · VO₂max · baselines · DIY scores   │
                          └───────────────┬─────────────────────────┘
                                          │ writes
                                          ▼
                          ┌─────────────────────────────────────────┐
                          │  METRICS DB  — daily_metrics, baselines  │   DERIVED DATA
                          └───────────────┬─────────────────────────┘
                                          │ query (SQL)
                          ┌───────────────┼───────────────┬─────────┐
                          ▼               ▼               ▼         ▼    VISUALIZE
                     Datasette        Grafana     macOS app (Tauri)  (scaffold →
                   (instant UI)   (borrowed panels)  (bespoke, owned)   destination)
```

### Component-by-component, today

**1. Acquisition — `open_oura`** *(exists; we configure, don't build)*
- One-time key: on a **factory-reset** ring, `oura pair` installs *our own* 16-byte key (opcode `0x24`, no prior auth) and saves it to `oura-<serial>.key` — **no Oura app, no key extraction**. (App only if the used ring is still locked → one-time restricted-mode reset, then `pair`.)
- After a key-only pairing, **enable measurement**: `oura features --enable-hr --enable-spo2` (HR/SpO2 are off until the app — or we — turn them on).
- Daily: `oura --key-file <key> --db oura.db sync` drains the on-ring buffer into SQLite. Also `live-hr`, `latest`, `accel`, `sleep-analyze`, `events`, `redecode`, `sessions` (Oura's own activity model).
- Flight mode: send opcode `0x26` via the Python bench (`tools/oura_protocol.py --include-danger`) to re-arm silence after each sync — **verify radio silence empirically** (unproven upstream).

**2. Raw store — `open_oura`'s SQLite** *(exists)*
- Keep as **read-only source of truth**. We never mutate it; we read from it.
- **Phase-0 task:** introspect the actual schema (table/column names) — the map below is expected shape, to be confirmed.

**3. Interpretation — Python jobs** *(we build; small)*
- `NeuroKit2` → HRV indices + respiratory rate (few function calls).
- Walch `sleep-accel` classifier → independent sleep staging (cross-check the ring's).
- ODI → count SpO₂ desaturations (trivial).
- VO₂max → the HRmax/HRrest formula.
- Baseline engine → rolling mean/SD + z-score per metric (the "how it evolves" requirement).

**4. Metrics DB — derived tables** *(we build)*
- SQLite (simplest) or DuckDB (nice for analytical rollups over the raw SQLite).

**5. Visualization** *(mostly exists; we wire)*
- **Datasette** on day one — instant browsable/queryable web UI over the SQLite file, near-zero setup.
- **Grafana** — borrow Bucket-B dashboard panels (`MaximeMichaud/oura-dashboard`), remap queries to our schema.
- **The desktop app (Tauri + React)** later — the bespoke, fully-owned frontend *and* control surface.

### What lights up in Phase 1 with minimal code
Requirements **1, 2, 3, 4, 7, 10, 13** — i.e. heart rate, steps, temperature + deviation, HRV, plus trends and baselines for all of them — from `oura sync` + a baseline job + Datasette/Grafana. Sleep **timing** (the `bedtime_period` window) lands here too, but sleep **staging (#5)** slips to Phase 1–2 because the hypnogram must be computed locally (not emitted over BLE). **Breathing (8), SpO₂/ODI (11), VO₂max (9)** follow in Phase 2 once NeuroKit2 is wired.

---

## Part 3 — Build doc: what we'd need to build

### Repo layout (proposed)
```
oura-local/
├── SPEC.md                  # this file
├── sync/
│   ├── sync.sh              # wraps: open_oura sync  →  interpret  →  re-arm flight mode
│   └── flight_mode.py       # opcode 0x26 via the protocol bench (with safety guard)
├── interpret/
│   ├── introspect.py        # Phase 0: dump open_oura's real schema
│   ├── hrv.py               # NeuroKit2 → nightly HRV indices
│   ├── respiration.py       # NeuroKit2 → respiratory rate
│   ├── sleep_walch.py       # Walch classifier → staging cross-check
│   ├── spo2_odi.py          # ODI from raw SpO2
│   ├── vo2max.py            # HRmax/HRrest estimate
│   ├── baselines.py         # rolling baseline + z-score engine (all metrics)
│   ├── scores.py            # DIY readiness + sleep scores
│   ├── insights.py          # rules over baselines → insight cards (z-score/streak/slope)
│   └── build_daily.py       # orchestrates the above → daily_metrics + insights
├── db/
│   ├── schema.sql           # derived tables (daily_metrics, baselines)
│   └── metrics.db           # derived store (SQLite/DuckDB)
├── viz/
│   ├── datasette/           # metadata.json + canned queries
│   └── grafana/             # dashboards (remapped from Bucket B)
├── app/                     # the destination: Tauri desktop app (Rust core embeds open_oura + React UI)
├── config.toml              # single config: serial, key ref (Keychain), DB paths, baseline window, units, demographics
├── justfile                 # easy verbs: sync · silence · wake · status · introspect · daily · viz · backup-key
└── README.md
```

### Data model

**Raw (from `open_oura`, read-only — CONFIRMED via `interpret/introspect.py`, Phase 0):**
Not typed tables (the list below was the *assumed* shape and is wrong). open_oura uses **4 generic tables**, and **all physiology lives in `events.decoded_json`, keyed by `name`**:
- `events(id, serial, tag, name, ring_timestamp, body BLOB, decoded_json TEXT, captured_unix)` — one row per history event; `UNIQUE(serial,tag,ring_timestamp,body)` makes sync idempotent. Physiological names: `temp_event` (`temps_c[]`), `hrv_event` (hr+rmssd), `ibi_and_amplitude_event` / `green_ibi_quality_event` (IBI→HR), `spo2_r_pi_event` (R/PI), `sleep_acm_period`, `activity_information`, `bedtime_period`; system names: `debug_data`, `debug_event`, `ble_connection`, `ring_start`.
- `readings(id, serial, kind, value, unit, captured_unix)` — normalized scalars (from `live-hr`/`latest`).
- `device(serial PK, hardware_id, firmware, api_version, mac, updated_unix)`.
- `sync_state(serial PK, next_cursor, last_sync_unix)` — incremental cursor.

→ **Every interpreter reads `events` filtered by `name` and parses `decoded_json`** (there are no `ppg_samples`/`hrv_5min`/etc. tables). Re-run `introspect.py` after the first *worn* sync to enumerate each physiological type's `decoded_json` fields.

**Derived (we build):**
```sql
-- one row per day: the "trends" substrate
daily_metrics(
  date PK,
  resting_hr, avg_hr, max_hr,
  steps, active_minutes,
  total_sleep_min, sleep_eff, latency_min, deep_min, rem_min, light_min, awake_min,
  temp_mean_c, temp_deviation_c,
  resp_rate,
  spo2_mean, odi,
  hrv_night_rmssd, hrv_night_sdnn,
  vo2max_est,
  readiness_diy, sleep_score_diy,
  wear_minutes
)

-- rolling baseline + how each metric evolves
baselines(
  metric, date,
  baseline_mean,          -- e.g. trailing 28-night mean
  baseline_sd,
  z_score,                -- (today − baseline) / sd  ← "how it deviates"
  slope_30d               -- trend direction ← "how it evolves"
)

-- my own ground-truth labels (full design in LABELING.md) — what I did / how I felt,
-- time-aligned to the raw stream so any data window joins to what I was actually doing
labels(
  id, start_ts, end_ts,   -- end_ts NULL = a point-in-time note
  kind,                   -- activity | context | symptom | note | correction
  category, text, tags,   -- e.g. category='cycling', text='rode to work 2–3pm'
  intensity, source,      -- source = typed | question | quick-add | auto
  confidence, created_unix
)
```

### Components to build (the glue — this is the actual work)
1. **`sync.sh`** — dock-triggered: run `open_oura sync`, then `build_daily.py`, then `flight_mode.py` to re-silence. (Sync is on-demand at dock time — flight mode clears only on the charger — so this runs when the ring connects, not on a fixed cron.)
2. **`flight_mode.py`** — send opcode `0x26` with a confirm-and-verify guard (watch for BLE advertisements dropping). This is a "danger"-gated op — handle carefully, test on a backed-up ring.
3. **`introspect.py`** — Phase 0: dump `open_oura`'s real SQLite schema so every job below reads the right columns.
4. **`baselines.py`** — the heart of the "baselines + evolution" ask: rolling mean/SD, z-score, and 30-day slope for every metric. Reused by scores.
5. **Interpreter jobs** — `hrv.py`, `respiration.py`, `sleep_walch.py`, `spo2_odi.py`, `vo2max.py` (Phase 2).
6. **`scores.py`** — DIY readiness + sleep composites (Phase 3), transparent formulas I own.
7. **Viz wiring** — Datasette metadata (Phase 1) → Grafana remap (Phase 1–2) → **the bespoke desktop app** (Tauri, Phase 3+, the destination). Every metric visualized at each stage; charts get progressively more custom (Recharts → visx/D3).
8. **`insights.py` / insight cards** — rules over `baselines` (z-score, streak, slope) → surfaced observations rendered in the frontend (Phase 3); later an MCP+LLM advisor over `metrics.db` (backlog).

### Sleep hypnogram — approach (SleepECG; implemented in `interpret/`)
> Being built in a parallel workstream — the code is the source of truth: `interpret/sleep_stage.py`,
> `interpret/oura_sleep_io.py`, `interpret/sleep_features.py` (full write-up owned by that stream).

**Why not Oura's own model:** Oura's hypnogram comes from an encrypted PyTorch model (SleepNet) whose AES key is
**server-delivered → not decryptable offline** (`open_oura/docs/algorithms/sleepnet.md`). So we don't use it —
and we don't need it.

**What we do instead — SleepECG, zero training (Tier 1, working now):** SleepECG ships GRU classifiers
**pretrained on thousands of MESA PSG records**. We feed them the ring's **reconstructed beat series** (overnight
IBI → heartbeat times) + HRV features + circadian clock + age/sex and get a **per-30-s hypnogram with confidence**,
no training:
- `ws-gru-mesa` → WAKE / SLEEP (2-class)
- `wrn-gru-mesa` → **WAKE / REM / NREM** (3-class, default)

Output → hypnogram CSV + a night summary (TIB, TST, efficiency, latency, WASO, stage minutes) → a `sleep_night`
table; plausibility checks (beat coverage ≥80%, efficiency, REM %, latency, fragmentation) stand in for ground truth.

**Ceiling & Tier-2 improve:** the pretrained models don't split NREM into **light vs deep**. A full 4-stage
(deep/light/REM/wake) hypnogram means **training a custom SleepECG classifier on MESA/SHHS N1/N2/N3 labels** (plus
optional temp/actigraphy features; validate timing vs `bedtime_period`). An *equivalent*, not Oura-identical;
deep-vs-light is the least reliable — but WAKE/REM/NREM works today from the ring alone.

### Ergonomics & configuration (make it pleasant to run)
Lightweight now (config file + CLI verbs), folded into the desktop app later.

- **One config file (`config.toml`):** ring serial, key reference, raw + derived DB paths, baseline window (28), units (°C/°F, metric/imperial), demographics (birth-year/sex/weight for HR/VO₂ math; optional HRmax override), flight-mode auto-arm, insight thresholds. One place to change behavior.
- **Secrets → macOS Keychain:** move the auth key out of the 0600 file into the Keychain; app/CLI read it from there.
- **Task runner (`just`/`make`) — easy verbs:** `sync` (dock→drain→interpret→re-silence) · `silence`/`wake` (flight mode `0x26` / dock) · `status` (battery, last-sync, flight-mode state, event counts, wear-time) · `introspect` · `daily` · `viz` · `backup-key` · `redecode`.
- **Robust `sync.sh`:** on-charger detect + **auto Bluetooth-toggle-on-drop** (via `blueutil`) to defeat the btleplug flakiness we hit in Phase 0; idempotent (cursor); logs each run.
- **Auto-dock sync:** detect ring-on-charger (launchd agent now, the desktop app later) → sync automatically → re-arm flight mode; macOS notification on sync-complete / low-battery / fired insight.
- **Backups:** periodic **encrypted** backup of `oura.db` + the key, so a wipe never loses history.
- **Tunable interpreter params** (in config, not hard-coded): baseline window, z-score thresholds, SpO₂ desaturation cutoff (3%/4%), VO₂max constants, score weightings — tuning is a one-line change.

### The local desktop app (the destination)
One **secure, local-only macOS app** that is both the **control surface** and the **data/insights UI** — replacing the CLI + Datasette/Grafana scaffolding over time. → **Full design: [`DESKTOP_APP_SPEC.md`](DESKTOP_APP_SPEC.md)** (architecture, UX + wireframes, the airplane-mode control, security/safety model).
- **Stack — Tauri.** A Rust core that **embeds `open_oura`'s crates directly** (`oura-link` for BLE sync/live-HR, the `0x26` flight-mode toggle, feature toggles, pairing) — no CLI shell-out — plus a **React/TypeScript frontend** that is my own design system (Recharts → visx/D3). *Why Tauri:* reuses my frontend skills for the UI **and** open_oura's Rust for acquisition, in one small, local, signed binary. (Alternatives: Electron — JS, heavier, must shell out for BLE; SwiftUI — most native but no Rust reuse and not my stack.)
- **Controls:** connect/sync, auto-sync on dock, **flight-mode on/off**, feature toggles (HR/SpO₂/real-steps), battery/wear/status, re-key/pair.
- **Views:** every metric, trends, baselines, sleep, DIY scores, and insight cards — the destination for requirements #14–16.
- **Security posture:** **no network** (offline by design), auth key in the **Keychain**, BLE entitlement only, DB local (FileVault; optional SQLCipher at rest), code-signed/notarized, zero telemetry.

### Roadmap

**Phase 0 — Hardware & plumbing** *(gate for everything)*
- ✅ Build `open_oura` (done: `target/release/oura`); Python bench venv (3.12, done).
- First BLE contact: `scan` → `info` (reads ownership state: `in_factory_reset` vs `not_original_onboarded_device`).
- Own the key **app-free**: on a reset ring, `oura pair` mints our key. If locked → try app-free `factory_reset` (danger op), else one-time Oura-app restricted-mode reset, then `pair`.
- `features --enable-hr --enable-spo2`, then wear + `sync`; run `introspect.py` to dump the real schema.
- Confirm the `0x26` flight-mode command works and **verify radio actually goes silent** (BLE scanner or SDR).
- ✅ Done when: I can dock → `sync` → data in SQLite → re-arm flight mode, fully app-free.

**Phase 1 — Core metrics + trends + baselines** *(covers most must-haves)*
- `build_daily.py` for HR, steps, sleep stages, temp + deviation, HRV(from ring's 5-min), wear.
- `baselines.py` for all of them.
- Datasette up; borrow one Grafana dashboard and remap.
- **Quality-of-life:** stand up `config.toml`, a `just` verb set, and a robust `sync.sh` (auto BT-toggle-on-drop) so daily use is one command.
- ✅ Done when: I can see HR/steps/sleep/temp trends with baselines in a browser.

**Phase 2 — Open-source interpreters**
- NeuroKit2 → respiratory rate + full HRV indices.
- ODI from SpO₂. VO₂max estimate.
- Optional: Walch sleep staging as a cross-check.
- ✅ Done when: breathing, SpO₂/ODI, VO₂max, richer HRV appear in `daily_metrics`.

**Phase 3 — Scores + my own frontend & insights** *(the real destination — continuous, not one-shot)*
- DIY readiness + sleep scores.
- **Bespoke local desktop app (Tauri)** reading `metrics.db`: Rust core embeds open_oura (BLE sync, flight-mode toggle, features); React frontend is my design system, my chart components (Recharts → visx/D3), every metric my way. The verified flight-mode `0x26` toggle moves into the app here.
- **Insights engine v1** — rules over the `baselines` table (z-score / streak / slope triggers) rendered as insight cards ("resting HR trending up 5 days", "temp +0.4°C vs baseline — possible illness").
- ✅ Done when: I've replaced every borrowed dashboard with UI I designed, and the app surfaces insights, not just charts. Then it keeps evolving.

**Phase 4 — Movement & fitness (see Part 4 for tooling)**
- **Auto workout-type detection** — start with cardio-type classification (rest/walk/run/cycle) from accel + HR; expand only if a self-labeled ring dataset justifies it.
- **Calibrated VO₂max** — implement a repeatable **Rockport walk-test** flow; optionally Firstbeat-style HR-vs-pace using phone GPS.
- ✅ Done when: workouts auto-tag at cardio-type granularity and VO₂max comes from a real test I can re-run.

**Backlog (add-later):**
- **Insights v2** — MCP server over `metrics.db` + my own LLM for a private, conversational advisor (surpasses Oura Advisor).
- Illness detection · vascular-age PPG model · cycle detection · Apple Health bridge.

### Open decisions
- **Derived store:** SQLite (simple) vs DuckDB (better analytical rollups). *Lean DuckDB.*
- **Interpreter language:** Python (NeuroKit2/Walch are Python) — call `open_oura` as a subprocess, read its SQLite. *Confirmed Python.*
- **Sync trigger:** manual `./sync.sh` vs auto-detect BLE presence at dock. *Start manual.*
- **Baseline window:** 28 nights? adaptive? *Start 28-night, revisit.*
- **Desktop app framework:** **Tauri** (Rust core reuses open_oura crates + React reuses my frontend skills) vs Electron (JS, heavier, shell-out for BLE) vs SwiftUI (native, no Rust reuse, not my stack). *Lean Tauri.*
- **Secrets:** auth key in **macOS Keychain** vs 0600 file. *Lean Keychain (file for now).*

### Risks / caveats
- `open_oura` schema is undocumented → Phase 0 introspection is a hard prerequisite.
- Flight-mode opcode payload is partly reverse-engineered ("Unknown" field) → verify silence empirically before trusting it.
- Interpreter outputs are *equivalents*, not Oura-identical numbers (different algorithms/calibration).
- VO₂max: rough until Phase 4 walk-test calibration; even calibrated, age-estimated HRmax is the main error source (Part 4.B).
- **Workout HAR placement risk:** all open datasets are wrist/arm/waist-worn, not finger — fine-grained lift recognition may be infeasible on a ring; gate on a data spike before investing (Part 4.A).
- **Frozen firmware:** never reopening the app means no future Oura features — accepted tradeoff.

---

## Part 4 — Later capabilities: workout detection & calibrated VO₂max (researched)

### A. Auto workout-type detection (HAR)

**Open datasets / repos that exist today:**
| Resource | What it is | Fit for us |
|---|---|---|
| **RecoFit** (`microsoft/Exercise-Recognition-from-Wearable-Sensors`) | 200+ people, accel+gyro, gym exercises; segment + recognize + **rep-count** (95–99% in-paper) | Best gym dataset — but **arm-worn** |
| **MM-Fit** | Multimodal exercise-logging dataset, arm-worn accel+gyro, rep counting | Reference for rep counting |
| **PAMAP2** | 18 activities incl. run/cycle/rope-jump/nordic-walk + HR monitor | Best for **cardio-type** classification + intensity |
| **ExerSense** (arXiv) | Real-time exercise segmentation/classification/counting from a **single IMU** | Closest single-sensor pattern |
| **WHAR Datasets** (arXiv 2508.16604) | Unified library of wearable-HAR datasets | Data plumbing |

**Model tooling:** `tsai`, `aeon`, `sktime` (time-series classification), or a simple 1D-CNN / DeepConvLSTM. The `guillaume-chevalier/LSTM-Human-Activity-Recognition` repo is a clean ~94% LSTM baseline.

**⚠️ The catch — sensor placement:** every dataset above is **wrist/arm/waist/chest**-worn. Our ring is **finger**-worn, lower sample rate (10/50 Hz), and may not stream accel continuously (battery). So:
- **Realistic v1:** *cardio-type* classification (rest / walk / run / cycle) by motion + HR intensity. This transfers reasonably and is genuinely useful.
- **Hard / maybe-never:** fine-grained gym-lift recognition + rep counting from a finger ring. Would require **collecting and hand-labeling my own ring data** and training from scratch — treat as a research spike, not a deliverable.
- **Decision gate:** before investing, log a day of ring accel and check whether workout motion is even separable at finger placement + the ring's duty cycle.

### B. Calibrated VO₂max (walk test)

The rough estimate (Phase 2) is a placeholder. The calibrated path:

- **Rockport 1-mile walk test (Kline / Kline-Dolgener equation)** — the concrete "walk test": walk 1 measured mile as fast as sustainable → VO₂max from **walk time + ending HR + age + sex + weight**. Ring supplies HR; I supply distance/time (measured course, track, or phone GPS) + demographics. Validated field test, open formula, **re-runnable** to track fitness over time. This is the target.
- **Firstbeat-style (HR vs. pace)** — continuously estimates VO₂max from HR response relative to speed across real runs/walks; ~3–5 ml/kg/min MAE vs lab. Method is published; the model is proprietary but reimplementable. **Needs pace → pair the ring's HR with phone GPS.** Optional upgrade after Rockport.
- **ACSM metabolic equations** — walk/run VO₂ from speed/grade; also gives age/sex percentile ratings for context.

**⚠️ Requirements/caveats:**
- Ring has **no GPS** → every calibrated method needs external distance/pace (measured course or phone GPS). Design the walk-test flow around that.
- All use an **age-estimated HRmax** unless I do a max test → that's the dominant error source; the estimate improves if I ever capture a true HRmax.
- Store each test as a first-class event (`vo2max_tests` table: date, protocol, distance, time, end_hr, weight → estimate) so I can trend calibrated VO₂max across re-tests.

**New build artifacts for Phase 4:** `interpret/workout_har.py`, `interpret/vo2max_calibrated.py`, a `vo2max_tests` table, and a small "start walk test" flow in the frontend.

## Appendix — Key references

> **Sleep staging (#5/#6):** the built Tier-1 (SleepECG) + Tier-2 (feature layer /
> Cole-Kripke) interpreters live in `interpret/`. Staging is **DIY** (not on-ring T1);
> Oura's own sleep model is not used (it is cloud-key-gated), so it is not our engine.

- `open_oura` — https://github.com/Th0rgal/open_oura · data-recovery map: `docs/data-recovery-map.md`
- Flight-mode opcode `0x26` — https://github.com/ringverse/protocol/blob/main/oura/BLE.md
- NeuroKit2 (HRV, respiration) — https://github.com/neuropsychology/NeuroKit
- Walch sleep-accel classifier — https://physionet.org/content/sleep-accel/1.0.0/
- Grafana dashboard to borrow — https://github.com/MaximeMichaud/oura-dashboard
- Datasette — https://datasette.io/
- RecoFit gym-exercise dataset — https://github.com/microsoft/Exercise-Recognition-from-Wearable-Sensors
- PAMAP2 (cardio-type + HR) — https://iotdataset.com/data/pamap2-physical-activity-monitoring
- LSTM-HAR baseline — https://github.com/guillaume-chevalier/LSTM-Human-Activity-Recognition
- Rockport walk-test VO₂max equation — https://www.whyiexercise.com/rockport-walking-test.html
- Firstbeat VO₂max method (HR vs pace) — https://www.firstbeat.com/en/science-and-physiology/fitness-level/
