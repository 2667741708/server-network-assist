#!/usr/bin/env python3
"""Local Clash/Mihomo controller used through an authenticated SSH session."""
from __future__ import annotations

import json
import ipaddress
import os
from pathlib import Path
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request


PROFILES = {
    "system_mihomo": Path("/etc/mihomo/config.yaml"),
    "cloud_mihomo": Path("/etc/mihomo-cloud/config.yaml"),
}
DELAY_URL = "https://www.gstatic.com/generate_204"
GEO_TARGETS = (("https://api.ip.sb/geoip", "geoip"),
               ("https://api.seeip.org/geoip", "geoip"),
               ("https://www.cloudflare.com/cdn-cgi/trace", "trace"),
               ("https://ipapi.co/json/", "json"),
               ("https://ipinfo.io/json", "json"))
BLOCKED_COUNTRIES = frozenset({"CN", "HK", "MO", "TW", "ZZ", "XX"})
BLOCKED_NAME = re.compile(
    r"(?:🇨🇳|🇭🇰|🇲🇴|🇹🇼|中国大陆|大陆节点|回国|国内|内地|大陆|北京|上海|广州|深圳|"
    r"香港|澳门|台湾|(?:^|[\s_\-/|])(?:CN|HK|MO|TW|China|Hong\s*Kong|Macau|Macao|Taiwan)(?:$|[\s_\-/|]))", re.I)
FOREIGN_NAME = re.compile(
    r"(?:🇺🇸|🇯🇵|🇸🇬|🇰🇷|🇬🇧|🇩🇪|🇫🇷|🇨🇦|🇦🇺|🇳🇱|🇨🇭|🇸🇪|🇳🇴|🇫🇮|🇮🇹|🇪🇸|🇮🇳|🇹🇭|🇻🇳|🇵🇭|🇲🇾|🇮🇩|🇧🇷|🇦🇷|🇲🇽|🇿🇦|🇹🇷|🇦🇪|🇷🇺|🇵🇱|🇨🇿|🇦🇹|🇩🇰|🇧🇪|🇮🇪|🇵🇹|🇳🇿|"
    r"美国|日本|新加坡|韩国|英国|德国|法国|加拿大|澳大利亚|荷兰|瑞士|瑞典|挪威|芬兰|意大利|西班牙|印度|泰国|越南|菲律宾|马来西亚|印尼|印度尼西亚|巴西|阿根廷|墨西哥|南非|土耳其|阿联酋|俄罗斯|波兰|捷克|奥地利|丹麦|比利时|爱尔兰|葡萄牙|新西兰|"
    r"(?:^|[\s_\-/|])(?:US|USA|JP|JPN|SG|SGP|KR|KOR|UK|GB|DE|FR|CA|AU|NL|CH|SE|NO|FI|IT|ES|IN|TH|VN|PH|MY|ID|BR|AR|MX|ZA|TR|AE|RU|PL|CZ|AT|DK|BE|IE|PT|NZ)(?:$|[\s_\-/|]))", re.I)


def foreign_candidate(name):
    return bool(name and not BLOCKED_NAME.search(name) and FOREIGN_NAME.search(name))
PROBE_LISTENER = "sna-admin-probe"
AUTO_GROUP = "SNA-OVERSEAS-AUTO"


def managed_leaf(values, group_name):
    outer = values.get(group_name) or {}
    if outer.get("type") != "Selector":
        raise ValueError("受管理出口缺少安全选择组")
    selected = str(outer.get("now", ""))
    uses_auto_group = selected == AUTO_GROUP
    if uses_auto_group:
        inner = values.get(AUTO_GROUP) or {}
        choices = inner.get("all") or []
        if inner.get("type") != "URLTest" or not choices or any(
                not foreign_candidate(name) for name in choices):
            raise ValueError("自动组包含地区未知或已排除的节点")
        selected = str(inner.get("now", ""))
    automatic = uses_auto_group and not bool(inner.get("fixed")) if uses_auto_group else False
    leaf = values.get(selected)
    if (not foreign_candidate(selected) or not isinstance(leaf, dict) or
            leaf.get("all")):
        raise ValueError("当前出口不是可验证的境外具体节点")
    return selected, automatic


