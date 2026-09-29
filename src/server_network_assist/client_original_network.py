"""The customer's WinINET settings, captured before entering borrowing."""
import json
import os
from pathlib import Path

STATE = 'customer-original-network.json'
KEY = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
FIELDS = ('ProxyEnable', 'ProxyServer', 'ProxyOverride', 'AutoConfigURL')


def capture(data):
    path = Path(data) / STATE
    if os.name != 'nt' or path.exists():
        return
    import winreg
    values = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        for name in FIELDS:
            try:
                value, kind = winreg.QueryValueEx(key, name)
                values[name] = {'value': value, 'kind': kind}
            except FileNotFoundError:
                values[name] = None
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'schema_version': 2, 'original': values, 'owned': {}}), encoding='utf-8')
    temporary.replace(path)


def restore(data):
    path = Path(data) / STATE
    if os.name != 'nt' or not path.exists():
        return
    import ctypes
    import winreg
    state = json.loads(path.read_text(encoding='utf-8'))
    # Old snapshots did not prove which settings the client changed.
    # Do not overwrite user changes based on an unowned legacy snapshot.
    values = state.get('original', {})
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        for name, owned in state.get('owned', {}).items():
            if name not in FIELDS:
                raise ValueError('原网络恢复记录包含非客户管理字段')
            try:
                value, kind = winreg.QueryValueEx(key, name)
                current = {'value': value, 'kind': kind}
            except FileNotFoundError:
                current = None
            if current != owned:
                continue
            item = values[name]
            if item is None:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, name, 0, item['kind'], item['value'])
    internet = ctypes.windll.wininet
    internet.InternetSetOptionW(None, 39, None, 0)
    internet.InternetSetOptionW(None, 37, None, 0)
    path.unlink()


def campus_bypass(data):
    """Add only a temporary WinINET bypass, leaving enable/server/PAC untouched."""
    if os.name != 'nt':
        return
    import winreg
    import ctypes
    capture(data)
    path = Path(data) / STATE
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('schema_version') != 2:
        state = {'schema_version': 2, 'original': state, 'owned': {}}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        try:
            old, kind = winreg.QueryValueEx(key, 'ProxyOverride')
        except FileNotFoundError:
            old, kind = '', winreg.REG_SZ
        entries = [item for item in old.split(';') if item]
        for item in ('auth1.ysu.edu.cn', '*.ysu.edu.cn', '124.124.124.124', '10.*'):
            if item not in entries:
                entries.append(item)
        value = ';'.join(entries)
        state['owned']['ProxyOverride'] = {'value': value, 'kind': kind}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state), encoding='utf-8')
        temporary.replace(path)
        winreg.SetValueEx(key, 'ProxyOverride', 0, kind, value)
    ctypes.windll.wininet.InternetSetOptionW(None, 39, None, 0)
    ctypes.windll.wininet.InternetSetOptionW(None, 37, None, 0)


def enable_explicit_proxy(data, port):
    """Own a loopback WinINET proxy and preserve the exact previous values."""
    if os.name != 'nt':
        return
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('本地代理端口无效')
    import ctypes
    import winreg
    capture(data)
    path = Path(data) / STATE
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('schema_version') != 2:
        raise ValueError('原网络恢复记录版本过旧，不能安全启用系统代理')
    desired = {
        'ProxyEnable': {'value': 1, 'kind': winreg.REG_DWORD},
        'ProxyServer': {'value': f'127.0.0.1:{port}', 'kind': winreg.REG_SZ},
        'ProxyOverride': {'value': '<local>;localhost;127.*;10.*;auth1.ysu.edu.cn;*.ysu.edu.cn;124.124.124.124',
                          'kind': winreg.REG_SZ},
        'AutoConfigURL': {'value': '', 'kind': winreg.REG_SZ},
    }
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
        for name, owned in state.get('owned', {}).items():
            try:
                value, kind = winreg.QueryValueEx(key, name)
                current = {'value': value, 'kind': kind}
            except FileNotFoundError:
                current = None
            if current != owned:
                raise ValueError('检测到系统代理已被其他程序修改，保持用户的新配置不接管')
        state['owned'].update(desired)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
        for name, item in desired.items():
            winreg.SetValueEx(key, name, 0, item['kind'], item['value'])
    internet = ctypes.windll.wininet
    internet.InternetSetOptionW(None, 39, None, 0)
    internet.InternetSetOptionW(None, 37, None, 0)
