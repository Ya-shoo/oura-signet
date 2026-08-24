// A single "health channel" tile. It animates its own size/position (Framer
// `layout`) as the grid morphs, and renders one of three views:
//   • normal   — the resting grid look
//   • compact  — shrunk to make room when another card is focused
//   • expanded — the focused "secondary view": big chart, range chips, full stats
import { useState, type CSSProperties } from "react";
import type { Channel } from "../lib/api";
import { METRICS, FAMILY_ACCENT } from "../lib/metrics";
import { fmtValue } from "../lib/format";
import { Icon } from "./icons";
import { Sparkline, TileChartingProvider, useTooltip } from "./charts";
import { useVo2Trend, useActivitySessions } from "../lib/hooks";
import { useSelectedDate } from "../lib/selectedDate";
import { fmtInt } from "../lib/format";
import type { ActivitySession } from "../lib/api";

export type TileMode = "normal" | "expanded";

function sparkFmt(v: number, unit: string): string {
  if (unit === "°C" || unit === "°F") return `${v.toFixed(1)} ${unit}`;
  if (unit === "MET") return `${v.toFixed(2)} MET`;
  if (unit === "%") return `${Math.round(v)} %`;
  return `${Math.round(v)}${unit ? " " + unit : ""}`;
}
function fmtBound(x: number, unit: string): string {
  if (unit === "°C" || unit === "°F" || unit === "MET") return x.toFixed(1);
  return String(Math.round(x));
}

// Per-point clock time for a sparkline hover, when the channel carries an intraday
// time axis (series_start_min / series_step_min from the backend). Undefined for
// non-temporal series → the tooltip then shows the value alone.
function timeLabelFor(ch: Channel): ((i: number, n: number) => string) | undefined {
  const start = ch.seriesStartMin;
  const step = ch.seriesStepMin;
  if (start == null || step == null) return undefined;
  return (i) => fmtClock(start + i * step);
}

export function Tile({
  channel,
  mode,
  style,
  onFocus,
}: {
  channel: Channel;
  mode: TileMode;
  style?: CSSProperties;
  onFocus?: () => void;
}) {
  const [charting, setCharting] = useState(false);
  const [headerHover, setHeaderHover] = useState(false);
  const meta = METRICS[channel.id];
  if (!meta) return null;

  const fam = FAMILY_ACCENT[meta.family];
  const isPending = channel.status === "pending";
  const cls = ["tile", `mode-${mode}`, isPending ? "pending" : "", charting ? "charting" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={cls}
      data-metric={channel.id}
      style={style}
      onMouseEnter={onFocus}
      onMouseLeave={() => setHeaderHover(false)}
    >
      <TileChartingProvider value={setCharting}>
        {channel.status === "real" ? (
          <span className="hint">i</span>
        ) : (
          <span className={`badge ${channel.status}`}>{channel.status}</span>
        )}

        <div className="thead">
          <span className="chip" style={{ background: fam.tint, color: fam.accent }}>
            <Icon name={meta.icon} sw={2} />
          </span>
          <span
            className="tname"
            onMouseEnter={() => setHeaderHover(true)}
            onMouseLeave={() => setHeaderHover(false)}
          >
            <span className="fam">{meta.famLabel}</span>
            {meta.name}
          </span>
        </div>

        {mode === "expanded" ? (
          isPending ? (
            <ExpandedPending channel={channel} />
          ) : channel.id === "sleep" ? (
            <SleepBody channel={channel} expanded />
          ) : (
            <ExpandedBody channel={channel} accent={fam.vivid} />
          )
        ) : isPending ? (
          <PendingBody note={channel.note} big={channel.id === "hr"} />
        ) : channel.id === "sleep" ? (
          <SleepBody channel={channel} />
        ) : channel.id === "vo2max" ? (
          <Vo2Body channel={channel} />
        ) : channel.id === "bmr" ? (
          <CalBody channel={channel} />
        ) : channel.id === "hr" ? (
          <HeroBody channel={channel} accent={fam.accent} vivid={fam.vivid} />
        ) : channel.id === "steps" ? (
          <StepsBody channel={channel} accent={fam.vivid} />
        ) : channel.id === "met" ? (
          <MetBody channel={channel} accent={fam.vivid} />
        ) : channel.id === "readiness" ? (
          <ReadinessBody channel={channel} />
        ) : channel.id === "resp" ? (
          <RespBody channel={channel} />
        ) : channel.id === "cva" ? (
          <CvaBody channel={channel} />
        ) : channel.id === "dayhr" ? (
          <DayHrBody channel={channel} />
        ) : (
          <StatBody channel={channel} accent={fam.vivid} />
        )}

        <div className={`pop ${headerHover ? "open" : ""}`}>
          <p className="pexp">{meta.explain}</p>
          {channel.status !== "real" && channel.note && (
            <p className="pexp" style={{ color: "var(--muted)", fontSize: 11 }}>
              {channel.status === "preview" ? "Preview — " : "Coming — "}
              {channel.note}
            </p>
          )}
        </div>
      </TileChartingProvider>
    </div>
  );
}

