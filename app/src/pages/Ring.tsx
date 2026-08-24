// Ring & Radio — the control room. In phase A everything shown is real, read
// from the last sync (device, firmware, MAC, event coverage, inferred feature
// state). Live control (Sync now, Dock & Silence via 0x26) connects in phase B;
// the safety modal is here as a faithful preview of that flow.
import { useState } from "react";
import { useDevice, useSyncStatus, useEventCounts } from "../lib/hooks";
import { fmtDateTime, fmtInt, relTime } from "../lib/format";
import { Icon } from "../components/icons";
import { useSync } from "../lib/sync";

export function Ring() {
  const device = useDevice();
  const syncStatus = useSyncStatus();
  const counts = useEventCounts();
  const sync = useSync();
  const [modal, setModal] = useState(false);

  const d = device.data;
  const s = syncStatus.data;
  const batteryKnown = sync.battery != null;
  const countMap = new Map((counts.data ?? []).map((c) => [c.name, c.count]));
  const has = (names: string[]) => names.some((n) => (countMap.get(n) ?? 0) > 0);

  const features = [
    {
      icon: "rhr" as const,
      fam: "recovery",
      label: "Daytime heart rate",
      sub: "automatic",
      on: has(["ibi_and_amplitude_event", "ppg_amplitude", "green_ibi_quality_event"]),
    },
    {
      icon: "hrv" as const,
      fam: "recovery",
      label: "Resting HR & HRV",
      sub: "overnight",
      on: has(["hrv_event"]),
    },
    {
      icon: "spo2" as const,
      fam: "body",
      label: "SpO₂ & breathing",
      sub: "overnight",
      on: has(["spo2_event", "spo2_ibi_and_amplitude_event"]),
    },
    {
      icon: "steps" as const,
      fam: "activity",
      label: "Steps & activity",
      sub: "on-device",
      on: has(["real_step_event_feature_1", "real_step_event_feature_2", "activity_information"]),
    },
  ];
  const famVar = (f: string) => ({
    background: `var(--${f}-tint)`,
    color: `var(--${f})`,
  });

  return (
    <main className="main">
      <div className="phead">
        <h1>Ring &amp; Radio</h1>
        <span className="sub">
          {d ? `Oura Ring · ${d.serial}` : "No ring paired"} · showing last-synced data
        </span>
      </div>

      <div className="cols">
        {/* LEFT */}
        <div>
          <div className="card">
            <div className="radio">
              <div className="ringart">
                <svg viewBox="0 0 120 120">
                  <circle cx="60" cy="60" r="50" fill="none" stroke="var(--good-tint)" strokeWidth="12" />
                  <circle
                    cx="60"
                    cy="60"
                    r="50"
                    fill="none"
                    stroke={batteryKnown ? "var(--good)" : "var(--card-2)"}
                    strokeWidth="12"
                    strokeLinecap="round"
                    strokeDasharray="314.2"
                    strokeDashoffset={batteryKnown ? 314.2 * (1 - (sync.battery as number) / 100) : 314.2}
                    transform="rotate(-90 60 60)"
                  />
                </svg>
                <div className="pct">
                  {batteryKnown ? (
                    <>
                      <b>{sync.battery}%</b>
                      <span>battery</span>
                    </>
                  ) : (
                    <>
                      <b style={{ fontSize: 18 }}>Gen 3</b>
                      <span>Oura Ring</span>
                    </>
                  )}
                </div>
              </div>
              <div className="radio-main">
                <span
                  className={`statepill ${
                    sync.silent ? "silent" : sync.running || !batteryKnown ? "paused" : ""
                  }`}
                >
                  <span className="dot" />
                  {sync.silent
                    ? "Silent · recording"
                    : sync.running
                    ? "Working…"
                    : batteryKnown
                    ? `Connected · ${sync.battery}%`
                    : "Not connected"}
                </span>
                <div className="radio-title">
                  {sync.silent
                    ? "Radio dark"
                    : sync.running
                    ? "Working…"
                    : batteryKnown
                    ? "Ring synced"
                    : "Dock & sync"}
                </div>
                <div className="radio-desc">
                  {sync.silent ? (
                    "The ring is recording on-device with its radio off. Dock it on the charger to wake it and sync — no button needed."
                  ) : (
                    <>
                      Put the ring on the charger and press <b>Sync now</b> to read the battery and
                      pull new history. <b>Dock &amp; Silence</b> arms airplane mode (
                      <code>0x26</code>) and scans to confirm the radio went quiet.
                    </>
                  )}
                </div>
                <div className="actions">
                  {sync.silent ? (
                    <button className="btn wide" onClick={sync.syncNow} disabled={sync.running}>
                      <Icon name="rewake" sw={2.1} />
                      Dock to wake &amp; sync
                    </button>
                  ) : (
                    <>
                      <button className="btn ghost" onClick={sync.syncNow} disabled={sync.running}>
                        <Icon name="sync" sw={2.1} />
                        {sync.running ? "Working…" : "Sync now"}
                      </button>
                      <button className="btn ghost" onClick={sync.analyzeSleep} disabled={sync.running}>
                        <Icon name="sleep" sw={2.1} />
                        Sleep from ring
                      </button>
                      <button
                        className="btn"
                        onClick={() => setModal(true)}
                        disabled={sync.running}
                      >
                        <Icon name="moon" sw={2.1} />
                        Dock &amp; Silence
                      </button>
                    </>
                  )}
                </div>
                {sync.error && (
                  <div className="caveat" style={{ marginTop: 10 }}>
                    * {sync.error}
                  </div>
                )}
              </div>
            </div>

            <div className="switch-explain">
              <div className="se off">
                <span className="ic">
                  <Icon name="moon" sw={2} />
                </span>
                <div>
                  <div className="t">Off = a tap</div>
                  <div className="d">Silence sends the radio dark</div>
                </div>
              </div>
              <div className="se on">
                <span className="ic">
                  <Icon name="lock" sw={2} />
                </span>
                <div>
                  <div className="t">On = a dock</div>
                  <div className="d">Charger wakes it automatically</div>
                </div>
              </div>
            </div>

            <div className="metarow">
              <div>
                <span className="k">Last sync</span>
                <span className="v">{relTime(s?.lastSyncUnix)}</span>
              </div>
              <div>
                <span className="k">Events</span>
                <span className="v">{fmtInt(s?.totalEvents ?? 0)}</span>
              </div>
              <div>
                <span className="k">Decoded</span>
                <span className="v">{fmtInt(s?.decodedEvents ?? 0)}</span>
              </div>
              <div>
                <span className="k">Battery</span>
                <span className="v" style={{ color: batteryKnown ? "var(--good)" : "var(--muted)" }}>
                  {batteryKnown ? `${sync.battery}%` : "—"}
                </span>
              </div>
            </div>
          </div>

          <div className="card">
            <h2>Measurement · inferred from recorded data</h2>
            {features.map((f) => (
              <div className="frow" key={f.label}>
                <span className="fic" style={famVar(f.fam)}>
                  <Icon name={f.icon} sw={2} />
                </span>
                <div className="fl">
                  <b>{f.label}</b>
                  <span>{f.on ? `${f.sub} · recording` : `${f.sub} · no data yet`}</span>
                </div>
                <div className={`tgl disabled ${f.on ? "on" : ""}`} />
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT */}
        <div>
          <div className="card">
            <h2>Device</h2>
            <div className="drow">
              <span>Firmware</span>
              <b>{d?.firmware ?? "—"}</b>
            </div>
            <div className="drow">
              <span>Serial</span>
              <b>{d?.serial ?? "—"}</b>
            </div>
            <div className="drow">
              <span>Hardware</span>
              <b>{d?.hardwareId ?? "Gen 3"}</b>
            </div>
            <div className="drow">
              <span>MAC</span>
              <b>{d?.mac ?? "—"}</b>
            </div>
            <div className="drow">
              <span>Auth key</span>
              <b>Local key file</b>
            </div>
            <div className="drow">
              <span>Account</span>
              <b className="ok">None · fully local</b>
            </div>
          </div>

          <div className="card">
            <h2>Recent syncs</h2>
            {s?.lastSyncUnix ? (
              <div className="srow">
                <span className="sd" />
                <span className="st">{fmtDateTime(s.lastSyncUnix)}</span>
                <span className="sc">{fmtInt(s.totalEvents)} events</span>
              </div>
            ) : (
              <div className="srow">
                <span className="sd" style={{ background: "var(--muted)" }} />
                <span className="st">No syncs recorded</span>
              </div>
            )}
            <div className="srow">
              <span className="sd" style={{ background: "var(--muted)" }} />
              <span className="st" style={{ color: "var(--muted)", fontWeight: 600 }}>
                Full sync history lands with the sync log
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* airplane-mode confirm (preview) */}
      <div className={`scrim ${modal ? "open" : ""}`} onClick={(e) => e.target === e.currentTarget && setModal(false)}>
        <div className="modal" role="dialog" aria-label="Silence the ring">
          <div className="micon">
            <Icon name="moon" sw={1.9} />
          </div>
          <h3>Silence the ring?</h3>
          <p className="lead">
            This switches off the ring's Bluetooth radio for privacy. Here's exactly what happens:
          </p>
          <div className="facts">
            <div className="fact good">
              <span className="fi">
                <Icon name="check" sw={2.2} />
              </span>
              <p>
                It <b>keeps recording on-device</b> — up to ~7 days of data waits safely on the ring.
              </p>
            </div>
            <div className="fact mute">
              <span className="fi">
                <Icon name="xcircle" sw={2.2} />
              </span>
              <p>
                You <b>can't sync or connect</b> while it's silent — it's fully radio-dark.
              </p>
            </div>
            <div className="fact key">
              <span className="fi">
                <Icon name="rewake" sw={2.2} />
              </span>
              <p>
                It <b>wakes automatically the moment you dock it</b> — that's the on-switch, no button
                needed.
              </p>
            </div>
          </div>
          <div className="nudge">
            <span className="ni">
              <Icon name="warn" sw={2.2} />
            </span>
            <p>
              This sends 0x26 over Bluetooth now — ring on the charger.
              <span>Your data is safe on-ring (~7 days); we scan to confirm the radio went quiet.</span>
            </p>
          </div>
          <div className="mbtns">
            <button className="btn ghost" onClick={() => setModal(false)}>
              Cancel
            </button>
            <button
              className="btn"
              onClick={() => {
                setModal(false);
                sync.armAirplane();
              }}
            >
              Silence the ring
            </button>
          </div>
          <div className="mfoot">
            <Icon name="check" sw={2.2} />
            We'll confirm the radio actually goes quiet before you undock.
          </div>
        </div>
      </div>
    </main>
  );
}
