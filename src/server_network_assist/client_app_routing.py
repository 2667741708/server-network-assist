"""Temporary process rules in the customer's existing Mihomo, removed on leave."""
import json
from pathlib import Path
import re

from . import clash_control

STATE = 'customer-clash-app-rules.json'


def context():
    paths = clash_control.candidates()
    if not paths:
        raise ValueError('请先启动本机 Clash Verge')
    path = paths[0]
    base, secret = clash_control.controller(path)
    runtime = clash_control.api(base, secret, 'GET', '/configs')
    return path, base, secret, runtime


def policies():
    _, base, secret, _ = context()
    proxies = clash_control.api(base, secret, 'GET', '/proxies')['proxies']
    return ['DIRECT'] + [k for k in proxies if k not in ('DIRECT', 'REJECT', 'GLOBAL')]


def apply(data, process, policy):
    import yaml
    if not re.fullmatch(r'[A-Za-z0-9_. -]{1,100}\.exe', process) or ',' in policy or '\n' in policy:
        raise ValueError('填写应用文件名，例如 Code.exe 或 curl.exe')
    if policy not in policies():
        raise ValueError('选择本机 Clash 中已有的策略或节点')
    path, base, secret, runtime = context()
    document = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    target = Path(data) / STATE
    state = json.loads(target.read_text()) if target.exists() else {
        'path': str(path), 'rules': [], 'original_find': document.get('find-process-mode')}
    if state['path'] != str(path):
        raise ValueError('请先退出组网恢复旧配置')
    rule = 'PROCESS-NAME,' + process + ',' + policy
    rules = document.setdefault('rules', [])
    # Never claim or remove a rule which the customer already had.
    if rule not in rules:
        rules.insert(0, rule)
        state['rules'].append(rule)
    document['find-process-mode'] = 'always'
    document['mode'] = runtime['mode']
    document.setdefault('tun', {})['enable'] = runtime['tun']['enable']
    from .client_clash_coexist import write_private, write_config
    write_private(target, state)
    write_config(path, yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    from .client_clash_coexist import reload_runtime
    reload_runtime(path, base, secret)
    return {'ok': True, 'rule': rule}


def restore(data):
    import yaml
    target = Path(data) / STATE
    if not target.exists():
        return
    state = json.loads(target.read_text())
    path = Path(state['path'])
    if path not in clash_control.candidates():
        raise ValueError('应用规则配置位置不属于当前用户')
    document = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
    for rule in state['rules']:
        if rule in document.get('rules', []):
            document['rules'].remove(rule)
    if document.get('find-process-mode') == 'always':
        if state['original_find'] is None:
            document.pop('find-process-mode', None)
        else:
            document['find-process-mode'] = state['original_find']
    base, secret = clash_control.controller(path)
    try:
        runtime = clash_control.api(base, secret, 'GET', '/configs')
    except Exception:
        runtime = None
    if runtime:
        document['mode'] = runtime['mode']
        document.setdefault('tun', {})['enable'] = runtime['tun']['enable']
    from .client_clash_coexist import write_config
    write_config(path, yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    if runtime:
        from .client_clash_coexist import reload_runtime
        reload_runtime(path, base, secret)
    target.unlink()
