"""Windows WireGuard/WinNAT customer relay enforcement.

Windows has no supported in-box equivalent of Linux ``tc`` for dependable
per-WireGuard-peer shaping.  This relay therefore exposes rate limiting as an
explicitly unsupported capability while still enforcing peer membership,
WinNAT, revocation, isolation firewall rules, and real WireGuard accounting.
"""
from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from typing import Callable

from .client_relay import PUBLIC_KEY_RE, RelayStore, parse_wg_transfer
from .client_relay_agent import RelayControlClient, sync


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SAFE_NAME_RE = re.compile(r"^[^\x00-\x1f\"']{1,128}$")


def validate_windows_policy(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("policy must be an object")
    allowed = {"peer_id", "public_key", "address", "interface", "egress_interface",
               "customer_subnet", "management_subnets", "download_bps", "upload_bps",
               "quota_bytes", "expires_at", "enabled"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown policy fields: " + ", ".join(sorted(unknown)))
    peer_id, public_key = value.get("peer_id"), value.get("public_key")
    interface, egress = value.get("interface"), value.get("egress_interface")
    if not isinstance(peer_id, str) or not SAFE_ID_RE.fullmatch(peer_id):
        raise ValueError("invalid peer_id")
    if not isinstance(public_key, str) or not PUBLIC_KEY_RE.fullmatch(public_key):
        raise ValueError("invalid WireGuard public key")
    if not isinstance(interface, str) or not SAFE_NAME_RE.fullmatch(interface):
        raise ValueError("invalid WireGuard interface")
    if not isinstance(egress, str) or not SAFE_NAME_RE.fullmatch(egress):
        raise ValueError("invalid egress interface")
    try:
        address = ipaddress.ip_interface(value.get("address"))
        customer = ipaddress.ip_network(value.get("customer_subnet"), strict=True)
    except (TypeError, ValueError):
        raise ValueError("invalid customer address or subnet") from None
    if address.version != 4 or address.network.prefixlen != 32 or address.ip not in customer:
        raise ValueError("customer address must be an IPv4 /32 inside customer_subnet")
    raw_management = value.get("management_subnets", [])
    if not isinstance(raw_management, list) or len(raw_management) > 32:
        raise ValueError("management_subnets must be a list")
    management = []
    for item in raw_management:
        try:
            network = ipaddress.ip_network(item, strict=True)
        except (TypeError, ValueError):
            raise ValueError("invalid management subnet") from None
        if network.version != 4 or network.overlaps(customer):
            raise ValueError("management subnet must be IPv4 and separate from customer_subnet")
        management.append(str(network))
    rates = {}
    for key in ("download_bps", "upload_bps"):
        item = value.get(key)
        if not isinstance(item, int) or not 8_000 <= item <= 100_000_000_000:
            raise ValueError(f"{key} must be between 8000 and 100000000000")
        rates[key] = item
    quota = value.get("quota_bytes")
    if quota is not None and (not isinstance(quota, int) or quota < 1):
        raise ValueError("quota_bytes must be null or a positive integer")
    expires = value.get("expires_at")
    if expires is not None and (not isinstance(expires, int) or expires < 1):
        raise ValueError("expires_at must be null or a Unix timestamp")
    if value.get("enabled", True) is not True:
        raise ValueError("apply only accepts enabled policies")
    return {"peer_id": peer_id, "public_key": public_key, "address": str(address),
            "interface": interface, "egress_interface": egress,
            "customer_subnet": str(customer), "management_subnets": management,
            **rates, "quota_bytes": quota, "expires_at": expires, "enabled": True}


def require_windows_admin() -> None:
    if platform.system() != "Windows" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("relay mutation requires an elevated Windows administrator")


def _run(argv: list[str], *, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, text=True, capture_output=True, check=check)


class WindowsRelayManager:
    def __init__(self, data: Path, runner: Callable = _run,
                 clock: Callable[[], float] = time.time, helper: Path | None = None,
                 wg_path: str | None = None):
        self.store, self.runner, self.clock = RelayStore(data), runner, clock
        self.helper = helper or Path(__file__).with_name("windows_client_relay_helper.ps1")
        self.wg = wg_path or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WireGuard" / "wg.exe")

    @staticmethod
    def capabilities() -> dict:
        return {"peer_enforcement": "enforced", "accounting": "wireguard_kernel",
                "revocation": "enforced", "nat": "winnat",
                "isolation": "windows_firewall",
                "download_rate_limit": "unsupported",
                "upload_rate_limit": "unsupported",
                "rate_limit_detail": "Windows has no supported in-box per-WireGuard-peer shaper"}

    def _powershell(self, action: str, policy: dict, check=True):
        management = ",".join(policy["management_subnets"])
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(self.helper), "-Action", action, "-PeerId", policy["peer_id"],
                "-Interface", policy["interface"], "-EgressInterface", policy["egress_interface"],
                "-Address", policy["address"], "-CustomerSubnet", policy["customer_subnet"],
                "-ManagementSubnets", management]
        return self.runner(argv, check=check)

    def apply(self, raw: dict) -> dict:
        require_windows_admin()
        policy = validate_windows_policy(raw)
        state = self.store.load()
        previous = state["peers"].get(policy["peer_id"])
        active = [row["policy"] for row in state["peers"].values() if row.get("status") == "active"
                  and row["policy"]["peer_id"] != policy["peer_id"]]
        for item in active:
            shared = ("interface", "egress_interface", "customer_subnet", "management_subnets")
            if any(item[key] != policy[key] for key in shared):
                raise ValueError("active Windows relay peers must share gateway and isolation networks")
        try:
            self._powershell("EnsureGateway", policy)
            self.runner([self.wg, "set", policy["interface"], "peer", policy["public_key"],
                         "allowed-ips", policy["address"]], check=True)
            self._powershell("ApplyPeer", policy)
        except Exception as exc:
            self.runner([self.wg, "set", policy["interface"], "peer", policy["public_key"], "remove"], check=False)
            self._powershell("RemovePeer", policy, check=False)
            state["peers"][policy["peer_id"]] = {"policy": policy, "status": "recovery_required",
                "error": str(exc), "updated_at": int(self.clock()), "capabilities": self.capabilities()}
            self.store.save(state)
            self.store.event("apply", policy["peer_id"], False, str(exc))
            raise RuntimeError("Windows relay apply failed; cleanup was attempted") from exc
        state["peers"][policy["peer_id"]] = {"policy": policy, "status": "active",
            "used_bytes": previous.get("used_bytes", 0) if previous else 0,
            "accounted_received": previous.get("accounted_received", 0) if previous else 0,
            "accounted_sent": previous.get("accounted_sent", 0) if previous else 0,
            "last_received": previous.get("last_received") if previous else None,
            "last_sent": previous.get("last_sent") if previous else None,
            "updated_at": int(self.clock()), "capabilities": self.capabilities()}
        self.store.save(state)
        self.store.event("apply", policy["peer_id"], True, "rate limiting unsupported on Windows")
        return state["peers"][policy["peer_id"]]

    def revoke(self, peer_id: str, reason="revoked") -> dict:
        require_windows_admin()
        if not isinstance(peer_id, str) or not SAFE_ID_RE.fullmatch(peer_id):
            raise ValueError("invalid peer_id")
        state = self.store.load()
        record = state["peers"].get(peer_id)
        if not record:
            return {"peer_id": peer_id, "status": "absent"}
        p = record["policy"]
        results = [self.runner([self.wg, "set", p["interface"], "peer", p["public_key"], "remove"], check=False),
                   self._powershell("RemovePeer", p, check=False)]
        failures = [str(index) for index, result in enumerate(results) if getattr(result, "returncode", 0)]
        record.update({"status": "recovery_required" if failures else "revoked", "reason": reason,
                       "updated_at": int(self.clock())})
        if failures:
            record["error"] = "failed cleanup steps: " + ",".join(failures)
        self.store.save(state)
        self.store.event("revoke", peer_id, not failures, record.get("error", reason))
        return record

    def measure(self, interface: str) -> dict:
        require_windows_admin()
        if not isinstance(interface, str) or not SAFE_NAME_RE.fullmatch(interface):
            raise ValueError("invalid WireGuard interface")
        output = self.runner([self.wg, "show", interface, "transfer"], check=True).stdout
        counters = parse_wg_transfer(output)
        state, now = self.store.load(), int(self.clock())
        for record in state["peers"].values():
            p = record["policy"]
            if p["interface"] != interface or p["public_key"] not in counters:
                continue
            current = counters[p["public_key"]]
            old_rx, old_tx = record.get("last_received"), record.get("last_sent")
            delta_rx = current["received_bytes"] if old_rx is None or current["received_bytes"] < old_rx else current["received_bytes"] - old_rx
            delta_tx = current["sent_bytes"] if old_tx is None or current["sent_bytes"] < old_tx else current["sent_bytes"] - old_tx
            record["accounted_received"] = record.get("accounted_received", 0) + delta_rx
            record["accounted_sent"] = record.get("accounted_sent", 0) + delta_tx
            record["used_bytes"] = record["accounted_received"] + record["accounted_sent"]
            record.update({"last_received": current["received_bytes"], "last_sent": current["sent_bytes"],
                           "delta_received": delta_rx, "delta_sent": delta_tx,
                           "delta_bytes": delta_rx + delta_tx, "measured_at": now})
        self.store.save(state)
        return self.status()

    def reconcile(self, desired: list[dict] | None = None) -> dict:
        require_windows_admin()
        if desired is not None:
            if not isinstance(desired, list) or len(desired) > 4096:
                raise ValueError("desired policies must be a list")
            policies = {p["peer_id"]: p for p in (validate_windows_policy(row) for row in desired)}
            state = self.store.load()
            for peer_id, record in list(state["peers"].items()):
                if record.get("status") in {"active", "recovery_required"} and peer_id not in policies:
                    self.revoke(peer_id, "not_desired")
            state = self.store.load()
            for peer_id, policy in policies.items():
                current = state["peers"].get(peer_id)
                if not current or current.get("status") != "active" or current.get("policy") != policy:
                    self.apply(policy)
        state, now = self.store.load(), int(self.clock())
        for peer_id, record in list(state["peers"].items()):
            if record.get("status") != "active":
                continue
            p = record["policy"]
            if p["expires_at"] is not None and p["expires_at"] <= now:
                self.revoke(peer_id, "expired")
            elif p["quota_bytes"] is not None and record.get("used_bytes", 0) >= p["quota_bytes"]:
                self.revoke(peer_id, "quota_exhausted")
        return self.status()

    def status(self) -> dict:
        state = self.store.load()
        return {"version": state["version"], "peers": state["peers"], "at": int(self.clock()),
                "capabilities": self.capabilities()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows commercial WireGuard/WinNAT relay")
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ServerNetworkAssist" / "commercial-relay")
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--interface", default="wg-customer")
    parser.add_argument("--relay-id", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args(argv)
    if not 10 <= args.interval <= 3600:
        raise ValueError("interval must be between 10 and 3600 seconds")
    token = args.token_file.read_text(encoding="utf-8").strip()
    manager = WindowsRelayManager(args.data)
    control = RelayControlClient(args.control_url, token, relay_id=args.relay_id)
    while True:
        result = sync(manager, control, args.data / "cursor", args.interface)
        if args.once:
            print(json.dumps({"ok": True, "peers": len(result["peers"]),
                              "capabilities": manager.capabilities()}, ensure_ascii=False))
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
