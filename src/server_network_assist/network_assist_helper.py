#!/usr/bin/env python3
"""Root-side helper for reversible WireGuard network assistance on Linux."""
from __future__ import annotations

import base64
import asyncio
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
import urllib.request


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
    print(json.dumps({"ok": True, "helper_version": 2, "roles": ["gateway", "client"]}))


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
    if p.get('proxy_mode', 'direct') not in ('direct', 'share'):
        raise ValueError('invalid proxy mode')
    if p.get('proxy_mode') == 'share':
        proxy = ipaddress.ip_address(p['proxy_host'])
        if proxy.version != 4 or proxy.is_unspecified or proxy.is_multicast:
            raise ValueError('invalid upstream proxy address')
        if not 1 <= int(p['proxy_port']) <= 65535 or int(p['relay_port']) != 17897:
            raise ValueError('invalid proxy port')
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
            if allowed.version != 4 or allowed.prefixlen != 32 or not allowed.subnet_of(subnet):
                raise ValueError("invalid gateway peer address")
            if not re.fullmatch(r"[A-Za-z0-9+/]{42,44}={0,2}", str(peer.get("public_key", ""))):
                raise ValueError("invalid peer key")
            lines += ["", "[Peer]", f"PublicKey = {peer['public_key']}", f"AllowedIPs = {allowed}"]
        p["uplink"] = uplink
        if p.get('proxy_mode') == 'share':
            # Accept only authenticated tunnel traffic at the relay, never LAN/public input.
            lines[3:3] = [f"PostUp = iptables -w 3 -A INPUT -i %i -s {subnet} -p tcp --dport 17897 -m comment --comment {tag} -j ACCEPT",
                      f"PostDown = iptables -w 3 -D INPUT -i %i -s {subnet} -p tcp --dport 17897 -m comment --comment {tag} -j ACCEPT || true"]
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
    old = load_state(p['profile_id'])
    if any(old.get(k) for k in ('desired', 'active', 'added_routes', 'proxy_files', 'desktop_proxy', 'relay_owned')):
        raise ValueError('disable profile and finish cleanup before reconfiguration')
    prepare(p["profile_id"])
    config = wg_config(p)
    atomic(WG_DIR / (p["interface"] + ".conf"), config)
    old = load_state(p["profile_id"])
    p['proxy_user'] = os.environ.get('SUDO_USER', 'root')
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
    added = list(state.get('added_routes', []))
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
        added.append(target)
        state['added_routes'] = added
        save_state(state['profile_id'], state)
        run(command)
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
    if not active_gateways and marker.exists():
        run(["sysctl", "-w", "net.ipv4.ip_forward=" + marker.read_text().strip()])
        marker.unlink()


def proxy_ok(state):
    host = state['proxy_host'] if state['role'] == 'gateway' else state['gateway_ip']
    port = state['proxy_port'] if state['role'] == 'gateway' else state['relay_port']
    # Set the proxy explicitly: NO_PROXY from the SSH session must not bypass it.
    for url in ('https://connectivitycheck.gstatic.com/generate_204', 'https://www.baidu.com'):
        try:
            request = urllib.request.Request(url)
            request.set_proxy(f'{host}:{port}', 'http')
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=6) as response:
                if response.status in (200, 204):
                    return True
        except Exception:
            pass
    return False


