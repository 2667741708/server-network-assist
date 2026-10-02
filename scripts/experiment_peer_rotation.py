"""Isolated loopback simulation of relay peer replacement gaps.

This does not create or change a WireGuard interface, route, firewall rule,
or remote connection. It drives RelayManager through an in-memory command
runner and sends UDP probes only over 127.0.0.1.
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server_network_assist.client_relay import RelayManager  # noqa: E402


KEY = "A" * 43 + "="
REPLACEMENTS = 10
COMMAND_DELAY_SECONDS = 0.01
PROBE_INTERVAL_SECONDS = 0.001


class SimulatedRelayRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.peers: dict[tuple[str, str], str] = {}
        self.routes: set[tuple[str, str]] = set()
        self.gap_started: float | None = None
        self.gaps_ms: list[float] = []
        self.lock = threading.Lock()

    @property
    def peer_available(self) -> bool:
        with self.lock:
            return bool(self.peers)

    def __call__(self, argv, input_text=None, check=True):
        self.calls.append(list(argv))
        stdout = ""
        if argv[:2] == ["wg", "show"] and len(argv) > 3 and argv[3] == "allowed-ips":
            interface = argv[2]
            with self.lock:
                stdout = "".join(
                    f"{key}\t{allowed}\n"
                    for (dev, key), allowed in self.peers.items() if dev == interface
                )
        elif argv[:3] == ["ip", "-4", "route"] and "dev" in argv:
            interface = argv[argv.index("dev") + 1]
            stdout = "".join(f"{address} dev {dev}\n"
                             for dev, address in sorted(self.routes) if dev == interface)
        elif argv[:2] == ["wg", "show"] and len(argv) > 3 and argv[3] == "transfer":
            stdout = ""

        time.sleep(COMMAND_DELAY_SECONDS)
        with self.lock:
            if argv[:2] == ["wg", "set"] and len(argv) >= 6:
                interface, key = argv[2], argv[4]
                was_available = bool(self.peers)
                if argv[-1:] == ["remove"]:
                    self.peers.pop((interface, key), None)
                elif "allowed-ips" in argv:
                    self.peers[(interface, key)] = argv[-1]
                is_available = bool(self.peers)
                if was_available and not is_available:
                    self.gap_started = time.perf_counter()
                elif not was_available and is_available and self.gap_started is not None:
                    self.gaps_ms.append((time.perf_counter() - self.gap_started) * 1000)
                    self.gap_started = None
            elif argv[:3] == ["ip", "route", "replace"] and len(argv) >= 6:
                self.routes.add((argv[5], argv[3]))
            elif argv[:3] == ["ip", "route", "del"] and len(argv) >= 6:
                self.routes.discard((argv[5], argv[3]))
        return CompletedProcess(argv, 0, stdout, "")


def make_policy(peer_id: str, now: int) -> dict:
    return {
        "peer_id": peer_id,
        "public_key": KEY,
        "address": "100.64.77.2/32",
        "interface": "wg-simulated",
        "egress_interface": "lo",
        "egress_policy": "source_proxy",
        "customer_subnet": "100.64.77.0/24",
        "management_subnets": ["10.0.0.0/8"],
        "download_bps": None,
        "upload_bps": None,
        "quota_bytes": None,
        "expires_at": now + 30 * 86400,
        "enabled": True,
    }


def run_udp_probes(runner: SimulatedRelayRunner) -> dict:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.05)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = receiver.getsockname()
    stop = threading.Event()
    totals = {"sent": 0, "sent_while_peer_absent": 0,
              "received_with_peer": 0, "simulated_tunnel_drops": 0}
    totals_lock = threading.Lock()

    def send() -> None:
        sequence = 0
        while not stop.is_set():
            available = runner.peer_available
            sender.sendto(struct.pack("!?Q", available, sequence), target)
            with totals_lock:
                totals["sent"] += 1
                if not available:
                    totals["sent_while_peer_absent"] += 1
            sequence += 1
            time.sleep(PROBE_INTERVAL_SECONDS)

    def receive() -> None:
        while not stop.is_set():
            try:
                payload, _ = receiver.recvfrom(32)
            except socket.timeout:
                continue
            peer_was_available, _sequence = struct.unpack("!?Q", payload)
            with totals_lock:
                key = "received_with_peer" if peer_was_available else "simulated_tunnel_drops"
                totals[key] += 1

    sender_thread = threading.Thread(target=send, daemon=True)
    receiver_thread = threading.Thread(target=receive, daemon=True)
    sender_thread.start()
    receiver_thread.start()
    return {
        "totals": totals,
        "stop": stop,
        "threads": (sender_thread, receiver_thread),
        "sockets": (sender, receiver),
    }


def main() -> None:
    now = [1_900_000_000]
    runner = SimulatedRelayRunner()
    with tempfile.TemporaryDirectory(prefix="sna-peer-rotation-") as directory:
        with patch("server_network_assist.client_relay.require_linux_root"):
            manager = RelayManager(Path(directory), runner, lambda: now[0])
            manager.apply(make_policy("lease-000", now[0]))
            probe = run_udp_probes(runner)
            try:
                for index in range(1, REPLACEMENTS + 1):
                    now[0] += 60
                    manager.reconcile([make_policy(f"lease-{index:03d}", now[0])])
                    time.sleep(0.02)

                dropped_before_refresh = probe["totals"]["sent_while_peer_absent"]
                runner.calls.clear()
                refreshed = make_policy(f"lease-{REPLACEMENTS:03d}", now[0])
                refreshed.update(snapshot_at=now[0], offline_deadline=now[0] + 900)
                manager.reconcile([refreshed])
                stable_mutations = sum(
                    1 for argv in runner.calls
                    if argv[:2] == ["wg", "set"] or argv[:3] in (
                        ["ip", "route", "replace"], ["ip", "route", "del"])
                )
                time.sleep(0.05)
                stable_refresh_drops = (
                    probe["totals"]["sent_while_peer_absent"] - dropped_before_refresh
                )
            finally:
                probe["stop"].set()
                for thread in probe["threads"]:
                    thread.join(timeout=1)
                for sock in probe["sockets"]:
                    sock.close()

    result = {
        "evidence_level": "isolated_loopback_simulation",
        "real_wireguard_or_relay_used": False,
        "peer_identity_replacements": REPLACEMENTS,
        "virtual_interval_seconds": 60,
        "simulated_command_delay_ms": COMMAND_DELAY_SECONDS * 1000,
        "replacement_gap_count": len(runner.gaps_ms),
        "replacement_gap_ms": [round(value, 2) for value in runner.gaps_ms],
        "udp_probes_sent": probe["totals"]["sent"],
        "udp_probes_emitted_during_absent_peer_windows": probe["totals"]["sent_while_peer_absent"],
        "udp_probes_classified_as_tunnel_drops": probe["totals"]["simulated_tunnel_drops"],
        "udp_probes_classified_as_delivered": probe["totals"]["received_with_peer"],
        "stable_authorization_refresh_route_or_wg_mutations": stable_mutations,
        "stable_authorization_refresh_additional_drops": stable_refresh_drops,
    }
    print(json.dumps(result, indent=2))
    if len(runner.gaps_ms) != REPLACEMENTS or stable_mutations or stable_refresh_drops:
        raise SystemExit(1)
    if probe["totals"]["sent_while_peer_absent"] == 0:
        raise SystemExit("simulation did not sample any absent-peer window")


if __name__ == "__main__":
    main()