def candidates(profile="auto"):
    if profile in PROFILES:
        path = PROFILES[profile]
        return [path] if path.is_file() else []
    if profile not in ("auto", "user_mihomo", "clash_verge"):
        raise ValueError("代理配置类型无效")
    home = Path.home()
    values = []
    appdata = os.environ.get("APPDATA")
    if os.name == 'nt':
        # SSH/service environments can inherit another user's APPDATA.
        import ctypes
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
                appdata = winreg.QueryValueEx(key, 'AppData')[0]
        except OSError:
            folder = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 26, None, 0, folder) == 0:
                appdata = folder.value
    if appdata and profile in ("auto", "clash_verge"):
        root = Path(appdata) / "io.github.clash-verge-rev.clash-verge-rev"
        values += [root / "clash-verge.yaml", root / "config.yaml"]
    if profile in ("auto", "user_mihomo"):
        values += [home / ".config/mihomo/config.yaml", home / ".config/clash/config.yaml"]
    if profile == "auto" and os.name != "nt":
        values += list(PROFILES.values())
    return [path for path in values if path.is_file()]


def selected_config(profile="auto"):
    paths = candidates(profile)
    if not paths:
        raise FileNotFoundError("未找到所选代理配置；请核对服务器配置类型")
    path = paths[0]
    try:
        path.read_text(encoding="utf-8-sig")
    except PermissionError as exc:
        raise PermissionError("代理配置不可读；请安装受限管理代理或授予专用账号读取权限") from exc
    return path


def scalar(text, name, default=""):
    match = re.search(rf"(?m)^{re.escape(name)}:\s*['\"]?([^'\"#\r\n]*)", text)
    return match.group(1).strip() if match else default


def managed_probe(text):
    """Read the dedicated loopback listener; reject ambiguous YAML layouts."""
    section = re.search(r"(?m)^listeners:\s*(?:#.*)?$", text)
    if not section:
        return None
    lines = text[section.end():].splitlines()
    entries = []
    current = []
    for line in lines:
        if line and not line[0].isspace() and not line.startswith("-"):
            break
        if re.match(r"^\s*-\s+name:\s*", line):
            if current:
                entries.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append(current)
    for entry in entries:
        values = {}
        for line in entry:
            match = re.match(r"^\s*(?:-\s*)?(name|type|listen|port|proxy):\s*(.*?)\s*(?:#.*)?$", line)
            if match:
                values[match.group(1)] = match.group(2).strip().strip("\"'")
        if values.get("name") != PROBE_LISTENER:
            continue
        if values.get("type") != "http" or values.get("listen") != "127.0.0.1":
            raise ValueError("管理探测入口必须是仅本机可访问的 HTTP 监听")
        port = int(values.get("port", "0"))
        group = values.get("proxy", "")
        if not 1 <= port <= 65535 or not group:
            raise ValueError("管理探测入口未绑定有效策略组或端口")
        return {"group": group, "port": port}
    return None


def controller(path):
    text = path.read_text(encoding="utf-8-sig")
    address = scalar(text, "external-controller")
    secret = scalar(text, "secret")
    if not address:
        pipe = scalar(text, "external-controller-pipe")
        if os.name == "nt" and pipe.startswith("\\\\.\\pipe\\"):
            return "pipe:" + pipe, secret
        # Verge may pass the pipe on the core command line instead of in YAML.
        if os.name == 'nt' and path.parent.name == 'io.github.clash-verge-rev.clash-verge-rev':
            return 'pipe:' + r'\\.\pipe\verge-mihomo', secret
        return None, secret
    if not re.fullmatch(r"(?:127\.0\.0\.1|localhost):\d{1,5}", address):
        raise ValueError("仅允许连接远端机器自身的 Clash Controller")
    return "http://" + address, secret


