// Home — Sleep is the hero; Steps / VO₂max / Movement / Calories are the key row;
// everything else lives behind "Show all metrics" (which lets the page scroll).
import { useState, type CSSProperties } from "react";
import type { Channel } from "../lib/api";
import { useHomeChannels, useSyncStatus, useDataStatus, useAvailableDays } from "../lib/hooks";
import { fmtDate, fmtInt, relTime } from "../lib/format";
import { Tile } from "../components/Tile";
import { useSync } from "../lib/sync";
import { SelectedDateContext } from "../lib/selectedDate";
import type { Page } from "../components/Shell";

// The cards that always fit on the main page (Sleep spans the full width as the
// hero; the other four are the key stat row).
const PRIMARY = ["sleep", "steps", "vo2max", "met", "bmr"];
// Revealed by "Show all"; the page becomes scrollable.
const SECONDARY = ["hr", "dayhr", "rhr", "hrv", "cva", "spo2", "temp", "resp", "readiness"];

// Sleep + the overnight-HR chart get double width (same height) — slightly
// prioritised, not dominant. Everything else is a 1×1.
function cardStyle(id: string): CSSProperties {
  if (id === "sleep" || id === "hr") return { gridColumn: "span 2" };
  return {};
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

// Label for a date relative to the latest day with data: Today / Yesterday / weekday.
function dayLabel(date: string, latest: string): string {
  if (date === latest) return "Today";
  const d = new Date(date + "T00:00:00");
  const l = new Date(latest + "T00:00:00");
  const days = Math.round((l.getTime() - d.getTime()) / 864e5);
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

// ◀ Today ▶ — steps through the days that have data (bounded to today, newest-first).
function DayNav({
  days,
  selected,
  onSelect,
}: {
  days: string[];
  selected: string | null;
  onSelect: (d: string | null) => void;
}) {
  if (!days.length) return null;
  const latest = days[0];
  const cur = selected ?? latest;
  const idx = days.indexOf(cur);
  if (idx < 0) return null;
  const canNewer = idx > 0; // a more recent day exists
  const canOlder = idx < days.length - 1;
  const goNewer = () => canNewer && onSelect(days[idx - 1] === latest ? null : days[idx - 1]);
  const goOlder = () => canOlder && onSelect(days[idx + 1]);
  return (
    <div className="daynav">
      <button className="daynav-btn" onClick={goOlder} disabled={!canOlder} aria-label="Previous day">
        ‹
      </button>
      <span className="daynav-label">{dayLabel(cur, latest)}</span>
      <button className="daynav-btn" onClick={goNewer} disabled={!canNewer} aria-label="Next day">
        ›
      </button>
    </div>
  );
}

export function Home({ onNavigate }: { onNavigate: (p: Page) => void }) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const channels = useHomeChannels(selectedDate);
  const availableDays = useAvailableDays();
  const syncStatus = useSyncStatus();
  const dataStatus = useDataStatus();
  const sync = useSync();
  const batteryKnown = sync.battery != null;
  const [showAll, setShowAll] = useState(false);
  const days = availableDays.data ?? [];
  const latest = days[0] ?? null;
  const viewingPast = selectedDate != null && selectedDate !== latest;

  const byId = new Map<string, Channel>((channels.data ?? []).map((c) => [c.id, c]));
  const realCount = (channels.data ?? []).filter((c) => c.status === "real").length;
  const ids = showAll ? [...PRIMARY, ...SECONDARY] : PRIMARY;

  if (dataStatus.data && !dataStatus.data.rawDbExists) {
    return (
      <main className="main home">
        <div className="notice">
          <h2>No ring data yet</h2>
          <p>
            The raw store isn't here yet. Run a sync — <code>oura --db oura.db sync</code> — or point
            the app at your data with <code>OURA_LOCAL_RAW_DB</code>.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className={`main home ${showAll ? "scroll" : ""}`}>
      <div className="strip">
        <div className="hello">
          <h1>{greeting()}</h1>
          <div className="sub">
            Last sync <b>{relTime(syncStatus.data?.lastSyncUnix)}</b> ·{" "}
            <b>{fmtInt(syncStatus.data?.totalEvents ?? 0)}</b> events · data through{" "}
            <b>{fmtDate(syncStatus.data?.lastCapturedUnix)}</b>
          </div>
          <DayNav days={days} selected={selectedDate} onSelect={setSelectedDate} />
        </div>
        <span
          className="radio-pill"
          style={
            sync.silent
              ? { background: "var(--sleep-tint)", color: "#6a61a6" }
              : batteryKnown
              ? { background: "var(--activity-tint)", color: "#5a7d63" }
              : { background: "var(--card-2)", color: "var(--muted)" }
          }
        >
          <span
            className="dot"
            style={
              sync.silent
                ? { background: "var(--sleep)", boxShadow: "none" }
                : batteryKnown
                ? {}
                : { background: "var(--muted)", boxShadow: "none" }
            }
          />
          {sync.silent
            ? "Silent · recording"
            : batteryKnown
            ? `Battery ${sync.battery}%`
            : "Ring · sync to check"}
        </span>
        <button className="btn ghost" onClick={sync.syncNow} disabled={sync.running}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 11a8 8 0 1 0-3 6" />
            <path d="M20 5v6h-6" />
          </svg>
          {sync.running ? "Syncing…" : "Sync now"}
        </button>
        <button className="btn" onClick={() => onNavigate("ring")}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 14a7 7 0 1 1-8-9 6 6 0 0 0 8 9z" />
          </svg>
          Dock &amp; Silence
        </button>
      </div>

      {channels.isError ? (
        <div className="notice">
          <h2>Couldn't read metrics</h2>
          <p>{String(channels.error)}</p>
        </div>
      ) : (
        <SelectedDateContext.Provider value={selectedDate}>
          <div className="grid">
            {ids.map((id) => {
              const ch = byId.get(id);
              if (!ch) return null;
              return <Tile key={id} channel={ch} mode="normal" style={cardStyle(id)} />;
            })}
          </div>
        </SelectedDateContext.Provider>
      )}

      <div className="foot">
        <button className="expandbtn" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show less ▲" : `Show all metrics ▾`}
        </button>
        <span>
          {viewingPast
            ? `${realCount} metrics for ${dayLabel(selectedDate!, latest ?? selectedDate!)}`
            : `${realCount} live from your last sync · more light up over ~a week of nights`}
        </span>
      </div>
    </main>
  );
}
