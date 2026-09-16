"""Linux WireGuard customer relay enforcement.

The public API accepts dictionaries with a fixed schema and executes argv lists
without a shell.  It is intentionally independent from the web control plane.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
from typing import Callable


PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
STATE_VERSION = 1


def validate_policy(value: object) -> dict:
    """Validate and normalize a relay policy; unknown fields are rejected."""
    if not isinstance(value, dict):
        raise ValueError("policy must be an object")
    allowed = {"peer_id", "public_key", "address", "interface", "egress_interface",
               "customer_subnet", "management_subnets", "download_bps", "upload_bps",
               "quota_bytes", "expires_at", "enabled"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("unknown policy fields: " + ", ".join(sorted(unknown)))
    peer_id = value.get("peer_id")
    public_key = value.get("public_key")
    interface = value.get("interface")
    egress = value.get("egress_interface")
    if not isinstance(peer_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", peer_id):
        raise ValueError("invalid peer_id")
    if not isinstance(public_key, str) or not PUBLIC_KEY_RE.fullmatch(public_key):
        raise ValueError("invalid WireGuard public key")
    if not isinstance(interface, str) or not NAME_RE.fullmatch(interface):
        raise ValueError("invalid WireGuard interface")
    if not isinstance(egress, str) or not NAME_RE.fullmatch(egress):
        raise ValueError("invalid egress interface")
    try:
        address = ipaddress.ip_interface(value.get("address"))
        customer = ipaddress.ip_network(value.get("customer_subnet"), strict=True)
    except (TypeError, ValueError):
        raise ValueError("invalid customer address or subnet") from None
    if address.version != 4 or address.network.prefixlen != 32 or address.ip not in customer:
        raise ValueError("customer address must be an IPv4 /32 inside customer_subnet")
    management = []
    raw_management = value.get("management_subnets", [])
    if not isinstance(raw_management, list) or len(raw_management) > 32:
        raise ValueError("management_subnets must be a list")
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
        raise ValueError("apply only accepts enabled policies; use revoke to disable")
    return {"peer_id": peer_id, "public_key": public_key, "address": str(address),
            "interface": interface, "egress_interface": egress,
            "customer_subnet": str(customer), "management_subnets": management,
            **rates, "quota_bytes": quota, "expires_at": expires, "enabled": True}


def parse_wg_transfer(output: str) -> dict[str, dict[str, int]]:
    """Parse ``wg show INTERFACE transfer`` into counters keyed by public key."""
    result = {}
    for number, raw in enumerate(output.splitlines(), 1):
        if not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) != 3 or not PUBLIC_KEY_RE.fullmatch(fields[0]):
            raise ValueError(f"invalid wg transfer row {number}")
        try:
            received, sent = int(fields[1]), int(fields[2])
        except ValueError:
            raise ValueError(f"invalid wg transfer counters on row {number}") from None
        if received < 0 or sent < 0:
            raise ValueError(f"negative wg transfer counters on row {number}")
        result[fields[0]] = {"received_bytes": received, "sent_bytes": sent}
    return result


def _minor(peer_id: str) -> int:
    return int.from_bytes(hashlib.sha256(peer_id.encode()).digest()[:2], "big") % 60000 + 1000


def _rate(value: int) -> str:
    return f"{value}bit"


def plan_apply(policy: dict, initialize=False) -> list[list[str]]:
    """Return the complete argv-only mutation plan for a normalized policy."""
    p = validate_policy(policy)
    ip = p["address"].split("/", 1)[0]
    minor = str(_minor(p["peer_id"]))
    commands = []
    if initialize:
        commands.extend([
            ["tc", "qdisc", "replace", "dev", p["interface"], "root", "handle", "1:", "htb", "default", "1"],
            ["tc", "class", "replace", "dev", p["interface"], "parent", "1:", "classid", "1:1",
             "htb", "rate", "100000000000bit", "ceil", "100000000000bit"],
            ["tc", "qdisc", "replace", "dev", p["interface"], "handle", "ffff:", "ingress"],
        ])
    commands.extend([
        ["wg", "set", p["interface"], "peer", p["public_key"], "allowed-ips", p["address"]],
        ["tc", "class", "replace", "dev", p["interface"], "parent", "1:", "classid", f"1:{minor}",
         "htb", "rate", _rate(p["download_bps"]), "ceil", _rate(p["download_bps"])],
        ["tc", "filter", "replace", "dev", p["interface"], "protocol", "ip", "parent", "1:",
         "pref", minor, "u32", "match", "ip", "dst", p["address"], "flowid", f"1:{minor}"],
        ["tc", "filter", "replace", "dev", p["interface"], "parent", "ffff:", "protocol", "ip",
         "pref", minor, "u32", "match", "ip", "src", p["address"], "police", "rate",
         _rate(p["upload_bps"]), "burst", "256k", "drop", "flowid", f":{minor}"],
        ["nft", "add", "element", "inet", "sna_relay", "customers", "{", ip, "}"],
    ])
    return commands


def plan_revoke(policy: dict) -> list[list[str]]:
    p = validate_policy(policy)
    ip = p["address"].split("/", 1)[0]
    minor = str(_minor(p["peer_id"]))
    return [
        ["wg", "set", p["interface"], "peer", p["public_key"], "remove"],
        ["tc", "filter", "del", "dev", p["interface"], "protocol", "ip", "parent", "1:", "pref", minor],
        ["tc", "class", "del", "dev", p["interface"], "classid", f"1:{minor}"],
        ["tc", "filter", "del", "dev", p["interface"], "parent", "ffff:", "protocol", "ip", "pref", minor],
        ["nft", "delete", "element", "inet", "sna_relay", "customers", "{", ip, "}"],
    ]


def firewall_script(policy: dict, addresses=(), replace=False) -> str:
    """Build the fixed nftables base policy for this relay."""
    p = validate_policy(policy)
    management = ", ".join(p["management_subnets"]) or "192.0.2.0/32"
    customers = ", ".join(addresses)
    prefix = ["delete table inet sna_relay"] if replace else []
    return "\n".join(prefix + [
        "table inet sna_relay {",
        f" set customers {{ type ipv4_addr; flags interval; elements = {{ {customers} }} }}",
        f" set management {{ type ipv4_addr; flags interval; elements = {{ {management} }} }}",
        " chain forward { type filter hook forward priority -10; policy accept;",
        f'  iifname "{p["interface"]}" ip saddr @customers ip daddr @management drop',
        f'  iifname "{p["interface"]}" oifname "{p["interface"]}" ip saddr @customers drop',
        f'  iifname "{p["interface"]}" ip saddr @customers oifname != "{p["egress_interface"]}" drop',
        f'  iifname "{p["interface"]}" ip saddr @customers accept',
        f'  oifname "{p["interface"]}" ip daddr @customers ct state established,related accept',
        f'  oifname "{p["interface"]}" ip daddr @customers drop',
        " }", " chain postrouting { type nat hook postrouting priority 100; policy accept;",
        f'  ip saddr {p["customer_subnet"]} oifname "{p["egress_interface"]}" masquerade',
        " }", "}", "",
    ])


class RelayStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "events.jsonl"

    def load(self) -> dict:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if value.get("version") != STATE_VERSION or not isinstance(value.get("peers"), dict):
                raise ValueError()
            return value
        except FileNotFoundError:
            return {"version": STATE_VERSION, "peers": {}}
        except Exception:
            raise RuntimeError("relay state is corrupt") from None

    def save(self, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.state_path)

    def event(self, action: str, peer_id: str, ok: bool, detail: str = "") -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": int(time.time()), "action": action, "peer_id": peer_id,
                                     "ok": ok, "detail": detail}, ensure_ascii=False) + "\n")
        self.journal_path.chmod(0o600)


def require_linux_root() -> None:
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise RuntimeError("relay mutation requires Linux root")


def _run(argv: list[str], *, input_text: str | None = None, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=input_text, text=True, capture_output=True, check=check)


class RelayManager:
    def __init__(self, data: Path, runner: Callable = _run, clock: Callable[[], float] = time.time):
        self.store, self.runner, self.clock = RelayStore(data), runner, clock

    def _base_firewall(self, policy: dict, addresses: list[str], replace: bool) -> None:
        self.runner(["nft", "-f", "-"], input_text=firewall_script(policy, addresses, replace), check=True)

    def apply(self, raw: dict) -> dict:
        require_linux_root()
        policy = validate_policy(raw)
        state = self.store.load()
        previous = state["peers"].get(policy["peer_id"])
        active = [item["policy"] for item in state["peers"].values() if item.get("status") == "active"
                  and item["policy"]["peer_id"] != policy["peer_id"]]
        for item in active:
            shared = ("interface", "egress_interface", "customer_subnet", "management_subnets")
            if any(item[key] != policy[key] for key in shared):
                raise ValueError("active relay peers must share interface, egress and isolation networks")
            if _minor(item["peer_id"]) == _minor(policy["peer_id"]):
                raise ValueError("peer_id traffic-control class collision")
        completed = []
        try:
            addresses = [item["address"].split('/', 1)[0] for item in active]
            addresses.append(policy["address"].split('/', 1)[0])
            self._base_firewall(policy, addresses, bool(active or previous))
            initialized = any(item.get("status") == "active" and
                              item["policy"]["interface"] == policy["interface"] and
                              item["policy"]["peer_id"] != policy["peer_id"]
                              for item in state["peers"].values())
            for command in plan_apply(policy, initialize=not initialized):
                self.runner(command, check=True)
                completed.append(command)
        except Exception as exc:
            for command in plan_revoke(policy):
                self.runner(command, check=False)
            state["peers"][policy["peer_id"]] = {"policy": policy, "status": "recovery_required",
                                                  "error": str(exc), "updated_at": int(self.clock())}
            if previous:
                state["peers"][policy["peer_id"]]["previous"] = previous
            self.store.save(state)
            self.store.event("apply", policy["peer_id"], False, str(exc))
            raise RuntimeError("relay apply failed; peer was revoked and recovery state recorded") from exc
        state["peers"][policy["peer_id"]] = {"policy": policy, "status": "active",
                                              "used_bytes": previous.get("used_bytes", 0) if previous else 0,
                                              "accounted_received": previous.get("accounted_received", 0) if previous else 0,
                                              "accounted_sent": previous.get("accounted_sent", 0) if previous else 0,
                                              "last_received": previous.get("last_received") if previous else None,
                                              "last_sent": previous.get("last_sent") if previous else None,
                                              "updated_at": int(self.clock())}
        self.store.save(state)
        self.store.event("apply", policy["peer_id"], True)
        return state["peers"][policy["peer_id"]]

    def revoke(self, peer_id: str, reason="revoked") -> dict:
        require_linux_root()
        if not isinstance(peer_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", peer_id):
            raise ValueError("invalid peer_id")
        state = self.store.load()
        record = state["peers"].get(peer_id)
        if not record:
            return {"peer_id": peer_id, "status": "absent"}
        failures = []
        for command in plan_revoke(record["policy"]):
            result = self.runner(command, check=False)
            if getattr(result, "returncode", 0) != 0:
                failures.append(" ".join(command[:4]))
        record.update({"status": "recovery_required" if failures else "revoked", "reason": reason,
                       "updated_at": int(self.clock())})
        if failures:
            record["error"] = "failed cleanup: " + ", ".join(failures)
        self.store.save(state)
        self.store.event("revoke", peer_id, not failures, record.get("error", reason))
        return record

    def measure(self, interface: str) -> dict:
        require_linux_root()
        if not isinstance(interface, str) or not NAME_RE.fullmatch(interface):
            raise ValueError("invalid WireGuard interface")
        output = self.runner(["wg", "show", interface, "transfer"], check=True).stdout
        counters = parse_wg_transfer(output)
        state = self.store.load()
        now = int(self.clock())
        for record in state["peers"].values():
            policy = record["policy"]
            if policy["interface"] != interface or policy["public_key"] not in counters:
                continue
            current = counters[policy["public_key"]]
            old_rx, old_tx = record.get("last_received"), record.get("last_sent")
            delta_rx = current["received_bytes"] if old_rx is None or current["received_bytes"] < old_rx else current["received_bytes"] - old_rx
            delta_tx = current["sent_bytes"] if old_tx is None or current["sent_bytes"] < old_tx else current["sent_bytes"] - old_tx
            # A WireGuard restart resets kernel counters. Cumulative accounted values remain monotonic.
            record["accounted_received"] = record.get("accounted_received", 0) + delta_rx
            record["accounted_sent"] = record.get("accounted_sent", 0) + delta_tx
            record["used_bytes"] = record["accounted_received"] + record["accounted_sent"]
            record.update({"last_received": current["received_bytes"], "last_sent": current["sent_bytes"],
                           "delta_received": delta_rx, "delta_sent": delta_tx,
                           "delta_bytes": delta_rx + delta_tx, "measured_at": now})
        self.store.save(state)
        return self.status()

    def reconcile(self, desired: list[dict] | None = None) -> dict:
        require_linux_root()
        if desired is not None:
            if not isinstance(desired, list) or len(desired) > 4096:
                raise ValueError("desired policies must be a list")
            policies = {item["peer_id"]: item for item in (validate_policy(row) for row in desired)}
            state = self.store.load()
            for peer_id, record in list(state["peers"].items()):
                if record.get("status") in {"active", "recovery_required"} and peer_id not in policies:
                    self.revoke(peer_id, "not_desired")
            state = self.store.load()
            for peer_id, item in policies.items():
                current = state["peers"].get(peer_id)
                if not current or current.get("status") != "active" or current.get("policy") != item:
                    self.apply(item)
        state = self.store.load()
        now = int(self.clock())
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
        return {"version": state["version"], "peers": state["peers"], "at": int(self.clock())}


def _read_policy(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_policy(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Enforce customer peers on a Linux WireGuard relay")
    parser.add_argument("--data", default="/var/lib/server-network-assist-relay")
    sub = parser.add_subparsers(dest="action", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--policy", required=True)
    revoke_parser = sub.add_parser("revoke")
    revoke_parser.add_argument("--peer-id", required=True)
    measure_parser = sub.add_parser("measure")
    measure_parser.add_argument("--interface", required=True)
    sub.add_parser("status")
    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--desired")
    args = parser.parse_args(argv)
    manager = RelayManager(Path(args.data))
    if args.action == "apply":
        result = manager.apply(_read_policy(args.policy))
    elif args.action == "revoke":
        result = manager.revoke(args.peer_id)
    elif args.action == "measure":
        result = manager.measure(args.interface)
    elif args.action == "reconcile":
        desired = json.loads(Path(args.desired).read_text(encoding="utf-8")) if args.desired else None
        result = manager.reconcile(desired)
    else:
        result = manager.status()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
