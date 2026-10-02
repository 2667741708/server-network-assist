from types import SimpleNamespace
from server_network_assist import client_paths


def test_windows_identity_query_has_no_console_and_keeps_sid_path(monkeypatch):
    seen = {}
    def output(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return '"DOMAIN\\customer","S-1-5-21-1000"\n'
    monkeypatch.setattr(client_paths, 'os', SimpleNamespace(name='nt', environ={'PROGRAMDATA': 'C:/ProgramData'}))
    monkeypatch.setattr(client_paths.subprocess, 'check_output', output)
    result = client_paths.customer_data()
    assert seen['creationflags'] == 0x08000000
    assert seen['argv'] == ['whoami', '/user', '/fo', 'csv', '/nh']
    assert result.as_posix().endswith('ServerNetworkAssist/client/S-1-5-21-1000')
