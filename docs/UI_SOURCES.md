# Desktop UI sources

The Server Network Assist desktop console is implemented with React, TypeScript, Vite, and Fluent UI React v9. Dependencies are pinned in `desktop-frontend/package.json` and installed from the package registry during development; the shipped panel serves only the generated local assets and makes no CDN requests.

The application shell uses Fluent UI React components for buttons, dialogs, fields, tabs, badges, and providers. The operations-specific layout, status colors, tables, route diagram, responsive sidebar, and WireGuard traffic SVG are local styles in `desktop-frontend/src/styles.css` and `desktop-frontend/src/theme/`.

This document describes the desktop console only. Other product surfaces in this repository may retain their own UI technology and third-party attribution. A frontend framework change must not be used as a reason to modify SSH routing, WireGuard operations, remote helpers, proxy mutation, recovery, audit semantics, or credential handling.

## Local build

```powershell
Set-Location desktop-frontend
npm install
npm run typecheck
npm run build
```

The generated output is `src/server_network_assist/desktop_ui/index.html`, `desktop.js`, and `desktop.css`. `scripts/sync_ui_assets.py` verifies that those artifacts exist and that the generated JavaScript has no Framework7 reference.

## Licenses

React, React DOM, Vite, and Fluent UI React are distributed under their respective package licenses. The package lock is committed with the frontend source so reviewers can inspect the resolved dependency graph. No Framework7 runtime or license file is shipped by the desktop panel after this migration.