// ---- expanded (this card is focused): the secondary detail view ----
function ExpandedBody({ channel, accent }: { channel: Channel; accent: string }) {
  const disp = channel.value != null ? fmtValue(channel.value, channel.unit) : { main: "—", small: "" };
  const preview = channel.status === "preview";
  const hasSeries = channel.series.length > 0;
  return (
    <>
      <div className="exp-top">
        <div className="value" style={{ fontSize: 38 }}>
          {disp.main}
          {preview && <sup className="ast">*</sup>}
          {disp.small && <small>{disp.small}</small>}
        </div>
        {hasSeries && (
          <div className="ranges">
            <button className="rchip on">Day</button>
            <button className="rchip" disabled title="Builds as more nights sync">
              Week
            </button>
            <button className="rchip" disabled title="Builds as more nights sync">
              Month
            </button>
          </div>
        )}
      </div>
      {hasSeries ? (
        <div className="spark" style={{ color: accent, flex: 1, minHeight: 0, marginTop: 6 }}>
          <Sparkline
            data={channel.series}
            area
            valueFmt={(x) => sparkFmt(x, channel.unit)}
            labelFor={timeLabelFor(channel)}
          />
        </div>
      ) : channel.sub ? (
        <div className="xrow" style={{ marginTop: 6 }}>{channel.sub}</div>
      ) : null}
      <div className="exp-stats">
        {channel.lo != null && (
          <div>
            <span>low</span>
            <b>{fmtBound(channel.lo, channel.unit)}</b>
          </div>
        )}
        {channel.mean != null && (
          <div>
            <span>avg</span>
            <b>{fmtBound(channel.mean, channel.unit)}</b>
          </div>
        )}
        {channel.hi != null && (
          <div>
            <span>high</span>
            <b>{fmtBound(channel.hi, channel.unit)}</b>
          </div>
        )}
        {channel.sampleCount > 0 && (
          <div>
            <span>reads</span>
            <b>{channel.sampleCount.toLocaleString()}</b>
          </div>
        )}
      </div>
      {preview && channel.note && <div className="caveat">* {channel.note}</div>}
    </>
  );
}

function ExpandedPending({ channel }: { channel: Channel }) {
  return (
    <div className="pending-body" style={{ marginTop: 10 }}>
      <div className="ranges" style={{ marginBottom: 4 }}>
        <button className="rchip" disabled>
          Day
        </button>
        <button className="rchip" disabled>
          Week
        </button>
        <button className="rchip" disabled>
          Month
        </button>
      </div>
      <div className="pending-line w2" />
      <div className="pending-line w1" />
      <div className="pending-note" style={{ fontSize: 12.5, marginTop: 6 }}>
        {channel.note}
      </div>
    </div>
  );
}

// ---- normal ----
function StatBody({ channel, accent }: { channel: Channel; accent: string }) {
  const v = channel.value;
  const disp = v != null ? fmtValue(v, channel.unit) : { main: "—", small: "" };
  const preview = channel.status === "preview";
  return (
    <>
      <div className="value">
        {disp.main}
        {preview && <sup className="ast">*</sup>}
        {disp.small && <small>{disp.small}</small>}
      </div>
      {preview ? (
        <div className="caveat">* {channel.note}</div>
      ) : channel.sub ? (
        <div className="delta">
          <span className="tag">{channel.sub}</span>
        </div>
      ) : null}
      {channel.series.length > 0 && (
        <div className="spark" style={{ color: accent, flex: 1, minHeight: 0, marginTop: 10 }}>
          <Sparkline
            data={channel.series}
            valueFmt={(x) => sparkFmt(x, channel.unit)}
            labelFor={timeLabelFor(channel)}
          />
        </div>
      )}
    </>
  );
}

