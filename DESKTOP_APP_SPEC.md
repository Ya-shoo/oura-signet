# oura-local — Desktop App & Health-Monitoring System Design

> **Status:** v0.2 — aesthetic + priorities locked: warm off-white, super-rounded **"Wii-channel"** style; all-four-families Home; start = viewer **and** ring control together. Opinionated recommendations throughout.
> Companion to `SPEC.md` (the data/pipeline spec). This doc is the **app**: architecture, UX, visualization,
> the airplane-mode control, and a real security/safety model.
> **Not a medical device.** Everything here surfaces *wellness signals*, not diagnoses (see §7).

---

## 1. Principles (what makes this app different from Oura's)

1. **Local-first & offline by design.** No network path exists in the shipping app. Your data never leaves the Mac.
2. **One surface, two jobs:** it both **controls the ring** (sync, features, airplane mode) and **shows the data** — no CLI, no separate tools.
3. **Owned, legible, customizable.** Your design system, your metric choices, transparent formulas you can read.
4. **Safe by construction.** The only "dangerous" ring command the UI ever exposes is airplane mode, and it's heavily guarded (§6). Factory-reset / DFU live in a hidden "developer" drawer, never a normal button.
5. **Calm, not alarming.** Health data invites anxiety. The app states facts and trends, flags deviations gently, and never plays doctor.

---

## 2. Architecture — how I'd build it

**Stack: Tauri 2** (Rust core + React/TypeScript webview). Chosen because the Rust core can **embed `open_oura`'s crates directly** — no shelling out — while the UI is your React/design-system wheelhouse. One small, code-signed, sandboxed binary.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Tauri app (single signed, sandboxed macOS binary)                    │
│                                                                       │
│  ┌── React / TS frontend (webview) ──────────────────────────────┐   │
│  │  Home · Trends · Sleep · Body · Activity · Ring · Insights     │   │
│  │  design system · Recharts→visx/D3 · TanStack Query over IPC    │   │
│  └───────────────▲───────────────────────────────┬───────────────┘   │
│      Tauri IPC   │ (typed commands, allow-listed) │ events (progress, │
│                  │                                 │  dock, sync-done) │
│  ┌── Rust core ──┴─────────────────────────────────▼──────────────┐   │
│  │  • oura-link  → BLE: scan/pair/sync/features/live-hr/0x26      │   │
│  │  • oura-store → raw events SQLite (read-only truth)            │   │
│  │  • dock-watcher (BLE presence) → auto-sync → re-arm airplane   │   │
│  │  • derived-store writer (DuckDB/SQLite: daily_metrics, …)      │   │
│  │  • Keychain access (auth key), backup/export, audit log        │   │
│  └───────────────┬───────────────────────────────────────────────┘   │
│                  │ subprocess (bundled, offline)                      │
│  ┌── Interpret sidecar (Python 3.12 venv, bundled) ──────────────┐    │
│  │  NeuroKit2 (HRV/resp) · Walch (sleep staging) · ODI · scores  │    │
│  │  reads raw SQLite → writes derived store                       │    │
│  └───────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────┘
        ▲ BLE (CoreBluetooth)                    ▼ local files (FileVault + optional SQLCipher)
     Oura Ring                          ~/Library/Application Support/oura-local/
