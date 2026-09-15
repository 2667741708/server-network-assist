#!/usr/bin/env python3
"""Local Clash/Mihomo controller used through an authenticated SSH session."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def candidates():
    home = Path.home()
    values = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = Path(appdata) / "io.github.clash-verge-rev.clash-verge-rev"
        values += [root / "clash-verge.yaml", root / "config.yaml"]
    values += [home / ".config/mihomo/config.yaml", home / ".config/clash/config.yaml"]
    return [path for path in values if path.is_file()]


def scalar(text, name, default=""):
    match = re.search(rf"(?m)^{re.escape(name)}:\s*['\"]?([^'\"#\r\n]*)", text)
    return match.group(1).strip() if match else default


def controller(path):
    text = path.read_text(encoding="utf-8-sig")
    address = scalar(text, "external-controller")
    secret = scalar(text, "secret")
    if not address:
        pipe = scalar(text, "external-controller-pipe")
        if os.name == "nt" and pipe.startswith("\\\\.\\pipe\\"):
            return "pipe:" + pipe, secret
        return None, secret
    if not re.fullmatch(r"(?:127\.0\.0\.1|localhost):\d{1,5}", address):
        raise ValueError("仅允许连接远端机器自身的 Clash Controller")
    return "http://" + address, secret


def api(base, secret, method, path, payload=None):
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    data = None if payload is None else json.dumps(payload).encode()
    if base.startswith("pipe:"):
        body = data or b""
        wire_headers = {**headers, "Host": "localhost", "Connection": "close",
                        "Content-Length": str(len(body))}
        request_bytes = (method + " " + path + " HTTP/1.1\r\n" +
                         "".join(f"{key}: {value}\r\n" for key, value in wire_headers.items()) +
                         "\r\n").encode() + body
        with open(base[5:], "r+b", buffering=0) as pipe:
            pipe.write(request_bytes)
            response = bytearray()
            for _ in range(256):
                response.extend(pipe.read(65536))
                if b"\r\n\r\n" not in response:
                    continue
                raw_head, raw_content = bytes(response).split(b"\r\n\r\n", 1)
                length = re.search(br"(?im)^content-length:\s*(\d+)\s*$", raw_head)
                if length and len(raw_content) >= int(length.group(1)):
                    break
                if b"transfer-encoding: chunked" in raw_head.lower() and raw_content.endswith(b"0\r\n\r\n"):
                    break
            response = bytes(response)
        head, _, content = response.partition(b"\r\n\r\n")
        first = head.splitlines()[0].decode(errors="replace") if head else ""
        match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d+)", first)
        if not match or int(match.group(1)) >= 400:
            raise ValueError("Clash 命名管道 Controller 返回异常：" + first[:200])
        if b"transfer-encoding: chunked" in head.lower():
            decoded = bytearray()
            remaining = content
            while remaining:
                size_text, separator, remaining = remaining.partition(b"\r\n")
                if not separator:
                    break
                size = int(size_text.split(b";", 1)[0], 16)
                if not size:
                    break
                decoded.extend(remaining[:size])
                remaining = remaining[size + 2:]
            content = bytes(decoded)
        return json.loads(content) if content else {}
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ValueError(f"Clash Controller 返回 HTTP {exc.code}: {detail[:300]}") from exc


def tun_enabled(text):
    block = re.search(r"(?ms)^tun:\s*\n((?:^[ \t]+.*\n?)*)", text)
    if not block:
        return False
    return bool(re.search(r"(?m)^\s+enable:\s*true\s*(?:#.*)?$", block.group(1), re.I))


def status():
    paths = candidates()
    if not paths:
        return {"installed": False, "running": False, "controller": False,
                "error": "没有找到 Clash/Mihomo 配置文件"}
    path = paths[0]
    text = path.read_text(encoding="utf-8-sig")
    base, secret = controller(path)
    result = {"installed": True, "running": False, "controller": bool(base),
              "config_path": str(path), "mode": scalar(text, "mode", "rule"),
              "mixed_port": int(scalar(text, "mixed-port", "0") or 0),
              "tun": tun_enabled(text), "system_proxy": system_proxy(),
              "rules": [], "policies": [], "error": ""}
    if not base:
        result["error"] = "配置未启用本机 HTTP Controller；请设置 external-controller: 127.0.0.1:9097"
        return result
    try:
        version = api(base, secret, "GET", "/version")
        config = api(base, secret, "GET", "/configs")
        rules = api(base, secret, "GET", "/rules")
        proxies = api(base, secret, "GET", "/proxies")
        proxy_values = proxies.get("proxies") or {}
        groups = []
        for name, value in proxy_values.items():
            choices = value.get("all") if isinstance(value, dict) else None
            if isinstance(choices, list) and choices:
                groups.append({"name": name, "type": value.get("type", ""),
                               "now": value.get("now", ""), "all": choices[:500]})
        result.update(running=True, version=version.get("version", ""),
                      mode=config.get("mode", result["mode"]),
                      tun=bool((config.get("tun") or {}).get("enable", result["tun"])),
                      rules=(rules.get("rules") or [])[:500],
                      policies=list(proxy_values.keys())[:500], groups=groups[:100])
    except Exception as exc:
        result["error"] = str(exc)[:600]
    return result


def system_proxy():
    if os.name != "nt":
        return {"supported": False, "enabled": None,
                "reason": "Linux SSH 会话不能代表桌面用户修改 GNOME 系统代理"}
    import winreg
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
        try:
            enabled = bool(winreg.QueryValueEx(key, "ProxyEnable")[0])
        except FileNotFoundError:
            enabled = False
        try:
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
        except FileNotFoundError:
            server = ""
    return {"supported": True, "enabled": enabled, "server": server}


def set_system_proxy(enabled, port):
    if os.name != "nt":
        raise ValueError("Linux 请在对应桌面会话中设置系统代理，或使用 TUN")
    import ctypes
    import winreg
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(enabled))
        if enabled:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{port}")
    for option in (39, 37):
        ctypes.windll.wininet.InternetSetOptionW(None, option, None, 0)


def backup(path):
    target = path.with_name(path.name + ".sna-backup-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, target)
    return target


def replace_mode(text, value):
    if value not in ("rule", "global", "direct"):
        raise ValueError("模式只能是 rule、global 或 direct")
    if re.search(r"(?m)^mode:", text):
        return re.sub(r"(?m)^mode:.*$", "mode: " + value, text, count=1)
    return "mode: " + value + "\n" + text


def replace_tun(text, enabled):
    block = re.search(r"(?ms)^tun:\s*\n((?:^[ \t]+.*\n?)*)", text)
    value = "true" if enabled else "false"
    if not block:
        return text.rstrip() + f"\n\ntun:\n  enable: {value}\n"
    body = block.group(1)
    if re.search(r"(?m)^\s+enable:", body):
        body = re.sub(r"(?m)^(\s+)enable:.*$", rf"\1enable: {value}", body, count=1)
    else:
        body = "  enable: " + value + "\n" + body
    return text[:block.start(1)] + body + text[block.end(1):]


def validate_rule(value):
    value = str(value).strip()
    if len(value) > 500 or any(char in value for char in "\r\n\x00"):
        raise ValueError("规则长度或字符无效")
    parts = [part.strip() for part in value.split(",")]
    allowed = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "GEOIP", "GEOSITE", "MATCH"}
    if len(parts) < 2 or parts[0] not in allowed or not all(parts):
        raise ValueError("规则格式无效，例如 DOMAIN-SUFFIX,openai.com,节点选择")
    return value


def prepend_rule(text, rule):
    match = re.search(r"(?m)^rules:\s*$", text)
    if not match:
        return text.rstrip() + "\n\nrules:\n- " + rule + "\n"
    return text[:match.end()] + "\n- " + rule + text[match.end():]


def apply(payload):
    paths = candidates()
    if not paths:
        raise ValueError("没有找到 Clash/Mihomo 配置文件")
    path = paths[0]
    text = path.read_text(encoding="utf-8-sig")
    base, secret = controller(path)
    action = payload.get("action")
    saved = None
    if action == "mode":
        text = replace_mode(text, str(payload.get("mode", "")))
    elif action == "tun":
        text = replace_tun(text, bool(payload.get("enabled")))
    elif action == "system_proxy":
        before = system_proxy()
        saved = path.with_name(path.name + ".sna-proxy-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        saved.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
        set_system_proxy(bool(payload.get("enabled")), int(scalar(text, "mixed-port", "7890") or 7890))
        result = status()
        result["backup"] = str(saved)
        return result
    elif action == "rule_add":
        text = prepend_rule(text, validate_rule(payload.get("rule", "")))
    elif action == "proxy_select":
        if not base:
            raise ValueError("Clash Controller 未启用，无法切换节点")
        group = str(payload.get("group", "")).strip()
        name = str(payload.get("name", "")).strip()
        proxies = (api(base, secret, "GET", "/proxies").get("proxies") or {})
        value = proxies.get(group)
        choices = value.get("all") if isinstance(value, dict) else None
        if not group or not name or not isinstance(choices, list) or name not in choices:
            raise ValueError("策略组或节点不存在")
        api(base, secret, "PUT", "/proxies/" + urllib.parse.quote(group, safe=""), {"name": name})
        return status()
    else:
        raise ValueError("不支持的 Clash 操作")
    saved = backup(path)
    temporary = path.with_name(path.name + ".sna-tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    try:
        if base and action == "mode":
            api(base, secret, "PATCH", "/configs", {"mode": payload.get("mode")})
        if base and action in ("tun", "rule_add"):
            api(base, secret, "PUT", "/configs?force=true", {"path": str(path)})
    except Exception:
        shutil.copy2(saved, path)
        if base:
            try:
                api(base, secret, "PUT", "/configs?force=true", {"path": str(path)})
            except Exception:
                pass
        raise
    result = status()
    result["backup"] = str(saved)
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = status() if payload.get("action") == "status" else apply(payload)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
