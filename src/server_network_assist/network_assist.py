"""Network-assist data model and fixed remote command helpers.

The web process never receives WireGuard private keys.  It only asks the
privileged helper on each managed host to generate a key and apply a narrowly
scoped wg-quick configuration.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shlex
import time


HELPER = "/usr/local/sbin/server-network-assist-helper"
PROBE_SCRIPT = r'''set -u
os="$(uname -s 2>/dev/null || printf unknown)"
host="$(hostname 2>/dev/null || printf unknown)"
route="$(ip -4 route show default 2>/dev/null | head -n 1 || true)"
dns=0
getent ahostsv4 connectivitycheck.gstatic.com >/dev/null 2>&1 && dns=1
http=000
if command -v curl >/dev/null 2>&1; then
  http="$(curl --noproxy '*' -L -sS -o /dev/null -w '%{http_code}' --connect-timeout 4 --max-time 8 https://connectivitycheck.gstatic.com/generate_204 2>/dev/null || printf 000)"
elif command -v wget >/dev/null 2>&1; then
  wget -q --no-proxy -T 8 -O /dev/null https://connectivitycheck.gstatic.com/generate_204 >/dev/null 2>&1 && http=204
fi
internet=0
case "$http" in 200|204) internet=1;; esac
printf 'os=%s\nhostname=%s\ndefault_route=%s\ndns=%s\nhttp_code=%s\ninternet=%s\n' "$os" "$host" "$route" "$dns" "$http" "$internet"
if test -x /usr/local/sbin/server-network-assist-helper && assist_status="$(sudo -n /usr/local/sbin/server-network-assist-helper status 2>/dev/null)"; then
  printf 'helper=1\n'
  printf '%s\n' "$assist_status"
else
  printf 'helper=0\n'
fi'''


def parse_probe(output: str, exit_status: int = 0) -> dict:
    result = {"ssh": exit_status == 0, "dns": False, "internet": False,
              "helper": False, "hostname": "", "os": "", "default_route": "",
              "http_code": "000", "assist": []}
    for line in output.splitlines():
        if line.startswith("assist_json="):
            try:
                value = base64.urlsafe_b64decode(line.split("=", 1)[1] + "===")
                result["assist"] = json.loads(value)
            except (ValueError, json.JSONDecodeError):
                pass
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ("dns", "internet", "helper"):
            result[key] = value == "1"
        elif key in result:
            result[key] = value[:1000]
    return result


def probe_command() -> str:
    return "sh -lc " + shlex.quote(PROBE_SCRIPT)


def helper_command(action: str, payload: dict | None = None, *args: str) -> str:
    if not re.fullmatch(r"[a-z-]+", action):
        raise ValueError("辅助程序操作无效")
    command = ["sudo", "-n", HELPER, action]
    if payload is not None:
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        command.append(encoded)
    command.extend(str(v) for v in args)
    return shlex.join(command)


def interface_name(profile_id: str) -> str:
    return "na" + hashlib.sha256(profile_id.encode()).hexdigest()[:10]


class NetworkStore:
    def __init__(self, state):
        self.state = state
        with state.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS network_profiles "
                       "(id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def profiles(self) -> list[dict]:
        with self.state.connect() as db:
            return [json.loads(row["body"]) for row in
                    db.execute("SELECT body FROM network_profiles ORDER BY rowid")]

    def get(self, profile_id: str) -> dict | None:
        return next((p for p in self.profiles() if p["id"] == profile_id), None)

    def _next_subnet(self) -> str:
        used = {p["tunnel_cidr"] for p in self.profiles()}
        for third in range(1, 255):
            value = f"10.213.{third}.0/24"
            if value not in used:
                return value
        raise ValueError("自动隧道网段已用完，请手动指定")

    @staticmethod
    def _endpoint(value: str) -> str:
        value = value.strip()
        try:
            address = ipaddress.ip_address(value)
            if address.version != 4:
                raise ValueError("出口地址目前仅支持 IPv4 或域名")
            return value
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", value):
                raise ValueError("出口地址无效")
            return value

    def save(self, payload: dict) -> dict:
        hosts = {h["id"]: h for h in self.state.hosts()}
        profile_id = str(payload.get("id", "")).strip() or secrets.token_hex(8)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", profile_id):
            raise ValueError("借网方案标识无效")
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 120:
            raise ValueError("请填写方案名称（最多 120 字）")
        gateway_id = str(payload.get("gateway_id", ""))
        raw_clients = payload.get("client_ids", [])
        if not isinstance(raw_clients, list):
            raise ValueError("客户端列表无效")
        client_ids = list(dict.fromkeys(str(v) for v in raw_clients))
        if gateway_id not in hosts or not client_ids or len(client_ids) > 32:
            raise ValueError("请选择一台出口机和 1–32 台客户端")
        if gateway_id in client_ids or any(v not in hosts for v in client_ids):
            raise ValueError("出口机不能同时作为客户端")
        port = int(payload.get("port", 51919))
        if not 1024 <= port <= 65535:
            raise ValueError("UDP 端口必须为 1024–65535")
        for existing in self.profiles():
            if existing["id"] != profile_id and existing["gateway_id"] == gateway_id and existing["port"] == port:
                raise ValueError("该出口机的 UDP 端口已被另一个借网方案使用")
        endpoint = self._endpoint(str(payload.get("endpoint", "") or hosts[gateway_id]["address"]))
        old = self.get(profile_id)
        cidr_text = str(payload.get("tunnel_cidr", "")).strip() or (old["tunnel_cidr"] if old else self._next_subnet())
        try:
            subnet = ipaddress.ip_network(cidr_text, strict=True)
        except ValueError as exc:
            raise ValueError("隧道网段无效") from exc
        if subnet.version != 4 or not subnet.is_private or subnet.prefixlen > 27 or subnet.prefixlen < 16:
            raise ValueError("隧道网段必须是 /16–/27 的私有 IPv4 网段")
        if subnet.num_addresses < len(client_ids) + 3:
            raise ValueError("隧道网段容纳不下所选客户端")
        for existing in self.profiles():
            if existing["id"] != profile_id and subnet.overlaps(ipaddress.ip_network(existing["tunnel_cidr"])):
                raise ValueError("隧道网段与另一个借网方案重叠")
        preserve = payload.get("preserve_routes", [])
        if isinstance(preserve, str):
            preserve = [v.strip() for v in re.split(r"[\s,]+", preserve) if v.strip()]
        if not isinstance(preserve, list) or len(preserve) > 64:
            raise ValueError("保留路由列表无效")
        checked = []
        for value in preserve:
            value = str(value).strip()
            try:
                checked.append(str(ipaddress.ip_network(value, strict=False)))
            except ValueError as exc:
                raise ValueError(f"保留路由无效：{value}") from exc
        state = old.get("state", "disabled") if old else "disabled"
        if old and state not in ("disabled", "error"):
            immutable = (gateway_id, client_ids, port, endpoint, str(subnet))
            previous = (old["gateway_id"], old["client_ids"], old["port"], old["endpoint"], old["tunnel_cidr"])
            if immutable != previous:
                raise ValueError("请先断开借网，再修改出口、客户端、端口或网段")
        profile = {"id": profile_id, "name": name, "gateway_id": gateway_id,
                   "client_ids": client_ids, "port": port, "endpoint": endpoint,
                   "tunnel_cidr": str(subnet), "preserve_routes": checked,
                   "maintenance": bool(payload.get("maintenance", True)),
                   "state": state, "updated_at": int(time.time()),
                   "interface": interface_name(profile_id),
                   "last_error": old.get("last_error", "") if old else ""}
        self.put(profile)
        return profile

    def put(self, profile: dict) -> None:
        with self.state.connect() as db:
            db.execute("INSERT OR REPLACE INTO network_profiles VALUES (?,?)",
                       (profile["id"], json.dumps(profile, ensure_ascii=False)))

    def set_state(self, profile_id: str, value: str, error: str = "") -> dict:
        profile = self.get(profile_id)
        if not profile:
            raise ValueError("借网方案不存在")
        profile.update(state=value, last_error=error[:2000], updated_at=int(time.time()))
        self.put(profile)
        return profile

    def delete(self, profile_id: str) -> None:
        profile = self.get(profile_id)
        if not profile:
            return
        if profile["state"] not in ("disabled", "error"):
            raise ValueError("请先断开借网再删除方案")
        with self.state.connect() as db:
            db.execute("DELETE FROM network_profiles WHERE id=?", (profile_id,))

    def runtime_payloads(self, profile: dict, public_keys: dict[str, str]) -> tuple[dict, dict[str, dict]]:
        subnet = ipaddress.ip_network(profile["tunnel_cidr"])
        addresses = list(subnet.hosts())
        gateway_ip = str(addresses[0])
        prefix = subnet.prefixlen
        gateway = {"version": 1, "profile_id": profile["id"], "interface": profile["interface"],
                   "role": "gateway", "address": f"{gateway_ip}/{prefix}",
                   "subnet": str(subnet), "port": profile["port"],
                   "maintenance": profile["maintenance"], "peers": []}
        clients = {}
        for index, client_id in enumerate(profile["client_ids"], 1):
            client_ip = str(addresses[index])
            gateway["peers"].append({"public_key": public_keys[client_id], "allowed_ip": f"{client_ip}/32"})
            control = list(profile["preserve_routes"])
            control.append(profile["endpoint"])
            for host in self.state.route(client_id):
                control.append(host["address"])
            clients[client_id] = {"version": 1, "profile_id": profile["id"],
                "interface": profile["interface"], "role": "client",
                "address": f"{client_ip}/{prefix}", "gateway_ip": gateway_ip,
                "endpoint": profile["endpoint"], "port": profile["port"],
                "gateway_public_key": public_keys[profile["gateway_id"]],
                "preserve_routes": list(dict.fromkeys(control)),
                "maintenance": profile["maintenance"]}
        return gateway, clients
