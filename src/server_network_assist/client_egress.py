"""Administrator-selected commercial egress, independent of the client proxy.

Only physical egress owns policy routes. The main table, source proxy settings
and management tunnels are never modified here.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re


def validate_egress(mode="source_proxy", gateway="", dns="") -> dict:
    if mode not in {"source_proxy", "physical"}:
        raise ValueError("egress_mode must be source_proxy or physical")
    gateway = str(gateway or "").strip()
    if mode == "physical":
        try:
            address = ipaddress.ip_address(gateway)
        except ValueError:
            raise ValueError("physical egress requires an IPv4 gateway") from None
        if address.version != 4 or address.is_unspecified or address.is_multicast or address.is_loopback:
            raise ValueError("invalid physical egress gateway")
        servers = [item.strip() for item in str(dns or "223.5.5.5,1.1.1.1").split(",")]
        if not 1 <= len(servers) <= 4:
            raise ValueError("physical egress requires one to four DNS servers")
        for server in servers:
            try:
                resolver = ipaddress.ip_address(server)
            except ValueError:
                raise ValueError("physical DNS must use public IPv4 addresses") from None
            if resolver.version != 4 or not resolver.is_global:
                raise ValueError("physical DNS must bypass private and fake-IP resolvers")
        dns = ",".join(servers)
    elif gateway:
        raise ValueError("source_proxy does not accept a physical gateway")
    return {"egress_mode": mode, "egress_gateway": gateway, "dns": str(dns)}


def validate_interface(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", value):
        raise ValueError("invalid egress interface")
    return value


def route_table(policy: dict) -> str:
    # Dedicated numeric range; conflicts are checked against kernel state before
    # writing anything. A lease rotation gets a new table and cleans up the old.
    return str(300000 + int.from_bytes(hashlib.sha256(policy["peer_id"].encode()).digest()[:2], "big"))


def route_commands(policy: dict, *, remove=False) -> list[list[str]]:
    if policy.get("egress_mode", "source_proxy") != "physical":
        return []
    table = route_table(policy)
    interface, gateway = policy["egress_interface"], policy["egress_gateway"]
    rule = ["ip", "-4", "rule", "del" if remove else "add", "priority", "100",
            "from", policy["address"], "iif", policy["interface"], "lookup", table]
    routes = [
        ["ip", "-4", "route", "del" if remove else "replace", "unreachable", "default",
         "table", table, "metric", "32760"],
        ["ip", "-4", "route", "del" if remove else "replace", gateway + "/32",
         "dev", interface, "scope", "link", "table", table],
        ["ip", "-4", "route", "del" if remove else "replace", "default", "via", gateway,
         "dev", interface, "table", table, "metric", "10"],
    ]
    # Install a terminal unreachable route before exposing the source rule.
    # Revoke the source rule first; never flush a whole table or main routes.
    return [rule, *reversed(routes)] if remove else [*routes, rule]
