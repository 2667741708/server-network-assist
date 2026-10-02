import ctypes
import json
import socket
import struct
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from server_network_assist import client_attachment, client_campus, client_lan_transport as lan, client_wifi
from server_network_assist.client_online import OnlineServiceClient
from test_client_attachment import network


def test_enrollment_unreachable_hotspot_does_not_send_token_or_save_keys(tmp_path, monkeypatch):
    client = OnlineServiceClient(tmp_path)
    client.physical_transport = True
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: network('192.168.43.2'))
    client.opener = Mock()
    probe = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(lan, 'connect', probe)
    with pytest.raises(ValueError, match='无法连接商业服务端'):
        client.enroll('http://10.20.32.13:9182/#enroll=one-time-secret-123')
    client.opener.open.assert_not_called()
    assert not client.path.exists()


@pytest.mark.parametrize('wifi,ip', [(True,'10.126.63.249'), (False,'10.20.31.134'), (True,'192.168.43.2')])
def test_enrollment_selects_physical_transport_and_never_uses_system_opener(tmp_path, monkeypatch, wifi, ip):
    client = OnlineServiceClient(tmp_path)
    client.physical_transport = True
    value = network(ip)
    value['links'][0]['wifi'] = wifi
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: value)
    selected = []
    def build(*handlers):
        selected.extend(handler.link for handler in handlers if isinstance(handler, lan._HTTPHandler))
        return fake
    fake = Mock()
    response = Mock()
    response.url = 'http://10.20.32.13:9182/client/v1/enroll'
    response.read.return_value = json.dumps({'device_id': 'dev-1', 'customer_id': 'customer-1'}).encode()
    # Mock implements context manager via explicit response context.
    from unittest.mock import MagicMock
    context = MagicMock()
    context.__enter__.return_value = response
    fake.open.return_value = context
    monkeypatch.setattr(lan.urllib.request, 'build_opener', build)
    from unittest.mock import MagicMock
    monkeypatch.setattr(lan, 'connect', Mock(return_value=MagicMock()))
    client.opener = Mock()
    client.enroll('http://10.20.32.13:9182/#enroll=one-time-secret-123')
    assert selected == value['links']
    client.opener.open.assert_not_called()


def test_private_subscription_prefers_second_campus_link_without_disabling_hotspot():
    value = network('192.168.43.2', index=17, metric=10)
    value['links'] += network(metric=100)['links']
    assert [link['id'] for link in lan.subscription_links(value, '10.20.32.13')] == [16, 17]
    assert [link['id'] for link in lan.subscription_links(value, 'public.example')] == [17, 16]
    with pytest.raises(ValueError):
        client_campus.campus_link(value)  # Reading permission never authorizes borrowing.


def test_enrollment_post_is_not_retried_on_second_link(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    client = OnlineServiceClient(tmp_path); client.physical_transport = True
    value = network('192.168.43.2', index=17, metric=10)
    value['links'] += network()['links']
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: value)
    monkeypatch.setattr(lan.urllib.request, 'getproxies', lambda: {})
    probe = Mock(return_value=MagicMock()); monkeypatch.setattr(lan, 'connect', probe)
    opener = Mock(); opener.open.side_effect = TimeoutError()
    monkeypatch.setattr(lan.urllib.request, 'build_opener', Mock(return_value=opener))
    with pytest.raises(ValueError):
        client.enroll('https://public.example/#enroll=one-time-secret-123')
    assert probe.call_count == 1 and opener.open.call_count == 1
    assert not client.path.exists()