// Breathing card — the overnight average breaths/min against the healthy range,
// with the (baseline-deviation or steadiness) readout in `sub`. Value is derived
// from respiratory sinus arrhythmia, the same signal Oura uses.
const RESP_LO = 8;
const RESP_HI = 22; // healthy overnight breathing band (br/min)
function RespBody({ channel }: { channel: Channel }) {
  const v = channel.value ?? 0;
  const preview = channel.status === "preview";
  const pos = Math.max(0, Math.min(1, (v - RESP_LO) / (RESP_HI - RESP_LO)));
  const trend = channel.series; // recent nightly rates (oldest→newest)
  return (
    <>
      <div className="value">
        {Math.round(v)}
        {preview && <sup className="ast">*</sup>}
        <small>br/min</small>
      </div>
      {channel.sub && (
        <div className="delta">
          <span className="tag">{channel.sub}</span>
        </div>
      )}
      {trend.length >= 2 && (
        <div className="spark" style={{ color: "var(--body-vivid)", flex: 1, minHeight: 0, marginTop: 12 }}>
          <Sparkline data={trend} area valueFmt={(x) => `${x.toFixed(1)} br/min`} />
        </div>
      )}
      <div className="respband">
        <div className="respscale">
          <i className="respmark" style={{ left: `${pos * 100}%` }} />
        </div>
        <div className="respaxis">
          <span>{RESP_LO}</span>
          <span>{trend.length >= 2 ? "healthy range" : "breaths / min"}</span>
          <span>{RESP_HI}</span>
        </div>
      </div>
    </>
  );
}

// Cardiovascular age — vascular age set against your real age (the headline),
// an age axis with both marked, and the pulse-wave-velocity (arterial stiffness)
// readout. `mean` carries chronological age, `lo` carries PWV (from Rust).
const CVA_LO = 18;
const CVA_HI = 45;
function CvaBody({ channel }: { channel: Channel }) {
  const vasc = Math.round(channel.value ?? 0);
  const age = channel.mean != null ? Math.round(channel.mean) : null;
  const pwv = channel.lo ?? null;
  const delta = age != null ? age - vasc : null; // + = younger than your age
  const younger = delta != null && delta > 0;
  const verdict =
    delta == null
      ? ""
      : delta === 0
      ? "on par with your age"
      : `${Math.abs(delta)} yr ${younger ? "younger" : "older"} than your age`;
  const px = (yr: number) => Math.max(0, Math.min(1, (yr - CVA_LO) / (CVA_HI - CVA_LO))) * 100;
  const pwvLabel =
    pwv == null ? null : pwv < 6.5 ? "supple arteries" : pwv < 8 ? "typical stiffness" : "elevated stiffness";
  const lo = age != null ? Math.min(age, vasc) : vasc;
  const hi = age != null ? Math.max(age, vasc) : vasc;
  return (
    <>
      <div className="value">
        {vasc}
        <small>yr</small>
      </div>
      {verdict && (
        <div className="delta">
          <span className={`tag ${younger ? "good" : delta! < 0 ? "warn" : ""}`}>{verdict}</span>
        </div>
      )}
      {age != null && (
        <div className="cvaaxis">
          <div className="cvatrack">
            <i className="cvaseg" style={{ left: `${px(lo)}%`, width: `${px(hi) - px(lo)}%` }} />
            <i className="cvatick chrono" style={{ left: `${px(age)}%` }} title={`your age ${age}`} />
            <i className="cvatick vasc" style={{ left: `${px(vasc)}%` }} title={`vascular ${vasc}`} />
          </div>
          <div className="cvaends">
            <span>{CVA_LO}</span>
            <span>years</span>
            <span>{CVA_HI}</span>
          </div>
        </div>
      )}
      <div className="cvalegend">
        <span>
          <i className="cdot vasc" /> vascular {vasc}
        </span>
        {age != null && (
          <span>
            <i className="cdot chrono" /> your age {age}
          </span>
        )}
      </div>
      {pwv != null && (
        <div className="cvapwv">
          <span className="cvapwvk">arterial stiffness</span>
          <b>{pwv.toFixed(1)} m/s</b>
          {pwvLabel && <span className="cvapwvlab">· {pwvLabel}</span>}
        </div>
      )}
    </>
  );
}