async def relay_connection(reader, writer, state):
    allowed = {p['allowed_ip'].split('/')[0] for p in state['peers']}
    peer = writer.get_extra_info('peername')
    upstream = None
    try:
        if not peer or peer[0] not in allowed:
            return
        source, upstream = await asyncio.wait_for(asyncio.open_connection(state['proxy_host'], int(state['proxy_port'])), 10)
        async def pump(incoming, outgoing):
            while data := await asyncio.wait_for(incoming.read(65536), 120):
                outgoing.write(data)
                await outgoing.drain()
        tasks = [asyncio.create_task(pump(reader, upstream)), asyncio.create_task(pump(source, writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def relay(profile_id):
    state = load_state(profile_id)
    if state.get('role') != 'gateway' or state.get('proxy_mode') != 'share':
        raise ValueError('proxy relay is not configured')
    tasks = set()
    def accept(reader, writer):
        if len(tasks) >= 128:
            writer.close()
            return
        task = asyncio.create_task(relay_connection(reader, writer, state))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    server = await asyncio.start_server(accept, state['address'].split('/')[0], int(state['relay_port']))
    async with server:
        await server.serve_forever()


def relay_service(state, enable):
    unit = 'server-network-assist-proxy-' + valid_iface(state['interface']) + '.service'
    if enable:
        if (UNIT_DIR / unit).exists() and not state.get('relay_owned'):
            raise ValueError('proxy relay service already exists; finish cleanup first')
        state['relay_owned'] = True
        save_state(state['profile_id'], state)
        atomic(UNIT_DIR / unit, '[Unit]\nDescription=SNA tunnel-only proxy relay\nAfter=network-online.target\n\n[Service]\n'
               f'ExecStart={SELF} relay {valid_id(state["profile_id"])}\nRestart=on-failure\nRestartSec=5\n'
               'NoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\n', 0o644)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'start', unit])
    elif state.get('relay_owned'):
        run(['systemctl', 'stop', unit])
        (UNIT_DIR / unit).unlink(missing_ok=True)
        run(['systemctl', 'daemon-reload'])
        state['relay_owned'] = False
        save_state(state['profile_id'], state)


def desktop_settings(state):
    # Only touch the SSH user's active GNOME session, never another logged-in user.
    import pwd
    user = state.get('proxy_user', 'root')
    uid = pwd.getpwnam(user).pw_uid
    if uid == 0 or not Path(f'/run/user/{uid}/bus').exists() or not shutil.which('gsettings'):
        return None
    return ['runuser', '-u', user, '--', 'env', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus', 'gsettings']


def client_proxy(state, enable):
    if enable:
        if state.get('proxy_mode') != 'share':
            return
        proxy = f'http://{state["gateway_ip"]}:{state["relay_port"]}'
        files = {
            '/etc/profile.d/server-network-assist-proxy.sh': f'# Managed by Server Network Assist\nexport http_proxy={proxy}\nexport https_proxy={proxy}\nexport HTTP_PROXY={proxy}\nexport HTTPS_PROXY={proxy}\n',
            '/etc/apt/apt.conf.d/90server-network-assist-proxy': f'Acquire::http::Proxy "{proxy}";\nAcquire::https::Proxy "{proxy}";\n',
        }
        for name, text in files.items():
            path = Path(name)
            if path.exists():
                raise ValueError(f'proxy configuration already exists: {name}; finish cleanup first')
            state.setdefault('proxy_files', {})[name] = text
            save_state(state['profile_id'], state)
            atomic(path, text, 0o644)
        command = desktop_settings(state)
        if command:
            values = [('org.gnome.system.proxy', 'mode', "'manual'"),
                      ('org.gnome.system.proxy.http', 'use-authentication', 'false'),
                      ('org.gnome.system.proxy.http', 'host', repr(state['gateway_ip'])),
                      ('org.gnome.system.proxy.http', 'port', str(state['relay_port'])),
                      ('org.gnome.system.proxy.https', 'host', repr(state['gateway_ip'])),
                      ('org.gnome.system.proxy.https', 'port', str(state['relay_port']))]
            state['desktop_proxy'] = [[schema, key, run(command + ['get', schema, key]).stdout.strip()] for schema, key, value in values]
            save_state(state['profile_id'], state)
            for schema, key, value in values:
                run(command + ['set', schema, key, value])
    else:
        for name, expected in list(state.get('proxy_files', {}).items()):
            path = Path(name)
            if path.exists():
                if path.read_text() != expected:
                    raise ValueError(f'proxy file was edited; refusing to remove: {name}')
                path.unlink()
            del state['proxy_files'][name]
            save_state(state['profile_id'], state)
        if state.get('desktop_proxy'):
            command = desktop_settings(state)
            if not command:
                raise ValueError('log in to the original GNOME session and retry proxy restoration')
            for schema, key, value in state['desktop_proxy']:
                run(command + ['set', schema, key, value])
            state['desktop_proxy'] = []
            save_state(state['profile_id'], state)


def enable(profile_id, failsafe=0):
    state = load_state(profile_id)
    if not state:
        raise ValueError("profile is not configured")
    iface = valid_iface(state["interface"])
    if state['role'] == 'gateway' and state.get('proxy_mode') == 'share' and not proxy_ok(state):
        raise ValueError('source HTTP proxy verification failed')
    try:
        if int(failsafe):
            token = os.urandom(16).hex()
            state['failsafe_token'] = token
            save_state(profile_id, state)
            unit = 'server-network-assist-failsafe-' + iface
            run(['systemd-run', '--unit', unit, f'--on-active={min(max(int(failsafe), 30), 600)}s', SELF, 'failsafe', profile_id, token])
        if state["role"] == "client":
            state["added_routes"] = preserve_routes(state)
            save_state(profile_id, state)
        else:
            set_forwarding(True)
        run(["wg-quick", "down", iface], check=False)
        run(["wg-quick", "up", iface], timeout=45)
        if state['role'] == 'gateway' and state.get('proxy_mode') == 'share':
            relay_service(state, True)
        elif state['role'] == 'client':
            client_proxy(state, True)
        state.update(desired=True, active=True, suspended=False, failures=0, updated_at=int(time.time()))
        save_state(profile_id, state)
    except Exception as original:
        # Persist resource journals before attempting rollback, including partial setup.
        save_state(profile_id, state)
        try:
            disable(profile_id, suspended=True)
        except Exception as recovery:
            raise RuntimeError(f'{original}; rollback incomplete: {recovery}') from original
        raise
    timer(iface, bool(state.get("maintenance")))
    print(json.dumps({"ok": True, "interface": iface, "failsafe": bool(failsafe)}))


def confirm(profile_id):
    state = load_state(profile_id)
    if not tunnel_ok(state):
        raise ValueError('selected network/proxy path verification failed')
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
    state['desired'] = False
    save_state(profile_id, state)
    errors = []
    def attempt(action):
        try:
            action()
        except Exception as exc:
            errors.append(str(exc))
    attempt(lambda: timer(iface, False))
    attempt(lambda: relay_service(state, False) if state.get('role') == 'gateway' else client_proxy(state, False))
    # A proxy restore error must not prevent removal of the tunnel and owned routes.
    attempt(lambda: run(["wg-quick", "down", iface], timeout=45) if Path('/sys/class/net', iface).exists() else None)
    for target in list(state.get("added_routes", [])):
        try:
            existing = run(['ip', '-j', '-4', 'route', 'show', target])
            if json.loads(existing.stdout or '[]'):
                run(["ip", "route", "del", target])
            state['added_routes'].remove(target)
            save_state(profile_id, state)
        except Exception as exc:
            errors.append(str(exc))
    state.update(desired=False, active=Path('/sys/class/net', iface).exists(), suspended=bool(suspended),
                 failures=0, updated_at=int(time.time()))
    state.pop("failsafe_token", None)
    save_state(profile_id, state)
    if state.get("role") == "gateway":
        attempt(lambda: set_forwarding(False))
    if errors:
        raise RuntimeError('rollback incomplete; retry disable: ' + '; '.join(errors))
    print(json.dumps({"ok": True, "interface": iface, "restored_original_routes": True}))


def failsafe(profile_id, token):
    state = load_state(profile_id)
    if state.get("failsafe_token") == token:
        disable(profile_id, suspended=True)


def tunnel_ok(state):
    if not Path("/sys/class/net", state["interface"]).exists():
        return False
    if state.get('proxy_mode') == 'share':
        return proxy_ok(state)
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
            if match['role'] == 'gateway' and match.get('proxy_mode') == 'share':
                relay_service(match, True)
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
    elif action == 'relay' and len(sys.argv) == 3: asyncio.run(relay(valid_id(sys.argv[2])))
    elif action == 'verify' and len(sys.argv) == 3:
        state = load_state(sys.argv[2])
        if not (proxy_ok(state) if state.get('proxy_mode') == 'share' and state['role'] == 'gateway' else tunnel_ok(state)):
            raise ValueError('selected network/proxy path verification failed')
        print(json.dumps({'ok': True}))
    else: raise SystemExit("invalid arguments")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}))
        raise SystemExit(1)
