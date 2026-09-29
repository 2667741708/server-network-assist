"""Online commercial-service credentials and signed device requests."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit, urlunsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .client_crypto import b64url_encode, sign_payload
from .control_plane import strict_json_loads, verify_directory_envelope


MAX_RESPONSE_BYTES = 1024 * 1024
KNOWN_SERVICE_URLS = ('https://whm12.art',)


class ServiceTransportError(ValueError):
    """The request was not accepted by any HTTP server and may use a fallback URL."""


def connection_error(exc: OSError, base_url: str) -> str:
    """Explain transport failures without echoing credentials or raw requests."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return '无法连接商业服务端：TLS 证书验证失败。请检查服务器证书、系统时间或校园认证拦截；不会关闭证书验证。'
    if isinstance(reason, ssl.SSLError):
        return '无法连接商业服务端：TLS 握手失败。请检查订阅地址的 HTTP/HTTPS 协议及代理或 TUN 转发。'
    if isinstance(reason, socket.gaierror):
        return '无法连接商业服务端：服务域名解析失败。请检查网络与 DNS。'
    if isinstance(reason, ConnectionRefusedError):
        return '无法连接商业服务端：连接被拒绝。请管理员检查服务监听端口与防火墙。'
    prefix = '无法连接商业服务端：连接超时。' if isinstance(reason, TimeoutError) else '无法连接商业服务端：网络连接失败。'
    parts = urlsplit(base_url)
    try:
        private = ipaddress.ip_address(parts.hostname or '').is_private
    except ValueError:
        private = False
    if private:
        return prefix + '这是内网订阅入口；连接 Wi-Fi 不代表能够到达源网。请检查校园无线与有线网络互通，以及 Clash TUN 的内网路径。可在接入保护与帮助中查看检测结果。'
    return prefix + '请检查服务是否可达以及代理或 TUN 的访问路径。'


def parse_enrollment_url(value: str) -> tuple[str, str]:
    """Parse an enrollment URL without sending its fragment to the server.

    HTTPS remains valid everywhere. Plain HTTP is accepted only for literal
    loopback/private/link-local addresses so a campus-only control plane does
    not need a public domain or cloud reverse proxy.
    """
    parts = urlsplit(value.strip())
    local_http = False
    if parts.scheme == "http" and parts.hostname:
        if parts.hostname == "localhost":
            local_http = True
        else:
            try:
                address = ipaddress.ip_address(parts.hostname)
                local_http = address.is_private or address.is_loopback or address.is_link_local
            except ValueError:
                local_http = False
    if (parts.scheme != "https" and not local_http) or not parts.netloc:
        raise ValueError("公网开户地址必须使用 HTTPS；HTTP 仅允许校园网私有 IP")
    if parts.username or parts.password or parts.query or parts.path not in ("", "/"):
        raise ValueError("开户注册地址格式无效")
    values = parse_qs(parts.fragment, strict_parsing=True).get("enroll", [])
    if len(values) != 1 or not 16 <= len(values[0]) <= 512:
        raise ValueError("开户注册地址缺少一次性令牌 #enroll=...")
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")), values[0]


def _https_url(value: object, label: str = "服务地址") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}无效")
    text = value.strip()
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError(f"{label}无效") from None
    if (parts.scheme != "https" or not parts.netloc or not hostname or
            parts.username or parts.password or parts.fragment or parts.query):
        raise ValueError(f"{label}必须使用不含凭据和查询的 HTTPS 地址")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label}端口无效")
    return text.rstrip("/")


def _same_origin(first: str, second: str) -> bool:
    left, right = urlsplit(first), urlsplit(second)
    return (left.scheme, left.hostname, left.port or 443) == (right.scheme, right.hostname, right.port or 443)