// Daytime HR card — the waking-hours counterpart to the overnight `hr` card.
// value = typical (median) HR; lo = resting (awake floor); hi = peak; mean =
// active (90th pct). `series` = the five HR-zone weights (resting|light|active|
// cardio|peak) → a "time in zone" bar. Cool→warm, conventional HR-zone colors.
const HR_ZONES = [
  { label: "resting", color: "#5b8def" },
  { label: "light", color: "#3fb27f" },
  { label: "active", color: "#e6b422" },
  { label: "cardio", color: "#ef8b3b" },
  { label: "peak", color: "#e5484d" },
];
function DayHrBody({ channel }: { channel: Channel }) {
  const typical = Math.round(channel.value ?? 0);
  const resting = channel.lo != null ? Math.round(channel.lo) : null;
  const peak = channel.hi != null ? Math.round(channel.hi) : null;
  const active = channel.mean != null ? Math.round(channel.mean) : null;
  const weights = channel.series ?? [];
  const total = weights.reduce((a, b) => a + b, 0);
  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginTop: 2 }}>
        <div className="value">
          {typical}
          <small>bpm typical</small>
        </div>
        {resting != null && peak != null && (
          <div className="delta" style={{ color: "var(--muted)" }}>
            resting {resting} · peak {peak}
          </div>
        )}
      </div>
      {total > 0 && (
        <div style={{ marginTop: 14 }}>
          <div
            style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", gap: 1 }}
            role="img"
            aria-label="share of your waking hours in each heart-rate zone"
          >
            {weights.map((w, i) => {
              const pct = (w / total) * 100;
              if (pct <= 0) return null;
              const z = HR_ZONES[i];
              return (
                <div
                  key={i}
                  title={`${z.label} · ${Math.round(pct)}% of your day`}
                  style={{ width: `${pct}%`, background: z.color }}
                />
              );
            })}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "5px 12px", marginTop: 10 }}>
            {HR_ZONES.map((z, i) => {
              const pct = total > 0 ? (weights[i] / total) * 100 : 0;
              if (pct < 1) return null;
              return (
                <span
                  key={i}
                  style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--muted)" }}
                >
                  <i style={{ width: 8, height: 8, borderRadius: 2, background: z.color, display: "inline-block" }} />
                  {z.label} {Math.round(pct)}%
                </span>
              );
            })}
          </div>
        </div>
      )}
      {active != null && (
        <div style={{ marginTop: 10, fontSize: 12, color: "var(--muted)" }}>
          active spells reach <b style={{ color: "var(--ink)" }}>{active}</b> bpm
        </div>
      )}
    </>
  );
}

// Readiness card — a recovery score with a ring gauge and a per-contributor
// breakdown. `series` carries the five contributor sub-scores (0-100, or -1 =
// still calibrating) in this fixed order, matching the Rust compute_readiness.
const RD_LABELS = ["Sleep", "Resting HR", "HRV balance", "Body temp", "Breathing"];
function rdColor(s: number): string {
  if (s >= 80) return "var(--good)";
  if (s >= 65) return "var(--watch)";
  return "var(--dev)";
}
function ReadinessRing({ score }: { score: number }) {
  const R = 27;
  const C = 2 * Math.PI * R;
  const frac = Math.max(0, Math.min(1, score / 100));
  const color = rdColor(score);
  return (
    <svg viewBox="0 0 72 72" className="rdgauge">
      <circle cx="36" cy="36" r={R} className="rdtrack" />
      <circle
        cx="36"
        cy="36"
        r={R}
        className="rdfill"
        style={{ stroke: color, strokeDasharray: C, strokeDashoffset: C * (1 - frac) }}
      />
      <text x="36" y="36" className="rdscore" style={{ fill: color }}>
        {Math.round(score)}
      </text>
    </svg>
  );
}
function ReadinessBody({ channel }: { channel: Channel }) {
  const score = channel.value ?? 0;
  const preview = channel.status === "preview";
  const contribs = channel.series; // [sleep, rhr, hrv, temp, resp]
  const sub = channel.sub ?? "";
  const label = sub.split("·")[0].trim() || "readiness";
  const detail = sub.split("·").slice(1).join("·").trim();
  return (
    <div className="rdbody">
      <div className="rdhead">
        <ReadinessRing score={score} />
        <div className="rdmeta">
          <div className="rdlabel-main">
            {label}
            {preview && <sup className="ast">*</sup>}
          </div>
          {detail && <div className="rdsub">{detail}</div>}
        </div>
      </div>
      {contribs.length > 0 && (
        <div className="rdcontribs">
          {contribs.map((s, i) => (
            <div className="rdcontrib" key={i}>
              <span className="rdclabel">{RD_LABELS[i] ?? ""}</span>
              {s >= 0 ? (
                <>
                  <div className="rdcbar">
                    <i style={{ width: `${Math.round(s)}%`, background: rdColor(s) }} />
                  </div>
                  <b className="rdcval">{Math.round(s)}</b>
                </>
              ) : (
                <>
                  <div className="rdcbar rdccal" title="still calibrating" />
                  <b className="rdcval rdcalx" title="still calibrating">
                    ·
                  </b>
                </>
              )}
            </div>
          ))}
        </div>
      )}
      {preview && channel.note && <div className="caveat">* {channel.note}</div>}
    </div>
  );
}

