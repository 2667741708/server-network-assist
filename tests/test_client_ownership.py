import base64
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import pytest
from server_network_assist.client_ownership import CustomerLock
from server_network_assist import client_tunnel


def test_other_thread_cannot_take_an_active_customer_owner(tmp_path):
    def contender():
        with pytest.raises(RuntimeError, match='仍在运行'):
            with CustomerLock(tmp_path, timeout=.1): pass
    with CustomerLock(tmp_path):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contender).result(timeout=3)
    with CustomerLock(tmp_path): pass


def test_devices_sharing_grant_never_share_tunnel_name():
    key = base64.b64encode(b'x'*32).decode()
    lease = {'grant_id': 'same', 'endpoint': '10.20.32.13:51910', 'relay_public_key': key,
             'allocated_address': '10.213.40.4/32', 'allowed_ips': '0.0.0.0/0'}
    a,_ = client_tunnel.configuration(lease,key)
    b,_ = client_tunnel.configuration(lease,base64.b64encode(b'y'*32).decode())
    assert a != b


def test_existing_windows_service_is_never_claimed_or_rolled_back(tmp_path, monkeypatch):
    monkeypatch.setattr(client_tunnel, 'os', SimpleNamespace(name='nt', environ={}))
    calls=[]
    def execute(argv,**kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(client_tunnel.subprocess, 'run', execute)
    monkeypatch.setattr(client_tunnel, '_run', Mock())
    key=base64.b64encode(b'x'*32).decode()
    lease={'grant_id':'same','endpoint':'10.20.32.13:51910','relay_public_key':key,
           'allocated_address':'10.213.40.4/32','allowed_ips':'0.0.0.0/0'}
    with pytest.raises(ValueError, match='同名客户隧道'):
        client_tunnel.install(tmp_path,lease,key)
    assert not (tmp_path/client_tunnel.OWNED).exists()
    assert not list(tmp_path.glob('*.conf'))
    client_tunnel._run.assert_not_called()
