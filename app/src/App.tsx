import { useState } from "react";
import { Shell, type Page } from "./components/Shell";
import { TooltipProvider } from "./components/charts";
import { SyncProvider } from "./lib/sync";
import { SyncToast } from "./components/Toast";
import { Home } from "./pages/Home";
import { Ring } from "./pages/Ring";
import { Settings } from "./pages/Settings";
import { Placeholder } from "./pages/Placeholder";
import { useDevice } from "./lib/hooks";

export default function App() {
  return (
    <SyncProvider>
      <AppShell />
    </SyncProvider>
  );
}

function AppShell() {
  const [page, setPage] = useState<Page>("home");
  const device = useDevice();
  const subtitle = device.data
    ? `${device.data.serial}${device.data.firmware ? ` · fw ${device.data.firmware}` : ""}`
    : undefined;

  return (
    <TooltipProvider>
      <Shell page={page} onNavigate={setPage} subtitle={subtitle}>
        {page === "home" ? (
          <Home onNavigate={setPage} />
        ) : page === "ring" ? (
          <Ring />
        ) : page === "settings" ? (
          <Settings />
        ) : (
          <Placeholder page={page} />
        )}
      </Shell>
      <SyncToast />
    </TooltipProvider>
  );
}
