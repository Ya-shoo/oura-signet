// Live ring control shared across the app: sync (battery + history drain) and
// Dock & Silence (arm airplane mode 0x26 + prove silence). Reflects the streamed
// `sync-progress` events so the UI shows real progress, battery, and radio state.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useQueryClient } from "@tanstack/react-query";

interface SyncProgress {
  phase: string;
  message: string;
  battery: number | null;
  running: boolean;
}
interface SyncOutcome {
  battery: number | null;
  received: number;
  newRows: number;
}
interface ArmOutcome {
  silent: boolean;
  message: string;
}
interface SyncState {
  running: boolean;
  phase: string;
  message: string;
  battery: number | null;
  silent: boolean;
  error: string | null;
  finishedAt: number | null;
}
interface SyncCtx extends SyncState {
  syncNow: () => void;
  armAirplane: () => void;
  analyzeSleep: () => void;
}

const Ctx = createContext<SyncCtx | null>(null);
export function useSync(): SyncCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSync must be used within <SyncProvider>");
  return c;
}

export function SyncProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [s, setS] = useState<SyncState>({
    running: false,
    phase: "idle",
    message: "",
    battery: null,
    silent: false,
    error: null,
    finishedAt: null,
  });
  const runningRef = useRef(false);

  useEffect(() => {
    const un = listen<SyncProgress>("sync-progress", (e) => {
      const p = e.payload;
      setS((prev) => ({
        ...prev,
        phase: p.phase,
        message: p.message,
        battery: p.battery ?? prev.battery,
        running: p.running,
        error: p.phase === "error" ? p.message : null,
        // silence goes on when armed, off when a sync completes (a dock wakes it)
        silent: p.phase === "silent" ? true : p.phase === "done" ? false : prev.silent,
      }));
      if (p.phase === "done") qc.invalidateQueries();
    });
    return () => {
      un.then((f) => f());
    };
  }, [qc]);

  const syncNow = useCallback(() => {
    if (runningRef.current) return;
    runningRef.current = true;
    setS((prev) => ({
      ...prev,
      running: true,
      phase: "start",
      message: "Waking the ring…",
      error: null,
    }));
    invoke<SyncOutcome>("sync_now")
      .then((r) => {
        setS((prev) => ({
          ...prev,
          running: false,
          phase: "done",
          silent: false,
          battery: r.battery ?? prev.battery,
          message: `Synced ${r.newRows} new ${r.newRows === 1 ? "row" : "rows"}`,
          finishedAt: Date.now(),
        }));
        qc.invalidateQueries();
      })
      .catch((err) => {
        setS((prev) => ({
          ...prev,
          running: false,
          phase: "error",
          error: String(err),
          message: String(err),
          finishedAt: Date.now(),
        }));
      })
      .finally(() => {
        runningRef.current = false;
      });
  }, [qc]);

  const armAirplane = useCallback(() => {
    if (runningRef.current) return;
    runningRef.current = true;
    setS((prev) => ({
      ...prev,
      running: true,
      phase: "arming",
      message: "Arming airplane mode…",
      error: null,
    }));
    invoke<ArmOutcome>("arm_airplane")
      .then((r) => {
        setS((prev) => ({
          ...prev,
          running: false,
          phase: r.silent ? "silent" : "error",
          silent: r.silent,
          message: r.message,
          error: r.silent ? null : r.message,
          finishedAt: Date.now(),
        }));
      })
      .catch((err) => {
        setS((prev) => ({
          ...prev,
          running: false,
          phase: "error",
          error: String(err),
          message: String(err),
          finishedAt: Date.now(),
        }));
      })
      .finally(() => {
        runningRef.current = false;
      });
  }, []);

  const analyzeSleep = useCallback(() => {
    if (runningRef.current) return;
    runningRef.current = true;
    setS((prev) => ({
      ...prev,
      running: true,
      phase: "analyzing",
      message: "Asking the ring to analyze last night…",
      error: null,
    }));
    invoke<SyncOutcome>("analyze_sleep")
      .then((r) => {
        setS((prev) => ({
          ...prev,
          running: false,
          phase: "done",
          silent: false,
          message: `Fetched the ring's sleep · ${r.newRows} new rows`,
          finishedAt: Date.now(),
        }));
        qc.invalidateQueries();
      })
      .catch((err) => {
        setS((prev) => ({
          ...prev,
          running: false,
          phase: "error",
          error: String(err),
          message: String(err),
          finishedAt: Date.now(),
        }));
      })
      .finally(() => {
        runningRef.current = false;
      });
  }, [qc]);

  return (
    <Ctx.Provider value={{ ...s, syncNow, armAirplane, analyzeSleep }}>{children}</Ctx.Provider>
  );
}