def test_public_https_can_use_existing_proxy_but_private_campus_cannot(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr(lan.urllib.request, 'getproxies', lambda: {'https':'http://127.0.0.1:7897'})
    probe = Mock(return_value=MagicMock()); monkeypatch.setattr(lan, 'connect', probe)
    build=Mock();monkeypatch.setattr(lan.urllib.request,'build_opener',build)
    lan.subscription_opener('https://public.example',attachment=network('192.168.43.2'))
    assert build.call_args.args[0].proxies == {'https':'http://127.0.0.1:7897'}
    assert isinstance(build.call_args.args[1],lan._SameOrigin)
    probe.assert_not_called()
    lan.subscription_opener('https://10.20.32.13:9182',attachment=network())
    assert build.call_args.args[0].proxies == {}
    probe.assert_called_once()


def test_public_api_proxy_keeps_original_tls_target_even_when_domain_is_bypassed(monkeypatch):
    from urllib.request import Request
    monkeypatch.setattr(lan.urllib.request,'proxy_bypass',lambda _:True)
    handler=lan._PublicHTTPSProxy({'https':'http://127.0.0.1:7897'})
    request=Request('https://public.example/client/v1/subscription')
    handler.https_open(request)
    assert request.type=='https' and request.host=='127.0.0.1:7897'
    assert request._tunnel_host=='public.example'
    assert request.full_url=='https://public.example/client/v1/subscription'


def test_bound_connect_never_calls_system_dns(monkeypatch):
    value = network()['links'][0]
    value['dns'] = ['10.20.0.1']
    sock = Mock()
    system_dns = Mock(side_effect=AssertionError('system DNS must not be used'))
    monkeypatch.setattr(lan.socket, 'getaddrinfo', system_dns)
    monkeypatch.setattr(lan, 'resolve_ipv4', lambda host, link: ['10.20.32.13'])
    monkeypatch.setattr(lan.socket, 'socket', Mock(return_value=sock))
    monkeypatch.setattr(lan, 'os', SimpleNamespace(name='nt'))
    assert lan.connect('campus.example', 9182, value) is sock
    sock.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 31, struct.pack('!I', value['id']))
    sock.bind.assert_called_once_with(('10.20.31.134', 0))
    sock.connect.assert_called_once_with(('10.20.32.13', 9182))
    system_dns.assert_not_called()


def query():
    return struct.pack('!6H', 123, 0x0100, 1, 0, 0, 0) + b'\x06campus\x07example\0\0\1\0\1'


def answer(ip='10.20.32.13', flags=0x8180, identifier=123):
    return (struct.pack('!6H', identifier, flags, 1, 1, 0, 0) + query()[12:] +
            b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton(ip))


def test_dns_parser_accepts_compressed_answer():
    assert lan._dns_answers(answer(), query()) == ['10.20.32.13']


@pytest.mark.parametrize('data', [answer(identifier=42), answer(flags=0x8380), answer(flags=0x8183),
                                    answer(ip='198.18.0.10'), answer()[:-2], b'bad'])
def test_dns_rejects_mismatched_truncated_failed_and_fakeip_responses(data):
    with pytest.raises(ValueError):
        lan._dns_answers(data, query())


def test_dns_uses_interface_server_and_source_with_no_fallback(monkeypatch):
    from unittest.mock import MagicMock
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.recv.return_value = answer()
    monkeypatch.setattr(lan.secrets, 'randbelow', lambda _: 123)
    monkeypatch.setattr(lan.socket, 'socket', Mock(return_value=sock))
    value = network()['links'][0]
    value['dns'] = ['10.20.0.1']
    assert lan.resolve_ipv4('campus.example', value) == ['10.20.32.13']
    sock.connect.assert_called_once_with(('10.20.0.1', 53))
    sock.bind.assert_called_once_with(('10.20.31.134', 0))
    sock.send.assert_called_once_with(query())
    sock.recv.side_effect = TimeoutError()
    with pytest.raises(ValueError, match='校园物理网卡的 DNS'):
        lan.resolve_ipv4('campus.example', value)


def test_redirect_rejected_before_followup_request():
    from urllib.request import Request
    with pytest.raises(ValueError, match='跨域'):
        lan._SameOrigin().redirect_request(Request('https://campus.example/'), None, 302, '', {}, 'https://other.example/')


def test_tls_uses_original_hostname_and_closes_socket_on_failure(monkeypatch):
    sock = Mock()
    context = Mock()
    context.wrap_socket.side_effect = __import__('ssl').SSLCertVerificationError('invalid certificate')
    monkeypatch.setattr(lan, 'connect', Mock(return_value=sock))
    conn = lan._HTTPS('campus.example', network()['links'][0], context=context)
    with pytest.raises(__import__('ssl').SSLCertVerificationError):
        conn.connect()
    context.wrap_socket.assert_called_once_with(sock, server_hostname='campus.example')
    sock.close.assert_called_once()


