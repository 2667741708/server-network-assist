from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .app import (State, clash_remote, network_disable_profile,
                  network_enable_profile, network_install_helper,
                  network_probe_host)


def resolve(values, token, kind):
    matches = [value for value in values if value.get("id") == token or value.get("name") == token]
    if len(matches) != 1:
        raise ValueError(f"找不到唯一的{kind}：{token}")
    return matches[0]


def host_roles(state, host_id):
    roles = []
    for profile in state.network.profiles():
        if profile["gateway_id"] == host_id:
            roles.append({"role": "server", "profile": profile["name"], "state": profile["state"]})
        if host_id in profile["client_ids"]:
            roles.append({"role": "client", "profile": profile["name"], "state": profile["state"]})
    return roles


def emit(value, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, list):
        for item in value:
            print("\t".join(str(item.get(key, "")) for key in ("id", "name", "role", "state", "address")))
        return
    print(json.dumps(value, ensure_ascii=False, indent=2))


async def execute(state, args):
    hosts = state.hosts()
    profiles = state.network.profiles()
    if args.command == "hosts":
        result = []
        for host in hosts:
            roles = host_roles(state, host["id"])
            summary = ", ".join(f'{item["role"]}:{item["profile"]}' for item in roles) or "unassigned"
            result.append({**host, "role": summary, "network_roles": roles})
        return result
    if args.command == "profiles":
        return profiles
    if args.command == "probe":
        selected = hosts if args.host == "all" else [resolve(hosts, args.host, "主机")]
        return await asyncio.gather(*(network_probe_host(state, host["id"]) for host in selected))
    if args.command == "helper-install":
        selected = resolve(hosts, args.host, "主机")
        return await network_install_helper(state, selected["id"])
    if args.command == "profile-create":
        gateway = resolve(hosts, args.server, "源网服务器")
        clients = [resolve(hosts, value, "客户端") for value in args.client]
        return state.network.save({
            "id": args.id, "name": args.name, "gateway_id": gateway["id"],
            "client_ids": [value["id"] for value in clients], "endpoint": args.endpoint,
            "port": args.port, "tunnel_cidr": args.tunnel_cidr,
            "preserve_routes": args.preserve_route, "maintenance": not args.no_maintenance,
            "proxy_mode": args.proxy_mode, "proxy_host": args.proxy_host,
            "proxy_port": args.proxy_port,
        })
    if args.command in ("profile-enable", "profile-disable", "profile-delete"):
        profile = resolve(profiles, args.profile, "借网方案")
        if args.command == "profile-enable":
            return await network_enable_profile(state, profile["id"])
        if args.command == "profile-disable":
            return await network_disable_profile(state, profile["id"])
        state.network.delete(profile["id"])
        state.audit("network_profile_deleted", profile["name"])
        return {"ok": True, "id": profile["id"]}
    if args.command.startswith("clash-"):
        host = resolve(hosts, args.host, "主机")
        action = args.command.removeprefix("clash-").replace("-", "_")
        payload = {"action": action}
        if action == "mode":
            payload["mode"] = args.mode
        elif action in ("tun", "system_proxy"):
            payload["enabled"] = args.value == "on"
        elif action == "proxy_select":
            payload.update(group=args.group, name=args.name)
        elif action == "rule_add":
            payload["rule"] = args.rule
        elif action == "status":
            payload["action"] = "status"
        result = await clash_remote(state, host["id"], payload)
        if action != "status":
            state.audit("clash_" + action, host["name"])
        return result
    raise ValueError("不支持的命令")


def parser():
    root = argparse.ArgumentParser(prog="server-network-assist-ctl")
    root.add_argument("--data", type=Path, default=Path("/var/lib/server-network-assist"))
    root.add_argument("--master-key", type=Path)
    root.add_argument("--json", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("hosts", help="列出主机及服务器/客户端角色")
    commands.add_parser("profiles", help="列出借网方案")
    probe = commands.add_parser("probe", help="探测一台或全部主机")
    probe.add_argument("--host", default="all", help="主机名称、ID 或 all")
    install = commands.add_parser("helper-install", help="安装远端网络辅助程序")
    install.add_argument("--host", required=True)
    create = commands.add_parser("profile-create", help="创建源网服务器到客户端的借网方案")
    create.add_argument("--id", default="")
    create.add_argument("--name", required=True)
    create.add_argument("--server", required=True, help="源网服务器名称或 ID")
    create.add_argument("--client", action="append", required=True, help="客户端名称或 ID，可重复")
    create.add_argument("--endpoint", required=True)
    create.add_argument("--port", type=int, default=51919)
    create.add_argument("--tunnel-cidr", default="")
    create.add_argument("--preserve-route", action="append", default=[])
    create.add_argument("--no-maintenance", action="store_true")
    create.add_argument("--proxy-mode", choices=("direct", "share"), default="direct")
    create.add_argument("--proxy-host", default="127.0.0.1")
    create.add_argument("--proxy-port", type=int, default=7897)
    for name in ("profile-enable", "profile-disable", "profile-delete"):
        command = commands.add_parser(name)
        command.add_argument("--profile", required=True, help="方案名称或 ID")
    clash_status = commands.add_parser("clash-status")
    clash_status.add_argument("--host", required=True)
    clash_mode = commands.add_parser("clash-mode")
    clash_mode.add_argument("--host", required=True)
    clash_mode.add_argument("--mode", choices=("rule", "global", "direct"), required=True)
    for name in ("clash-tun", "clash-system-proxy"):
        command = commands.add_parser(name)
        command.add_argument("--host", required=True)
        command.add_argument("--value", choices=("on", "off"), required=True)
    select = commands.add_parser("clash-proxy-select")
    select.add_argument("--host", required=True)
    select.add_argument("--group", required=True)
    select.add_argument("--name", required=True)
    rule = commands.add_parser("clash-rule-add")
    rule.add_argument("--host", required=True)
    rule.add_argument("--rule", required=True)
    return root


def main():
    args = parser().parse_args()
    key = args.master_key or args.data / "master.key"
    if not (args.data / "credentials.json").is_file() or not key.is_file():
        raise SystemExit("数据目录尚未初始化，或 master key 不存在")
    try:
        emit(asyncio.run(execute(State(args.data, key), args)), args.json)
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
