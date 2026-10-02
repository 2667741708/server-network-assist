import hashlib
import json
from types import SimpleNamespace
import pytest
from server_network_assist import client_dependencies as dependencies
from server_network_assist import client_install, client_tunnel, client

def test_missing_wireguard_stops_before_configuration_or_network(tmp_path,monkeypatch):
    monkeypatch.setattr(dependencies,'wireguard_directory',lambda:tmp_path/'missing')
    monkeypatch.setattr(client_tunnel.os,'name','nt')
    with pytest.raises(ValueError,match='缺少 WireGuard'):
        client_tunnel.install(tmp_path/'private',{},'invalid-key')
    assert not (tmp_path/'private').exists()

def test_missing_wireguard_stops_before_personal_logout_or_lease(tmp_path,monkeypatch):
    monkeypatch.setattr(client,'WINDOWS',True)
    monkeypatch.setattr(dependencies,'wireguard_directory',lambda:tmp_path/'missing')
    panel=SimpleNamespace(active=lambda:None,leaving_path=tmp_path/'not-leaving')
    with pytest.raises(ValueError,match='原订阅已保存'):
        client.ClientPanel.online_connect(panel,'test-grant')

def test_both_wireguard_programs_required(tmp_path,monkeypatch):
    monkeypatch.setattr(dependencies,'wireguard_directory',lambda:tmp_path)
    (tmp_path/'wireguard.exe').write_bytes(b'fixture-only')
    with pytest.raises(ValueError,match='wg.exe'):
        dependencies.require_wireguard()
    (tmp_path/'wg.exe').write_bytes(b'fixture-only')
    dependencies.require_wireguard()

def offline(tmp_path):
    files={}
    for name in dependencies.OFFLINE_FILES:
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'fixture-never-execute')
        files[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (tmp_path/'文件校验.json').write_text(json.dumps({'product':'纯享入网','contains_user_data':False,'files':files}),encoding='utf8')

def test_offline_payload_missing_and_tampered_rejected(tmp_path):
    offline(tmp_path)
    assert dependencies.verify_offline_payload(tmp_path)
    first=tmp_path/dependencies.OFFLINE_FILES[0]
    first.write_bytes(b'changed')
    with pytest.raises(ValueError,match='校验失败'):
        dependencies.verify_offline_payload(tmp_path)
    first.unlink()
    with pytest.raises(ValueError,match='缺少离线依赖'):
        dependencies.verify_offline_payload(tmp_path)

def test_offline_payload_cannot_escape_bundle(tmp_path):
    root=tmp_path/'bundle';root.mkdir();offline(root)
    target=root/dependencies.OFFLINE_FILES[0];target.unlink()
    outside=tmp_path/'outside';outside.write_bytes(b'fixture-never-execute')
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip('Host does not grant symlink creation')
    with pytest.raises(ValueError,match='缺少離線依賴|缺少离线依赖'):
        dependencies.verify_offline_payload(root)

def test_installer_reuses_standard_portable_identity_and_existing_local(tmp_path):
    local,legacy=tmp_path/'local',tmp_path/'legacy'
    assert client_install.choose_data_directory(local,legacy)==local
    legacy.mkdir();identity=legacy/'customer-online-service.json'
    identity.write_text('preserve-private-identity')
    assert client_install.choose_data_directory(local,legacy)==legacy
    assert identity.read_text()=='preserve-private-identity'
    local.mkdir();(local/'customer-online-subscriptions.json').write_text('existing')
    assert client_install.choose_data_directory(local,legacy)==local