class OnlineServiceClient:
    """Persist device keys locally and call the separate ``/client/v1`` API."""

    def __init__(self, data: Path, opener=None, clock=None):
        self.data = Path(data)
        self.path = self.data / "customer-online-service.json"
        self.lease_path = self.data / "customer-online-lease.json"
        context = ssl.create_default_context()
        # Python 3.13 enables X509 strict mode. Some managed/campus trust roots
        # predate the keyUsage requirement; keep normal chain and hostname
        # verification while accepting those installed roots.
        if hasattr(ssl, 'VERIFY_X509_STRICT'):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        self.clock = clock or time.time

    def configured(self) -> bool:
        return self.path.is_file()

    @staticmethod
    def _new_keys() -> tuple[str, str, str]:
        signing = Ed25519PrivateKey.generate()
        wireguard = X25519PrivateKey.generate()
        return (b64url_encode(signing.private_bytes_raw()),
                b64url_encode(signing.public_key().public_bytes_raw()),
                base64.b64encode(wireguard.private_bytes_raw()).decode("ascii"))

    @staticmethod
    def _write_private(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)

    def _record(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            required = ("base_url", "device_id", "customer_id", "signing_private_key", "wireguard_private_key")
            if not isinstance(value, dict) or any(not isinstance(value.get(k), str) for k in required):
                raise ValueError()
            value["base_url"] = _https_url(value["base_url"], "服务地址")
            for key in ("preferred_base_url", "directory_url"):
                if value.get(key) is not None:
                    value[key] = _https_url(value[key], key)
            if value.get("directory_public_key") is not None:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                Ed25519PublicKey.from_public_bytes(
                    base64.urlsafe_b64decode(value["directory_public_key"] + "=" * (-len(value["directory_public_key"]) % 4)))
            return value
        except Exception:
            raise ValueError("尚未完成商业服务开户注册") from None

    def summary(self) -> dict:
        record = self._record()
        return {"configured": True, "provider": urlsplit(record["base_url"]).hostname,
                "device_id": record["device_id"], "customer_id": record["customer_id"],
                "lease": self.active_lease()}

    def _open_json(self, request: urllib.request.Request, base_url: str) -> dict:
        try:
            with self.opener.open(request, timeout=15) as response:
                original, final = urlsplit(base_url), urlsplit(response.url)
                if final.scheme != original.scheme or final.hostname != original.hostname or final.port != original.port:
                    raise ValueError("服务请求发生了不受信任的跨域跳转")
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except ValueError:
            raise
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace").strip()
            raise ValueError(f"服务拒绝请求（{exc.code}）：{detail[:300]}") from None
        except (OSError, urllib.error.URLError) as exc:
            raise ServiceTransportError(connection_error(exc, base_url)) from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("服务响应过大")
        try:
            value = strict_json_loads(body)
        except Exception:
            raise ValueError("服务响应不是有效 JSON") from None
        if not isinstance(value, dict):
            raise ValueError("服务响应格式无效")
        return value

    def enroll(self, enrollment_url: str, label: str | None = None) -> dict:
        base_url, token = parse_enrollment_url(enrollment_url)
        signing_private, signing_public, wireguard_private = self._new_keys()
        wireguard = X25519PrivateKey.from_private_bytes(base64.b64decode(wireguard_private))
        body = json.dumps({
            "enrollment_token": token,
            "signing_public_key": signing_public,
            "wireguard_public_key": base64.b64encode(wireguard.public_key().public_bytes_raw()).decode("ascii"),
            "label": (label or socket.gethostname())[:120],
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(base_url + "/client/v1/enroll", data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "User-Agent": "ServerNetworkAssist-Client/1"}, method="POST")
        result = self._open_json(request, base_url)
        device_id, customer_id = result.get("device_id"), result.get("customer_id")
        if not isinstance(device_id, str) or not isinstance(customer_id, str):
            raise ValueError("服务端没有返回有效设备身份")
        directory_url = result.get("directory_url")
        directory_public_key = result.get("directory_public_key")
        if directory_url is not None:
            directory_url = _https_url(directory_url, "节点目录地址")
            if not _same_origin(base_url, directory_url):
                raise ValueError("节点目录地址必须与开户注册服务使用同一 HTTPS 来源")
        if directory_public_key is not None:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            try:
                Ed25519PublicKey.from_public_bytes(
                    base64.urlsafe_b64decode(directory_public_key + "=" * (-len(directory_public_key) % 4)))
            except Exception:
                raise ValueError("服务端节点目录公钥无效") from None
        service_urls = [base_url]
        for item in result.get('service_urls', []):
            if not isinstance(item, str):
                continue
            try:
                candidate, _ = parse_enrollment_url(item.rstrip('/') + '/#enroll=validation-token-1234')
            except ValueError:
                continue
            if candidate not in service_urls:
                service_urls.append(candidate)
        self._write_private(self.path, {"schema_version": 3, "base_url": base_url,
            'service_urls': service_urls, 'preferred_base_url': base_url,
            'directory_url': directory_url, 'directory_public_key': directory_public_key,
            "device_id": device_id, "customer_id": customer_id,
            "signing_private_key": signing_private, "wireguard_private_key": wireguard_private,
            "enrolled_at": int(self.clock())})
        return {"device_id": device_id, "customer_id": customer_id, "provider": urlsplit(base_url).hostname}

    def service_urls(self) -> list[str]:
        record = self._record()
        result = []
        for value in [record.get('preferred_base_url'), record.get('base_url'),
                      *(record.get('service_urls') or [])]:
            if isinstance(value, str):
                try:
                    value = _https_url(value)
                except ValueError:
                    continue
                if value not in result:
                    result.append(value)
        if urlsplit(record['base_url']).hostname in {'whm12.art', '10.20.32.13'}:
            result.extend(value for value in KNOWN_SERVICE_URLS if value not in result)
        return result

    def prefer_base_url(self, value: str) -> None:
        record = self._record()
        if value not in self.service_urls() or record.get('preferred_base_url') == value:
            return
        record['preferred_base_url'] = value
        self._write_private(self.path, record)

    def request(self, method: str, path: str, value: dict | None = None) -> dict:
        record = self._record()
        body = b"" if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        failures = []
        for base_url in self.service_urls():
            timestamp = int(self.clock())
            nonce = secrets.token_urlsafe(24)
            signed = {"method": method, "path": path, "timestamp": timestamp, "nonce": nonce,
                      "body_sha256": hashlib.sha256(body).hexdigest()}
            headers = {"Accept": "application/json", "User-Agent": "ServerNetworkAssist-Client/1",
                "X-Device-ID": record["device_id"], "X-Device-Timestamp": str(timestamp),
                "X-Device-Nonce": nonce,
                "X-Device-Signature": sign_payload(record["signing_private_key"], signed)}
            if value is not None:
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(base_url + path,
                data=body if value is not None else None, headers=headers, method=method)
            try:
                result = self._open_json(request, base_url)
            except ServiceTransportError as exc:
                failures.append(exc)
                continue
            self.prefer_base_url(base_url)
            return result
        raise failures[0] if failures else ValueError('商业服务端入口无效')

    def routes(self) -> list[dict]:
        result = self.subscription()
        routes = result.get("routes")
        if not isinstance(routes, list):
            raise ValueError("服务端线路列表无效")
        return routes

    def subscription(self) -> dict:
        result = self.request("GET", "/client/v1/subscription")
        if not isinstance(result, dict):
            raise ValueError("服务端订阅响应无效")
        if "node_directory" not in result:
            if not isinstance(result.get("routes"), list):
                raise ValueError("服务端线路列表无效")
            return result
        spec = result.get("node_directory")
        if not isinstance(spec, dict):
            raise ValueError("服务端节点目录声明无效")
        directory_url = _https_url(spec.get("url"), "节点目录地址")
        record = self._record()
        pinned_key = record.get("directory_public_key")
        public_key = spec.get("public_key")
        if not isinstance(public_key, str):
            raise ValueError("服务端节点目录公钥无效")
        if pinned_key is not None and pinned_key != public_key:
            raise ValueError("服务端节点目录信任锚已改变，请重新开户注册")
        if record.get("directory_url") is not None and not _same_origin(record["directory_url"], directory_url):
            raise ValueError("服务端节点目录来源已改变，请重新开户注册")
        if not _same_origin(record["base_url"], directory_url):
            raise ValueError("节点目录来源必须与服务端保持一致")
        if pinned_key is None:
            # Older records predate the explicit enrollment pin.  HTTPS server
            # authentication permits a one-time migration; all future calls
            # require the persisted key to match exactly.
            record["directory_public_key"] = public_key
            record["directory_url"] = directory_url
            self._write_private(self.path, record)
            pinned_key = public_key
        request = urllib.request.Request(directory_url, headers={"Accept": "application/json"}, method="GET")
        envelope = self._open_json(request, directory_url)
        try:
            directory = verify_directory_envelope(
                envelope, pinned_key, now=int(self.clock()),
                minimum_revision=int(spec.get("minimum_revision", 0)))
        except Exception as exc:
            raise ValueError(f"节点目录验证失败：{exc}") from None
        routes = self._resolve_logical_routes(result, directory)
        return result | {"routes": routes, "resolved_directory_revision": directory["revision"]}

    def _resolve_logical_routes(self, subscription: dict, directory: dict) -> list[dict]:
        now = int(self.clock())
        nodes = {row["node_id"]: row for row in directory.get("nodes", [])}
        refs = subscription.get("node_refs")
        if not isinstance(refs, list):
            raise ValueError("服务端逻辑节点引用无效")
        if any(not isinstance(ref, dict) for ref in refs):
            raise ValueError("服务端逻辑节点引用无效")
        routes = []
        for ref in sorted(refs, key=lambda row: (int(row.get("priority", 100)), str(row.get("id", "")))):
            if not isinstance(ref.get("node_id"), str):
                raise ValueError("服务端逻辑节点引用无效")
            pending = [ref["node_id"]]
            visited = set()
            while pending and len(visited) <= 16:
                node_id = pending.pop(0)
                if node_id in visited:
                    continue
                visited.add(node_id)
                node = nodes.get(node_id)
                if not node:
                    continue
                pending.extend(row for row in node.get("fallback_node_ids", []) if isinstance(row, str))
                egress_ids = ref.get("egress", [])
                if isinstance(egress_ids, str):
                    egress_ids = [egress_ids]
                if not isinstance(egress_ids, list) or any(not isinstance(row, str) for row in egress_ids):
                    raise ValueError("服务端逻辑节点出口引用无效")
                egress = next((row for row in node.get("egress", [])
                               if row.get("id") in egress_ids), None)
                if egress is None:
                    continue
                transports = node.get("transports", [])
                requested_transport = ref.get("transport")
                if requested_transport is not None and not isinstance(requested_transport, str):
                    raise ValueError("服务端逻辑节点传输引用无效")
                if requested_transport:
                    transports = [row for row in transports
                                  if row.get("id") == requested_transport or row.get("kind") == requested_transport]
                for transport in sorted(transports, key=lambda row: (int(row.get("priority", 100)), str(row.get("id", "")))):
                    endpoints = [row for row in transport.get("endpoints", [])
                                 if isinstance(row, dict) and int(row.get("expires_at", 0)) > now]
                    if not endpoints:
                        continue
                    endpoint = sorted(endpoints, key=lambda row: (int(row.get("priority", 100)), row.get("address", "")))[0]
                    route = {
                        "id": ref.get("id"), "name": ref.get("name", node.get("name", node_id)),
                        "available": True, "access_mode": ref.get("access_mode", "wireguard"),
                        "endpoint": endpoint["address"], "node_id": node_id,
                        "transport": transport.get("kind"), "transport_id": transport.get("id"),
                        "access": transport.get("access"), "egress": egress.get("id"),
                        "egress_mode": egress.get("kind"), "fallback_used": node_id != ref["node_id"],
                    }
                    if not isinstance(route["id"], str) or not route["id"]:
                        raise ValueError("服务端逻辑线路编号无效")
                    routes.append(route)
                    break
                if routes and routes[-1].get("id") == ref.get("id"):
                    break
        if not routes:
            raise ValueError("节点目录没有可用的逻辑线路")
        return routes

    def usage(self) -> dict:
        return self.request("GET", "/client/v1/usage")

    def lease(self, grant_id: str) -> dict:
        result = self.request("POST", "/client/v1/lease", {"grant_id": grant_id})
        lease = result.get("lease")
        if not isinstance(lease, dict) or not isinstance(lease.get("id"), str):
            raise ValueError("服务端租约响应无效")
        self._write_private(self.lease_path, lease)
        return lease

    def active_lease(self) -> dict | None:
        try:
            value = json.loads(self.lease_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and isinstance(value.get("id"), str) else None
        except (OSError, ValueError):
            return None

    def renew(self, lease_id: str | None = None) -> dict:
        current = self.active_lease()
        lease_id = lease_id or (current or {}).get("id")
        if not lease_id:
            raise ValueError("当前没有可续租的商业租约")
        result = self.request("POST", "/client/v1/lease/renew", {"lease_id": lease_id})
        lease = result.get("lease")
        if not isinstance(lease, dict) or not isinstance(lease.get("id"), str):
            raise ValueError("服务端续租响应无效")
        self._write_private(self.lease_path, lease)
        return lease

    def release(self, lease_id: str | None = None) -> None:
        current = self.active_lease()
        lease_id = lease_id or (current or {}).get("id")
        if not lease_id:
            return
        result = self.request("POST", "/client/v1/lease/release", {"lease_id": lease_id})
        if result.get("ok") is not True:
            raise ValueError("服务端没有确认释放租约")
        if current and current.get("id") == lease_id:
            self.lease_path.unlink(missing_ok=True)

    def remove(self) -> None:
        self.lease_path.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)
