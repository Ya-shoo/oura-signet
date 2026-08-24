// Pages not built yet in phase A — honest "coming" state, not a broken link.
import type { Page } from "../components/Shell";

const COMING: Partial<Record<Page, string>> = {
  trends: "Per-metric detail with the 28-night baseline ribbon, z-scores, slope and your pinned annotations.",
  sleep: "The hypnogram, stage durations, timing consistency and overnight curves — lands with the sleep-staging model (GOAL 3).",
  body: "HR day/night, HRV indices, skin-temp deviation and SpO₂ + ODI in one place.",
  activity: "MET timeline, steps, active minutes and (later) auto-tagged workouts.",
  settings: "Units, demographics, baseline window, theme, backups and the developer drawer.",
};

export function Placeholder({ page }: { page: Page }) {
  const title = page.charAt(0).toUpperCase() + page.slice(1);
  return (
    <main className="main">
      <div className="notice">
        <h2>{title}</h2>
        <p>{COMING[page] ?? "Coming soon."}</p>
      </div>
    </main>
  );
}
