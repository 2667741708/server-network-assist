"""Linux WireGuard customer relay enforcement.

The public API accepts dictionaries with a fixed schema and executes argv lists
without a shell.  It is intentionally independent from the web control plane.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import functools
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import threading
import time
from typing import Callable

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX only
    msvcrt = None


PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
STATE_VERSION = 1
DEFAULT_MAX_ACTIVE_PEERS = 256
DEFAULT_MAX_OFFLINE_SECONDS = 900
CLOCK_SKEW_SECONDS = 30


def _policy_deadline(policy: dict) -> int | None:
    """Return the earliest local deadline known for a relay policy."""
    deadlines = [value for value in (policy.get("expires_at"),
                                      policy.get("offline_deadline"))
                 if value is not None]
    return min(deadlines) if deadlines else None


def validate_policy(value: object) -> dict:
    """Validate and normalize a relay policy; unknown fields are rejected."""
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
               "offline_deadline", "enabled", "tunnel_probe_address", "tunnel_probe_port",
               "probe_only", "probe_echo_addresses"}
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
    probe = value.get('tunnel_probe_address')
    probe_port = value.get('tunnel_probe_port')
    if (probe is None) != (probe_port is None):
        raise ValueError('tunnel probe address and port must be configured together')
    if probe is not None:
        try:
            probe_ip = ipaddress.IPv4Address(probe)
        except (TypeError, ValueError):
            raise ValueError('invalid tunnel probe address') from None
        private_ranges = (ipaddress.IPv4Network('10.0.0.0/8'),
                          ipaddress.IPv4Network('172.16.0.0/12'),
                          ipaddress.IPv4Network('192.168.0.0/16'))
        if not any(probe_ip in network for network in private_ranges) or probe_ip == address.ip:
            raise ValueError('tunnel probe address must be a distinct relay address')
        if type(probe_port) is not int or not 1 <= probe_port <= 65535:
            raise ValueError('invalid tunnel probe port')
    probe_only = value.get('probe_only', False)
    if type(probe_only) is not bool:
        raise ValueError('probe_only must be a boolean')
    echo_addresses = value.get('probe_echo_addresses')
    if probe_only:
        if probe is None or not isinstance(echo_addresses, list) or not 1 <= len(echo_addresses) <= 16:
            raise ValueError('probe-only policy requires a challenge and echo address list')
        try:
            echoes = [ipaddress.IPv4Address(item) for item in echo_addresses]
            if (any(not isinstance(item, str) or str(address) != item or not address.is_global
                    for item, address in zip(echo_addresses, echoes)) or
                    len(echoes) != len(set(echoes))):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('invalid probe echo address') from None
    elif echo_addresses is not None:
        raise ValueError('echo addresses require probe_only')
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
        if item is not None and (type(item) is not int or not 8_000 <= item <= 100_000_000_000):
            raise ValueError(f"{key} must be null or between 8000 and 100000000000")
        rates[key] = item
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
    if (expires is not None and offline_deadline is not None and
            offline_deadline > expires):
        raise ValueError("offline_deadline cannot extend lease expiry")
    if value.get("enabled", True) is not True:
        raise ValueError("apply only accepts enabled policies; use revoke to disable")
    return {"peer_id": peer_id, "public_key": public_key, "address": str(address),
            "interface": interface, "egress_interface": egress,
            "egress_policy": egress_policy,
            "customer_subnet": str(customer), "management_subnets": management,
            **rates, "quota_bytes": quota, "usage_baseline_bytes": usage_baseline,
            "expires_at": expires,
            "snapshot_at": snapshot_at, "offline_deadline": offline_deadline,
            "enabled": True,
            **({'tunnel_probe_address': str(probe_ip), 'tunnel_probe_port': probe_port}
               if probe is not None else {}),
            **({'probe_only': True,
                'probe_echo_addresses': [str(address) for address in echoes]}
               if probe_only else {})}


def relay_policy_fingerprint(value: object) -> str:
    """Stable digest of the peer's packet policy, excluding poll timestamps."""
    policy = validate_policy(value)
    policy.pop('snapshot_at', None)
    policy.pop('offline_deadline', None)
    policy.pop('usage_baseline_bytes', None)
    payload = json.dumps(policy, sort_keys=True, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(payload).hexdigest()


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
    minor = format(_minor(p["peer_id"]), 'x')
    preference = str(_minor(p["peer_id"]))
    commands = []
    if initialize:
        commands.extend([
            ["tc", "qdisc", "replace", "dev", p["interface"], "root", "handle", "1:", "htb", "default", "1"],
            ["tc", "class", "replace", "dev", p["interface"], "parent", "1:", "classid", "1:1",
             "htb", "rate", "100000000000bit", "ceil", "100000000000bit"],
            ["tc", "qdisc", "replace", "dev", p["interface"], "handle", "ffff:", "ingress"],
        ])
    commands.extend([
        ["ip", "route", "replace", p["address"], "dev", p["interface"]],
        ["wg", "set", p["interface"], "peer", p["public_key"], "allowed-ips", p["address"]],
    ])
    if p['download_bps'] is not None:
        commands.extend([
        ["tc", "class", "replace", "dev", p["interface"], "parent", "1:", "classid", f"1:{minor}",
         "htb", "rate", _rate(p["download_bps"]), "ceil", _rate(p["download_bps"])],
        ["tc", "filter", "replace", "dev", p["interface"], "protocol", "ip", "parent", "1:",
         "pref", preference, "u32", "match", "ip", "dst", p["address"], "flowid", f"1:{minor}"],
        ])
    if p['upload_bps'] is not None:
        commands.extend([
        ["tc", "filter", "replace", "dev", p["interface"], "parent", "ffff:", "protocol", "ip",
         "pref", preference, "u32", "match", "ip", "src", p["address"], "police", "rate",
         _rate(p["upload_bps"]), "burst", "256k", "drop", "flowid", f":{minor}"],
        ])
    # The owned nft table is rebuilt before this plan.  Adding the address here
    # as well would either reset its kernel timeout or fail on a duplicate
    # element.  Keeping membership in the table rebuild makes the expiry
    # barrier atomic with the rest of the desired customer set.
    return commands


def plan_revoke(policy: dict) -> list[list[str]]:
    p = validate_policy(policy)
    ip = p["address"].split("/", 1)[0]
    minor = format(_minor(p["peer_id"]), 'x')
    preference = str(_minor(p["peer_id"]))
    commands = [
        ["wg", "set", p["interface"], "peer", p["public_key"], "remove"],
        ["ip", "route", "del", p["address"], "dev", p["interface"]],
    ]
    if p['download_bps'] is not None:
        commands.extend([
        ["tc", "filter", "del", "dev", p["interface"], "protocol", "ip", "parent", "1:", "pref", preference],
        ["tc", "class", "del", "dev", p["interface"], "classid", f"1:{minor}"],
        ])
    if p['upload_bps'] is not None:
        commands.append(
        ["tc", "filter", "del", "dev", p["interface"], "parent", "ffff:", "protocol", "ip", "pref", preference],
        )
    commands.extend([
        ["nft", "delete", "element", "inet", "sna_relay", "customers", "{", ip, "}"],
    ])
    return commands


def _customer_elements(addresses, now: int | None = None) -> tuple[str, bool]:
    now = int(time.time()) if now is None else now
    elements = []
    has_timeout = False
    for item in addresses:
        if isinstance(item, dict):
            address = item.get("address")
            deadline = _policy_deadline(item)
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            address, deadline = item
        else:
            address, deadline = item, None
        if not isinstance(address, str):
            raise ValueError("customer firewall address is invalid")
        ip = address.split("/", 1)[0]
        if deadline is None:
            elements.append(ip)
        else:
            # A positive timeout keeps the kernel barrier fail-closed even in
            # the small interval between a deadline and watchdog cleanup.
            has_timeout = True
            elements.append(f"{ip} timeout {max(1, int(deadline) - now)}s")
    return ", ".join(elements), has_timeout


def firewall_script(policy: dict, addresses=(), replace=False, *, now: int | None = None) -> str:
    """Build the fixed nftables base policy for this relay."""
    p = validate_policy(policy)
    management = ", ".join(p["management_subnets"]) or "192.0.2.0/32"
    customers, has_timeout = _customer_elements(addresses, now=now)
    customer_flags = "timeout" if has_timeout else "interval"
    prefix = ["delete table inet sna_relay"] if replace else []
    customer_subnet = p["customer_subnet"]
    interface = p["interface"]
    peers = [validate_policy(item) for item in addresses if isinstance(item, dict)]
    if not peers:
        peers = [p]
    exits = []
    challenge = []
    nat = []
    for peer in peers:
        host = peer["address"].split("/", 1)[0]
        outlet = peer["egress_interface"]
        if 'tunnel_probe_address' in peer:
            challenge.append(f'  iifname "{interface}" ip saddr {host} '
                             f'ip daddr {peer["tunnel_probe_address"]} '
                             f'tcp dport {peer["tunnel_probe_port"]} accept')
        if peer.get('probe_only'):
            echoes = ', '.join(peer['probe_echo_addresses'])
            exits.append(f'  iifname "{interface}" ip saddr {host} '
                         f'ip daddr {{ {echoes} }} tcp dport 443 '
                         f'oifname "{outlet}" accept')
            exits.append(f'  iifname "{interface}" ip saddr {host} drop')
            nat.append(f'  ip saddr {host} ip daddr {{ {echoes} }} '
                       f'tcp dport 443 oifname "{outlet}" masquerade')
            continue
        if peer["egress_policy"] == "source_physical":
            exits.append(f'  iifname "{interface}" ip saddr {host} ip daddr 198.18.0.0/15 drop')
        exits.append(f'  iifname "{interface}" ip saddr {host} oifname != "{outlet}" drop')
        exits.append(f'  iifname "{interface}" ip saddr {host} ip saddr @customers oifname "{outlet}" accept')
        nat.append(f'  ip saddr {host} oifname "{outlet}" masquerade')
    return "\n".join(prefix + [
        "table inet sna_relay {",
        f" set customers {{ type ipv4_addr; flags {customer_flags}; elements = {{ {customers} }} }}",
        f" set management {{ type ipv4_addr; flags interval; elements = {{ {management} }} }}",
        " chain input { type filter hook input priority -10; policy accept;",
        *challenge,
        f'  iifname "{interface}" ip saddr {customer_subnet} drop',
        f'  iifname "{interface}" ip6 saddr ::/0 drop',
        " }",
        " chain forward { type filter hook forward priority -10; policy accept;",
        f'  iifname "{interface}" ip saddr {customer_subnet} ip daddr @management drop',
        f'  iifname "{interface}" ip saddr {customer_subnet} oifname "{interface}" drop',
        *exits,
        f'  iifname "{interface}" ip saddr {customer_subnet} drop',
        f'  oifname "{interface}" ip daddr @customers ct state established,related accept',
        f'  oifname "{interface}" ip daddr {customer_subnet} drop',
        f'  iifname "{interface}" ip6 saddr ::/0 drop',
        f'  oifname "{interface}" ip6 daddr ::/0 drop',
        " }", " chain postrouting { type nat hook postrouting priority 100; policy accept;",
        *nat,
        " }", "}", "",
    ])


class RelayStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "events.jsonl"
        self.lock_path = self.root / "state.lock"
        self._thread_lock = threading.RLock()
        self._lock_local = threading.local()

    @contextmanager
    def exclusive(self):
        """Serialize state transitions across threads and relay processes."""
        depth = getattr(self._lock_local, "depth", 0)
        if depth:
            self._lock_local.depth = depth + 1
            try:
                yield
            finally:
                self._lock_local.depth -= 1
            return
        with self._thread_lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.lock_path.open("a+b") as stream:
                self.lock_path.chmod(0o600)
                stream.seek(0)
                if stream.read(1) != b"\0":
                    stream.seek(0)
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                elif msvcrt is not None:  # pragma: no cover - Windows only
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("relay state lock timeout") from None
                            time.sleep(0.05)
                self._lock_local.depth = 1
                try:
                    yield
                finally:
                    self._lock_local.depth = 0
                    if fcntl is not None:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None:  # pragma: no cover - Windows only
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def load(self) -> dict:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if value.get("version") != STATE_VERSION or not isinstance(value.get("peers"), dict):
                raise ValueError()
            for peer_id, record in value["peers"].items():
                if not isinstance(peer_id, str) or not isinstance(record, dict):
                    raise ValueError()
                if record.get("status") not in {"active", "revoked", "recovery_required"}:
                    raise ValueError()
                if not isinstance(record.get("policy"), dict):
                    raise ValueError()
                pending = record.get("pending_usage")
                if pending is not None and (not isinstance(pending, dict) or
                                            not isinstance(pending.get("report_id"), str) or
                                            any(isinstance(pending.get(key), bool) or
                                                not isinstance(pending.get(key), int) or
                                                pending[key] < 0 for key in ("rx_total", "tx_total"))):
                    raise ValueError()
                reported = (record.get("reported_received"), record.get("reported_sent"))
                if any(value is not None for value in reported):
                    accounted = (record.get("accounted_received"), record.get("accounted_sent"))
                    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                           for value in (*reported, *accounted)) or any(
                               sent > total for sent, total in zip(reported, accounted)):
                        raise ValueError()
                if (record["status"] in {"active", "recovery_required"}
                        and "egress_policy" not in record["policy"]):
                    policy = _normalize_active_legacy_policy(record["policy"])
                    record["policy"] = policy
                elif record["status"] == "revoked" and "egress_policy" not in record["policy"]:
                    _validate_historical_revoked_policy(record["policy"])
                    policy = record["policy"]
                else:
                    policy = validate_policy(record["policy"])
                if policy["peer_id"] != peer_id:
                    raise ValueError()
            last_clock = value.get("last_clock")
            if last_clock is not None and (isinstance(last_clock, bool) or not isinstance(last_clock, int)):
                raise ValueError()
            if "clock_quarantined" in value and not isinstance(value["clock_quarantined"], bool):
                raise ValueError()
            value.setdefault("last_clock", None)
            value.setdefault("clock_quarantined", False)
            last_snapshot = value.get("last_snapshot_at")
            if (last_snapshot is not None and
                    (isinstance(last_snapshot, bool) or not isinstance(last_snapshot, int))):
                raise ValueError()
            value.setdefault("last_snapshot_at", None)
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


