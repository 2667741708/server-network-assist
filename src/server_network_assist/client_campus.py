"""Read-only, physically bound campus authentication checks. Never log users out."""
import http.client
from contextlib import closing
import ipaddress
import json
import socket
import ssl
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from .client_attachment import preferred

PORTAL = 'https://auth1.ysu.edu.cn/'
PORTAL_ADDRESS = '222.30.148.40'
# Deployment policy for the verified YSU campus; not a Wi-Fi-name heuristic.
CAMPUS_CIDRS = ('10.20.0.0/16',)


def campus_link(attachment):
    # No cross-family metric can prove that a user's hotspot is unused.
    # Until campus IPv6 prefixes are configured, reject IPv6 defaults and
    # every non-campus physical default, even when its metric is higher.
    for candidate in attachment.get('links', []):
        if not candidate.get('connected'):
            continue
        campus = any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr)
                     for address in candidate.get('addresses', []) for cidr in CAMPUS_CIDRS)
        if candidate.get('defaults6') or (candidate.get('defaults') and not campus):
            raise ValueError('当前系统出口不是已配置的校园接入网络；存在热点或未配置的 IPv6 出口，保持原网络，不启动借网')
    link, route = preferred(attachment)
    if not link or not any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr)
                           for address in link.get('addresses', []) for cidr in CAMPUS_CIDRS):
        raise ValueError('当前系统出口不是已配置的校园接入网络；保持原网络，不启动借网')
    return link


class BoundHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, link):
        self.link = link
        super().__init__(host, timeout=4, context=ssl.create_default_context())

    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.setsockopt(socket.IPPROTO_IP, 31, socket.htonl(int(self.link['id'])))
            sock.bind((self.link['addresses'][0], 0))
            # The deployment's verified portal address avoids Clash fake-IP
            # DNS and WireGuard DNS affecting this physically bound probe.
            sock.connect((PORTAL_ADDRESS, self.port))
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def authentication(attachment):
    """Return online/offline/unknown for this physical IP, with TLS verification."""
    link = campus_link(attachment)
    url = PORTAL
    session = None
    try:
        for _ in range(6):
            parts = urlsplit(url)
            if parts.scheme not in ('https', 'http') or parts.hostname != 'auth1.ysu.edu.cn' or parts.port not in (None, 443):
                return 'unknown'
            # The school's HTTPS redirect currently advertises an HTTP portal.
            # Keep requests on authenticated HTTPS; never downgrade transport.
            session = parse_qs(parts.query).get('sessionId', [None])[0]
            if session:
                break
            with closing(BoundHTTPS(parts.hostname, link)) as conn:
                conn.request('GET', parts.path + ('?' + parts.query if parts.query else ''))
                response = conn.getresponse()
                location = response.getheader('Location')
                response.read(65536)
                if response.status not in (301, 302, 303, 307, 308) or not location:
                    return 'unknown'
                url = urljoin(url, location)
                # A verified HTTPS response from the campus portal points an
                # unauthenticated physical IP at the school's captive probe.
                if urlsplit(url).hostname == '124.124.124.124':
                    return 'offline'
        if not session:
            return 'unknown'
        with closing(BoundHTTPS('auth1.ysu.edu.cn', link)) as conn:
            conn.request('GET', '/eportal/adaptor/getOnlineUserInfo?sessionId=' + quote(session, safe=''))
            response = conn.getresponse()
            if response.status != 200:
                return 'unknown'
            body = response.read(65537)
            if len(body) > 65536:
                return 'unknown'
            value = json.loads(body)
        result = (value.get('data') or {}).get('portalOnlineUserInfo', {}).get('result')
        return {'success': 'online', 'fail': 'offline'}.get(result, 'unknown')
    except (OSError, ValueError, http.client.HTTPException):
        return 'unknown'