def api(base, secret, method, path, payload=None, timeout=8):
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
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=timeout) as response:
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


def status(profile="auto", compact=False):
    try:
        path = selected_config(profile)
    except (FileNotFoundError, PermissionError) as exc:
        return {"installed": False, "running": False, "controller": False,
                "profile": profile, "error": str(exc)}
    text = path.read_text(encoding="utf-8-sig")
    base, secret = controller(path)
    probe = managed_probe(text)
    result = {"installed": True, "running": False, "controller": bool(base),
              "config_path": str(path), "profile": profile,
              "managed_group": probe["group"] if probe else None,
              "probe_port": probe["port"] if probe else None,
              "mode": scalar(text, "mode", "rule"),
              "mixed_port": int(scalar(text, "mixed-port", "0") or 0),
              "tun": tun_enabled(text), "system_proxy": system_proxy(),
              "rules": [], "policies": [], "error": ""}
    if not base:
        result["error"] = "配置未启用本机 HTTP Controller；请设置 external-controller: 127.0.0.1:9097"
        return result
    try:
        version = api(base, secret, "GET", "/version", timeout=3)
        proxies = api(base, secret, "GET", "/proxies", timeout=3)
        config = {} if compact else api(base, secret, "GET", "/configs")
        rules = {} if compact else api(base, secret, "GET", "/rules")
        proxy_values = proxies.get("proxies") or {}
        groups = []
        for name, value in proxy_values.items():
            choices = value.get("all") if isinstance(value, dict) else None
            if isinstance(choices, list) and choices:
                groups.append({"name": name, "type": value.get("type", ""),
                               "now": value.get("now", ""), "all": choices[:500],
                               "leaf_names": [choice for choice in choices[:500]
                                              if choice not in ("DIRECT", "REJECT") and
                                              foreign_candidate(choice) and
                                              isinstance(proxy_values.get(choice), dict) and
                                              not (proxy_values.get(choice) or {}).get("all")]})
        result.update(running=True, version=version.get("version", ""),
                      mode=config.get("mode", result["mode"]),
                      tun=bool((config.get("tun") or {}).get("enable", result["tun"])),
                      rules=(rules.get("rules") or [])[:500],
                      policies=list(proxy_values.keys())[:500], groups=groups[:100])
        try:
            active, automatic = managed_leaf(proxy_values, probe["group"])
            result["selection_safe_candidate"] = True
            result["automatic"] = automatic
            result["active_leaf"] = active
        except (ValueError, TypeError, KeyError):
            result["selection_safe_candidate"] = False
            result["automatic"] = False
        if not result["selection_safe_candidate"]:
            result["policy_warning"] = "未绑定受管理入口，或当前选择不是具体的境外候选节点"
        else:
            result["policy_warning"] = "当前节点名称符合预筛；实际出口地区仍须单独实测"
    except Exception as exc:
        result["error"] = str(exc)[:600]
    return result


