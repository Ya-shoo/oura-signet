// Icon set — SVG paths ported verbatim from the mockups. One <Icon name/> that
// renders a stroked 24x24 glyph in currentColor.
import type { ReactNode } from "react";

export type IconName =
  | "home"
  | "trends"
  | "sleep"
  | "body"
  | "activity"
  | "ring"
  | "settings"
  | "journal"
  | "insights"
  | "readiness"
  | "rhr"
  | "hrv"
  | "hr"
  | "spo2"
  | "temp"
  | "resp"
  | "steps"
  | "met"
  | "vo2"
  | "flame"
  | "sync"
  | "rewake"
  | "check"
  | "xcircle"
  | "warn"
  | "lock"
  | "moon"
  | "key";

const P: Record<IconName, ReactNode> = {
  home: (
    <>
      <path d="M4 11l8-6 8 6" />
      <path d="M6 10v9h12v-9" />
    </>
  ),
  trends: <path d="M4 15c3 0 4-8 8-8s5 6 8 6" />,
  sleep: <path d="M20 14a7 7 0 1 1-8-9 6 6 0 0 0 8 9z" />,
  moon: <path d="M20 14a7 7 0 1 1-8-9 6 6 0 0 0 8 9z" />,
  body: <path d="M12 21s-6-4.6-6-9a6 6 0 0 1 12 0c0 4.4-6 9-6 9z" />,
  hr: <path d="M12 21s-6-4.6-6-9a6 6 0 0 1 12 0c0 4.4-6 9-6 9z" />,
  activity: <path d="M4 12h4l2 5 4-12 2 7h4" />,
  met: <path d="M4 12h4l2 5 4-12 2 7h4" />,
  vo2: (
    <>
      <path d="M4 18a8 8 0 0 1 16 0" />
      <path d="M12 18l3.4-4.6" />
      <circle cx="12" cy="18" r="1.3" fill="currentColor" stroke="none" />
    </>
  ),
  flame: (
    <path d="M12 3c3.4 4 5 6.5 5 9.5a5 5 0 0 1-10 0c0-1.6.7-3 1.8-4.2.4 1.2 1.2 1.8 1.9 1.9C10.6 8.4 9.7 6.2 12 3z" />
  ),
  ring: (
    <>
      <circle cx="12" cy="13" r="6" />
      <path d="M9 6l1-2h4l1 2" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.5 5.5l2 2M16.5 16.5l2 2M18.5 5.5l-2 2M7.5 16.5l-2 2" />
    </>
  ),
  journal: <path d="M6 4h9a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2V6a2 2 0 0 1 2-2z" />,
  insights: (
    <>
      <path d="M12 3a6 6 0 0 0-4 10c1 1 1 2 1 3h6c0-1 0-2 1-3a6 6 0 0 0-4-10z" />
      <path d="M9 20h6" />
    </>
  ),
  readiness: <path d="M12 20s-7-4.5-7-10a4.2 4.2 0 0 1 7-3 4.2 4.2 0 0 1 7 3c0 5.5-7 10-7 10z" />,
  rhr: <path d="M3 12h4l2-5 3 10 2-6 2 3h5" />,
  hrv: <path d="M3 12h3l2 6 4-14 3 10 2-4h4" />,
  spo2: <path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z" />,
  temp: <path d="M12 3a2 2 0 0 1 2 2v8.5a4 4 0 1 1-4 0V5a2 2 0 0 1 2-2z" />,
  resp: <path d="M3 12h3c1 0 1-4 2-4s1.5 8 2.5 8 1.5-6 2.5-6 1 2 2 2h5" />,
  steps: (
    <>
      <path d="M9 4c1.6 0 2.5 1.6 2.5 4S10.5 15 9 15s-2.5-.5-2.5-3S7.4 4 9 4z" />
      <path d="M6.7 15.5c-.4 1.8.2 3 1.6 3.4M15 8c1.5 0 2.4 1.6 2.4 4s-1 4-2.4 4" />
    </>
  ),
  sync: (
    <>
      <path d="M20 11a8 8 0 1 0-3 6" />
      <path d="M20 5v6h-6" />
    </>
  ),
  rewake: (
    <>
      <path d="M4 12a8 8 0 1 0 3-6" />
      <path d="M4 4v5h5" />
    </>
  ),
  key: (
    <>
      <path d="M4 12a8 8 0 1 0 3-6" />
      <path d="M4 4v5h5" />
    </>
  ),
  check: <path d="M20 6L9 17l-5-5" />,
  xcircle: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M6 6l12 12" />
    </>
  ),
  warn: (
    <path d="M12 9v4M12 17h.01M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  ),
  lock: (
    <>
      <rect x="4" y="9" width="16" height="9" rx="2.5" />
      <path d="M8 9V7a4 4 0 0 1 8 0v2" />
    </>
  ),
};

export function Icon({
  name,
  sw = 1.9,
  className,
}: {
  name: IconName;
  sw?: number;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      {P[name]}
    </svg>
  );
}
