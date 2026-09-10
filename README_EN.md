# Server Network Assist

Server Network Assist is a self-hosted console for SSH inventory, layered reachability checks, browser terminals, and reversible WireGuard-based Internet sharing between Windows and Ubuntu/Linux machines. Either system can be a gateway or client, subject to the prerequisites below.

Windows gateways use native WireGuard and WinNAT; existing NATs are preserved and cause a conflict refusal. Profiles can optionally share an upstream HTTP/HTTPS proxy through a tunnel-only relay. Direct mode preserves the client's existing proxy settings. Proxy sharing does not copy subscriptions, PAC or credentials, and disabling it does not bypass gateway VPN/TUN routing. See [sharing scope and validation limits](docs/SHARING.md) and the [standalone illustrated blog](docs/blog/README.md). Reinstall current helpers on all participating hosts when upgrading.

Version 0.2.0 adds a local desktop panel. Run `server-network-assist-desktop` to open a standalone browser app window with local tunnel controls, traffic, handshake and system-proxy diagnostics. The Windows installer creates a desktop shortcut and a protected offline runtime; no WSL is required. See the [desktop guide](docs/DESKTOP.md).

It probes only hosts explicitly configured through SSH. A server without Internet access can temporarily route public traffic through a selected gateway. Disabling assistance removes the tunnel and temporary routes so the client can return to its campus login, hotspot, or original local uplink.

## Illustrated use cases

These screenshots show the shipped UI rendered with **synthetic demonstration data**, not production captures or benchmark results. Replace the documentation addresses (`192.0.2.x`) with your own. The UI shown is in Chinese; see the [step-by-step walkthroughs](README.md#使用案例与截图).

### 1. Windows apps fail while WSL still works

Open the desktop panel and compare direct Internet access with access using the current user's system proxy settings. When direct access succeeds but system access fails, check whether the configured proxy is running. If the manual proxy is no longer needed, **关闭手动代理** backs it up and disables it; PAC and the proxy application's own configuration are retained. Refresh and retry the affected Windows app. This diagnoses one possible cause, not every Windows/WSL connectivity difference.

[![Windows proxy diagnostic: direct access succeeds while system access fails](docs/images/windows-proxy-diagnosis.png)](docs/images/windows-proxy-diagnosis.png)

### 2. Share an Ubuntu gateway with Windows and Ubuntu clients

Add the hosts, verify SSH fingerprints, probe connectivity, and install the helpers. Under **网络借助**, select the Internet-connected gateway and both clients, then configure a reachable endpoint, UDP port, unused tunnel subnet, and management routes to preserve. Enable and wait for SSH and selected-path verification; failures trigger rollback. Windows uses native WireGuard and does not require WSL; Windows gateways additionally require WinNAT and no conflicting existing NAT.

The screenshot shows an example profile **before activation**. To finish a managed session and clean up its temporary routes, use **断开并恢复原网络** in the management console. See the [network guide](docs/NETWORK_ASSIST.md).

[![A pending profile with an Ubuntu gateway and Windows/Ubuntu clients](docs/images/mixed-network-profile.png)](docs/images/mixed-network-profile.png)

### 3. Check and control an existing tunnel from the desktop

Open the **服务器网络助手** shortcut or run `server-network-assist-desktop`. Check reachability, the latest handshake, endpoint, and cumulative tunnel traffic (not live speed). Status refreshes every 30 seconds. **断开连接** asks for confirmation before stopping the selected existing WireGuard service; **连接** starts it again without deleting its configuration.

[![Desktop overview with connectivity, traffic, handshake, proxy and gateway status](docs/images/desktop-overview.png)](docs/images/desktop-overview.png)

[![Confirmation before disconnecting an existing tunnel](docs/images/disconnect-confirmation.png)](docs/images/disconnect-confirmation.png)

Closing the window keeps the tunnel running. Desktop service controls are separate from the management console's full profile cleanup. This panel does not include Clash subscriptions, rule editing, or a system tray menu. See the [desktop guide](docs/DESKTOP.md) for installation and permissions.

## Quick start

Run the following from a checkout of the current source on Ubuntu/Linux. The old `v0.1.0` release does not include Windows support or the desktop panel. For Windows PowerShell commands, see the [Chinese quick start](README.md#快速开始).

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install .
server-network-assist init --data ./data
server-network-assist serve --data ./data --bind 127.0.0.1 --port 9180
```

Open `http://127.0.0.1:9180` and use the one-time credentials written to `data/initial-login.json`. Use an HTTPS reverse proxy and an exact `PANEL_ORIGIN` in production.

See [INSTALL.md](docs/INSTALL.md), [USER_GUIDE.md](docs/USER_GUIDE.md), [NETWORK_ASSIST.md](docs/NETWORK_ASSIST.md), and [SECURITY.md](docs/SECURITY.md) for the complete operational guide.

## License

MIT. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for direct dependencies.
