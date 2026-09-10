#!/usr/bin/env python3
"""Root-side helper for reversible WireGuard network assistance on Linux."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time


BASE = Path("/var/lib/server-network-assist/helper")
WG_DIR = Path("/etc/wireguard")
UNIT_DIR = Path("/etc/systemd/system")
SELF = "/usr/local/sbin/server-network-assist-helper"
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def run(args, *, check=True, timeout=30, stdin=None):
    result = subprocess.run(args, input=stdin, text=True, capture_output=True,
                            timeout=timeout, env=ENV)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f"exit {result.returncode}").strip())
    return result


def atomic(path: Path, text: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def decode(value: str) -> dict:
    try:
        payload = json.loads(base64.urlsafe_b64decode(value + "===").decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return payload


def valid_id(value):
    value = str(value)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("invalid profile id")
    return value


def valid_iface(value):
    value = str(value)
    if not re.fullmatch(r"na[a-f0-9]{10}", value):
        raise ValueError("invalid interface")
    return value


def state_path(profile_id):
    return BASE / valid_id(profile_id) / "state.json"


def load_state(profile_id):
    path = state_path(profile_id)
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(profile_id, value):
    atomic(state_path(profile_id), json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def require_tools():
    missing = [name for name in ("wg", "wg-quick", "ip", "iptables", "systemctl") if not shutil.which(name)]
    if missing:
        raise RuntimeError("missing tools: " + ", ".join(missing))


def bootstrap():
    require_tools()
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = """[Unit]\nDescription=Server Network Assist check for %i\nAfter=network-online.target\n\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/server-network-assist-helper watch %i\n"""
    timer = """[Unit]\nDescription=Maintain server panel network assistance for %i\n\n[Timer]\nOnBootSec=75s\nOnUnitActiveSec=60s\nAccuracySec=10s\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"""
    atomic(UNIT_DIR / "server-network-assist-watch@.service", service, 0o644)
    atomic(UNIT_DIR / "server-network-assist-watch@.timer", timer, 0o644)
    sudo_user = os.environ.get("SUDO_USER", "")
    if sudo_user and sudo_user != "root" and re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", sudo_user):
        sudoers = Path("/etc/sudoers.d/server-network-assist-" + sudo_user)
        atomic(sudoers, f"{sudo_user} ALL=(root) NOPASSWD: {SELF} *\n", 0o440)
        run(["visudo", "-cf", str(sudoers)])
    run(["systemctl", "daemon-reload"])
    print(json.dumps({"ok": True, "helper_version": 1}))


def prepare(profile_id):
    require_tools()
    profile_id = valid_id(profile_id)
    directory = BASE / profile_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = directory / "private.key"
    if not private.exists():
        key = run(["wg", "genkey"]).stdout.strip()
        atomic(private, key + "\n")
    public = run(["wg", "pubkey"], stdin=private.read_text()).stdout.strip()
    print(json.dumps({"ok": True, "public_key": public}))


def validate_config(p):
    if p.get("version") != 1 or p.get("role") not in ("gateway", "client"):
        raise ValueError("unsupported configuration")
    p["profile_id"] = valid_id(p.get("profile_id"))
    p["interface"] = valid_iface(p.get("interface"))
    interface = ipaddress.ip_interface(p.get("address"))
    if interface.version != 4 or not interface.ip.is_private:
        raise ValueError("invalid tunnel address")
    port = int(p.get("port"))
    if not 1024 <= port <= 65535:
        raise ValueError("invalid port")
    p["port"] = port
    return p


def private_key(profile_id):
    path = BASE / valid_id(profile_id) / "private.key"
    if not path.exists():
        raise ValueError("profile key is not prepared")
    return path.read_text().strip()


def default_uplink():
    result = run(["ip", "-j", "-4", "route", "show", "default"])
    routes = json.loads(result.stdout or "[]")
    route = next((v for v in routes if v.get("dev")), None)
    if not route:
        raise RuntimeError("no IPv4 default route")
    return route["dev"]


def wg_config(p):
    lines = ["[Interface]", f"Address = {p['address']}",
             f"PrivateKey = {private_key(p['profile_id'])}"]
    if p["role"] == "gateway":
        subnet = ipaddress.ip_network(p["subnet"])
        uplink = default_uplink()
        tag = "spna-" + p["interface"]
        lines += [f"ListenPort = {p['port']}",
            f"PostUp = iptables -w 3 -A INPUT -p udp --dport {p['port']} -m comment --comment {tag} -j ACCEPT",
            f"PostUp = iptables -w 3 -A FORWARD -i %i -m comment --comment {tag} -j ACCEPT",
            f"PostUp = iptables -w 3 -A FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment {tag} -j ACCEPT",
            f"PostUp = iptables -w 3 -t nat -A POSTROUTING -s {subnet} -o {uplink} -m comment --comment {tag} -j MASQUERADE",
            f"PostDown = iptables -w 3 -D INPUT -p udp --dport {p['port']} -m comment --comment {tag} -j ACCEPT || true",
            f"PostDown = iptables -w 3 -D FORWARD -i %i -m comment --comment {tag} -j ACCEPT || true",
            f"PostDown = iptables -w 3 -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment {tag} -j ACCEPT || true",
            f"PostDown = iptables -w 3 -t nat -D POSTROUTING -s {subnet} -o {uplink} -m comment --comment {tag} -j MASQUERADE || true"]
        for peer in p.get("peers", []):
            allowed = ipaddress.ip_network(peer["allowed_ip"], strict=True)
            if allowed.version != 4 or allowed.prefixlen != 32 or allowed not in subnet:
                raise ValueError("invalid gateway peer address")
            if not re.fullmatch(r"[A-Za-z0-9+/]{42,44}={0,2}", str(peer.get("public_key", ""))):
                raise ValueError("invalid peer key")
            lines += ["", "[Peer]", f"PublicKey = {peer['public_key']}", f"AllowedIPs = {allowed}"]
        p["uplink"] = uplink
    else:
        endpoint = str(p.get("endpoint", ""))
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}", endpoint):
            raise ValueError("invalid endpoint")
        if not re.fullmatch(r"[A-Za-z0-9+/]{42,44}={0,2}", str(p.get("gateway_public_key", ""))):
            raise ValueError("invalid gateway key")
        ipaddress.ip_address(p["gateway_ip"])
        lines += ["", "[Peer]", f"PublicKey = {p['gateway_public_key']}",
                  f"Endpoint = {endpoint}:{p['port']}",
                  "AllowedIPs = 0.0.0.0/1, 128.0.0.0/1", "PersistentKeepalive = 25"]
    return "\n".join(lines) + "\n"


def configure(payload):
    require_tools()
    p = validate_config(payload)
    prepare(p["profile_id"])
    config = wg_config(p)
    atomic(WG_DIR / (p["interface"] + ".conf"), config)
    old = load_state(p["profile_id"])
    p.update({"desired": old.get("desired", False), "active": old.get("active", False),
              "failures": 0, "suspended": False, "added_routes": old.get("added_routes", []),
              "updated_at": int(time.time())})
    save_state(p["profile_id"], p)
    print(json.dumps({"ok": True, "interface": p["interface"]}))


def resolve_targets(values):
    result = []
    for value in values:
        value = str(value)
        try:
            network = ipaddress.ip_network(value, strict=False)
            result.append(str(network))
            continue
        except ValueError:
            pass
        try:
            for item in socket.getaddrinfo(value, None, socket.AF_INET):
                result.append(item[4][0] + "/32")
        except socket.gaierror:
            raise RuntimeError("cannot resolve preserved endpoint: " + value)
    return list(dict.fromkeys(result))


def preserve_routes(state):
    added = []
    for target in resolve_targets(state.get("preserve_routes", [])):
        existing = run(["ip", "-j", "-4", "route", "show", target], check=False)
        if existing.returncode == 0 and json.loads(existing.stdout or "[]"):
            continue
        route = json.loads(run(["ip", "-j", "-4", "route", "get", target.split("/", 1)[0]]).stdout)[0]
        command = ["ip", "route", "replace", target]
        if route.get("gateway"):
            command += ["via", route["gateway"]]
        command += ["dev", route["dev"]]
        if route.get("prefsrc"):
            command += ["src", route["prefsrc"]]
        run(command)
        added.append(target)
    return added


def timer(iface, enabled):
    unit = f"server-network-assist-watch@{valid_iface(iface)}.timer"
    run(["systemctl", "enable", "--now", unit] if enabled else
        ["systemctl", "disable", "--now", unit], check=False)


def set_forwarding(enable):
    marker = BASE / "original-ip-forward"
    if enable:
        if not marker.exists():
            atomic(marker, Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() + "\n")
        run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
        return
    active_gateways = [p for p in BASE.glob("*/state.json") if json.loads(p.read_text()).get("role") == "gateway" and json.loads(p.read_text()).get("active")]
    if not active_gateways and marker.exists() and marker.read_text().strip() == "0":
        run(["sysctl", "-w", "net.ipv4.ip_forward=0"], check=False)


def enable(profile_id, failsafe=0):
    state = load_state(profile_id)
    if not state:
        raise ValueError("profile is not configured")
    iface = valid_iface(state["interface"])
    try:
        if state["role"] == "client":
            state["added_routes"] = preserve_routes(state)
            save_state(profile_id, state)
        else:
            set_forwarding(True)
        run(["wg-quick", "down", iface], check=False)
        run(["wg-quick", "up", iface], timeout=45)
        state.update(desired=True, active=True, suspended=False, failures=0, updated_at=int(time.time()))
        if int(failsafe):
            token = os.urandom(16).hex()
            state["failsafe_token"] = token
            unit = "server-network-assist-failsafe-" + iface
            run(["systemd-run", "--unit", unit, f"--on-active={min(max(int(failsafe), 30), 600)}s",
                 SELF, "failsafe", profile_id, token])
        save_state(profile_id, state)
    except Exception:
        run(["wg-quick", "down", iface], check=False)
        for target in state.get("added_routes", []):
            run(["ip", "route", "del", target], check=False)
        state.update(desired=False, active=False, added_routes=[], updated_at=int(time.time()))
        save_state(profile_id, state)
        if state.get("role") == "gateway":
            set_forwarding(False)
        raise
    timer(iface, bool(state.get("maintenance")))
    print(json.dumps({"ok": True, "interface": iface, "failsafe": bool(failsafe)}))


def confirm(profile_id):
    state = load_state(profile_id)
    state.pop("failsafe_token", None)
    save_state(profile_id, state)
    run(["systemctl", "stop", "server-network-assist-failsafe-" + state["interface"] + ".timer"], check=False)
    print(json.dumps({"ok": True}))


def disable(profile_id, *, suspended=False):
    state = load_state(profile_id)
    if not state:
        print(json.dumps({"ok": True, "already_disabled": True}))
        return
    iface = valid_iface(state["interface"])
    timer(iface, False)
    run(["wg-quick", "down", iface], check=False, timeout=45)
    for target in state.get("added_routes", []):
        run(["ip", "route", "del", target], check=False)
    state.update(desired=False, active=False, suspended=bool(suspended), added_routes=[],
                 failures=0, updated_at=int(time.time()))
    state.pop("failsafe_token", None)
    save_state(profile_id, state)
    if state.get("role") == "gateway":
        set_forwarding(False)
    print(json.dumps({"ok": True, "interface": iface, "restored_original_routes": True}))


def failsafe(profile_id, token):
    state = load_state(profile_id)
    if state.get("failsafe_token") == token:
        disable(profile_id, suspended=True)


def tunnel_ok(state):
    if not Path("/sys/class/net", state["interface"]).exists():
        return False
    target = state.get("gateway_ip")
    if state.get("role") == "gateway":
        return True
    result = run(["ping", "-I", state["interface"], "-c", "1", "-W", "3", target], check=False, timeout=6)
    if result.returncode:
        return False
    for address in ("1.1.1.1", "223.5.5.5"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(4)
                connection.setsockopt(socket.SOL_SOCKET, 25, state["interface"].encode() + b"\0")
                connection.connect((address, 443))
                return True
        except OSError:
            pass
    return False


def watch(iface):
    iface = valid_iface(iface)
    match = None
    for path in BASE.glob("*/state.json"):
        value = json.loads(path.read_text())
        if value.get("interface") == iface:
            match = value
            break
    if not match or not match.get("desired"):
        return
    if not Path("/sys/class/net", iface).exists():
        try:
            if match["role"] == "client":
                match["added_routes"] = preserve_routes(match)
            else:
                set_forwarding(True)
            run(["wg-quick", "up", iface], timeout=45)
            for _ in range(5):
                if tunnel_ok(match):
                    match.update(failures=0, active=True, updated_at=int(time.time()))
                    save_state(match["profile_id"], match)
                    return
                time.sleep(3)
        except Exception:
            pass
        disable(match["profile_id"], suspended=True)
        return
    if tunnel_ok(match):
        match["failures"] = 0
        match["active"] = True
    else:
        match["failures"] = int(match.get("failures", 0)) + 1
        if match["failures"] >= 6:
            disable(match["profile_id"], suspended=True)
            return
        if match["failures"] >= 3:
            run(["wg-quick", "down", iface], check=False)
            run(["wg-quick", "up", iface], check=False)
    match["updated_at"] = int(time.time())
    save_state(match["profile_id"], match)


def status():
    values = []
    for path in BASE.glob("*/state.json"):
        state = json.loads(path.read_text())
        values.append({k: state.get(k) for k in
                       ("profile_id", "interface", "role", "desired", "active", "suspended", "failures", "updated_at")})
    encoded = base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode()).decode()
    print("assist_json=" + encoded)


def main():
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "bootstrap": bootstrap()
    elif action == "prepare" and len(sys.argv) == 3: prepare(sys.argv[2])
    elif action == "configure" and len(sys.argv) == 3: configure(decode(sys.argv[2]))
    elif action == "enable" and len(sys.argv) in (3, 4): enable(sys.argv[2], int(sys.argv[3]) if len(sys.argv) == 4 else 0)
    elif action == "confirm" and len(sys.argv) == 3: confirm(sys.argv[2])
    elif action == "disable" and len(sys.argv) == 3: disable(sys.argv[2])
    elif action == "failsafe" and len(sys.argv) == 4: failsafe(sys.argv[2], sys.argv[3])
    elif action == "watch" and len(sys.argv) == 3: watch(sys.argv[2])
    elif action == "status": status()
    else: raise SystemExit("invalid arguments")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}))
        raise SystemExit(1)
