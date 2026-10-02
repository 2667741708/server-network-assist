import hashlib
import json

import pytest

from server_network_assist.client_install import install_files, installed_data


def bundle(tmp_path):
    source = tmp_path / 'bundle'
    source.mkdir()
    (source / 'ServerNetworkAssistClient.exe').write_bytes(b'fake-exe-do-not-execute')
    (source / '使用说明.md').write_text('public instructions', encoding='utf-8')
    (source / 'private-subscription.json').write_text('never copy')
    (source / '文件校验.json').write_text(json.dumps({
        'product': '纯享入网', 'contains_user_data': False, 'revision': 'fixture',
        'files': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                  for name in ('ServerNetworkAssistClient.exe', '使用说明.md')}}), encoding='utf-8')
    return source


def test_install_versioned_files_persists_subscriptions_without_network(tmp_path):
    source = bundle(tmp_path)
    data = tmp_path / 'private-data'
    data.mkdir()
    config = data / 'customer-online-subscriptions.json'
    config.write_text('existing identity')
    destination = tmp_path / 'chosen-folder'
    exe = install_files(source, destination, data)
    assert exe.read_bytes() == b'fake-exe-do-not-execute'
    assert not (exe.parent / 'private-subscription.json').exists()
    assert installed_data(exe) == data
    assert config.read_text() == 'existing identity'
    assert install_files(source, destination, data) == exe
    assert config.read_text() == 'existing identity'
    assert not list(destination.glob('.install-*'))


def test_tampering_stops_before_config_or_install(tmp_path):
    source = bundle(tmp_path)
    (source / 'ServerNetworkAssistClient.exe').write_bytes(b'tampered')
    data = tmp_path / 'private'
    with pytest.raises(ValueError, match='校验失败'):
        install_files(source, tmp_path / 'install', data)
    assert not data.exists()


def test_reject_source_and_root_directory(tmp_path):
    source = bundle(tmp_path)
    for target in (source, source / 'subdir', source.anchor):
        with pytest.raises(ValueError):
            install_files(source, target, tmp_path / 'data')


def test_prepare_permission_failure_does_not_install(tmp_path):
    source = bundle(tmp_path)
    def deny(_):
        raise PermissionError('cannot protect data')
    with pytest.raises(PermissionError):
        install_files(source, tmp_path / 'install', tmp_path / 'data', prepare_data=deny)
    assert not (tmp_path / 'install').exists()


def test_existing_modified_app_and_invalid_config_are_not_overwritten(tmp_path):
    source = bundle(tmp_path)
    exe = install_files(source, tmp_path / 'install', tmp_path / 'data')
    exe.write_bytes(b'changed')
    with pytest.raises(ValueError, match='修改'):
        install_files(source, tmp_path / 'install', tmp_path / 'data')
    assert exe.read_bytes() == b'changed'
    (exe.parent / 'client-install.json').write_text('{"schema_version":1,"data_directory":"relative"}')
    with pytest.raises(ValueError):
        installed_data(exe)
