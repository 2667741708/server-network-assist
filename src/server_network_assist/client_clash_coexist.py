"""Own only the local Windows Mihomo fields required for a leased underlay.

Reuse the customer's installed Clash core and subscription. Never store its secret.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
from pathlib import Path
import time
import warnings

from . import clash_control

STATE = 'customer-clash-coexist.json'
MISSING = {'__sna_absent__': True}


def controller():
    paths = clash_control.candidates()
    if not paths:
        return None
    path = paths[0]
    base, secret = clash_control.controller(path)
    if not base:
        return None
    config = clash_control.api(base, secret, 'GET', '/configs')
    return path, base, secret, config


def managed_fields(document, tunnel, public_routes, direct_cidrs=()):
    tun = document.get('tun') or {}
    excluded = list(tun.get('route-exclude-address') or [])
    for network in ['10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
                    '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16',
                    '222.30.148.40/32', '124.124.124.124/32', '202.206.240.0/24', *direct_cidrs]:
        # Fake IPs belong to this local Clash, even if excluded from WireGuard.
        if ipaddress.ip_network(network).overlaps(ipaddress.ip_network('198.18.0.0/15')):
            continue
        if network not in excluded:
            excluded.append(network)
    dns = document.get('dns') or {}
    routes = list(public_routes)
    if dns.get('enhanced-mode') == 'fake-ip':
        fake = str(ipaddress.ip_network(dns.get('fake-ip-range', '198.18.0.1/16'), strict=False))
        if fake not in routes:
            routes.append(fake)
    return {
        'interface-name': tunnel,
        'tun.auto-detect-interface': False,
        'tun.route-address': routes,
        'tun.route-exclude-address': excluded,
        'dns.default-nameserver': ['tls://223.5.5.5', 'tls://1.12.12.12'],
        'dns.nameserver': ['tls://223.5.5.5', 'tls://1.12.12.12'],
        'dns.fallback': ['tls://1.1.1.1', 'tls://8.8.8.8'],
    }


def get(document, key):
    value = document
    for part in key.split('.'):
        if not isinstance(value, dict) or part not in value:
            return copy.deepcopy(MISSING)
        value = value[part]
    return copy.deepcopy(value)


def put(document, key, value):
    parts = key.split('.')
    target = document
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    if value == MISSING:
        target.pop(parts[-1], None)
    else:
        target[parts[-1]] = copy.deepcopy(value)


def write_private(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)


def write_config(path, text):
    temporary = path.with_name(path.name + '.sna-tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def reload_runtime(path, base, secret):
    import urllib.parse
    groups = clash_control.api(base, secret, 'GET', '/proxies').get('proxies', {})
    selected = {name: row['now'] for name, row in groups.items()
                if isinstance(row, dict) and row.get('type') == 'Selector' and row.get('now')}
    clash_control.api(base, secret, 'PUT', '/configs?force=true', {'path': str(path)})
    for name, choice in selected.items():
        clash_control.api(base, secret, 'PUT', '/proxies/' + urllib.parse.quote(name, safe=''), {'name': choice})


def ensure(data, tunnel, routes, direct_cidrs=()):
    """Prepare both proxy and TUN even while TUN is off; retain user's mode/enable."""
    import yaml
    found = controller()
    if not found:
        return {'supported': False, 'reason': '未发现本机 Clash Controller'}
    path, base, secret, runtime = found
    document = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    state_path = Path(data) / STATE
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else None
    if state and (state['tunnel'] != tunnel or state['path'] != str(path)):
        raise ValueError('请先退出旧节点并恢复它的 Clash 兼容配置')
    if state and any(get(document, key) != value for key, value in state['owned'].items()):
        raise ValueError('检测到用户修改代理兼容字段，将退出借网并保留用户的新配置')
    values = managed_fields(document, tunnel, routes, direct_cidrs)
    # Keep private exceptions added by the user's existing profile and local policy.
    if state:
        values['tun.route-exclude-address'] = list(dict.fromkeys(
            state['owned']['tun.route-exclude-address'] + values['tun.route-exclude-address']))
    if state and all(get(document, key) == value for key, value in values.items()):
        return {'supported': True, 'configured': True, 'tun': bool((runtime.get('tun') or {}).get('enable'))}
    original_text = path.read_text(encoding='utf-8-sig')
    if not state:
        state = {'path': str(path), 'tunnel': tunnel,
                 'original': {key: get(document, key) for key in values}, 'owned': values}
        # Persist ownership before changing the core, so a process crash can restore it.
        write_private(state_path, state)
    else:
        state['owned'] = values
        write_private(state_path, state)
    for key, value in values.items():
        put(document, key, value)
    # Runtime can differ from the generated file after a UI/API toggle.
    document.setdefault('tun', {})['enable'] = bool((runtime.get('tun') or {}).get('enable'))
    if runtime.get('mode') is not None:
        document['mode'] = runtime['mode']
    backup = path.with_name(path.name + '.sna-coexist-' + time.strftime('%Y%m%d-%H%M%S'))
    if not backup.exists():
        backup.write_text(original_text, encoding='utf-8')
    write_config(path, yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    try:
        reload_runtime(path, base, secret)
    except Exception:
        write_config(path, original_text)
        try:
            reload_runtime(path, base, secret)
        except Exception:
            pass
        raise
    return {'supported': True, 'configured': True, 'tun': document['tun']['enable']}


def restore(data, tunnel):
    """Restore only owned fields which have not been changed by the customer."""
    state_path = Path(data) / STATE
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if state['tunnel'] != tunnel:
        return
    import yaml
    path = Path(state['path'])
    if path not in clash_control.candidates():
        raise ValueError('Clash 配置不在本机已知位置')
    document = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    base, secret = clash_control.controller(path)
    runtime = None
    if base:
        try:
            runtime = clash_control.api(base, secret, 'GET', '/configs')
        except Exception:
            # Exiting borrowing must also work when the customer has closed Clash.
            pass
    for key, owned in state['owned'].items():
        if get(document, key) == owned:
            put(document, key, state['original'][key])
    # Mode, TUN enable and node choices belong to the user's own Clash.
    if runtime:
        document.setdefault('tun', {})['enable'] = bool((runtime.get('tun') or {}).get('enable'))
        if runtime.get('mode') is not None:
            document['mode'] = runtime['mode']
    write_config(path, yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    if base and runtime:
        try:
            reload_runtime(path, base, secret)
        except Exception as exc:
            raise RuntimeError('Clash 配置文件已恢复，但运行中的内核未加载；恢复记录已保留，请重试退出') from exc
    state_path.unlink()


def is_clash_route(alias):
    found = controller()
    if not found or not (found[3].get('tun') or {}).get('enable'):
        return False
    device = str(found[3]['tun'].get('device') or 'Mihomo')
    return alias in {device, 'Mihomo', 'Meta'}