```

### Key architectural decisions
- **Interpreters = bundled Python sidecar, not a Rust rewrite.** NeuroKit2 and the Walch sleep classifier are Python and non-trivial to port; bundling a frozen 3.12 venv (PyInstaller/`uv`-built, no network) and invoking it from Rust is the pragmatic call. Simple math (baselines, z-scores, ODI, VO₂max estimate) can live in Rust for speed; the heavy science stays Python. *Revisit if bundling pain > porting pain.*
- **Two stores, clean split.** Raw `open_oura` SQLite stays **read-only truth**; a separate **derived store** (lean DuckDB or SQLite) holds `daily_metrics`, `baselines`, insights, and app data. UI reads derived; never touches raw.
- **Dock-watcher is the heartbeat.** A background task watches for the ring's BLE presence; on dock it can auto-sync → interpret → (optionally) re-arm airplane mode, then fire a `sync-done` event the UI reacts to. Manual "Sync now" always available. *Hardware note:* a single night is a **multi-minute** drain (one sync pulled **14k+ events ≈ 2.5 min** at BLE throughput), so stream **live sync progress** (events/bytes + cursor) and allow **cancel** — never a blocking spinner. The prototype `sync/dock_watch.py` (edge-triggered detect → `pipeline.sh` → re-arm → verify-silent) is the reference for this loop.
- **BLE reliability = one long-lived, self-healing connection — the user must NEVER toggle Bluetooth.** *Hardware finding 2026-08-05:* driving CoreBluetooth through short-lived, one-shot processes (how the `open_oura` CLI works) **wedges** the macOS adapter after repeated ops, in two modes — a **disconnect-hang** (sync commits the DB and prints `Done:`, then the client never exits) and a **connect-hang** (no link at all until Bluetooth is toggled off/on). Crucially, the **Python/bleak path never wedged once** all session, which pins the cause on **process/central churn**, not the ring or CoreBluetooth itself. So the embedded Rust core **must**: hold **one `CBCentralManager` for the app's lifetime** (reuse it — no per-op processes); run a **supervised `connect → sync → disconnect`** with timeouts and explicit teardown (`cancelPeripheralConnection`, drop peripheral handles — no zombies); **serialize** BLE ops (never overlap a scan with a connect on the same central); and **auto-heal** a stuck central by **recreating the manager object in-process** (the programmatic equivalent of the toggle, done silently) with backoff — so a wedge self-recovers invisibly. Port the prototype's **sync guard** (`pipeline.sh`: treat a committed `Done:` as success even if the hung client must be reaped; distinguish connect-hang vs. mid-sync drop) into the Rust sync supervisor. **Fallback:** if btleplug can't be made stable enough, drive **native CoreBluetooth** (a small Swift/ObjC sidecar or a vetted `core-bluetooth` crate) — bleak's flawless run proves correct central-lifecycle management is the entire fix.
- **IPC is a narrow, typed, allow-listed surface.** The frontend can only call specific commands (`sync`, `get_daily(range)`, `set_feature`, `arm_airplane`, …), each validating inputs in Rust. No arbitrary SQL from the webview; no raw BLE from JS.
- **Frontend data layer:** TanStack Query wrapping Tauri commands; the Rust side runs parameterized SQL against the derived store and returns typed rows. Charts render from those.

---

## 3. Local data model (additions for the app)

Beyond `SPEC.md`'s `daily_metrics` + `baselines`:
- `app_layout` — your Home widget grid (which cards, order, size, chosen range) so the UI is data-driven and portable.
- `prefs` — units (°C/°F), theme, demographics (birth-year/sex/weight → HR/VO₂ math), baseline window, insight thresholds, "auto-arm airplane after sync" on/off.
- `annotations` — your notes pinned to a day/metric ("sick", "travel", "big workout", "caffeine late") → overlaid on charts and used to explain deviations.
- `sync_log` — every sync: when, events pulled, errors, battery — powers the Ring page + trust.
- `airplane_log` — every arm/disarm with verification result — an audit trail for the one danger op.

---

## 4. Information architecture & UX

**Left rail nav:** `Home · Trends · Sleep · Body · Activity · Ring · Journal · Insights · Settings`
(Body = HR/HRV/temp/SpO₂; **Journal** = the self-labeling / annotation surface where I log what I did so the app can learn what my data means — full design in [`LABELING.md`](LABELING.md); Insights arrives Phase 3.)

### 4a. Home — customizable "at a glance" (your #1 ask)
A **draggable, resizable widget grid** (react-grid-layout) — a **wall of soft, rounded "health channels"** (Wii-menu style). You pick which cards, their size, and each card's default range; **defaults surface all four families — Recovery · Sleep · Body · Activity.** Layout persists in `app_layout`.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Good morning          ●  Radio: SILENT (recording)    [ Sync now ]  │  ← ring status strip
│  Last sync 7m ago · battery 86% · worn 7h12m last night                    │
├───────────────────────────────────────────────────────────────────────────┤
│ ┌── Readiness (DIY) ──┐ ┌── Resting HR ──────┐ ┌── HRV (rMSSD) ──────────┐ │
│ │      78             │ │   52 bpm    ▼2      │ │   64 ms      ▲ +6       │ │
│ │  ▲ balanced         │ │  ▁▂▂▁▃▂▁ vs base    │ │  ▂▃▃▄▅▄▅  +0.8σ         │ │  ← stat tiles:
│ │  ▁▂▃▄▅▅▄ 14d        │ │  28-night band █░░  │ │  within normal          │ │    value · Δ vs
│ └─────────────────────┘ └────────────────────┘ └─────────────────────────┘ │    baseline ·
│ ┌── Sleep last night ─────────────┐ ┌── Temp deviation ──┐ ┌── Steps ────┐ │    sparkline ·
│ │ 7h12m  eff 91%  ▇▇▅▂▇▇▃ hypno    │ │  −0.1°C  normal    │ │  6,204  ▲   │ │    status color
│ │ deep 1h20 · rem 1h48 · light …   │ │  ▁▁▂▁▁▂▁ 30d       │ │  ▂▄▃▅▆▄▇    │ │
│ └──────────────────────────────────┘ └────────────────────┘ └─────────────┘ │
│ [ + Add widget ]                                          [ Edit layout ]   │
└───────────────────────────────────────────────────────────────────────────┘
```

