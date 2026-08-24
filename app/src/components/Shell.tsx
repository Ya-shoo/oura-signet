// App shell: a warm draggable titlebar (native macOS traffic lights overlay it
// on the left) + the left nav rail + the active page.
import type { ReactNode } from "react";
import { Icon, type IconName } from "./icons";

export type Page = "home" | "trends" | "sleep" | "body" | "activity" | "ring" | "settings";

const NAV: { page: Page; icon: IconName; label: string }[] = [
  { page: "home", icon: "home", label: "Home" },
  { page: "trends", icon: "trends", label: "Trends" },
  { page: "sleep", icon: "sleep", label: "Sleep" },
  { page: "body", icon: "body", label: "Body" },
  { page: "activity", icon: "activity", label: "Activity" },
  { page: "ring", icon: "ring", label: "Ring" },
];

export function Shell({
  page,
  onNavigate,
  subtitle,
  children,
}: {
  page: Page;
  onNavigate: (p: Page) => void;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <div className="titlebar" data-tauri-drag-region>
        <span className="app">oura-local</span>
        {subtitle && <span className="app-sub">{subtitle}</span>}
      </div>
      <div className="app-body">
        <aside className="rail">
          <div className="brand">
            <svg viewBox="0 0 24 24" fill="none" stroke="#6f92ba" strokeWidth={2.1}>
              <circle cx="12" cy="12" r="7" />
              <circle cx="12" cy="12" r="2.4" fill="#6f92ba" stroke="none" />
            </svg>
          </div>
          <nav className="nav">
            {NAV.map((n) => (
              <button
                key={n.page}
                className={page === n.page ? "active" : ""}
                title={n.label}
                onClick={() => onNavigate(n.page)}
              >
                <Icon name={n.icon} />
              </button>
            ))}
            <span className="spacer" />
            <button
              className={page === "settings" ? "active" : ""}
              title="Settings"
              onClick={() => onNavigate("settings")}
            >
              <Icon name="settings" />
            </button>
          </nav>
        </aside>
        {children}
      </div>
    </div>
  );
}