def _locked_method(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.store.exclusive():
            return method(self, *args, **kwargs)
    return wrapper


def _validate_historical_revoked_policy(value: dict) -> None:
    """Validate revoked legacy state without inventing egress semantics."""
    if not isinstance(value, dict):
        raise ValueError("historical policy must be an object")
    legacy_mode = value.get("egress_mode")
    if legacy_mode not in (None, "physical", "source_proxy"):
        raise ValueError("unknown legacy egress mode")
    candidate = {
        key: item for key, item in value.items()
        if key not in {"egress_mode", "egress_gateway"}
    }
    # Validation-only sentinel; it is never persisted or used for authorization.
    candidate["egress_policy"] = "source_physical"
    validate_policy(candidate)


def _normalize_active_legacy_policy(value: dict) -> dict:
    """Normalize only explicit legacy egress modes for active state loading."""
    if not isinstance(value, dict):
        raise ValueError("legacy policy must be an object")
    mode_map = {"physical": "source_physical", "source_proxy": "source_proxy"}
    try:
        egress_policy = mode_map[value["egress_mode"]]
    except (KeyError, TypeError):
        raise ValueError("active legacy policy has no explicit egress mode") from None
    candidate = {
        key: item for key, item in value.items()
        if key not in {"egress_mode", "egress_gateway"}
    }
    candidate["egress_policy"] = egress_policy
    return validate_policy(candidate)


class RelayManager:
    def __init__(self, data: Path, runner: Callable = _run, clock: Callable[[], float] = time.time,
                 max_active_peers: int | None = None):
        if max_active_peers is None:
            raw_limit = os.environ.get("SNA_RELAY_MAX_ACTIVE_PEERS", str(DEFAULT_MAX_ACTIVE_PEERS))
            try:
                max_active_peers = int(raw_limit)
            except (TypeError, ValueError):
                raise ValueError("SNA_RELAY_MAX_ACTIVE_PEERS must be an integer") from None
        if not 1 <= max_active_peers <= 4096:
            raise ValueError("max_active_peers must be between 1 and 4096")
        self.store, self.runner, self.clock = RelayStore(data), runner, clock
        self.max_active_peers = max_active_peers

    def _historical_tc(self, record):
        commands = []
        while record:
            for command in plan_revoke(record['policy']):
                if command[0] == 'tc' and command not in commands:
                    commands.append(command)
            record = record.get('previous')
        return commands

    def _remove_tc(self, command):
        """Delete this peer's TC rule, or prove it is already absent."""
        result = self.runner(command, check=False)
        if result.returncode == 0:
            return
        interface = command[command.index('dev') + 1]
        if command[1] == 'filter':
            parent = command[command.index('parent') + 1]
            rows = json.loads(self.runner(['tc', '-j', 'filter', 'show', 'dev', interface,
                                          'parent', parent], check=True).stdout)
            key, expected = 'pref', command[command.index('pref') + 1]
            if not isinstance(rows, list) or any(not isinstance(row, dict) or key not in row for row in rows):
                raise RuntimeError('Cannot verify previous customer TC filter cleanup')
            present = any(str(row[key]) == expected for row in rows)
        else:
            rows = json.loads(self.runner(['tc', '-j', 'class', 'show', 'dev', interface], check=True).stdout)
            expected = command[command.index('classid') + 1]
            def identifier(value):
                return tuple(int(part or '0', 16) for part in value.split(':'))
            if not isinstance(rows, list) or any(not isinstance(row, dict) or not (row.get('classid') or row.get('handle')) for row in rows):
                raise RuntimeError('Cannot verify previous customer TC class cleanup')
            present = any(identifier(row.get('classid') or row['handle']) == identifier(expected) for row in rows)
        if present:
            raise RuntimeError('Previous customer TC limit still exists; retry required')

    def _customer_nft_element_present(self, address: str) -> bool:
        """Verify exact membership in the relay-owned customer set."""
        try:
            target = ipaddress.ip_address(address)
            if target.version != 4:
                raise ValueError
            result = self.runner(
                ["nft", "-j", "list", "set", "inet", "sna_relay", "customers"],
                check=False,
            )
            if getattr(result, "returncode", 1) != 0:
                raise ValueError
            document = json.loads(getattr(result, "stdout", "") or "")
            if not isinstance(document, dict) or not isinstance(document.get("nftables"), list):
                raise ValueError
            matches = []
            for row in document["nftables"]:
                if not isinstance(row, dict):
                    raise ValueError
                candidate = row.get("set")
                if (isinstance(candidate, dict) and candidate.get("family") == "inet" and
                        candidate.get("table") == "sna_relay" and
                        candidate.get("name") == "customers"):
                    matches.append(candidate)
            if len(matches) != 1 or matches[0].get("type") != "ipv4_addr":
                raise ValueError
            elements = matches[0].get("elem", [])
            if not isinstance(elements, list):
                raise ValueError
            for row in elements:
                if isinstance(row, str):
                    element = {"val": row}
                elif isinstance(row, dict) and isinstance(row.get("elem"), dict):
                    element = row["elem"]
                else:
                    raise ValueError
                first_raw = element.get("val")
                if not isinstance(first_raw, str):
                    raise ValueError
                first = ipaddress.ip_address(first_raw)
                if first.version != 4:
                    raise ValueError
                last_raw = element.get("val2")
                if last_raw is None:
                    if first == target:
                        return True
                    continue
                if not isinstance(last_raw, str):
                    raise ValueError
                last = ipaddress.ip_address(last_raw)
                if last.version != 4 or last < first:
                    raise ValueError
                # Be conservative for nft interval endpoints: any represented
                # range containing the address means cleanup is not proven.
                if first <= target <= last:
                    return True
            return False
        except Exception:
            raise RuntimeError("Cannot verify previous customer nft element cleanup") from None

    def _remove_customer_nft_element(self, command):
        """Delete an owned customer element, accepting only verified absence."""
        expected = ["nft", "delete", "element", "inet", "sna_relay", "customers", "{"]
        if len(command) != 9 or command[:7] != expected or command[8] != "}":
            raise RuntimeError("Unexpected customer nft cleanup command")
        try:
            address = ipaddress.ip_address(command[7])
            if address.version != 4:
                raise ValueError
        except ValueError:
            raise RuntimeError("Invalid customer nft cleanup address") from None
        result = self.runner(command, check=False)
        if getattr(result, "returncode", 1) == 0:
            return
        if self._customer_nft_element_present(str(address)):
            raise RuntimeError("Previous customer nft element still exists; retry required")

    def _base_firewall(self, policy: dict, addresses: list, replace: bool, *, now: int | None = None) -> None:
        # Revoked peers leave the fail-closed relay table installed.  Its
        # existence, not the count of active peers, determines whether the
        # next atomic ruleset must replace it.
        existing = self.runner(["nft", "list", "table", "inet", "sna_relay"], check=False)
        replace = getattr(existing, "returncode", 1) == 0
        result = self.runner(["nft", "-f", "-"],
                             input_text=firewall_script(policy, addresses, replace,
                                                         now=int(self.clock()) if now is None else now), check=False)
        if getattr(result, "returncode", 0):
            # A concurrent removal can invalidate the replace. Retry create
            # only when the table is really absent. Never turn a syntax or
            # permission failure on an existing table into a false success.
            probe = self.runner(["nft", "list", "table", "inet", "sna_relay"], check=False)
            if getattr(probe, "returncode", 1) == 0:
                raise RuntimeError("relay firewall table replacement failed")
            self.runner(["nft", "-f", "-"],
                        input_text=firewall_script(policy, addresses, False,
                                                   now=int(self.clock()) if now is None else now), check=True)

    @staticmethod
    def _physical_rule(policy: dict, action: str) -> list[str]:
        return ["ip", "-4", "rule", action, "priority", "101", "from", policy["address"],
                "iif", policy["interface"], "lookup", "main"]

    def _physical_rule_present(self, policy: dict) -> bool:
        rows = json.loads(self.runner(["ip", "-4", "-j", "rule", "show"], check=True).stdout or "[]")
        host = policy["address"].split("/", 1)[0]
        exact = [row for row in rows if row.get("priority") == 101 and
                 row.get("src") == host and row.get("srclen", 32) == 32 and
                 row.get("iif") == policy["interface"] and str(row.get("table")) in {"main", "254"}]
        if len(exact) > 1:
            raise RuntimeError("duplicate physical egress rule requires operator recovery")
        for row in rows:
            priority = int(row.get("priority", 32766))
            if row in exact or priority > 101 or (priority == 0 and
                    str(row.get("table")) in {"local", "255"}):
                continue
            if row.get("iif") not in (None, policy["interface"]):
                continue
            source = row.get("src", "all")
            if source == "all" or "not" in row:
                overlap = True
            else:
                try:
                    prefix = source if "/" in source else source + "/" + str(row.get("srclen", 32))
                    overlap = ipaddress.ip_address(host) in ipaddress.ip_network(prefix, strict=False)
                except (TypeError, ValueError):
                    raise RuntimeError("cannot verify earlier policy rule") from None
            if overlap:
                raise RuntimeError("foreign policy rule can override physical egress")
        return bool(exact)

    def _ensure_physical_route(self, policy: dict, *, owned: bool, install: bool = True) -> None:
        if policy["egress_policy"] != "source_physical":
            return
        link = json.loads(self.runner(["ip", "-j", "-d", "link", "show", "dev",
                                       policy["egress_interface"]], check=True).stdout or "[]")
        if len(link) != 1 or link[0].get("link_type") != "ether" or not link[0].get("parentdev") or \
                link[0].get("parentbus") not in {"pci", "usb"} or \
                link[0].get("linkinfo", {}).get("info_kind") in {"wireguard", "tun", "ipip", "sit"}:
            raise RuntimeError("physical egress is not a verified physical interface")
        routes = json.loads(self.runner(["ip", "-4", "-j", "route", "show", "table", "main",
                                         "default"], check=True).stdout or "[]")
        defaults = [row for row in routes if row.get("dst") == "default"]
        if not defaults or any(row.get("dev") != policy["egress_interface"] or
                               not row.get("gateway") for row in defaults):
            raise RuntimeError("main table has no unambiguous physical default route")
        present = self._physical_rule_present(policy)
        if present and not owned:
            raise RuntimeError("physical egress rule exists without relay ownership")
        if install and not present:
            self.runner(self._physical_rule(policy, "add"), check=True)

    def _remove_physical_route(self, policy: dict, *, owned: bool) -> None:
        if policy["egress_policy"] != "source_physical" or not owned:
            return
        if not self._physical_rule_present(policy):
            return
        result = self.runner(self._physical_rule(policy, "del"), check=False)
        if getattr(result, "returncode", 1) != 0 and self._physical_rule_present(policy):
            raise RuntimeError("owned physical egress rule remains installed")

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
        peer_ids: set[str] = set()
        addresses: dict[tuple[str, str], str] = {}
        keys: dict[tuple[str, str], str] = {}
        for policy in policies:
            if policy["peer_id"] in peer_ids:
                raise ValueError("duplicate desired peer_id")
            peer_ids.add(policy["peer_id"])
            deadline = _policy_deadline(policy)
            if deadline is not None and deadline <= now:
                raise ValueError("expired policy cannot be applied")
            address_key = (policy["interface"], policy["address"])
            previous = addresses.get(address_key)
            if previous and previous != policy["peer_id"]:
                raise ValueError("duplicate desired customer address on interface")
            addresses[address_key] = policy["peer_id"]
            key_key = (policy["interface"], policy["public_key"])
            previous = keys.get(key_key)
            if previous and previous != policy["peer_id"]:
                raise ValueError("duplicate desired WireGuard public key on interface")
            keys[key_key] = policy["peer_id"]

    @_locked_method
    def apply(self, raw: dict) -> dict:
        require_linux_root()
        policy = validate_policy(raw)
        now = int(self.clock())
        if _policy_deadline(policy) is not None and _policy_deadline(policy) <= now:
            raise ValueError("expired policy cannot be applied")
        state = self.store.load()
        self._update_clock(state, now)
        previous = state["peers"].get(policy["peer_id"])
        if previous and previous.get("status") in {"active", "recovery_required"}:
            old_policy = previous["policy"]
            if any(old_policy[key] != policy[key] for key in (
                    "interface", "address", "public_key", "egress_policy", "egress_interface")):
                raise ValueError("active peer replacement requires explicit revoke")
            # A fresh server snapshot may contain more already-acknowledged
            # bytes, but the same relay lease keeps its original local
            # counter baseline.  Otherwise those bytes would be counted twice.
            policy["usage_baseline_bytes"] = old_policy.get("usage_baseline_bytes", 0)
        for other_id, reserved in state['peers'].items():
            if other_id == policy['peer_id'] or reserved.get('status') not in {'active', 'recovery_required'}:
                continue
            while reserved:
                owner = reserved['policy']
                if owner['interface'] == policy['interface'] and _minor(owner['peer_id']) == _minor(policy['peer_id']):
                    raise ValueError('peer_id traffic-control class collision with reserved owner')
                if (owner.get('interface') == policy['interface'] and
                        owner.get('address') == policy['address']):
                    raise ValueError('customer address is already reserved on this interface')
                if (owner.get('interface') == policy['interface'] and
                        owner.get('public_key') == policy['public_key']):
                    raise ValueError('WireGuard public key is already reserved on this interface')
                reserved = reserved.get('previous')
        active = [item["policy"] for item in state["peers"].values()
                  if item.get("status") in {"active", "recovery_required"}
                  and item["policy"]["peer_id"] != policy["peer_id"]]
        if len(active) + 1 > self.max_active_peers:
            raise ValueError("relay active peer capacity exceeded")
        for item in active:
            shared = ("interface", "customer_subnet", "management_subnets")
            if any(item[key] != policy[key] for key in shared):
                raise ValueError("active relay peers must share interface and isolation networks")
            if _minor(item["peer_id"]) == _minor(policy["peer_id"]):
                raise ValueError("peer_id traffic-control class collision")
        physical_owned = bool(previous and previous.get("physical_rule_owned"))
        self._ensure_physical_route(policy, owned=physical_owned, install=False)
        # Reconciliation may update a peer before the independent usage POST
        # receives its ACK.  Preserve its persisted report and counter watermarks
        # even in the interim recovery record, so a crash cannot erase billing.
        accounting_fields = ("used_bytes", "accounted_received", "accounted_sent",
                             "reported_received", "reported_sent", "pending_usage",
                             "last_received", "last_sent")
        carried_accounting = ({key: previous[key] for key in accounting_fields
                               if key in previous} if previous else {})
        state["peers"][policy["peer_id"]] = {
            "policy": policy, "status": "recovery_required",
            "physical_rule_owned": policy["egress_policy"] == "source_physical",
            "updated_at": now,
            **carried_accounting,
        }
        self.store.save(state)
        completed = []
        try:
            if previous and (previous.get('status') == 'recovery_required' or any(
                    previous['policy'][key] != policy[key] for key in ('download_bps', 'upload_bps'))):
                # Remove only this peer's previous filters before changing its
                # limits; null rates must not leave an old policer behind.
                for command in self._historical_tc(previous):
                    self._remove_tc(command)
            addresses = [item["policy"] for key, item in state["peers"].items()
                         if key != policy["peer_id"] and item.get("status") == "active"] + [policy]
            self._base_firewall(policy, addresses, bool(active or previous), now=now)
            self._ensure_physical_route(policy, owned=True)
            initialized = any(item.get("status") in {"active", "recovery_required"} and
                              item["policy"]["interface"] == policy["interface"] and
                              item["policy"]["peer_id"] != policy["peer_id"]
                              for item in state["peers"].values())
            existing_qdiscs = []
            if not initialized:
                probe = self.runner(['tc', '-j', 'qdisc', 'show', 'dev', policy['interface']], check=True)
                existing_qdiscs = json.loads(probe.stdout or '[]')
            for command in plan_apply(policy, initialize=not initialized):
                if command[:3] == ['tc', 'qdisc', 'replace']:
                    kind, handle = ('htb', '1:') if 'root' in command else ('ingress', 'ffff:')
                    if any(row.get('kind') == kind and row.get('handle') == handle for row in existing_qdiscs):
                        continue
                self.runner(command, check=True)
                completed.append(command)
        except Exception as exc:
            route_error = None
            try:
                self._remove_physical_route(policy, owned=True)
            except Exception as cleanup_error:
                route_error = cleanup_error
            if previous:
                for command in self._historical_tc(previous):
                    self.runner(command, check=False)
            for command in plan_revoke(policy):
                self.runner(command, check=False)
            state["peers"][policy["peer_id"]] = {"policy": policy, "status": "recovery_required",
                                                  "physical_rule_owned": route_error is not None,
                                                  "error": str(exc), "updated_at": int(self.clock()),
                                                  **carried_accounting}
            if previous:
                state["peers"][policy["peer_id"]]["previous"] = previous
            self.store.save(state)
            self.store.event("apply", policy["peer_id"], False, str(exc))
            raise RuntimeError("relay apply failed; peer was revoked and recovery state recorded") from exc
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
                                              "physical_rule_owned": policy["egress_policy"] == "source_physical",
                                              "used_bytes": (previous.get("used_bytes", 0) if previous else
                                                              policy.get("usage_baseline_bytes", 0)),
                                              "accounted_received": previous.get("accounted_received", 0) if previous else 0,
                                              "accounted_sent": previous.get("accounted_sent", 0) if previous else 0,
                                              "last_received": (previous.get("last_received") if previous else
                                                                 baseline.get("last_received") if baseline else None),
                                              "last_sent": (previous.get("last_sent") if previous else
                                                             baseline.get("last_sent") if baseline else None),
                                              "updated_at": int(self.clock())}
        if previous and "reported_received" in previous and "reported_sent" in previous:
            state["peers"][policy["peer_id"]].update(
                reported_received=previous["reported_received"],
                reported_sent=previous["reported_sent"])
        if previous and "pending_usage" in previous:
            state["peers"][policy["peer_id"]]["pending_usage"] = previous["pending_usage"]
        self.store.save(state)
        self.store.event("apply", policy["peer_id"], True)
        return state["peers"][policy["peer_id"]]

    @_locked_method
    def revoke(self, peer_id: str, reason="revoked", *, expected_policy: dict | None = None) -> dict:
        require_linux_root()
        if not isinstance(peer_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", peer_id):
            raise ValueError("invalid peer_id")
        state = self.store.load()
        record = state["peers"].get(peer_id)
        if not record:
            return {"peer_id": peer_id, "status": "absent"}
        if expected_policy is not None and record.get("policy") != expected_policy:
            return {"peer_id": peer_id, "status": "stale_cleanup_ignored"}
        failures = []
        shared_key = any(key != peer_id and item.get('status') in {'active', 'recovery_required'} and
                         item['policy']['interface'] == record['policy']['interface'] and
                         item['policy']['public_key'] == record['policy']['public_key']
                         for key, item in state['peers'].items())
        shared_address = any(key != peer_id and item.get('status') in {'active', 'recovery_required'} and
                             item['policy']['interface'] == record['policy']['interface'] and
                             item['policy']['address'] == record['policy']['address']
                             for key, item in state['peers'].items())
        if not shared_address:
            try:
                self._remove_physical_route(record['policy'],
                                            owned=bool(record.get('physical_rule_owned')))
            except Exception:
                failures.append("owned physical egress rule")
        commands = plan_revoke(record['policy'])
        for command in self._historical_tc(record):
            if command not in commands:
                commands.append(command)
        for command in commands:
            if (command[0] == 'wg' and shared_key) or (command[0] in {'ip', 'nft'} and shared_address):
                continue
            if command[0] == 'tc':
                try:
                    self._remove_tc(command)
                except Exception:
                    failures.append(' '.join(command[:4]))
                continue
            if (command[:3] == ["ip", "route", "del"] and len(command) >= 6 and
                    command[4] == "dev"):
                # Route cleanup is idempotent. A previous rollback/reconcile
                # may already have removed this exact owned route.
                probe = self.runner(["ip", "route", "show", command[3], "dev", command[5]],
                                    check=False)
                if (getattr(probe, "returncode", 0) == 0 and
                        not str(getattr(probe, "stdout", "") or "").strip()):
                    continue
            if command[0] == "nft":
                try:
                    self._remove_customer_nft_element(command)
                except Exception:
                    failures.append("nft delete element inet sna_relay customers")
                continue
            result = self.runner(command, check=False)
            if getattr(result, "returncode", 0) != 0:
                failures.append(" ".join(command[:4]))
        if failures:
            # Keep a subnet-wide deny in place even if the WireGuard peer
            # deletion failed.  Removing an element from @customers must not
            # turn the table's default-accept policy into a bypass.
            addresses = [item["policy"] for key, item in state["peers"].items()
                         if key != peer_id and item.get("status") == "active"]
            try:
                self._base_firewall(record["policy"], addresses, True)
            except Exception:
                failures.append("nft fail-closed base")
        record.update({"status": "recovery_required" if failures else "revoked", "reason": reason,
                       "updated_at": int(self.clock())})
        if failures:
            record["error"] = "failed cleanup: " + ", ".join(failures)
        else:
            record.pop("error", None)
        self.store.save(state)
        self.store.event("revoke", peer_id, not failures, record.get("error", reason))
        return record

    @_locked_method
    def measure(self, interface: str) -> dict:
        require_linux_root()
        if not isinstance(interface, str) or not NAME_RE.fullmatch(interface):
            raise ValueError("invalid WireGuard interface")
        output = self.runner(["wg", "show", interface, "transfer"], check=True).stdout
        counters = parse_wg_transfer(output)
        state = self.store.load()
        now = int(self.clock())
        self._update_clock(state, now)
        for record in state["peers"].values():
            if record.get('status') != 'active':
                continue
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
            record["used_bytes"] = (record["policy"].get("usage_baseline_bytes", 0) +
                                     record["accounted_received"] + record["accounted_sent"])
            record.update({"last_received": current["received_bytes"], "last_sent": current["sent_bytes"],
                           "delta_received": delta_rx, "delta_sent": delta_tx,
                           "delta_bytes": delta_rx + delta_tx, "measured_at": now})
            totals = (record["accounted_received"], record["accounted_sent"])
            reported = (record.get("reported_received"), record.get("reported_sent"))
            if totals != reported and (any(totals) or record.get("pending_usage")):
                # Legacy state has no ACK watermark; retry its cumulative
                # totals once. A response timeout keeps this exact report ID.
                record["pending_usage"] = {
                    "report_id": f"{policy['peer_id']}:{totals[0]}:{totals[1]}",
                    "rx_total": totals[0], "tx_total": totals[1],
                }
            else:
                record.pop("pending_usage", None)
        self.store.save(state)
        return self.status()

    @_locked_method
    def prepare_usage_reports(self) -> None:
        """Persist deterministic retries for pre-ACK-watermark relay state."""
        state = self.store.load()
        changed = False
        for peer_id, record in state["peers"].items():
            if record.get("pending_usage"):
                continue
            totals = (record.get("accounted_received", 0), record.get("accounted_sent", 0))
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                   for value in totals):
                continue
            reported = (record.get("reported_received"), record.get("reported_sent"))
            if totals == reported or not any(totals):
                continue
            record["pending_usage"] = {
                "report_id": f"{peer_id}:{totals[0]}:{totals[1]}",
                "rx_total": totals[0], "tx_total": totals[1],
            }
            changed = True
        if changed:
            self.store.save(state)

    @_locked_method
    def mark_usage_reported_many(self, reports: list[tuple[str, str]]) -> None:
        state = self.store.load()
        changed = False
        for peer_id, report_id in reports:
            record = state["peers"].get(peer_id)
            pending = record.get("pending_usage") if record else None
            if not pending or pending.get("report_id") != report_id:
                continue
            record["reported_received"] = pending["rx_total"]
            record["reported_sent"] = pending["tx_total"]
            record.pop("pending_usage", None)
            changed = True
        if changed:
            self.store.save(state)

    def mark_usage_reported(self, peer_id: str, report_id: str) -> None:
        self.mark_usage_reported_many([(peer_id, report_id)])

    @_locked_method
    def reconcile(self, desired: list[dict] | None = None) -> dict:
        require_linux_root()
        now = int(self.clock())
        state = self.store.load()
        self._update_clock(state, now)
        if desired is not None:
            if not isinstance(desired, list) or len(desired) > 4096:
                raise ValueError("desired policies must be a list")
            normalized = [validate_policy(row) for row in desired]
            self._validate_policy_batch(normalized, now)
            policies = {item["peer_id"]: item for item in normalized}
            state = self.store.load()
            for peer_id, record in list(state["peers"].items()):
                if record.get("status") in {"active", "recovery_required"} and peer_id not in policies:
                    self.revoke(peer_id, "not_desired")
            state = self.store.load()
            for peer_id, item in policies.items():
                current = state["peers"].get(peer_id)
                if not current or current.get("status") != "active" or current.get("policy") != item:
                    self.apply(item)
                else:
                    # Restore owned peer/return route if an interrupted cleanup removed them.
                    self.runner(['ip', 'route', 'replace', item['address'], 'dev', item['interface']], check=True)
                    self.runner(['wg', 'set', item['interface'], 'peer', item['public_key'], 'allowed-ips', item['address']], check=True)
                    self._ensure_physical_route(item,
                                                owned=bool(current.get('physical_rule_owned')))
                    if item['egress_policy'] == 'source_physical' and not current.get('physical_rule_owned'):
                        current['physical_rule_owned'] = True
                        state['peers'][peer_id] = current
                        self.store.save(state)
        state = self.store.load()
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
        require_linux_root()
        now = int(self.clock()) if now is None else now
        state = self.store.load()
        self._update_clock(state, now)
        if now == int(self.clock()):
            interfaces = {record["policy"]["interface"] for record in state["peers"].values()
                          if record.get("status") == "active"}
            for interface in sorted(interfaces):
                try:
                    # Capture the final kernel counters before removing an
                    # expired peer.  Failure is recorded, but never delays
                    # the local block/revoke decision.
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
        """Validate freshness and local deadlines before any network write."""
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
        raw_max_offline = value.get("max_offline_seconds", DEFAULT_MAX_OFFLINE_SECONDS)
        if (isinstance(raw_max_offline, bool) or not isinstance(raw_max_offline, int) or
                not 1 <= raw_max_offline <= 86400):
            raise ValueError("control response max_offline_seconds is invalid")
        for raw in value["policies"]:
            policy = validate_policy(raw)
            if policy.get("snapshot_at") != generated_at:
                raise ValueError("relay policy snapshot timestamp is inconsistent")
            deadline = policy.get("offline_deadline")
            if (not isinstance(deadline, int) or deadline <= generated_at or
                    deadline > generated_at + raw_max_offline or
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
        return {"version": state["version"], "peers": state["peers"],
                "at": int(self.clock()), "max_active_peers": self.max_active_peers,
                "last_snapshot_at": state.get("last_snapshot_at")}

    def verify_relay_peers(self, policies: list[dict]) -> None:
        """Confirm the active local record and kernel WireGuard AllowedIPs."""
        state = self.status()['peers']
        by_interface: dict[str, list[dict]] = {}
        for raw in policies:
            policy = validate_policy(raw)
            record = state.get(policy['peer_id'])
            if (not record or record.get('status') != 'active' or
                    relay_policy_fingerprint(record.get('policy')) !=
                    relay_policy_fingerprint(policy)):
                raise RuntimeError('relay peer policy is not active')
            by_interface.setdefault(policy['interface'], []).append(policy)
        for interface, items in by_interface.items():
            output = self.runner(['wg', 'show', interface, 'allowed-ips'], check=True).stdout
            actual = {}
            for line in output.splitlines():
                parts = line.split()
                if len(parts) == 2:
                    actual[parts[0]] = set(parts[1].split(','))
            for policy in items:
                if actual.get(policy['public_key']) != {policy['address']}:
                    raise RuntimeError('relay peer is missing from kernel WireGuard')


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
    sub.add_parser("expire")
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
    elif args.action == "expire":
        result = manager.expire()
    else:
        result = manager.status()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

