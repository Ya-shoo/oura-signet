// Client-side ports of the anthropometric formulas the Rust core uses, so the
// Settings screen can preview VO₂max / BMR / BMI *as you type*. The home cards
// still read the authoritative values from Rust (`metrics.rs`) after you Save —
// these are kept byte-for-byte in step with it (Jackson NES + Schofield).

const LB_PER_KG = 2.20462;
const IN_PER_CM = 1 / 2.54;

/** Jackson non-exercise VO₂max estimate (ml/kg/min). */
export function vo2maxJackson(age: number, female: boolean, weightKg: number): number {
  const lb = weightKg * LB_PER_KG;
  return Math.max(0, 79.9 - 0.39 * age - 13.7 * (female ? 1 : 0) - 0.127 * lb);
}

// Schofield BMR bands: (age-upper-bound, weight-coeff, intercept).
const SCHOFIELD_M: [number, number, number][] = [
  [3, 59.512, -30.4], [10, 22.706, 504.3], [18, 17.686, 658.2],
  [30, 15.057, 692.2], [60, 11.472, 873.1], [Infinity, 11.711, 587.7],
];
const SCHOFIELD_F: [number, number, number][] = [
  [3, 58.317, -31.1], [10, 20.315, 485.9], [18, 13.384, 692.6],
  [30, 14.818, 486.6], [60, 8.126, 845.6], [Infinity, 9.082, 658.5],
];

/** Schofield basal metabolic rate (kcal/day). Oura-parity alternative to Mifflin. */
export function bmrSchofield(age: number, female: boolean, weightKg: number): number {
  const bands = female ? SCHOFIELD_F : SCHOFIELD_M;
  const [, s, i] = bands.find(([hi]) => age < hi) ?? bands[bands.length - 1];
  return s * weightKg + i;
}

/** Mifflin–St Jeor basal metabolic rate (kcal/day) — uses height; the most accurate
 *  general-population equation, so it's the default the home Calories card uses. */
export function bmrMifflin(
  age: number,
  female: boolean,
  weightKg: number,
  heightCm: number,
): number {
  const base = 10 * weightKg + 6.25 * heightCm - 5 * age;
  return Math.max(0, base + (female ? -161 : 5));
}

/** ACSM-style fitness category for a VO₂max value, sex- and age-adjusted. */
export function vo2Category(v: number, age: number, female: boolean): string {
  const base = female ? [27, 32, 37, 42, 48] : [32, 38, 44, 50, 56];
  const adj = Math.max(0, age - 27) * 0.3;
  const zones = base.map((b) => Math.max(0, b - adj));
  const cats = ["Very poor", "Poor", "Fair", "Good", "Excellent", "Superior"];
  const idx = zones.findIndex((z) => v <= z);
  return cats[idx === -1 ? 5 : idx];
}

export function bmiOf(weightKg: number, heightCm: number): number | null {
  return heightCm > 0 ? weightKg / (heightCm / 100) ** 2 : null;
}

export function bmiClass(bmi: number): string {
  if (bmi < 18.5) return "underweight";
  if (bmi < 25) return "healthy";
  if (bmi < 30) return "overweight";
  return "obese";
}

// ---- unit conversions (canonical store is metric: kg / cm) ----
export const kgToLb = (kg: number) => kg * LB_PER_KG;
export const lbToKg = (lb: number) => lb / LB_PER_KG;
export const cmToFtIn = (cm: number): { ft: number; inch: number } => {
  const totalIn = Math.round(cm * IN_PER_CM);
  return { ft: Math.floor(totalIn / 12), inch: totalIn % 12 };
};
export const ftInToCm = (ft: number, inch: number) => (ft * 12 + inch) / IN_PER_CM;
