from __future__ import annotations

import re
import subprocess
from pathlib import Path


SAFE_ALIAS = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def _ssh_values(config: Path, alias: str) -> dict[str, list[str]]:
    if not SAFE_ALIAS.fullmatch(alias):
        raise ValueError(f"SSH 别名无效：{alias}")
    result = subprocess.run(
        ["ssh", "-G", "-F", str(config), "--", alias],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"无法读取 SSH 配置：{alias}")
    values: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if separator:
            values.setdefault(key.lower(), []).append(value.strip())
    return values


def _path(value: str) -> Path:
    value = value.replace("%d", str(Path.home()))
    return Path(value).expanduser()


def _private_key(values: dict[str, list[str]]) -> Path:
    for value in values.get("identityfile", []):
        path = _path(value)
        if not path.is_file():
            continue
        try:
            beginning = path.read_text(encoding="utf-8")[:200]
        except (OSError, UnicodeError):
            continue
        if "PRIVATE KEY" in beginning:
            return path
    raise ValueError("未找到可读取的 SSH 私钥文件")


def _host_key(values: dict[str, list[str]], fallback_known_hosts: Path | None) -> str:
    hostname = values.get("hostname", [""])[0]
    port = int(values.get("port", ["22"])[0])
    lookup = values.get("hostkeyalias", [hostname])[0]
    if port != 22 and lookup == hostname:
        lookup = f"[{hostname}]:{port}"
    files = [_path(item) for value in values.get("userknownhostsfile", []) for item in value.split()]
    if fallback_known_hosts:
        files.append(fallback_known_hosts.expanduser())
    candidates: list[str] = []
    for path in dict.fromkeys(files):
        if not path.is_file():
            continue
        result = subprocess.run(
            ["ssh-keygen", "-F", lookup, "-f", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        for line in result.stdout.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[-2].startswith(("ssh-", "ecdsa-")):
                candidates.append(f"{parts[-2]} {parts[-1]}")
    if not candidates:
        raise ValueError(f"known_hosts 中没有 {lookup} 的已核验主机密钥")
    order = {"ssh-ed25519": 0, "ecdsa-sha2-nistp256": 1, "ssh-rsa": 2}
    return min(candidates, key=lambda value: order.get(value.split()[0], 9))


def _host_id(alias: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]", "-", alias).strip("-")[:64]
    if not value:
        raise ValueError(f"无法从 SSH 别名生成主机标识：{alias}")
    return value


def import_aliases(state, config: Path, aliases: list[str], group: str = "SSH 组网",
                   fallback_known_hosts: Path | None = None) -> list[dict]:
    """Import only explicitly requested OpenSSH aliases into the encrypted panel vault."""
    config = config.expanduser().resolve()
    if not config.is_file():
        raise ValueError(f"SSH 配置不存在：{config}")
    requested = list(dict.fromkeys(aliases))
    if not requested or len(requested) > 64:
        raise ValueError("请明确提供 1–64 个 SSH 别名")
    resolved: dict[str, dict[str, list[str]]] = {}

    def visit(alias: str) -> None:
        if alias in resolved:
            return
        values = _ssh_values(config, alias)
        jump = values.get("proxyjump", ["none"])[0]
        if jump not in ("none", ""):
            if "," in jump or "@" in jump or ":" in jump:
                raise ValueError(f"{alias}：当前导入器只接受配置中以别名表示的单一 ProxyJump")
            visit(jump)
        resolved[alias] = values

    for alias in requested:
        visit(alias)

    credential_by_path: dict[Path, str] = {}
    existing_credentials = {item["name"]: item["id"] for item in state.credentials()}
    imported: dict[str, dict] = {}
    for alias, values in resolved.items():
        key_path = _private_key(values).resolve()
        credential_name = f"SSH key · {key_path.name}"
        credential_id = existing_credentials.get(credential_name)
        if not credential_id:
            credential_id = credential_by_path.get(key_path)
        if not credential_id:
            credential_id = state.save_credential({
                "name": credential_name,
                "kind": "key",
                "secret": key_path.read_text(encoding="utf-8"),
                "passphrase": "",
            })
            credential_by_path[key_path] = credential_id
            existing_credentials[credential_name] = credential_id
        jump = values.get("proxyjump", ["none"])[0]
        host = state.save_host({
            "id": _host_id(alias),
            "name": alias,
            "address": values.get("hostname", [alias])[0],
            "port": int(values.get("port", ["22"])[0]),
            "username": values.get("user", [""])[0],
            "credential_id": credential_id,
            "jump_id": "" if jump in ("none", "") else _host_id(jump),
            "host_key": _host_key(values, fallback_known_hosts),
            "group": group,
            "favorite": alias in requested,
            "terminal_enabled": True,
            "codex_enabled": True,
            "browser_enabled": True,
            "codex_workspace": "",
        })
        imported[alias] = host
    return [imported[alias] for alias in requested]
