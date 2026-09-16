"""Online commercial-service credentials and signed device requests."""
from __future__ import annotations

import base64
import hashlib
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


MAX_RESPONSE_BYTES = 1024 * 1024


def parse_enrollment_url(value: str) -> tuple[str, str]:
    """Parse ``https://service/#enroll=...`` without putting the secret on the wire."""
    parts = urlsplit(value.strip())
    loopback_http = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "::1", "localhost"}
    if (parts.scheme != "https" and not loopback_http) or not parts.netloc:
        raise ValueError("开户注册地址必须使用 HTTPS")
    if parts.username or parts.password or parts.query or parts.path not in ("", "/"):
        raise ValueError("开户注册地址格式无效")
    values = parse_qs(parts.fragment, strict_parsing=True).get("enroll", [])
    if len(values) != 1 or not 16 <= len(values[0]) <= 512:
        raise ValueError("开户注册地址缺少一次性令牌 #enroll=...")
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")), values[0]


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
        except (OSError, urllib.error.URLError):
            raise ValueError("无法连接商业服务端") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("服务响应过大")
        try:
            value = json.loads(body)
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
        self._write_private(self.path, {"schema_version": 1, "base_url": base_url,
            "device_id": device_id, "customer_id": customer_id,
            "signing_private_key": signing_private, "wireguard_private_key": wireguard_private,
            "enrolled_at": int(self.clock())})
        return {"device_id": device_id, "customer_id": customer_id, "provider": urlsplit(base_url).hostname}

    def request(self, method: str, path: str, value: dict | None = None) -> dict:
        record = self._record()
        body = b"" if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
        request = urllib.request.Request(record["base_url"] + path,
            data=body if value is not None else None, headers=headers, method=method)
        return self._open_json(request, record["base_url"])

    def routes(self) -> list[dict]:
        result = self.request("GET", "/client/v1/routes")
        if not isinstance(result.get("routes"), list):
            raise ValueError("服务端线路列表无效")
        return result["routes"]

    def subscription(self) -> dict:
        return self.request("GET", "/client/v1/subscription")

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
