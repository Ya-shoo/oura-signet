// Typed wrappers over the allow-listed Rust IPC commands. The webview only ever
// talks to the ring/DB through these — no raw SQL, no raw BLE from JS.
import { invoke } from "@tauri-apps/api/core";

export interface DeviceInfo {
  serial: string;
  firmware: string | null;
  hardwareId: string | null;
  apiVersion: string | null;
  mac: string | null;
  updatedUnix: number;
}

export interface SyncStatus {
  serial: string | null;
  nextCursor: number;
  lastSyncUnix: number | null;
  totalEvents: number;
  decodedEvents: number;
  firstCapturedUnix: number | null;
  lastCapturedUnix: number | null;
}

export interface EventCount {
  name: string;
  count: number;
}

export interface Vo2Point {
  date: string;
  vo2: number;
}

export interface ActivitySession {
  date: string;
  startMin: number;
  endMin: number;
  durationMin: number;
  avgMet: number;
  peakMet: number;
  activeKcal: number;
  /** AAD model's is_workout probability (0..1) — the reliable detection signal — when source is "aad". */
  workoutProb: number | null;
  /** The model's tentative sport guess (weak; gait input stubbed). null for the MET heuristic. */
  sport: string | null;
  /** "aad" = Oura's Automatic Activity Detection model; "met" = our MET-stream heuristic. */
  source: string;
}

export type ChannelStatus = "real" | "preview" | "pending";

export interface Channel {
  id: string;
  status: ChannelStatus;
  value: number | null;
  unit: string;
  lo: number | null;
  hi: number | null;
  mean: number | null;
  series: number[];
  sampleCount: number;
  sub: string | null;
  note: string;
  source: string | null;
  stageMin: number[];
  /** Intraday sparklines: clock time (min since local midnight) of series[0] and
   *  minutes per point, for per-point timestamps on hover. null when non-temporal. */
  seriesStartMin: number | null;
  seriesStepMin: number | null;
}

export interface DataStatus {
  rawDbPath: string;
  rawDbExists: boolean;
  derivedDbPath: string;
  derivedDbExists: boolean;
}

export type Units = "metric" | "imperial";

// The editable profile + the estimates it drives (Rust is the source of truth).
export interface Profile {
  age: number;
  sex: "M" | "F";
  weightKg: number;
  heightCm: number;
  units: Units;
  vo2max: number;
  vo2maxCategory: string;
  bmr: number;
  bmi: number | null;
}

export interface ProfileInput {
  age: number;
  sex: string;
  weightKg: number;
  heightCm: number;
  units: string;
}

export const api = {
  dataStatus: () => invoke<DataStatus>("get_data_status"),
  device: () => invoke<DeviceInfo | null>("get_device"),
  syncStatus: () => invoke<SyncStatus>("get_sync_status"),
  eventCounts: () => invoke<EventCount[]>("get_event_counts"),
  homeChannels: (date?: string | null) =>
    invoke<Channel[]>("get_home_channels", { date: date ?? null }),
  availableDays: () => invoke<string[]>("get_available_days"),
  vo2History: () => invoke<number[]>("get_vo2_history"),
  vo2Trend: () => invoke<Vo2Point[]>("get_vo2_trend"),
  activitySessions: (date?: string | null) =>
    invoke<ActivitySession[]>("get_activity_sessions", { date: date ?? null }),
  profile: () => invoke<Profile>("get_profile"),
  saveProfile: (profile: ProfileInput) => invoke<Profile>("set_profile", { profile }),
};
