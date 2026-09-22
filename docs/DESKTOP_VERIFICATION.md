# Desktop verification

This checklist covers the React/TypeScript/Vite desktop console. It is intentionally separate from physical network acceptance: browser fixtures mock API responses and must not be used to claim that a real WireGuard or proxy change was performed.

## Static build

```text
Set-Location desktop-frontend
npm install
npm run typecheck
npm run build
Set-Location ..
python scripts/sync_ui_assets.py
```

The Python panel serves `index.html`, `desktop.js`, `desktop.css`, and `icon.svg` from its package directory. The desktop server does not expose Framework7 assets or a generic arbitrary-file route.

## Browser regression

```text
node frontend/desktop-e2e.cjs
```

The browser check covers:

- API token enforcement and removal of the launch token from the URL;
- sidebar navigation at 1280×960 and 390×844;
- overview rendering and explicit WireGuard-only traffic scope;
- tunnel connect, disconnect, pause, restore, and campus password clearing;
- host and credential separation, secret clearing, and SSH fingerprint warning/confirmation;
- sharing profile list/detail path, source-proxy distinction, helper confirmation, probe, enable, and recovery;
- proxy backup, modification, disable, restore, unsupported state, and PAC wording;
- diagnostic status/guidance separation, local/Fleet events, recovery confirmation, and report export;
- update status without automatic installation, long values, failure messages, and theme persistence.

All mutation requests remain the existing typed API actions. No generic shell, arbitrary remote command, arbitrary path, or new network operation is part of the UI.

## Real-system boundary

The desktop UI test does not disconnect a real tunnel, modify a real system proxy, log into a campus network, or alter a remote host. Production verification must separately confirm the existing backend safety guarantees, including route ownership, rollback, host-key validation, credential encryption, and campus physical-network preconditions.