**Card types (a small kit you compose):** `stat tile` (value + Δ vs baseline + sparkline + status), `mini-trend` (line + baseline ribbon), `sleep card` (hypnogram + stage breakdown), `calendar heatmap` (steps/sleep/wear by day), `ring status`, `insight card`. Every card links to its full **detail view**.

**Interaction & fit (locked in the mockup `mockups/home.html`):**
- **Fits one MacBook screen — no scroll** (verified 1440×900). It's a native app's home; the channel grid tiles cleanly (one big 2×2 *Sleep* hero + compact tiles + wide trends).
- **Hover a card body** → a soft, elevated **detail card pops out on top** (metric explanation + the numbers behind it; contents stagger in). A subtle `i` dot hints it.
- **Hover a graph / bars / hypnogram** → **that point's data** (value + day/time) with a marker dot; the pop-out is *suppressed* over charts, so the two interactions never collide.
- All curved (monotone) lines, warm/flat, thin soft shadows.

### 4b. Trends / metric detail
One reusable detail template, driven by metric config:
```
┌───────────────────────────────────────────────────────────────────────────┐
│  Resting HR                              [ D ][ W ][ M ][ Y ]  [ ⌵ compare ]│
│  52 bpm today · 28-night baseline 54 ± 3 · z −0.7 · 30-day slope ↓ −1.2/mo │
│                                                                            │
│   bpm  60┤                        ╭─╮        ● today                       │
│        55┤   ░░░░░░░░░░░░░░░░░░░░░░╯ ╰░░░░░  ░ = baseline band (mean ± SD)  │
│        50┤ ●───●──●─●───●──●─●──●────────●   ─ = your nightly value         │
│           └┬─────┬─────┬─────┬─────┬─────┬                                  │
│           Jul 8  15    22    29   Aug 5    ▲sick(Jul19)  ▲travel(Jul27)     │  ← annotations
└───────────────────────────────────────────────────────────────────────────┘
```
Baseline ribbon + z-score everywhere (the "how it deviates / how it evolves" ask). Annotations explain spikes. Compare overlays two metrics (e.g., RHR vs temp deviation).