def _group_choices(base, secret, group):
    proxies = (api(base, secret, "GET", "/proxies").get("proxies") or {})
    value = proxies.get(group)
    choices = value.get("all") if isinstance(value, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("策略组不存在或没有可选节点")
    return choices, str(value.get("now", ""))


def proxy_delay(base, secret, name, timeout_ms=2500):
    timeout_ms = max(500, min(int(timeout_ms), 5000))
    query = urllib.parse.urlencode({"url": DELAY_URL, "timeout": timeout_ms})
    path = "/proxies/" + urllib.parse.quote(name, safe="") + "/delay?" + query
    started = time.monotonic()
    try:
        value = api(base, secret, "GET", path,
                    timeout=timeout_ms / 1000 + 1)
        delay = int(value.get("delay", 0))
        if delay <= 0:
            raise ValueError("节点未返回有效延迟")
        return {"name": name, "reachable": True, "delay_ms": delay,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}
    except (ValueError, OSError, urllib.error.URLError) as exc:
        return {"name": name, "reachable": False, "delay_ms": None,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "error": type(exc).__name__}


def proxy_test(payload):
    path = selected_config(str(payload.get("profile", "auto")))
    base, secret = controller(path)
    if not base:
        raise ValueError("代理未开放仅本机可访问的 Controller")
    group = str(payload.get("group", "")).strip()
    choices, selected = _group_choices(base, secret, group)
    names = payload.get("names")
    if names is None:
        names = [str(payload.get("name", "")).strip()]
    probe = managed_probe(path.read_text(encoding="utf-8-sig"))
    proxy_values = api(base, secret, "GET", "/proxies").get("proxies") or {}
    auto_choices = ((proxy_values.get(AUTO_GROUP) or {}).get("all") or []
                    if probe and probe["group"] == group else [])
    allowed = set(choices) | set(auto_choices)
    if not isinstance(names, list) or not 1 <= len(names) <= 12 or any(
            not isinstance(name, str) or name not in allowed for name in names):
        raise ValueError("只能测试当前策略组中最多 12 个节点")
    names = list(dict.fromkeys(names))
    if any(not foreign_candidate(name) for name in names):
        raise ValueError("只能测试具有明确境外国家标识的节点；CN/HK/MO/TW 和未知地区已排除")
    if any(isinstance((proxy_values.get(name) or {}).get("all"), list) and
           (proxy_values.get(name) or {}).get("all") for name in names):
        raise ValueError("只能测试具体节点，不能使用可能包含国内节点的自动组")
    with ThreadPoolExecutor(max_workers=min(6, len(names))) as pool:
        results = list(pool.map(lambda name: proxy_delay(base, secret, name), names))
    return {"group": group, "selected": selected, "results": results,
            "kind": "mihomo_node_delay", "probe_url": DELAY_URL,
            "tested_at": int(time.time())}


def proxy_egress(profile="auto", expected_group=None, expected_name=None):
    path = selected_config(profile)
    text = path.read_text(encoding="utf-8-sig")
    probe = managed_probe(text)
    if not probe:
        raise ValueError("缺少绑定实际策略组的本机管理探测入口，不能证明所选节点的出口地区")
    if expected_group and probe["group"] != expected_group:
        raise ValueError("所选策略组不是借网出口使用的受管理策略组")
    base, secret = controller(path)
    if not base:
        raise ValueError("无法读取受管理策略组的当前节点")
    def selected_leaf():
        values = (api(base, secret, "GET", "/proxies", timeout=3).get("proxies") or {})
        name, _automatic = managed_leaf(values, probe["group"])
        if expected_name and name != expected_name:
            raise ValueError("受管理出口当前节点与待验证节点不一致")
        return name
    selected = selected_leaf()
    # Explicit proxying prevents NO_PROXY from bypassing the bound listener.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    failure = "unverified"
    for url, format_name in GEO_TARGETS:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                                       "User-Agent": "LanBridge-egress-check/1"})
        request.set_proxy(f"127.0.0.1:{probe['port']}", "http")
        try:
            with opener.open(request, timeout=4) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
            if format_name == "trace":
                value = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
                country = str(value.get("loc", "")).upper()
            else:
                value = json.loads(body)
                country = str(value.get("country_code" if format_name == "geoip" else "country", "")).upper()
            parsed_ip = ipaddress.ip_address(str(value.get("ip", "")))
            if not parsed_ip.is_global:
                raise ValueError("出口返回的不是公网 IP")
            if not re.fullmatch(r"[A-Z]{2}", country):
                raise ValueError("出口地区无法验证")
            if selected_leaf() != selected:
                raise ValueError("地区验证期间节点发生变化")
            return {"reachable": True, "public_ip": str(parsed_ip), "country_code": country,
                    "policy_allowed": country not in BLOCKED_COUNTRIES,
                    "group": probe["group"], "node": selected,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "tested_at": int(time.time()), "kind": "server_proxy_public_ip_and_country",
                    "provider": urllib.parse.urlsplit(url).hostname,
                    "transport_verified": True}
        except (ValueError, OSError, urllib.error.URLError) as exc:
            failure = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
    return {"reachable": False, "public_ip": None,
            "country_code": None, "policy_allowed": False,
            "group": probe["group"], "node": selected,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "tested_at": int(time.time()), "kind": "server_proxy_public_ip_and_country",
            "error": failure}