def test_native_wlan_structures_and_non_ascii_ssid():
    assert ctypes.sizeof(client_wifi.Interface) == 532
    assert ctypes.sizeof(client_wifi.Network) == 628
    row = client_wifi.Network()
    raw = '校园无线'.encode()
    row.ssid.length = len(raw)
    row.ssid.data[:len(raw)] = raw
    buffer = ctypes.create_string_buffer(struct.pack('<II', 1, 0) + bytes(row))
    found = client_wifi._rows(ctypes.c_void_p(ctypes.addressof(buffer)), client_wifi.Network, 10)
    assert bytes(found[0].ssid.data)[:found[0].ssid.length].decode() == '校园无线'


def test_wifi_permission_error_is_actionable():
    assert '位置访问' in client_wifi._error(5)
    assert 'WLAN AutoConfig' in client_wifi._error(1062)


@pytest.mark.parametrize('status', [0, 5])
def test_native_scan_frees_buffers_and_handle_even_when_permission_denied(monkeypatch, status):
    adapter = client_wifi.Interface()
    row = client_wifi.Network()
    raw = '校园网络'.encode()
    row.ssid.length = len(raw)
    row.ssid.data[:len(raw)] = raw
    row.flags = 1; row.signal = 81; row.secure = 1; row.connectable = 1
    buffers = [ctypes.create_string_buffer(struct.pack('<II', 1, 0) + bytes(item))
               for item in (adapter, row)]
    def output(argument, value):
        ctypes.cast(argument, ctypes.POINTER(ctypes.c_void_p))[0] = value
    def open_handle(version, reserved, negotiated, handle):
        output(handle, 123)
        return 0
    def interfaces(handle, reserved, result):
        output(result, ctypes.addressof(buffers[0]))
        return 0
    def networks(handle, guid, flags, reserved, result):
        if not status:
            output(result, ctypes.addressof(buffers[1]))
        return status
    dll = SimpleNamespace(WlanOpenHandle=Mock(side_effect=open_handle),
        WlanEnumInterfaces=Mock(side_effect=interfaces), WlanScan=Mock(return_value=0),
        WlanGetAvailableNetworkList=Mock(side_effect=networks),
        WlanFreeMemory=Mock(), WlanCloseHandle=Mock(return_value=0))
    monkeypatch.setattr(client_wifi, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(client_wifi.c, 'WinDLL', Mock(return_value=dll), raising=False)
    monkeypatch.setattr(client_wifi.time, 'sleep', Mock())
    result = client_wifi.scan()
    assert dll.WlanFreeMemory.call_count == (2 if not status else 1)
    dll.WlanCloseHandle.assert_called_once()
    if status:
        assert not result['networks'] and '位置访问' in result['message']
    else:
        assert result['networks'][0]['ssid'] == '校园网络'
        assert result['networks'][0]['connected'] and result['networks'][0]['signal'] == 81


def test_source_hostname_resolves_on_campus_before_install_and_network_change_releases(tmp_path, monkeypatch):
    from server_network_assist import client, client_tunnel
    panel = client.ClientPanel(tmp_path)
    value = network('10.126.63.249')
    monkeypatch.setattr(client, 'WINDOWS', True)
    monkeypatch.setattr(client_attachment, 'snapshot', lambda: value)
    monkeypatch.setattr(client_attachment, 'validate_selected_access', Mock())
    monkeypatch.setattr(client_campus, 'authentication', lambda _: 'offline')
    monkeypatch.setattr(panel.online, '_record', lambda: {'base_url': 'http://10.20.32.13:9182', 'wireguard_private_key': 'test-only'})
    monkeypatch.setattr(panel.online, 'lease', lambda _: {'id': 'lease-test', 'endpoint': 'source.example:51910'})
    resolver = Mock(return_value=['10.20.32.13'])
    install = Mock(return_value='sna-test')
    monkeypatch.setattr(lan, 'resolve_ipv4', resolver)
    monkeypatch.setattr(client_tunnel, 'install', install)
    panel.online_connect('grant-1')
    assert install.call_args.args[1]['endpoint'] == '10.20.32.13:51910'
    resolver.assert_called_once_with('source.example', value['links'][0])
    panel.clear_active()
    monkeypatch.setattr(client_attachment, 'snapshot', Mock(side_effect=[value, network('192.168.43.2')]))
    install.reset_mock()
    leave = Mock()
    monkeypatch.setattr(panel, 'leave_network', leave)
    with pytest.raises(ValueError, match='物理网络已改变'):
        panel.online_connect('grant-1')
    install.assert_not_called()
    leave.assert_called_once()
