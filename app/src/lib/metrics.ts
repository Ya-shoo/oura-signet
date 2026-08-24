// Presentation config for each channel id. The Rust core owns the *data*
// (value/series/status); the frontend owns *how it looks + what it means*.
// Explanations are ported from the mockup's DETAIL map (explanation-only — the
// hover pop-out never shows numbers).
import type { IconName } from "../components/icons";

export type Family = "recovery" | "sleep" | "body" | "activity";

export interface MetricMeta {
  name: string;
  famLabel: string;
  family: Family;
  icon: IconName;
  explain: string;
}

export const FAMILY_ACCENT: Record<
  Family,
  { accent: string; tint: string; vivid: string }
> = {
  recovery: { accent: "var(--recovery)", tint: "var(--recovery-tint)", vivid: "var(--recovery-vivid)" },
  sleep: { accent: "var(--sleep)", tint: "var(--sleep-tint)", vivid: "var(--sleep-vivid)" },
  body: { accent: "var(--body)", tint: "var(--body-tint)", vivid: "var(--body-vivid)" },
  activity: { accent: "var(--activity)", tint: "var(--activity-tint)", vivid: "var(--activity-vivid)" },
};

export const METRICS: Record<string, MetricMeta> = {
  hr: {
    name: "Overnight heart rate",
    famLabel: "Recovery · last night",
    family: "recovery",
    icon: "hr",
    explain:
      "Your heart rate through the night, beat to beat — from the ring's inter-beat intervals, keeping only the beats it flags as good signal, so non-worn time is filtered out. The low, steady stretches are deep sleep; the climbs are REM and brief wakes.",
  },
  dayhr: {
    name: "Daytime heart rate",
    famLabel: "Heart · today",
    family: "recovery",
    icon: "hr",
    explain:
      "Your heart rate through your waking hours — the daytime counterpart to the overnight curve. The ring runs an everyday-HR sampler while you're up and about and buffers it on-device; we decode that buffer locally (it never records while you sleep). The bar shows how your day split across HR zones, from resting to hard effort.",
  },
  rhr: {
    name: "Resting HR",
    famLabel: "Recovery",
    family: "recovery",
    icon: "rhr",
    explain:
      "Your lowest resting heart rate — a core recovery signal. Taken from the low end of the ring's good-quality overnight beats (non-worn time filtered out). The reading is accurate now; your personal normal range fills in as more nights accrue.",
  },
  hrv: {
    name: "HRV · rMSSD",
    famLabel: "Recovery",
    family: "recovery",
    icon: "hrv",
    explain:
      "Heart-rate variability (rMSSD): the millisecond variation between consecutive heartbeats — higher usually means better recovery and less stress. From the ring's good-quality overnight beats; your personal normal range builds over ~a week.",
  },
  readiness: {
    name: "Readiness",
    famLabel: "Recovery",
    family: "recovery",
    icon: "readiness",
    explain:
      "A recovery score built from contributors — sleep, resting heart rate, HRV balance, body temperature and breathing — each weighed against your own baseline, the same shape as Oura's readiness (which is also a formula over contributors, not a single sensor). Computed locally, and it sharpens as ~2 weeks of nights fill in your baseline.",
  },
  cva: {
    name: "Cardiovascular age",
    famLabel: "Recovery",
    family: "recovery",
    icon: "hr",
    explain:
      "Your vascular age — an estimate of how old your cardiovascular system looks — read from the shape of your pulse wave in the ring's raw PPG and run through an on-device cardiovascular model locally. It needs a raw-PPG burst, which the ring only records while docked and syncing.",
  },
  rhr28: {
    name: "Resting HR trend",
    famLabel: "Recovery · 28 days",
    family: "recovery",
    icon: "trends",
    explain:
      "Your resting heart rate across the last 28 nights, drawn against your baseline band (mean ± SD). The slope shows whether it's trending up or down. Builds as more nights sync.",
  },
  sleep: {
    name: "Time asleep",
    famLabel: "Sleep · last night",
    family: "sleep",
    icon: "sleep",
    explain:
      "Total time asleep plus the stages within it — deep, REM, light, awake. The ring doesn't transmit a hypnogram, so we stage the night locally from your heart rate, HRV, temperature and motion — a transparent model you own.",
  },
  spo2: {
    name: "SpO₂ · night",
    famLabel: "Body",
    family: "body",
    icon: "spo2",
    explain:
      "Blood-oxygen saturation overnight, read by the ring's red/infrared sensor. We calibrate its raw R-ratio into a percentage; frequent dips (the ODI) can flag disrupted breathing. A wellness signal, not a diagnosis.",
  },
  temp: {
    name: "Skin temperature",
    famLabel: "Body",
    family: "body",
    icon: "temp",
    explain:
      "Your skin temperature overnight. What matters for illness or cycle tracking is the deviation versus your own rolling baseline — which needs more nights before it's meaningful.",
  },
  resp: {
    name: "Breathing",
    famLabel: "Body",
    family: "body",
    icon: "resp",
    explain:
      "Breaths per minute overnight, derived from the subtle rhythm your breathing imprints on your heart rate (respiratory sinus arrhythmia).",
  },
  steps: {
    name: "Steps · today",
    famLabel: "Activity",
    family: "activity",
    icon: "steps",
    explain:
      "Your daily step count, tallied on-device from the ring's motion sensor. Activity intensity (MET) adds context beyond the raw number.",
  },
  met: {
    name: "Movement · MET",
    famLabel: "Activity",
    family: "activity",
    icon: "met",
    explain:
      "The metabolic equivalent of your movement — 1.0 is rest, 3+ is brisk walking. Read from the ring's on-device activity stream.",
  },
  vo2max: {
    name: "VO₂max",
    famLabel: "Activity",
    family: "activity",
    icon: "vo2",
    explain:
      "An estimate of your aerobic fitness — the maximum oxygen your body can use. This is a population estimate from your age, sex and weight (the Jackson non-exercise formula, ported from Oura's engine); a walk or step test would personalize it to you.",
  },
  bmr: {
    name: "Calories · today",
    famLabel: "Body",
    family: "body",
    icon: "flame",
    explain:
      "Total calories burned today: your resting metabolism (Schofield BMR from age, sex, weight — the same equation Oura's engine uses) plus active calories read from the ring's movement (MET) stream. The resting part is a solid estimate; the active part is as accurate as the MET data.",
  },
};
