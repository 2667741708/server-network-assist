"""Reversible current-user proxy preferences (Windows WinINET / GNOME).

Backups are essential recovery state, unlike optional diagnostic logs. A backup
failure stops a settings change. No environment file or WinHTTP setting is edited.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import secrets
import time
from pathlib import Path

REGISTRY = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
VALUES = ('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL')
GNOME = ('mode', 'autoconfig-url', 'ignore-hosts', 'use-same-proxy')


def validate_server(server):
    server = str(server).strip()
    if not server or len(server) > 1024:
        raise ValueError('请输入代理地址，如 127.0.0.1:7897')
    # WinINET per-protocol syntax supported; no embedded credentials, PAC URL,
    # command fragments or URL path are accepted as a manual proxy endpoint.
    for endpoint in server.split(';'):
        value = endpoint
        if '=' in endpoint:
            protocol, value = endpoint.split('=', 1)
            if protocol not in ('http', 'https', 'socks'):
                raise ValueError('代理协议仅支持 http、https、socks')
        if not re.fullmatch(r'(?:[a-zA-Z0-9][a-zA-Z0-9.-]*|\[[0-9a-fA-F:]+\]):[0-9]{1,5}', value):
            raise ValueError('代理格式应为 主机:端口，不含密码或 URL 路径')
        if not 1 <= int(value.rsplit(':', 1)[1]) <= 65535:
            raise ValueError('代理端口必须为 1–65535')
    return server


class ProxySettings:
    def __init__(self, data: Path, windows: bool, run):
        self.data, self.windows, self.run = data, windows, run

    def snapshot(self):
        if self.windows:
            import winreg
            values = {}
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
                for name in VALUES:
                    with contextlib.suppress(FileNotFoundError):
                        values[name] = list(winreg.QueryValueEx(key, name))
            return {'platform': 'windows', 'values': values}
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            raise ValueError('当前进程没有 GNOME 用户会话总线；请从 Ubuntu 桌面启动面板')
        values = {}
        for name in GNOME:
            values['org.gnome.system.proxy/' + name] = self.run(['gsettings', 'get', 'org.gnome.system.proxy', name])
        for protocol in ('http', 'https', 'socks'):
            for name in ('host', 'port'):
                schema = 'org.gnome.system.proxy.' + protocol
                values[schema + '/' + name] = self.run(['gsettings', 'get', schema, name])
        return {'platform': 'gnome', 'values': values}

    def read(self):
        try:
            snap = self.snapshot()
        except Exception as exc:
            return {'supported': False, 'enabled': None, 'server': '', 'bypass': '',
                    'scope': '当前用户 GNOME 桌面代理；需要 gsettings 和桌面会话',
                    'error': str(exc)[:600]}
        values = snap['values']
        if self.windows:
            return {'supported': True, 'enabled': bool(values.get('ProxyEnable', [0])[0]),
                    'server': values.get('ProxyServer', [''])[0], 'bypass': values.get('ProxyOverride', [''])[0],
                    'pac': bool(values.get('AutoConfigURL', [''])[0]),
                    'scope': '当前 Windows 用户 WinINET；不修改 WinHTTP、PAC 或其他用户'}
        server = ';'.join(protocol + '=' + values['org.gnome.system.proxy.' + protocol + '/host'].strip("'") + ':' +
                          values['org.gnome.system.proxy.' + protocol + '/port'] for protocol in ('http', 'https', 'socks')
                          if values['org.gnome.system.proxy.' + protocol + '/host'].strip("'"))
        return {'supported': True, 'enabled': values['org.gnome.system.proxy/mode'] == "'manual'",
                'server': server, 'bypass': values['org.gnome.system.proxy/ignore-hosts'],
                'pac': values['org.gnome.system.proxy/mode'] == "'auto'",
                'scope': '当前用户 GNOME 应用；终端环境变量和系统服务代理不在此范围'}

    def backup(self, snap):
        self.data.mkdir(parents=True, exist_ok=True)
        name = 'proxy-before-' + time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4) + '.json'
        file = self.data / name
        with file.open('x', encoding='utf-8') as stream:
            json.dump({**snap, 'created_at': int(time.time())}, stream, ensure_ascii=False, indent=2)
        file.chmod(0o600)
        return name

    def backups(self):
        items = []
        for file in sorted(self.data.glob('proxy-before-*.json'), reverse=True)[:100]:
            with contextlib.suppress(OSError, ValueError, TypeError):
                snap = json.loads(file.read_text(encoding='utf-8'))
                platform = snap.get('platform', 'windows' if snap.get('key') == REGISTRY else '')
                items.append({'id': file.name, 'created_at': snap.get('created_at', int(file.stat().st_mtime)),
                              'platform': platform, 'compatible': platform == ('windows' if self.windows else 'gnome')})
        return items

    def _apply(self, snap):
        values = snap['values']
        if self.windows:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY, 0, winreg.KEY_SET_VALUE) as key:
                for name in VALUES:
                    if name in values:
                        value, kind = values[name]
                        if kind != (winreg.REG_DWORD if name == 'ProxyEnable' else winreg.REG_SZ):
                            raise ValueError('代理备份包含不支持的注册表值类型')
                        winreg.SetValueEx(key, name, 0, kind, value)
                    else:
                        with contextlib.suppress(FileNotFoundError):
                            winreg.DeleteValue(key, name)
            for option in (39, 37):
                ctypes.windll.wininet.InternetSetOptionW(None, option, None, 0)
        else:
            allowed = {'org.gnome.system.proxy/' + k for k in GNOME}
            allowed |= {'org.gnome.system.proxy.' + proto + '/' + name for proto in ('http', 'https', 'socks') for name in ('host', 'port')}
            if set(values) != allowed:
                raise ValueError('GNOME 代理备份字段不完整')
            # Mode last: only activate after all endpoints are configured.
            for item in sorted(values, key=lambda k: k.endswith('/mode')):
                schema, key = item.split('/')
                self.run(['gsettings', 'set', schema, key, values[item]])

    def _change(self, before, after):
        backup_id = self.backup(before)
        try:
            self._apply(after)
            actual = self.snapshot()
            # gsettings may exit successfully while warning that no dconf
            # service is available. Read back the saved values before success.
            if self.windows:
                verified = actual['values'] == after['values']
            else:
                import ast
                def normalized(value):
                    if value.startswith('@as '):
                        value = value[4:]
                    try:
                        return ast.literal_eval(value)
                    except (SyntaxError, ValueError):
                        return value
                verified = {k: normalized(v) for k, v in actual['values'].items()} == {k: normalized(v) for k, v in after['values'].items()}
            if not verified:
                raise ValueError('代理设置写入后复检不一致；没有将未保存的更改标为成功')
        except Exception as exc:
            try:
                self._apply(before)
                if self.snapshot()['values'] != before['values']:
                    raise ValueError('恢复后的代理配置复检不一致')
            except Exception as rollback:
                raise ValueError(f'修改失败且自动恢复失败；请用备份 {backup_id} 重试恢复：{rollback}') from exc
            raise ValueError(f'修改失败，已恢复原代理：{exc}') from exc
        return {'ok': True, 'backup_id': backup_id, 'proxy': self.read(), 'backups': self.backups()}

    def save(self, payload):
        enabled = payload.get('enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError('enabled 必须为布尔值')
        server = validate_server(payload.get('server', '')) if enabled else str(payload.get('server', '')).strip()
        bypass = str(payload.get('bypass', '')).strip()
        if len(bypass) > 4096 or any(c in bypass for c in '\r\n\x00'):
            raise ValueError('代理绕过列表无效')
        before = self.snapshot()
        after = json.loads(json.dumps(before))
        values = after['values']
        if self.windows:
            values['ProxyEnable'] = [int(enabled), 4]
            if enabled:
                values['ProxyServer'] = [server, 1]
                values['ProxyOverride'] = [bypass, 1]
        else:
            if enabled:
                entries = dict(item.split('=', 1) for item in server.split(';')) if '=' in server else {'http': server, 'https': server}
                for protocol in ('http', 'https', 'socks'):
                    endpoint = entries.get(protocol, '')
                    host, port = endpoint.rsplit(':', 1) if endpoint else ('', '0')
                    values[f'org.gnome.system.proxy.{protocol}/host'] = "'" + host.strip('[]') + "'"
                    values[f'org.gnome.system.proxy.{protocol}/port'] = port
                values['org.gnome.system.proxy/use-same-proxy'] = 'false'
                if 'bypass' in payload:
                    import ast
                    try:
                        ignored = ast.literal_eval(bypass) if bypass.startswith('[') else [v for v in re.split(r'[;, ]+', bypass) if v]
                    except (ValueError, SyntaxError) as exc:
                        raise ValueError('绕过列表请用分号分隔主机，或 GNOME 字符串列表') from exc
                    if not isinstance(ignored, list) or not all(isinstance(item, str) for item in ignored):
                        raise ValueError('绕过列表必须为字符串列表')
                    values['org.gnome.system.proxy/ignore-hosts'] = repr(ignored)
            values['org.gnome.system.proxy/mode'] = "'manual'" if enabled else "'none'"
        return self._change(before, after)

    def restore(self, backup_id):
        if not re.fullmatch(r'proxy-before-[A-Za-z0-9-]+\.json', str(backup_id)):
            raise ValueError('备份标识无效')
        target = self.data / backup_id
        if target.is_symlink() or not target.is_file() or target.stat().st_size > 32768:
            raise ValueError('备份文件不可用')
        snap = json.loads(target.read_text(encoding='utf-8'))
        platform = snap.get('platform', 'windows' if snap.get('key') == REGISTRY else '')
        if platform != ('windows' if self.windows else 'gnome') or not isinstance(snap.get('values'), dict):
            raise ValueError('备份系统类型与当前系统不匹配')
        return self._change(self.snapshot(), snap)
