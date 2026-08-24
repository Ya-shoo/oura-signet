// The sync toast — shows live progress while a sync runs, then the result.
import { useEffect, useState } from "react";
import { useSync } from "../lib/sync";

export function SyncToast() {
  const { running, phase, message, finishedAt } = useSync();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (running) {
      setVisible(true);
      return;
    }
    if (finishedAt) {
      setVisible(true);
      const t = setTimeout(() => setVisible(false), phase === "error" ? 7000 : 3800);
      return () => clearTimeout(t);
    }
  }, [running, finishedAt, phase]);

  if (!visible) return null;
  return (
    <div className={`sync-toast ${phase === "error" ? "err" : ""}`}>
      {running ? (
        <span className="sp" />
      ) : phase === "error" ? (
        <span className="tx">!</span>
      ) : (
        <span className="ok">✓</span>
      )}
      <span>{message || (running ? "Syncing…" : "Done")}</span>
    </div>
  );
}
