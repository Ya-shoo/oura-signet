# Signet (app)

The Signet desktop app: a Tauri 2 shell with a Rust core (`src-tauri/`) and a
React + TypeScript + Vite front end (`src/`).

See the [root README](../README.md) for the full project overview, and
[`../DESKTOP_APP_SPEC.md`](../DESKTOP_APP_SPEC.md) for the UX and security design.

```bash
npm install
npm run tauri dev     # hot-reload dev
npm run tauri build   # release .app + .dmg
```
