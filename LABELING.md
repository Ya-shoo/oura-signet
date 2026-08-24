# oura-local — Self-Labeling & LLM-Assisted Algorithm Loop

> **The idea (yours):** let me label what I actually did — by free text *or* by answering a few
> questions the app asks when I dock the ring — so the app builds a **private, ground-truth dataset**
> that maps ring signals → activities/context over time. That dataset (a) unblocks workout detection,
> (b) sharpens insights, and (c) can be packaged for an **LLM / coding-agent to refine or author the
> interpreter algorithms themselves.**
>
> Companion to `SPEC.md` (data/pipeline) and `DESKTOP_APP_SPEC.md` (the app). **Local & optional throughout.**

---

## 1. Why this is worth building

- **It solves our one real blocker.** SPEC Part 4.A said finger-worn workout detection is stuck because
  every open dataset is wrist/arm-worn — there's no finger ground truth. **Your labels *are* that ground
  truth.** The ring becomes its own training set.
- **No commercial wearable does this.** Oura/Whoop don't let you label + retrain on your own data. This
  makes the "owned, self-improving" pitch real instead of just "local storage."
- **It compounds.** Every label improves the activity classifier, the anomaly context, and the personalized
  baselines/scores. The app literally gets smarter the more you use it.
- **It's a perfect fit for a coding agent.** Because everything is local and legible, we can package labeled
  data + current algorithm code into an artifact an LLM (e.g., Claude Code) reads to propose new features,
  tune thresholds, or write new `interpret/*` modules — a human-in-the-loop ML loop you own.

## 2. How labels get in — four modes, all optional, all *gentle*

1. **Free-text log.** I type natural language: *"rode a bike 2–3pm"*, *"sick all day"*, *"wine at dinner"*,
   *"stressed, bad sleep"*. A **local LLM parses** it into structured, time-aligned labels
   (activity, start/end, intensity, tags), confirming anything ambiguous.
2. **Dynamic questions (active learning).** After a dock+sync, the app scans the *new* data for **notable or
   uncertain segments** (HR spike + sustained motion, MET burst, temp anomaly, odd sleep) and asks a few
   **targeted, skippable** questions: *"Elevated HR + steady motion, 2:05–2:58pm yesterday — what was this?"*
   → quick chips (Walk · Run · Cycle · Workout · Stress · Other) + free text. It asks **sparingly, highest-value first.**
3. **Quick-add.** A deliberate "log activity" in ~2 taps: type + time range.
4. **Passive labels (free).** Wear / sleep / on-charger windows auto-label themselves; confirmed insights
   ("possible illness") can become labels when I confirm them.

## 3. Data model (add the table now, even before the UI)

```sql
labels(
  id            PK,
  start_ts,                 -- ring/wall time
  end_ts,                   -- NULL = a point-in-time note
  kind,                     -- activity | context | symptom | note | correction
  category,                 -- 'cycling' | 'run' | 'lift' | 'coffee' | 'alcohol' | 'sick' | 'stress' | 'travel' | 'meal' | …
  text,                     -- the raw thing I typed / said
  tags,                     -- JSON array, freeform
  intensity,                -- optional (easy/moderate/hard, or 1–10)
  source,                   -- typed | question | quick-add | auto
  confidence,               -- 0–1 (LLM parse / my certainty)
  created_unix
)
```
- Lives in the **derived store** (never touches the raw `events` table).
- **Time-aligned**, so any window of ring data joins to whatever label overlaps it.
- **Start logging early** (even from the CLI / a text file) — the sooner labels accrue, the more we have when
  it's time to actually train something.

## 4. The two LLM roles (both local/optional, both human-in-the-loop)

**A. Ingest / parse.** free text → structured labels; generate the dock-time questions from detected
segments; ask me to confirm ambiguous parses. Small, local model is fine.

**B. Algorithm authoring / upgrading — the exciting part.** Periodically the app assembles a **training
bundle** and hands it to a coding agent to *improve the pipeline*:
- propose new features, tune thresholds in `interpret/*`,
- train/refine an activity classifier,
- or write a brand-new interpreter module.
- Output is **reviewed, tested against held-out labels, and dropped in** — **never auto-deployed** (see §7).

### The training bundle (the LLM-consumable artifact)
- **Per labeled interval:** a feature vector — HR min/mean/max, HRV, MET distribution, motion MAD / intensity,
  accel summary, temperature, duration, time-of-day — **plus the label.**
- **A manifest:** which interpreters exist, their current thresholds/params, and their current accuracy vs my
  labels (a confusion matrix I can watch improve).
- **Format:** JSONL/Parquet for the vectors + a Markdown manifest. Reproducible, local, diffable.

## 5. Active learning — ask smartly, stay calm

- Prioritize questions where the model is **most uncertain** or a segment is **most anomalous** — max label
  value per question.
- **Cap to 1–3 questions per dock; always skippable; never nag** (the app's calm principle).
- Surface the model's **guesses for one-tap confirmation** (*"We think this was a run — right?"*) — a
  confirmation is a cheap, high-value label.

## 6. UX in the app (a "Journal" surface)

- **Journal screen:** a day timeline; tap a day → the ring's data + auto-detected segments + my labels;
  add/edit via chips or free text.
- **Dock-time card:** after a sync, a soft *"A few things I'm curious about"* card with 1–3 quick questions,
  fully skippable.
- **Always-available free-text box:** *"What did you do, and when?"* → parsed into labels.
- All **local**; labels are mine; the LLM is local/optional; nothing leaves the machine.

## 7. Honesty / risks (so we build it right)

- **Volume:** ML needs examples. Bootstrap with transparent **rules/heuristics**; the trained classifier
  earns its place over **weeks–months** of labels. Don't promise a model on day one.
- **Noisy input:** free text is messy → confirm ambiguous parses; keep a `correction` label kind so I can fix
  mistakes (which are themselves great training signal).
- **LLM-authored code is a *proposal*.** Always human-reviewed and tested on held-out labels before it runs.
  **Never auto-deploy** — and keep the **"not a medical device"** line: this improves activity/context
  detection, not diagnosis.
- **Don't nag.** A labeler that interrupts gets ignored; rare + valuable wins.

## 8. Phasing

- **Now (cheap, high return):** add the `labels` table + a simple free-text / quick-add log (even CLI) so
  labels start accumulating immediately.
- **Phase 3 (app):** Journal screen + dock-time questions + local-LLM parsing.
- **Phase 4 (ML):** training-bundle export + LLM-assisted classifier authoring + active learning →
  the calibrated, personalized workout detection SPEC Part 4 wanted.

## 9. What it unlocks beyond workouts

- Context for anomalies/insights ("HRV drops the night after alcohol"; "temp rose the day before I logged sick").
- Personalized baselines/scores tuned to *my* labeled reality.
- A growing, owned dataset any future model — or LLM — can learn from. This is the real long-game moat.