// Steps card — an iOS-Health-style hourly bar chart: rounded tall bars, one per
// hour of the day, with the day's total called out above. `series` is 24 hourly
// step counts from the ring's own step model (empty on days without hourly detail).
function hourLabel(h: number): string {
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12} ${ap}`;
}
function StepsBody({ channel, accent }: { channel: Channel; accent: string }) {
  const total = Math.round(channel.value ?? 0);
  const hourly = channel.series;
  const hasHourly = hourly.length === 24 && hourly.some((v) => v > 0);
  const peak = Math.max(1, ...hourly);
  return (
    <div className="steps-body">
      <div className="steps-total">
        <span className="value">{fmtInt(total)}</span>
        <small>steps</small>
      </div>
      {hasHourly ? (
        <>
          <div className="steps-chart">
            {hourly.map((v, h) => (
              <div key={h} className="steps-col" title={`${hourLabel(h)} · ${Math.round(v)} steps`}>
                <div
                  className="steps-bar"
                  style={{ height: v > 0 ? `${Math.max(8, (v / peak) * 100)}%` : "0%", background: accent }}
                />
              </div>
            ))}
          </div>
          <div className="steps-axis">
            <span>12A</span>
            <span>6A</span>
            <span>12P</span>
            <span>6P</span>
            <span />
          </div>
        </>
      ) : (
        <div className="steps-empty">{channel.sub ?? "no hourly step detail for this day"}</div>
      )}
    </div>
  );
}

// Movement card = the MET sparkline (StatBody) + detected activity sessions.
function MetBody({ channel, accent }: { channel: Channel; accent: string }) {
  return (
    <>
      <StatBody channel={channel} accent={accent} />
      <SessionsStrip />
    </>
  );
}

function relDay(date: string): string {
  const d = new Date(date + "T00:00:00");
  const today = new Date();
  const days = Math.round((today.setHours(0, 0, 0, 0) - d.getTime()) / 864e5);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short" });
}

// Activity sessions. Prefers Oura's real Automatic Activity Detection model
// (source "aad": window + is_workout probability + a tentative sport guess); falls
// back to our MET-stream heuristic (source "met") when the model has nothing yet.
// Shows the most recent one prominently + a count if there are more.
function SessionsStrip() {
  const date = useSelectedDate();
  const sessions = useActivitySessions(date).data ?? [];
  if (!sessions.length) return null;
  const s: ActivitySession = sessions[0];
  const dur =
    s.durationMin >= 60
      ? `${Math.floor(s.durationMin / 60)}h ${s.durationMin % 60}m`
      : `${s.durationMin}m`;
  const isAad = s.source === "aad";
  const pct = s.workoutProb != null ? Math.round(s.workoutProb * 100) : null;
  return (
    <div className="sessions">
      <div className="sesshead">
        <span className="sessdot" />
        {isAad ? (
          <>
            Oura workout{sessions.length > 1 ? `s · ${sessions.length}` : ""}
            {pct != null && <span className="sessprob"> · {pct}% likely</span>}
          </>
        ) : (
          <>activity session{sessions.length > 1 ? `s · ${sessions.length}` : ""}</>
        )}
      </div>
      <div className="sessrow">
        <b>{dur}</b>
        <span>
          {relDay(s.date)} · {fmtClock(s.startMin)}–{fmtClock(s.endMin)}
        </span>
        <span className="sessmet">
          peak {s.peakMet.toFixed(1)} · {Math.round(s.activeKcal)} kcal
        </span>
      </div>
      {isAad && s.sport && (
        // The model even guesses a type — but the sport label is weak (gait input
        // stubbed), so it's shown as a clearly-tentative hint, never as fact.
        <div className="sesshint">model’s guess: {s.sport}? (low confidence)</div>
      )}
    </div>
  );
}

function HeroBody({ channel, accent, vivid }: { channel: Channel; accent: string; vivid: string }) {
  const mean = channel.mean ?? channel.value ?? 0;
  const preview = channel.status === "preview";
  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginTop: 4 }}>
        <div className="value" style={{ fontSize: 40 }}>
          {Math.round(mean)}
          {preview && <sup className="ast">*</sup>}
          <small>bpm avg</small>
        </div>
        {channel.sub && (
          <div className="delta" style={{ color: "var(--muted)" }}>
            {channel.sub}
          </div>
        )}
      </div>
      <div className="spark" style={{ color: vivid, flex: 1, minHeight: 0, marginTop: 10 }}>
        <Sparkline
          data={channel.series}
          area
          valueFmt={(x) => sparkFmt(x, channel.unit)}
          labelFor={timeLabelFor(channel)}
        />
      </div>
      <div className="legend">
        {channel.lo != null && <b style={{ color: accent }}>low {Math.round(channel.lo)}</b>}
        <b style={{ color: accent }}>avg {Math.round(mean)}</b>
        {channel.hi != null && <b style={{ color: accent }}>high {Math.round(channel.hi)}</b>}
      </div>
    </>
  );
}

// ---- sleep (hypnogram from the local SleepECG stager) ----
// Sequential sleep-depth ramp (monotonic light→dark: wake→light→rem→deep) + a
// warm tan for wake so it reads as "not asleep". Cells carry hover labels too.
const HYP_COLOR: Record<number, string> = {
  0: "#dcbf82", // wake — warm tan
  1: "#b99cfb", // light — lavender
  2: "#8f5ff6", // REM — purple
  3: "#4a2fcc", // deep — indigo
};
const HYP_HEIGHT: Record<number, string> = { 0: "26%", 1: "56%", 2: "76%", 3: "100%" };
const STAGE_NAME: Record<number, string> = { 0: "Awake", 1: "Light", 2: "REM", 3: "Deep" };

// Sleep-efficiency quality bands → colour class for the badge. Oura treats ~85%+
// as a solid night; below ~78% the night is trending fragmented.
function effBand(eff: number): string {
  if (eff >= 88) return "good";
  if (eff >= 78) return "watch";
  return "low";
}

// Duration in minutes → "2h44m" / "50m".
function fmtDur(min: number): string {
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

// 12-hour clock with AM/PM. `min` is minutes-since-local-midnight (already in the
// laptop's timezone — Rust computes it via SQLite localtime), so this is display-only.
function fmtClock(min: number): string {
  const t = (((min % 1440) + 1440) % 1440);
  const h24 = Math.floor(t / 60);
  const mm = Math.floor(t % 60);
  const ap = h24 < 12 ? "AM" : "PM";
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h12}:${String(mm).padStart(2, "0")} ${ap}`;
}