def proxy_guard(profile):
    """Stop a managed selector whenever its current public exit is unverified."""
    path = selected_config(profile)
    probe = managed_probe(path.read_text(encoding="utf-8-sig"))
    if not probe:
        raise ValueError("缺少受管理探测入口")
    base, secret = controller(path)
    choices, current = _group_choices(base, secret, probe["group"])
    if "REJECT" not in choices:
        raise ValueError("受管理策略组缺少 REJECT")
    if current == "REJECT":
        return {"group": probe["group"], "selected": "REJECT", "safe": True,
                "reason": "already_disabled"}
    try:
        proof = proxy_egress(profile, probe["group"])
        safe = bool(proof["reachable"] and proof["policy_allowed"] and
                    proof.get("country_code") not in BLOCKED_COUNTRIES)
    except Exception:
        safe = False
        proof = {"error": "egress_verification_failed"}
    if not safe:
        group_path = "/proxies/" + urllib.parse.quote(probe["group"], safe="")
        api(base, secret, "PUT", group_path, {"name": "REJECT"})
        _, confirmed = _group_choices(base, secret, probe["group"])
        if confirmed != "REJECT":
            raise RuntimeError("代理出口安全停用未获 Controller 确认")
        return {"group": probe["group"], "selected": "REJECT", "safe": True,
                "reason": "egress_verification_failed", "proof": proof}
    return {"group": probe["group"], "selected": proof["node"],
            "safe": True, "reason": "foreign_egress_verified", "proof": proof}


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
    profile = str(payload.get("profile", "auto"))
    path = selected_config(profile)
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
        choices, before = _group_choices(base, secret, group)
        automatic = payload.get("automatic") is True
        if not group or (not automatic and not name):
            raise ValueError("策略组或节点不存在")
        if "REJECT" not in choices:
            raise ValueError("受管理策略组缺少 REJECT 安全停用项；请先更新服务器代理配置")
        probe = managed_probe(text)
        if not probe or probe["group"] != group:
            raise ValueError("只能切换绑定借网出口和地区探测入口的受管理策略组")
        proxy_values = api(base, secret, "GET", "/proxies").get("proxies") or {}
        if (proxy_values.get(group) or {}).get("type") != "Selector":
            raise ValueError("受管理策略组必须是安全选择组")
        if AUTO_GROUP not in choices:
            raise ValueError("受管理策略组缺少境外自动组")
        leaf_names = [choice for choice in choices if choice not in ("REJECT", AUTO_GROUP)]
        if any(not foreign_candidate(choice) or
               not isinstance(proxy_values.get(choice), dict) or
               (proxy_values.get(choice) or {}).get("all") for choice in leaf_names):
            raise ValueError("手动候选包含 CN/HK/MO/TW、未知地区或嵌套组；请先修正服务器配置")
        auto_state = proxy_values.get(AUTO_GROUP) or {}
        auto_choices = auto_state.get("all") or []
        if auto_state.get("type") != "URLTest" or not auto_choices or any(
                not foreign_candidate(choice) or
                not isinstance(proxy_values.get(choice), dict) or
                (proxy_values.get(choice) or {}).get("all") for choice in auto_choices):
            raise ValueError("自动候选包含 CN/HK/MO/TW、未知地区或嵌套组；请先修正服务器配置")
        manual_auto = not automatic and name not in choices and name in auto_choices
        if not automatic and name not in choices and not manual_auto:
            raise ValueError("策略组或节点不存在")
        if not automatic and not foreign_candidate(name):
            raise ValueError("策略禁止选择 CN/HK/MO/TW 或地区未知的节点")
        group_path = "/proxies/" + urllib.parse.quote(group, safe="")
        def disable_unsafe_egress():
            api(base, secret, "PUT", group_path, {"name": "REJECT"})
            _, confirmed = _group_choices(base, secret, group)
            if confirmed != "REJECT":
                raise RuntimeError("代理出口安全停用未获 Controller 确认")
        if before not in ("REJECT", AUTO_GROUP) and not foreign_candidate(before):
            disable_unsafe_egress()
            before = "REJECT"
        delay = None
        if not automatic:
            delay = proxy_delay(base, secret, name)
            if not delay["reachable"]:
                raise ValueError("目标节点延迟检测失败，未切换正在使用的节点")
        try:
            if automatic:
                auto_path = "/proxies/" + urllib.parse.quote(AUTO_GROUP, safe="")
                api(base, secret, "DELETE", auto_path)
                if api(base, secret, "GET", auto_path).get("fixed"):
                    raise ValueError("Controller 未清除自动组固定节点")
            elif manual_auto:
                auto_path = "/proxies/" + urllib.parse.quote(AUTO_GROUP, safe="")
                api(base, secret, "PUT", auto_path, {"name": name})
                if api(base, secret, "GET", auto_path).get("now") != name:
                    raise ValueError("Controller 未确认具体节点固定")
            outer_target = AUTO_GROUP if automatic or manual_auto else name
            api(base, secret, "PUT", group_path, {"name": outer_target})
            _, current = _group_choices(base, secret, group)
            if current != outer_target:
                raise ValueError("Controller 未确认节点切换")
            egress = proxy_egress(profile, group, None if automatic else name)
            if (not egress["reachable"] or not egress["policy_allowed"] or
                    egress.get("country_code") in BLOCKED_COUNTRIES or
                    not egress.get("country_code")):
                raise ValueError("目标节点出口地区不可验证或位于 CN/HK/MO/TW；" +
                                 str(egress.get("error") or egress.get("country_code") or "unknown")[:60])
        except Exception as failure:
            try:
                disable_unsafe_egress()
            except Exception as exc:
                raise RuntimeError("节点地区验证失败且安全停用失败；请立即人工检查当前节点") from exc
            raise ValueError("节点切换未通过境外地区验证，代理出口已安全停用：" +
                             str(failure)[:120]) from failure
        return {"group": group, "previous": before, "selected": egress["node"],
                "automatic": automatic,
                "changed": True, "delay": delay, "egress": egress,
                "status": status(profile, compact=True)}
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
    result = status(profile)
    result["backup"] = str(saved)
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        guard_mode = (Path(sys.argv[0]).name == "sna-proxy-agent" and
                      len(sys.argv) == 3 and sys.argv[1] == "--guard")
        payload = ({"action": "proxy_guard", "profile": sys.argv[2]}
                   if guard_mode else json.loads(sys.stdin.read() or "{}"))
        action = payload.get("action")
        profile = str(payload.get("profile", "auto"))
        if Path(sys.argv[0]).name == "sna-proxy-agent":
            if action not in ("status", "proxy_status", "proxy_test", "proxy_egress", "proxy_select", "proxy_guard"):
                raise ValueError("受限代理不允许修改代理模式、规则或系统接管设置")
            if profile not in ("system_mihomo", "cloud_mihomo"):
                raise ValueError("受限代理仅允许管理系统 Mihomo 配置")
        if action in ("status", "proxy_status"):
            result = status(profile, compact=action == "proxy_status")
        elif action == "proxy_test":
            result = proxy_test(payload)
        elif action == "proxy_egress":
            result = proxy_egress(profile)
        elif action == "proxy_guard":
            result = proxy_guard(profile)
        else:
            result = apply(payload)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:1000]}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
