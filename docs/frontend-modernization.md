# Desktop frontend modernization

## Current implementation

The Server Network Assist desktop console now uses React, TypeScript, Vite, and Fluent UI React v9. The source lives in `desktop-frontend/`; Vite emits the three offline assets served by the Python loopback panel:

- `src/server_network_assist/desktop_ui/index.html`
- `src/server_network_assist/desktop_ui/desktop.js`
- `src/server_network_assist/desktop_ui/desktop.css`

The frontend has no CDN dependency and no Framework7 runtime dependency. `desktop.py` remains the static asset server and typed API boundary. The migration does not change SSH routing, WireGuard operations, remote helper behavior, proxy mutation, recovery semantics, audit storage, credential encryption, host-key validation, or campus login scripts.

## Information architecture

The seven existing desktop capabilities remain available: 连接概览, 本机隧道, 共享网络, 主机与凭据, 系统代理, 诊断与恢复, and 后台与更新. The shell uses a desktop sidebar at normal widths and an accessible overlay navigation below 900px.

The overview distinguishes direct public connectivity, system-application connectivity, and WireGuard counter traffic. The traffic scope is stated next to the chart: “仅为 WireGuard 隧道流量，不是整机网卡流量。” Unknown telemetry is not rendered as zero.

Sharing profiles use a list/detail workflow. The detail view shows the actual Client → WireGuard → Source Host → Internet path, or Client → WG → Source Proxy when source proxy sharing is enabled. Host and credential data use separate tabs; credentials never display secret material after submission. Proxy writes and recovery actions retain confirmation and backup boundaries.

## Build and verification

From the repository root:

```powershell
Set-Location desktop-frontend
npm install
npm run typecheck
npm run build
```

The generated desktop bundle is intentionally a single JavaScript entry plus a single stylesheet so the existing Python static server does not need a generic asset route. `python scripts/sync_ui_assets.py` now verifies source and generated artifacts; it does not copy third-party UI files.

The browser regression test is:

```text
node frontend/desktop-e2e.cjs
```

It covers loopback API authentication, token removal from the URL, sidebar navigation, desktop/mobile layouts, tunnel confirmation, campus password clearing, host fingerprint warnings, credential clearing, sharing profile paths and recovery, proxy backup/restore, diagnostics export, update state, failures, and theme persistence. The test mocks API responses and does not change the operator's network.

## Future options

React and TypeScript are now the committed desktop direction. shadcn/ui or Fluent UI composition primitives can be evaluated later for local component ownership, but a second visual-framework migration should not be combined with backend or network behavior changes. Any future work should retain this typed API boundary and add contract fixtures before changing operation semantics.