function SleepBody({ channel, expanded }: { channel: Channel; expanded?: boolean }) {
  const { show, hide } = useTooltip();
  const tst = channel.value ?? 0;
  const h = Math.floor(tst / 60);
  const m = Math.round(tst % 60);
  const startMin = channel.mean; // window start (min since local midnight)
  const perBar = channel.lo; // minutes each bar covers
  const eff = channel.hi; // sleep efficiency (%) — carried in `hi`
  const n = channel.series.length;
  const native = channel.source === "oura-native";
  // stageMin = [wake, light, rem, deep]; show most→least prominent, skip empties
  const sm = channel.stageMin ?? [];
  const stages = [
    { code: 3, label: "Deep", min: sm[3] ?? 0 },
    { code: 1, label: "Light", min: sm[1] ?? 0 },
    { code: 2, label: "REM", min: sm[2] ?? 0 },
    { code: 0, label: "Awake", min: sm[0] ?? 0 },
  ].filter((s) => s.min > 0);
  return (
    <>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginTop: 2 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div className="value" style={{ fontSize: expanded ? 36 : 27 }}>
              {h}
              <small>h</small> {m}
              <small>m</small>
              <sup className="ast">*</sup>
            </div>
            {eff != null && (
              <div className={`sleepeff ${effBand(eff)}`} title="time asleep ÷ time in bed">
                <b>{Math.round(eff)}%</b>
                <span>efficiency</span>
              </div>
            )}
          </div>
          {channel.sub && <div className="sleepwin">{channel.sub}</div>}
        </div>
        {stages.length > 0 && (
          <div className="sleepstages">
            {stages.map((s) => (
              <div className="sstage" key={s.code}>
                <i style={{ background: HYP_COLOR[s.code] }} />
                <span className="slab">{s.label}</span>
                <b>{fmtDur(s.min)}</b>
              </div>
            ))}
          </div>
        )}
      </div>
      <div
        className="hyp"
        style={{ flex: 1, minHeight: 0, height: "auto", marginTop: expanded ? 12 : 8 }}
        onMouseLeave={hide}
      >
        {channel.series.map((code, i) => (
          <i
            key={i}
            style={
              {
                "--c": HYP_COLOR[code] ?? HYP_COLOR[1],
                height: HYP_HEIGHT[code] ?? "56%",
                animationDelay: `${i * 11}ms`,
              } as CSSProperties
            }
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              const time =
                startMin != null && perBar != null
                  ? `${fmtClock(startMin + i * perBar)}–${fmtClock(startMin + (i + 1) * perBar)}`
                  : `cell ${i + 1} of ${n}`;
              show(r.left + r.width / 2, r.top, STAGE_NAME[code] ?? "Sleep", time);
            }}
          />
        ))}
      </div>
      <div className="caveat" style={{ marginTop: 7 }}>
        {native
          ? "the ring's own 4-stage staging · algorithmic, no EEG · hover a cell for its stage & time"
          : "* estimated locally from HR/HRV + motion · no EEG ground truth · hover a cell for its stage & time"}
      </div>
    </>
  );
}

// ---- VO₂max: where you land on the population distribution + a trend strip ----

// standard-normal CDF (Abramowitz–Stegun) → the percentile of a z-score.
function normCdf(z: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp(-z * z / 2);
  let p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z > 0 ? 1 - p : p;
}
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));