### 4c. Sleep / Body / Activity
- **Sleep:** hypnogram, stage durations, efficiency/latency, timing consistency, overnight HR/HRV/temp/SpO₂ curves, monthly calendar heatmap. (Stages computed locally — SPEC #5.)
- **Body:** HR (day/night), HRV indices, skin-temp + deviation, SpO₂ + ODI.
- **Activity:** MET timeline, steps, active minutes, later workout auto-tags (Phase 4).

### 4d. Ring — the control room (see §5 for airplane detail)
Battery, firmware, last sync, wear-time, feature toggles (HR/SpO₂/steps), **Sync now**, **Dock & Silence**, re-pair, and the **key backup** status. This is where the prominent airplane control lives (also mirrored on Home's status strip).

---

## 5. The airplane-mode control (your specific ask, designed in full)

You wanted: a **prominent button to ready the ring for airplane mode from the dock**, and to **be warned about the on/off switch**. The subtlety that drives the whole UX:

> **Turning the radio OFF is a command; turning it back ON is physical.**
> Arming airplane mode sends BLE opcode `0x26` (only possible while connected, i.e., docked); the ring ACKs and **goes silent immediately** — even while still on the charger. Waking is physical: it comes back on a **dock *event*** (an undock→redock), **not** from merely sitting on the charger. So "un-silencing" isn't a button, it's *(re)docking*. The UI must teach this or it's confusing.

> **✅ Verified on hardware (2026-08-05).** Full cycle proven on our ring: `0x26` (frame `26026027`, sent **after auth** — `auth_state=success`) → ring replies **`0x27 00`** (success) → radio **silent immediately** (two scans confirmed absence) → **stayed silent** on the charger for 2+ min → **woke on undock→redock**. Then the whole unattended loop (`dock_watch`: detect → sync 14k events → re-arm → verify silent) ran start-to-finish. These findings drive the guards below and the **BLE-reliability decision in §2**. Reference prototype: `sync/` (`flight_mode.py`, `dock_watch.py`, `pipeline.sh`).

### The control
- **Prominent placement:** a persistent **radio-state pill** in the Home status strip + a large primary button on the Ring page.
- **Two clear states,** always visible:
  - 🟢 **Radio ON — syncable** (docked/connected)
  - 🌙 **SILENT — recording** (airplane armed; still logging on-device)
- **The button is context-aware:**
  - When docked & radio on → **`Dock & Silence`** (arms airplane, then you can undock).
  - When armed → shows **`SILENT`** with helper text: *"Dock the ring to wake its radio and sync."*

### The warning (your "warn me about the on/off switch")
A confirm sheet before arming — plain language, no jargon:

```
┌── Silence the ring? ─────────────────────────────────────────┐
│  🌙  This turns OFF the ring's Bluetooth radio.               │
│                                                              │
│   • The ring keeps recording on-device (up to ~7 days).      │
│   • You WON'T be able to sync or connect while it's silent.  │
│   • It turns back ON automatically when you dock it.         │
│                                                              │
│   ⚠  3h of data hasn't been synced yet.                      │
│      [ Sync first, then silence ]   ← recommended            │
│                                                              │
│              [ Cancel ]      [ Silence the ring ]            │
└──────────────────────────────────────────────────────────────┘
```

### Safety guards baked in
1. **Sync-before-silence:** if unsynced data exists, the dialog nudges to sync first (no data stranded on a ring you're about to pocket).
2. **Verify silence — two ways:** the app checks the ring's **`0x27 00` ACK** to the `0x26` write **and** confirms it stopped advertising (a quick scan), reporting **"Confirmed silent"** — not just "command sent." (`0x26` is now hardware-verified, so the old "unverified" badge is gone; the ACK+scan check stays as the per-arm proof, since a stray failure should still be caught.)
3. **Audit + undo:** every arm/disarm logs to `airplane_log`; "undo" = the always-true instruction *dock it*.
4. **No accidental danger:** factory-reset/DFU are **never** on this screen; they live behind a hidden developer drawer with typed confirmation.

---

## 6. Design language — "health channels" (Wii-inspired)

**The look: soft, warm, super-rounded — Nintendo Wii-channel energy.** Friendly and calm, not clinical, not a generic dashboard.
- **Shape:** large corner radii on everything (cards, buttons, inputs), pill primary buttons, soft ambient shadows, generous padding, rounded-square tiles.
- **Palette & depth:** a **warm off-white** base (eggshell/cream); soft, muted **category accents** (gentle blue/green/coral per metric family, à la Wii channels); status colors stay soft — *noticing*, never alarm-red dread. **Flat-warm — no gloss, no big gradients**; the only depth is a **thin, soft, tight shadow** behind cards (small blur, minimal spread), never a wide diffuse drop-shadow.
- **Type:** a rounded geometric sans (SF Rounded / Nunito / Quicksand) for warmth; **tabular figures** for the numbers.
- **Motion:** gentle, springy micro-interactions — a tile lifts on hover, cards settle with a soft spring; satisfying, never noisy. (Optional Wii-ish sound accents, off by default.)
- **The Home *is* a channel grid:** the customizable widget grid reads as a wall of soft "health channels" — each metric a rounded tile you arrange and pop into. That Wii-menu feel is the perfect match for "customize my key metrics at a glance."

### Visualization grammar (inside that skin)

- **Stat-tile anatomy (one consistent grammar):** big value + unit · Δ vs baseline (number + arrow) · sparkline · **status color** (calm/watch/deviation) · tap → detail. Same grammar on Home and in cards everywhere.
- **Baseline-first charting:** every trend shows the **28-night baseline ribbon** (mean ± SD) behind your line; deviation is the story, not the raw number.
- **Right chart per shape:** line+ribbon (HR/HRV/temp) — **curved / monotone splines, never jagged straight segments** — plus **hypnogram** bars (sleep), **calendar heatmap** (steps/wear/sleep consistency), gauge/arc (scores), scatter (e.g., HRV vs sleep), horizon/small-multiples for the "everything at once" view.
- **Color = meaning, accessibly:** a restrained palette where hue encodes *status* (in-band / watch / notable deviation), never decoration; colorblind-safe; readable in light & dark. (We'll run the **`dataviz`** skill when we build charts and **`frontend-design`** for the component system — both tuned to avoid the generic "AI dashboard" look you dislike.)
- **Density is yours:** the grid supports a compact "cockpit" or an airy "calm" layout; a preference, not a rebuild.

---

## 7. Safety & security

### 7a. Security posture (local-only threat model)
Because there's no backend, the real threats are **device theft**, **local malware/other apps reading files**, **supply-chain**, and **self-inflicted danger ops**. Mitigations:

| Area | Measure |
|---|---|
| **Secrets** | Auth key in the **macOS Keychain** (Security framework), never plaintext; app reads on demand. Key also backup-exportable as an encrypted file. |
| **Data at rest** | FileVault as baseline; **optionally SQLCipher-encrypt** the derived + raw DBs, key in Keychain. Backups always encrypted (age/`gpg`). |
| **No network** | Ship with **no networking entitlement**; strict Tauri **CSP** (no remote origins); dependency review for any phone-home. **Zero telemetry/analytics.** |
| **App hardening** | App Sandbox with **minimal entitlements (Bluetooth only)**, Hardened Runtime, **code-sign + notarize**, no JIT/eval. |
| **IPC surface** | Frontend calls only **allow-listed, typed commands**; every input validated in Rust; no arbitrary SQL or BLE from the webview. |
| **Supply chain** | Pin `open_oura` to a reviewed commit; `cargo audit` + `osv-scanner`/`npm audit` in CI; minimal deps; reproducible builds; verify the Python sidecar's wheels. |
| **Backups** | Local, **encrypted**, user-chosen location; never cloud by default; one-command restore. |

### 7b. Health-data safety (this is the part people skip)
- **Not a medical device — stated up front and in context.** SpO₂/ODI, HR, HRV, temperature, "readiness," and any future "illness"/apnea signals are **wellness estimates, not diagnoses**. A first-run notice + a quiet persistent footnote on clinical-adjacent views. Never tell you to take medical action.
- **Calm framing:** deviations are shown as "worth noticing," with your annotations/context, never red-alarm dread. No push-notification anxiety loops.
- **Estimates are labeled as estimates:** every DIY score/number links to its transparent formula and its confidence/caveats (e.g., VO₂max is a rough estimate until the walk test).

### 7c. Operational / device safety
- **Raw store is immutable** truth; interpreters only ever write the derived store → a bad interpreter can't corrupt your history; `redecode` rebuilds.
- **Idempotent, resumable sync** (cursor); sync failures never lose data; DB integrity checks on open.
- **Danger ops gated:** airplane mode = confirm + sync-guard + verify + audit (§5); factory-reset/DFU hidden behind a dev drawer + typed confirmation. The app can't brick the ring through a normal click.
- **Key-loss recovery documented:** lose the key → factory-reset (app/BLE) + re-pair; that's why we back the key up to Keychain **and** an encrypted export.

### 7d. Resilience — surviving shutdown, sleep & Bluetooth loss
**Stance: crash-only + declarative reconciliation.** The app is *never* a fragile `connect → sync → arm` script that breaks if interrupted. It persists a **desired state** ("synced through cursor X, then silent") and **reconciles toward it** on every startup, adapter power-on, and dock event — each step idempotent and resumable. It's safe to kill at any instant with no shutdown cleanup, which makes power loss, sleep, reboot, and Bluetooth off→on **non-events** rather than bugs.

**Two durable facts it tracks:**
1. **Sync cursor** (`sync_state.next_cursor`) — already transactional + idempotent (`UNIQUE(serial,tag,ring_timestamp,body)`): resume from the last committed cursor, never a dup or gap. `open_oura`'s `drain_events` loops to `bytes_left == 0` and **persists the cursor after every 255-event batch**, so resume is **granular** (not all-or-nothing) — a link drop at event 9,000 of 14,000 resumes at ~9,000. The ring buffers **~7 days**, so nothing is lost even across a multi-day host outage. **App UX:** expose a manual **"Sync / Resume"** with **live progress** that drives to full drain (loop the guarded pass, resuming on drop) — the prototype `sync/pipeline.sh` (resume-to-completion) + `sync/status.py` (cursor/staleness) are the reference.
2. **Radio intent + a write-ahead intent journal.** Before arming, write `intent=arm, sync_confirmed=true`; after the `0x27 00` ACK **and** the silence scan, write `arm_confirmed`. On restart, an `intent=arm` with no `arm_confirmed` is re-verified by a scan: silent ⇒ confirm, awake ⇒ re-arm.

**Two ordering rules that make any interruption safe:**
- **Never arm over unsynced data.** `0x26` only fires after a `sync_complete` marker. If sync can't finish (BT off, undocked, wedge), we simply *don't* arm — the ring stays reachable (docked) or keeps buffering (worn). Data is at worst "not yet synced," never lost or stranded.
- **Never claim silence without proof.** The UI says "silent — safe to wear" *only* after ACK + scan. An un-ACKed arm is treated as "still awake" and re-verified on the next dock/restart. This prevents the one genuinely bad outcome: wearing a ring you *think* is silent but isn't.

**Why each op is safe to interrupt:** an **arm is atomic + idempotent** — BLE write-with-response delivers the whole `26026027` frame or nothing (no "half-armed" state), and re-sending to an already-silent ring is a physical no-op (silent ⇒ not connectable). A **sync is transactional + resumable** (above). So "did it actually happen?" is *always* answerable by a scan + the ACK/cursor — no ambiguity survives a restart.

| Interruption | Raw effect | How the app handles it |
|---|---|---|
| **Shutdown / sleep / crash — idle** | nothing in flight | Startup reconcile → converge to desired (sync if docked+awake, else wait). No-op. |
| **… mid-sync** | cursor mid-advance | Resume from last committed cursor on restart/reconnect; idempotent → no dups/gaps. 7-day buffer covers the delay. |
| **… mid-arm** | ambiguous: armed or not | Intent journal + scan: silent ⇒ confirm, awake ⇒ re-arm. Never trust an un-ACKed arm; sync-before-arm means nothing was stranded. |
| **Host airplane mode / BT off (idle)** | adapter `PoweredOff` | First-class CoreBluetooth state event: pause the dock-watcher, show "Bluetooth off — can't reach the ring," no spin, no crash. |
| **… mid-sync / mid-arm** | link drops | Catch the disconnect, mark the op for retry, resume on `PoweredOn`. Same reconcile. |
| **BT off → on later** | fresh `PoweredOn` | Central rebuilds, re-scans, resumes reconcile. (A *user* toggle is the clean recovery — distinct from the app's internal wedge, which it auto-heals per §2.) |
| **Ring undocked mid-op** | link lost | Normal disconnect; reconcile on next dock. |

**Adapter state is a first-class input.** The app subscribes to CoreBluetooth `CBManagerState`, gates all BLE on `PoweredOn`, and **distinguishes host-BT-off (a host condition) from ring-silent (a device condition)** so it never conflates them. With the long-lived, self-healing central (§2), the only observable states are *reachable & syncing*, *silent (armed)*, *host-BT-off (paused)*, or *ring-away* — never a corrupt or ambiguous one. **You're protected from:** data loss, a stranded-unsynced silence, a falsely-claimed silence, and adapter wedges. **Still needs a human (by design):** physically re-docking to wake a silent ring.

---

## 8. Build phases for the app (maps onto SPEC Phase 3)

> **Decided:** start with **A + B together** — a viewer that also runs the ring (data *and* control from day one).

- **A — Read-only viewer (your frontend playground).** Point React at the existing `metrics.db`; build the Home grid + one metric-detail template + the card kit. No ring control yet. Fastest path to something *yours* on screen.
- **B — Ring control in-app.** Bring `oura-link` into the Rust core: status, Sync now, feature toggles, and the **airplane-mode control** (§5) with its guards. Retire the CLI for daily use.
- **C — Interpreters + insights.** Wire the Python sidecar (HRV/resp/sleep/scores) + the insights engine → insight cards.
- **D — Harden & package.** Keychain, encryption-at-rest, backups, sandbox/hardened-runtime/notarize, auto-dock daemon, menu-bar mini-app (battery + one-click Silence).

---

## 9. Decisions

**Locked (2026-08-05):**
1. ✅ **Aesthetic** — warm off-white, **super-rounded, Nintendo-Wii-channel** style: soft, friendly, calm.
2. ✅ **Default Home** — all four families: **Recovery · Sleep · Body · Activity.**
3. ✅ **Start** — build the **read-only viewer *and* ring control together** (phases A+B): a real app that both shows data and runs the ring.
4. ✅ **BLE reliability** — one **long-lived, self-healing `CBCentralManager`**; supervised connect→sync→disconnect; auto-heal a wedged central in-process. **The user never toggles Bluetooth.** (§2, from the 2026-08-05 hardware findings.)
5. ✅ **Crash-safety** — the sync/arm subsystem is a **crash-only, declarative reconciler**: durable desired-state, idempotent + resumable ops, sync-before-arm, proof-gated silence. Shutdown / sleep / BT-loss are non-events. (§7d.)

**Still open:**
6. **Gloss level** — how much Wii sheen vs flat-warm (your OWdle work leaned flat/anti-"AI-look").
7. **Encryption depth** — FileVault-only vs SQLCipher-at-rest from day one.
8. **Menu-bar mini-app** — build the tray "Silence" toggle early, or fold into the full app later?
