#!/usr/bin/env python3
"""Install the customer WireGuard relay reconciler on Linux."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wireguard-interface", default="wg-customer")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--relay-id", required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise RuntimeError("Run this installer as root on Linux")
    if not 10 <= args.interval <= 3600:
        raise ValueError("interval must be between 10 and 3600 seconds")
    if urlsplit(args.control_url).scheme != "https":
        raise ValueError("control-url must use HTTPS")
    token_file = Path(args.token_file).resolve()
    if not token_file.is_file() or token_file.is_symlink():
        raise ValueError("token-file must be a regular file, not a symlink")
    if token_file.stat().st_uid != 0:
        raise ValueError("token-file must be owned by root")
    token_file.chmod(0o600)
    if not all(shutil.which(item) for item in ("wg", "tc", "nft", "systemctl")):
        raise RuntimeError("Install wireguard-tools, iproute2 and nftables first")
    import server_network_assist.client_relay_agent  # noqa: F401
    data = Path("/var/lib/server-network-assist-relay")
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = Path("/etc/systemd/system/server-network-assist-relay.service")
    timer = Path("/etc/systemd/system/server-network-assist-relay.timer")
    executable = str(Path(sys.executable).resolve())
    service.write_text("\n".join([
        "[Unit]", "Description=Measure and reconcile customer WireGuard relay",
        "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=oneshot",
        f"ExecStart={executable} -m server_network_assist.client_relay_agent --data {data} --control-url {args.control_url} --token-file {token_file} --interface {args.wireguard_interface} --relay-id {args.relay_id}",
        "PrivateTmp=true", "ProtectHome=true", "ProtectSystem=strict",
        f"ReadOnlyPaths={token_file}", f"ReadWritePaths={data}",
        "CapabilityBoundingSet=CAP_NET_ADMIN", "AmbientCapabilities=CAP_NET_ADMIN", "",
    ]), encoding="utf-8")
    timer.write_text("\n".join([
        "[Unit]", "Description=Periodic customer relay accounting", "",
        "[Timer]", f"OnBootSec={args.interval}s", f"OnUnitActiveSec={args.interval}s",
        "AccuracySec=5s", "Persistent=true", "Unit=server-network-assist-relay.service", "",
        "[Install]", "WantedBy=timers.target", "",
    ]), encoding="utf-8")
    service.chmod(0o644)
    timer.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "server-network-assist-relay.timer"], check=True)
    subprocess.run(["systemctl", "start", "server-network-assist-relay.service"], check=True)
    print("Installed server-network-assist-relay.timer")


if __name__ == "__main__":
    main()
