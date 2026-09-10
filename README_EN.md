# Server Network Assist

Server Network Assist is a self-hosted console for SSH inventory, layered reachability checks, browser terminals, and reversible WireGuard-based Internet sharing between Linux servers.

It probes only hosts explicitly configured through SSH. A server without Internet access can temporarily route public traffic through a selected gateway. Disabling assistance removes the tunnel and temporary routes so the client can return to its campus login, hotspot, or original local uplink.

## Quick start

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
