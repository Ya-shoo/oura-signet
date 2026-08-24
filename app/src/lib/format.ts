// Small formatting helpers. Times from Rust are unix seconds (UTC).

export function relTime(unix: number | null | undefined): string {
  if (!unix) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unix));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

export function fmtDateTime(unix?: number | null): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function fmtDate(unix?: number | null): string {
  if (!unix) return "—";
  return new Date(unix * 1000).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export function fmtInt(n: number): string {
  return Math.round(n).toLocaleString();
}

export function fmtFixed(n: number, digits = 1): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Format a channel value with its unit for display (value + optional unit small). */
export function fmtValue(v: number, unit: string): { main: string; small: string } {
  if (unit === "°C" || unit === "°F") return { main: fmtFixed(v, 1), small: unit };
  if (unit === "MET") return { main: fmtFixed(v, 2), small: "MET" };
  if (unit === "%") return { main: fmtInt(v), small: "%" };
  return { main: fmtInt(v), small: unit };
}
