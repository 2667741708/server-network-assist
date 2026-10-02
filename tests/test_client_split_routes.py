import ipaddress
from server_network_assist.client_tunnel import split_allowed_ips


def test_public_borrow_preserves_lan_and_campus_dns():
    routes = [ipaddress.ip_network(r.strip()) for r in split_allowed_ips(
        '0.0.0.0/1,128.0.0.0/1', ['202.206.240.0/24', '198.18.0.0/15']).split(',')]
    for address in ['10.20.32.13', '10.201.250.20', '192.168.1.1', '172.20.1.1',
                    '127.0.0.1', '169.254.1.1', '100.70.1.1', '202.206.240.12', '198.18.0.2', '198.19.0.2']:
        assert not any(ipaddress.ip_address(address) in route for route in routes)
    for address in ['1.1.1.1', '8.8.8.8', '140.143.202.144', '202.206.241.1']:
        assert any(ipaddress.ip_address(address) in route for route in routes)


def test_narrow_lease_is_not_expanded():
    assert split_allowed_ips('8.8.8.0/24,10.0.0.0/8') == '8.8.8.0/24'


def test_source_fake_ips_are_not_globally_bypassed_without_local_clash():
    # Source proxy DNS may legitimately map to a fake IP. Only a local Clash
    # adapter may claim this range; bare customers still send it to their source.
    routes = [ipaddress.ip_network(r.strip()) for r in split_allowed_ips('0.0.0.0/0').split(',')]
    assert any(ipaddress.ip_address('198.18.0.2') in r for r in routes)
