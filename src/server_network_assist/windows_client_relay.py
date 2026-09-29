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

from .client_relay import (CLOCK_SKEW_SECONDS, DEFAULT_MAX_ACTIVE_PEERS,
                           DEFAULT_MAX_OFFLINE_SECONDS, PUBLIC_KEY_RE, RelayStore,
                           _locked_method, _policy_deadline, parse_wg_transfer)
from .client_relay_agent import RelayControlClient, sync


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SAFE_NAME_RE = re.compile(r"^[^\x00-\x1f\"']{1,128}$")


def validate_windows_policy(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("policy must be an object")
    required = {"egress_policy", "customer_subnet", "management_subnets",
                "download_bps", "upload_bps", "quota_bytes", "expires_at", "enabled"}
    missing = required - set(value)
    if missing:
        raise ValueError("missing policy fields: " + ", ".join(sorted(missing)))
    allowed = {"peer_id", "public_key", "address", "interface", "egress_interface",
               "egress_policy",
               "customer_subnet", "management_subnets", "download_bps", "upload_bps",
               "quota_bytes", "usage_baseline_bytes", "expires_at", "snapshot_at",
               "offline_deadline", "enabled"}
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
    egress_policy = value.get("egress_policy", "source_physical")
    if egress_policy not in {"source_physical", "source_proxy"}:
        raise ValueError("invalid egress policy")
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
        if item is not None and (type(item) is not int or not 8_000 <= item <= 100_000_000_000):
            raise ValueError(f"{key} must be null or between 8000 and 100000000000")
        rates[key] = item
    if any(value[key] is not None for key in ("download_bps", "upload_bps")):
        raise ValueError("Windows relay cannot enforce per-peer rate limits")
    quota = value.get("quota_bytes")
    if quota is not None and (not isinstance(quota, int) or quota < 1):
        raise ValueError("quota_bytes must be null or a positive integer")
    usage_baseline = value.get("usage_baseline_bytes", 0)
    if isinstance(usage_baseline, bool) or not isinstance(usage_baseline, int) or usage_baseline < 0:
        raise ValueError("usage_baseline_bytes must be a non-negative integer")
    expires = value.get("expires_at")
    if expires is not None and (not isinstance(expires, int) or expires < 1):
        raise ValueError("expires_at must be null or a Unix timestamp")
    snapshot_at = value.get("snapshot_at")
    if snapshot_at is not None and (not isinstance(snapshot_at, int) or snapshot_at < 1):
        raise ValueError("snapshot_at must be null or a Unix timestamp")
    offline_deadline = value.get("offline_deadline")
    if offline_deadline is not None and (not isinstance(offline_deadline, int) or offline_deadline < 1):
        raise ValueError("offline_deadline must be null or a Unix timestamp")
    if (snapshot_at is not None and offline_deadline is not None and
            offline_deadline <= snapshot_at):
        raise ValueError("offline_deadline must be after snapshot_at")
    if (expires is not None and offline_deadline is not None and offline_deadline > expires):
        raise ValueError("offline_deadline cannot extend lease expiry")
    if value.get("enabled", True) is not True:
        raise ValueError("apply only accepts enabled policies")
    return {"peer_id": peer_id, "public_key": public_key, "address": str(address),
            "interface": interface, "egress_interface": egress,
            "egress_policy": egress_policy,
            "customer_subnet": str(customer), "management_subnets": management,
            **rates, "quota_bytes": quota, "usage_baseline_bytes": usage_baseline,
            "expires_at": expires,
            "snapshot_at": snapshot_at, "offline_deadline": offline_deadline,
            "enabled": True}


def require_windows_admin() -> None:
    if platform.system() != "Windows" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("relay mutation requires an elevated Windows administrator")


def _run(argv: list[str], *, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, text=True, capture_output=True, check=check)


class WindowsRelayManager:
    def __init__(self, data: Path, runner: Callable = _run,
                 clock: Callable[[], float] = time.time, helper: Path | None = None,
                 wg_path: str | None = None, max_active_peers: int = DEFAULT_MAX_ACTIVE_PEERS):
        if not 1 <= max_active_peers <= 4096:
            raise ValueError("max_active_peers must be between 1 and 4096")
        self.store, self.runner, self.clock = RelayStore(data), runner, clock
        self.helper = helper or Path(__file__).with_name("windows_client_relay_helper.ps1")
        self.wg = wg_path or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WireGuard" / "wg.exe")
        self.max_active_peers = max_active_peers

    @staticmethod
    def capabilities() -> dict:
        return {"peer_enforcement": "enforced", "accounting": "wireguard_kernel",
                "revocation": "enforced", "nat": "winnat",
                "isolation": "windows_firewall",
                "expiry_enforcement": "process_watchdog_only",
                "isolation_enforcement": "unverified",
                "download_rate_limit": "unsupported",
                "upload_rate_limit": "unsupported",
                "rate_limit_detail": "Windows has no supported in-box per-WireGuard-peer shaper",
                "rate_limit_mode": "unlimited_only",
                "max_active_peers": DEFAULT_MAX_ACTIVE_PEERS}

    def _powershell(self, action: str, policy: dict, check=True):
        management = ",".join(policy["management_subnets"])
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(self.helper), "-Action", action, "-PeerId", policy["peer_id"],
                "-Interface", policy["interface"], "-EgressInterface", policy["egress_interface"],
                "-Address", policy["address"], "-CustomerSubnet", policy["customer_subnet"],
                "-ManagementSubnets", management]
        return self.runner(argv, check=check)

    def _update_clock(self, state: dict, now: int) -> None:
        if state.get("clock_quarantined"):
            raise RuntimeError("relay wall clock rollback requires manual recovery")
        last = state.get("last_clock")
        if last is not None and now < last:
            state["clock_quarantined"] = True
            state["clock_rollback_at"] = last
            self.store.save(state)
            for peer_id, record in list(state["peers"].items()):
                if record.get("status") == "active":
                    self.revoke(peer_id, "clock_rollback", expected_policy=record["policy"])
            raise RuntimeError("relay wall clock moved backwards; active peers were revoked")
        if last is None or now > last:
            state["last_clock"] = now
            self.store.save(state)

    def _validate_policy_batch(self, policies: list[dict], now: int) -> None:
        if len(policies) > self.max_active_peers:
            raise ValueError("desired policies exceed relay active peer capacity")
        peer_ids = set()
        addresses = {}
        keys = {}
        for policy in policies:
            if policy["peer_id"] in peer_ids:
                raise ValueError("duplicate desired peer_id")
            peer_ids.add(policy["peer_id"])
            if _policy_deadline(policy) is not None and _policy_deadline(policy) <= now:
                raise ValueError("expired policy cannot be applied")
            address_key = (policy["interface"], policy["address"])
            if address_key in addresses and addresses[address_key] != policy["peer_id"]:
                raise ValueError("duplicate desired customer address on interface")
            addresses[address_key] = policy["peer_id"]
            key_key = (policy["interface"], policy["public_key"])
            if key_key in keys and keys[key_key] != policy["peer_id"]:
                raise ValueError("duplicate desired WireGuard public key on interface")
            keys[key_key] = policy["peer_id"]

    @_locked_method
    def apply(self, raw: dict) -> dict:
        require_windows_admin()
        policy = validate_windows_policy(raw)
        now = int(self.clock())
        if _policy_deadline(policy) is not None and _policy_deadline(policy) <= now:
            raise ValueError("expired policy cannot be applied")
        state = self.store.load()
        self._update_clock(state, now)
        previous = state["peers"].get(policy["peer_id"])
        if previous and previous.get("status") in {"active", "recovery_required"}:
            old_policy = previous["policy"]
            if any(old_policy[key] != policy[key] for key in ("interface", "address", "public_key")):
                raise ValueError("active peer replacement requires explicit revoke")
            policy["usage_baseline_bytes"] = old_policy.get("usage_baseline_bytes", 0)
        active = [row["policy"] for row in state["peers"].values()
                  if row.get("status") in {"active", "recovery_required"}
                  and row["policy"]["peer_id"] != policy["peer_id"]]
        if len(active) + 1 > self.max_active_peers:
            raise ValueError("relay active peer capacity exceeded")
        for item in active:
            shared = ("interface", "egress_interface", "customer_subnet", "management_subnets")
            if any(item[key] != policy[key] for key in shared):
                raise ValueError("active Windows relay peers must share gateway and isolation networks")
            if item["interface"] == policy["interface"] and item["address"] == policy["address"]:
                raise ValueError("customer address is already reserved on this interface")
            if item["interface"] == policy["interface"] and item["public_key"] == policy["public_key"]:
                raise ValueError("WireGuard public key is already reserved on this interface")
        try:
            self._powershell("EnsureGateway", policy)
            self.runner([self.wg, "set", policy["interface"], "peer", policy["public_key"],
                         "allowed-ips", policy["address"]], check=True)
            self._powershell("ApplyPeer", policy)
        except Exception as exc:
            self.runner([self.wg, "set", policy["interface"], "peer", policy["public_key"], "remove"], check=False)
            self._powershell("QuarantinePeer", policy, check=False)
            state["peers"][policy["peer_id"]] = {"policy": policy, "status": "recovery_required",
                "error": str(exc), "updated_at": int(self.clock()), "capabilities": self.capabilities()}
            self.store.save(state)
            self.store.event("apply", policy["peer_id"], False, str(exc))
            raise RuntimeError("Windows relay apply failed; cleanup was attempted") from exc
        baseline = None
        if not previous:
            history = [record for key, record in state["peers"].items()
                       if key != policy["peer_id"] and
                       record.get("policy", {}).get("interface") == policy["interface"] and
                       record.get("policy", {}).get("public_key") == policy["public_key"] and
                       record.get("policy", {}).get("address") == policy["address"]]
            if history:
                baseline = max(history, key=lambda item: item.get("updated_at", 0))
        state["peers"][policy["peer_id"]] = {"policy": policy, "status": "active",
            "used_bytes": (previous.get("used_bytes", 0) if previous else
                            policy.get("usage_baseline_bytes", 0)),
            "accounted_received": previous.get("accounted_received", 0) if previous else 0,
            "accounted_sent": previous.get("accounted_sent", 0) if previous else 0,
            "last_received": (previous.get("last_received") if previous else
                               baseline.get("last_received") if baseline else None),
            "last_sent": (previous.get("last_sent") if previous else
                           baseline.get("last_sent") if baseline else None),
            "updated_at": int(self.clock()), "capabilities": self.capabilities()}
        self.store.save(state)
        self.store.event("apply", policy["peer_id"], True, "rate limiting unsupported on Windows")
        return state["peers"][policy["peer_id"]]

    @_locked_method
    def revoke(self, peer_id: str, reason="revoked", *, expected_policy=None) -> dict:
        require_windows_admin()
        if not isinstance(peer_id, str) or not SAFE_ID_RE.fullmatch(peer_id):
            raise ValueError("invalid peer_id")
        state = self.store.load()
        record = state["peers"].get(peer_id)
        if not record:
            return {"peer_id": peer_id, "status": "absent"}
        p = record["policy"]
        if expected_policy is not None and p != expected_policy:
            return {"peer_id": peer_id, "status": "stale_cleanup_ignored"}
        wg_result = self.runner([self.wg, "set", p["interface"], "peer", p["public_key"], "remove"], check=False)
        firewall_action = "RemovePeer" if getattr(wg_result, "returncode", 0) == 0 else "QuarantinePeer"
        results = [wg_result, self._powershell(firewall_action, p, check=False)]
        failures = [str(index) for index, result in enumerate(results) if getattr(result, "returncode", 0)]
        record.update({"status": "recovery_required" if failures else "revoked", "reason": reason,
                       "updated_at": int(self.clock())})
        if failures:
            record["error"] = "failed cleanup steps: " + ",".join(failures)
        self.store.save(state)
        self.store.event("revoke", peer_id, not failures, record.get("error", reason))
        return record

    @_locked_method
    def measure(self, interface: str) -> dict:
        require_windows_admin()
        if not isinstance(interface, str) or not SAFE_NAME_RE.fullmatch(interface):
            raise ValueError("invalid WireGuard interface")
        output = self.runner([self.wg, "show", interface, "transfer"], check=True).stdout
        counters = parse_wg_transfer(output)
        state, now = self.store.load(), int(self.clock())
        self._update_clock(state, now)
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
            record["used_bytes"] = (record["policy"].get("usage_baseline_bytes", 0) +
                                     record["accounted_received"] + record["accounted_sent"])
            record.update({"last_received": current["received_bytes"], "last_sent": current["sent_bytes"],
                           "delta_received": delta_rx, "delta_sent": delta_tx,
                           "delta_bytes": delta_rx + delta_tx, "measured_at": now})
            record["pending_usage"] = {
                "report_id": f"{record['policy']['peer_id']}:{record['accounted_received']}:{record['accounted_sent']}",
                "rx_total": record["accounted_received"],
                "tx_total": record["accounted_sent"],
            }
        self.store.save(state)
        return self.status()

    @_locked_method
    def mark_usage_reported(self, peer_id: str, report_id: str) -> None:
        state = self.store.load()
        record = state["peers"].get(peer_id)
        pending = record.get("pending_usage") if record else None
        if pending and pending.get("report_id") == report_id:
            record.pop("pending_usage", None)
            self.store.save(state)

    @_locked_method
    def reconcile(self, desired: list[dict] | None = None) -> dict:
        require_windows_admin()
        now = int(self.clock())
        state = self.store.load()
        self._update_clock(state, now)
        if desired is not None:
            if not isinstance(desired, list) or len(desired) > 4096:
                raise ValueError("desired policies must be a list")
            normalized = [validate_windows_policy(row) for row in desired]
            self._validate_policy_batch(normalized, now)
            policies = {p["peer_id"]: p for p in normalized}
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
            if _policy_deadline(p) is not None and _policy_deadline(p) <= now:
                self.revoke(peer_id, "expired")
            elif p["quota_bytes"] is not None and record.get("used_bytes", 0) >= p["quota_bytes"]:
                self.revoke(peer_id, "quota_exhausted")
        return self.status()

    @_locked_method
    def expire(self, *, now: int | None = None) -> dict:
        """Enforce local expiry without contacting the control plane."""
        require_windows_admin()
        now = int(self.clock()) if now is None else now
        state = self.store.load()
        self._update_clock(state, now)
        if now == int(self.clock()):
            interfaces = {record["policy"]["interface"] for record in state["peers"].values()
                          if record.get("status") == "active"}
            for interface in sorted(interfaces):
                try:
                    self.measure(interface)
                except Exception as exc:
                    self.store.event("measure_before_expiry", interface, False, type(exc).__name__)
            state = self.store.load()
        for peer_id, record in list(state["peers"].items()):
            if record.get("status") != "active":
                continue
            policy = record["policy"]
            reason = None
            if _policy_deadline(policy) is not None and _policy_deadline(policy) <= now:
                reason = "expired"
            elif policy.get("quota_bytes") is not None and record.get("used_bytes", 0) >= policy["quota_bytes"]:
                reason = "quota_exhausted"
            if reason:
                self.revoke(peer_id, reason, expected_policy=policy)
        return self.status()

    @_locked_method
    def validate_snapshot(self, value: object, *, now: int | None = None) -> int:
        if not isinstance(value, dict) or not isinstance(value.get("policies"), list):
            raise ValueError("control response has no policy list")
        generated_at = value.get("generated_at")
        if (isinstance(generated_at, bool) or not isinstance(generated_at, int) or
                generated_at < 1):
            raise ValueError("control response has an invalid snapshot timestamp")
        now = int(self.clock()) if now is None else now
        state = self.store.load()
        self._update_clock(state, now)
        if generated_at > now + CLOCK_SKEW_SECONDS:
            raise ValueError("control response snapshot is from the future")
        last = state.get("last_snapshot_at")
        if last is not None and generated_at < last:
            raise ValueError("control response snapshot is stale")
        max_offline = value.get("max_offline_seconds", DEFAULT_MAX_OFFLINE_SECONDS)
        if (isinstance(max_offline, bool) or not isinstance(max_offline, int) or
                not 1 <= max_offline <= 86400):
            raise ValueError("control response max_offline_seconds is invalid")
        for raw in value["policies"]:
            policy = validate_windows_policy(raw)
            deadline = policy.get("offline_deadline")
            if (policy.get("snapshot_at") != generated_at or
                    not isinstance(deadline, int) or deadline <= generated_at or
                    deadline > generated_at + max_offline or
                    _policy_deadline(policy) is not None and deadline > _policy_deadline(policy)):
                raise ValueError("relay policy offline deadline is invalid")
        return generated_at

    @_locked_method
    def record_snapshot(self, generated_at: int) -> None:
        state = self.store.load()
        previous = state.get("last_snapshot_at")
        if previous is not None and generated_at < previous:
            raise ValueError("cannot record an older relay snapshot")
        state["last_snapshot_at"] = generated_at
        self.store.save(state)

    def status(self) -> dict:
        state = self.store.load()
        return {"version": state["version"], "peers": state["peers"], "at": int(self.clock()),
                "capabilities": self.capabilities(), "max_active_peers": self.max_active_peers,
                "last_snapshot_at": state.get("last_snapshot_at")}


def run_loop(manager, control, cursor_path: Path, interface: str, *, once: bool, interval: int,
             sleeper: Callable[[float], None] = time.sleep):
    while True:
        manager.expire()
        try:
            result = sync(manager, control, cursor_path, interface)
        except Exception as exc:
            manager.store.event("sync", "control", False, type(exc).__name__)
            if once:
                raise
            sleeper(interval)
            continue
        if once:
            return result
        sleeper(interval)


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
    result = run_loop(manager, control, args.data / "cursor", args.interface,
                      once=args.once, interval=args.interval)
    if args.once:
        print(json.dumps({"ok": True, "peers": len(result["peers"]),
                          "capabilities": manager.capabilities()}, ensure_ascii=False))
        return


if __name__ == "__main__":
    main()
