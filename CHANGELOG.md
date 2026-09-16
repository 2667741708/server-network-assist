# Changelog

## 0.6.0 - 2026-09-16

- Add a separate customer-only desktop client with no fleet, SSH, terminal, proxy or campus-account management routes.
- Add pinned Ed25519 HTTPS subscriptions with device binding, expiry, schema validation and tamper rejection.
- Add authorized-line latency probes, local WireGuard connect/disconnect controls, quota and speed display, and direct-network verification after disconnect.
- Add an offline subscription signing utility, Windows customer-client task/shortcut installer, documentation and focused security tests.

## 0.4.1 - 2026-09-11

- Allow explicitly configured trusted private-network browser origins alongside the primary HTTPS origin.
- Choose cookie security per validated browser origin so a WireGuard/LAN HTTP entry can use password login while passkeys remain restricted to the primary HTTPS origin.

## 0.4.0 - 2026-09-11

- Add explicit OpenSSH alias import with encrypted private-key storage, pinned known-host keys and ProxyJump chains.
- Automatically refresh SSH, internet and Codex CLI availability after login.
- Add per-server Codex CLI conversations with persisted sessions, polling, cancellation and read-only/workspace-write sandboxes.
- Support hosting the console under an HTTPS path prefix such as `/network-assist/`.
- Add host-level Codex enablement and a default remote workspace.

## 0.2.0 - 2026-09-10

- Add a local desktop panel with an independent app window, desktop shortcut, WireGuard status and connection controls.
- Compare direct HTTPS with system-proxy connectivity and back up manual proxy settings before disabling them.
- Add an offline Windows desktop installer with a protected embedded runtime, on-demand background task and per-user state permissions.
- Support native Windows network probes and WireGuard clients without WSL; retain Ubuntu/Linux gateways.
- Add scheduled failsafe recovery, route ownership journaling and full-tunnel conflict protection for Windows clients.
- Fix Linux gateway peer subnet validation; add Windows/Linux CI and desktop API/UI tests.

## 0.1.0 - 2026-09-10

- Initial public alpha structure
- Angular Material responsive console and Xterm.js terminal
- SSH inventory, encrypted credentials, pinned host keys and jump chains
- Layered SSH/DNS/HTTPS reachability probes
- Reversible WireGuard gateway profiles with advanced ports and preserved routes
- Failsafe confirmation, periodic maintenance and automatic route restoration
- Session security, WebAuthn, audit events, systemd template and bilingual documentation
