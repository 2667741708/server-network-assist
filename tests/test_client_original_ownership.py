import ctypes
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import pytest
from server_network_assist import client_original_network as original


@pytest.fixture
def registry(monkeypatch):
    values = {'ProxyEnable': (0, 4), 'ProxyServer': ('old:7897', 1), 'ProxyOverride': ('<local>', 1)}
    key = MagicMock(); key.__enter__.return_value = key
    def query(k, name):
        if name not in values: raise FileNotFoundError(name)
        return values[name]
    def delete(k, name):
        if name not in values: raise FileNotFoundError(name)
        del values[name]
    fake = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, KEY_QUERY_VALUE=4, REG_SZ=1,
        OpenKey=Mock(return_value=key), QueryValueEx=query, DeleteValue=delete,
        SetValueEx=lambda k,n,z,t,v: values.__setitem__(n,(v,t)))
    monkeypatch.setitem(sys.modules, 'winreg', fake)
    monkeypatch.setattr(original, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(wininet=Mock()), raising=False)
    return values


def test_leave_preserves_user_proxy_server_enable_and_pac(tmp_path, registry):
    original.campus_bypass(tmp_path)
    assert 'auth1.ysu.edu.cn' in registry['ProxyOverride'][0]
    registry['ProxyEnable'] = (1,4)
    registry['ProxyServer'] = ('user-new:8899',1)
    registry['AutoConfigURL'] = ('https://user.example/pac',1)
    original.restore(tmp_path)
    assert registry['ProxyOverride'] == ('<local>',1)
    assert registry['ProxyEnable'] == (1,4)
    assert registry['ProxyServer'] == ('user-new:8899',1)
    assert registry['AutoConfigURL'] == ('https://user.example/pac',1)


def test_leave_preserves_user_modified_bypass(tmp_path, registry):
    original.campus_bypass(tmp_path)
    registry['ProxyOverride'] = ('user.example;<local>',1)
    original.restore(tmp_path)
    assert registry['ProxyOverride'] == ('user.example;<local>',1)


def test_snapshot_alone_claims_no_registry_settings(tmp_path, registry):
    original.capture(tmp_path)
    registry['ProxyEnable'] = (1,4)
    original.restore(tmp_path)
    assert registry['ProxyEnable'] == (1,4)