// A population bell-curve: your VO₂max marked against the distribution for your
// age/sex (centered on the "typical" estimate). The area to your left is shaded —
// that's your percentile. More telling than a zone bar: it shows where you fall.
function Vo2Curve({ value, typical }: { value: number; typical?: number }) {
  const { show, hide } = useTooltip();
  const W = 240;
  const PAD = 14;
  const BASE = 84;
  const AMP = 66;
  const SD = 7.5; // within-age/sex population SD (ml/kg/min)
  const center = typical ?? value;
  const half = 3.1 * SD;
  const dmin = center - half;
  const dmax = center + half;
  const xToPx = (v: number) => PAD + ((clamp(v, dmin, dmax) - dmin) / (dmax - dmin)) * (W - 2 * PAD);
  const pdf = (v: number) => Math.exp(-0.5 * ((v - center) / SD) ** 2);
  const yOf = (v: number) => BASE - pdf(v) * AMP;
  const N = 64;
  const pts = Array.from({ length: N + 1 }, (_, i) => {
    const v = dmin + (i / N) * (dmax - dmin);
    return [xToPx(v), yOf(v)] as [number, number];
  });
  const curve = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const youX = xToPx(value);
  const youY = yOf(value);
  const left = pts.filter((p) => p[0] <= youX);
  const shade =
    (left.length ? left.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") : `M ${xToPx(dmin).toFixed(1)} ${BASE}`) +
    ` L ${youX.toFixed(1)} ${youY.toFixed(1)} L ${youX.toFixed(1)} ${BASE} L ${xToPx(dmin).toFixed(1)} ${BASE} Z`;
  const pct = typical != null ? Math.round(normCdf((value - center) / SD) * 100) : null;
  const typX = typical != null ? xToPx(typical) : null;
  return (
    <div className="vo2curve" onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} 100`} style={{ width: "100%", display: "block" }}>
        <path className="curvefill" d={`${curve} L ${xToPx(dmax).toFixed(1)} ${BASE} L ${xToPx(dmin).toFixed(1)} ${BASE} Z`} />
        <path className="curveshade" d={shade} />
        <path className="curveline" d={curve} pathLength={1} />
        {typX != null && <line className="curvetyp" x1={typX} y1={BASE} x2={typX} y2={yOf(typical as number) - 2} />}
        {typX != null && (
          <text className="curvelab typ" x={typX} y={yOf(typical as number) - 6} textAnchor="middle">
            typical {Math.round(typical as number)}
          </text>
        )}
        <line className="curveyou" x1={youX} y1={BASE} x2={youX} y2={youY - 2} />
        <circle
          className="curvedot"
          cx={youX}
          cy={youY - 2}
          r={4.5}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            show(r.left + r.width / 2, r.top, `you · ${Math.round(value)} ml/kg`, pct != null ? `fitter than ~${pct}%` : undefined);
          }}
        />
        <text className="curvelab you" x={clamp(youX, PAD + 14, W - PAD - 14)} y={BASE + 13} textAnchor="middle">
          you {Math.round(value)}
        </text>
        <text className="curveend" x={PAD} y={BASE + 13} textAnchor="start">
          low
        </text>
        <text className="curveend" x={W - PAD} y={BASE + 13} textAnchor="end">
          elite
        </text>
      </svg>
      {pct != null && (
        <div className="vo2pct">
          fitter than <b>~{pct}%</b> for your age &amp; sex
        </div>
      )}
    </div>
  );
}

// Peak + a small trend indicator over the last ~6 months of logged tests.
function Vo2Trend() {
  const trend = useVo2Trend().data ?? [];
  if (trend.length < 2) {
    return <div className="vo2histnote">📈 6-month trend &amp; peak fill in as you re-test</div>;
  }
  // window to the last ~183 days from the most recent test
  const last = trend[trend.length - 1];
  const cutoff = Date.parse(last.date) - 183 * 864e5;
  const win = trend.filter((p) => Date.parse(p.date) >= cutoff);
  const series = win.map((p) => p.vo2);
  const peak = Math.max(...series);
  const delta = series[series.length - 1] - series[0];
  const arrow = delta > 0.5 ? "↗" : delta < -0.5 ? "↘" : "→";
  const cls = delta > 0.5 ? "up" : delta < -0.5 ? "down" : "flat";
  return (
    <div className="vo2hist">
      <div className="vo2histmini" style={{ color: "var(--activity-vivid)" }}>
        <Sparkline
          data={series}
          valueFmt={(x) => `${Math.round(x)} ml/kg`}
          labelFor={(i) =>
            win[i]
              ? new Date(win[i].date + "T00:00:00").toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                })
              : ""
          }
        />
      </div>
      <div className="vo2histstats">
        <span>
          peak <b>{Math.round(peak)}</b>
        </span>
        <span className={`vo2arrow ${cls}`}>
          {arrow} {delta > 0 ? "+" : ""}
          {Math.round(delta)} · 6mo
        </span>
      </div>
    </div>
  );
}

function Vo2Body({ channel }: { channel: Channel }) {
  const v = channel.value ?? 0;
  const typical = channel.mean ?? undefined; // population estimate, when a test supersedes it
  return (
    <>
      <div className="value">
        {Math.round(v)}
        <sup className="ast">*</sup>
        <small>ml/kg</small>
      </div>
      {channel.sub && (
        <div className="delta">
          <span className="tag">{channel.sub}</span>
        </div>
      )}
      <Vo2Curve value={v} typical={typical} />
      <Vo2Trend />
      {channel.note && (
        <div className="caveat" style={{ marginTop: 6 }}>
          * {channel.note}
        </div>
      )}
    </>
  );
}

// ---- Calories: total burned + an intraday active-burn curve + resting/active split ----
function fmtHour(h: number): string {
  const hh = ((h % 24) + 24) % 24;
  const ap = hh < 12 ? "AM" : "PM";
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${h12} ${ap}`;
}

function CalBody({ channel }: { channel: Channel }) {
  const { show, hide } = useTooltip();
  const total = channel.value ?? 0;
  const resting = channel.lo ?? 0;
  const active = channel.hi ?? 0;
  const burn = channel.series; // 24 per-hour active-kcal buckets (today)
  const firstIdx = burn.findIndex((v) => v > 0.3);
  const lastIdx = burn.length - 1 - [...burn].reverse().findIndex((v) => v > 0.3);
  const hasBurn = firstIdx >= 0;
  const lo = hasBurn ? Math.max(0, firstIdx - 1) : 0;
  const hi = hasBurn ? Math.min(23, lastIdx + 1) : 0;
  const bars = hasBurn ? burn.slice(lo, hi + 1).map((v, k) => ({ v, hour: lo + k })) : [];
  const peak = Math.max(...bars.map((b) => b.v), 0.001);
  const peakHour = bars.reduce((a, b) => (b.v > a.v ? b : a), { v: -1, hour: -1 }).hour;
  return (
    <>
      <div className="value">
        {Math.round(total).toLocaleString()}
        <small>kcal</small>
      </div>
      <div className="delta">
        <span className="tag">burned today</span>
      </div>
      {hasBurn ? (
        <div className="burnwrap">
          <div
            className="burnbars"
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const frac = (e.clientX - rect.left) / rect.width;
              const idx = Math.max(0, Math.min(bars.length - 1, Math.floor(frac * bars.length)));
              const b = bars[idx];
              const x = rect.left + ((idx + 0.5) / bars.length) * rect.width;
              const y = rect.bottom - Math.max(0.04, b.v / peak) * rect.height;
              show(
                x,
                y,
                `${fmtHour(b.hour)}–${fmtHour(b.hour + 1)}`,
                `${Math.round(b.v)} active kcal`
              );
            }}
            onMouseLeave={hide}
          >
            {bars.map((b) => (
              <div key={b.hour} className="burnbar">
                <i
                  className={b.hour === peakHour ? "peak" : ""}
                  style={{
                    height: `${Math.max(4, (b.v / peak) * 100)}%`,
                    opacity: b.hour === peakHour ? 1 : 0.62,
                    animationDelay: `${(b.hour - lo) * 20}ms`,
                  }}
                />
              </div>
            ))}
          </div>
          <div className="burnaxis">
            <span>{fmtHour(lo)}</span>
            <span>active burn / hour</span>
            <span>{fmtHour(hi + 1)}</span>
          </div>
        </div>
      ) : (
        <div className="burnempty">hourly burn fills in as you move with the ring on</div>
      )}
      <div className="calsplit">
        <div className="calbar">
          <div className="calrest" style={{ flexGrow: Math.max(0.001, resting) }} />
          <div className="calactive" style={{ flexGrow: Math.max(0.001, active) }} />
        </div>
        <div className="callegend">
          <span>
            <i className="dotrest" /> resting {Math.round(resting).toLocaleString()}
          </span>
          <span>
            <i className="dotactive" /> active {Math.round(active).toLocaleString()}
          </span>
        </div>
      </div>
    </>
  );
}

function PendingBody({ note, big }: { note: string; big?: boolean }) {
  return (
    <div className="pending-body">
      <div className="pending-line w2" />
      <div className="pending-line w1" />
      {big && <div className="pending-line w3" />}
      <div className="pending-note">{note}</div>
    </div>
  );
}
