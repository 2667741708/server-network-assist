"""Campus control traffic bound to a physical Windows interface, including DNS."""
import http.client
import base64
import ipaddress
import os
import secrets
import socket
import ssl
import struct
import urllib.request
from urllib.parse import urlsplit, unquote


def bind_socket(sock, link):
    if os.name == 'nt':
        sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', int(link['id'])))
    addresses = [address for address in link.get('addresses', [])
                 if ipaddress.ip_address(address).version == 4]
    if not addresses:
        raise ValueError('校园物理网卡没有可用 IPv4 地址')
    sock.bind((addresses[0], 0))


def _skip_name(data, offset):
    for _ in range(128):
        if offset >= len(data):
            break
        size = data[offset]
        if size & 0xc0 == 0xc0:
            if offset + 1 < len(data):
                return offset + 2
            break
        if size & 0xc0:
            break
        offset += 1
        if not size:
            return offset
        offset += size
    raise ValueError('校园 DNS 响应格式无效')


def _dns_answers(data, query):
    if len(data) < 12:
        raise ValueError('校园 DNS 响应格式无效')
    identifier, flags, questions, answers, _, _ = struct.unpack('!6H', data[:12])
    if identifier != int.from_bytes(query[:2], 'big') or not flags & 0x8000 or flags & 0x020f or questions != 1:
        raise ValueError('校园 DNS 未返回完整有效的 IPv4 解析结果')
    offset = _skip_name(data, 12) + 4
    if data[12:offset] != query[12:]:
        raise ValueError('校园 DNS 响应与请求不匹配')
    result = []
    for _ in range(answers):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError('校园 DNS 响应格式无效')
        kind, family, _, size = struct.unpack('!HHIH', data[offset:offset + 10])
        offset += 10
        if offset + size > len(data):
            raise ValueError('校园 DNS 响应格式无效')
        if kind == 1 and family == 1 and size == 4:
            address = socket.inet_ntoa(data[offset:offset + size])
            # Clash fake-IP is never a valid physical campus control endpoint.
            if ipaddress.ip_address(address) in ipaddress.ip_network('198.18.0.0/15'):
                raise ValueError('校园 DNS 返回了 TUN 虚拟地址，请检查网卡 DNS')
            result.append(address)
        offset += size
    if not result:
        raise ValueError('校园 DNS 没有返回 IPv4 地址')
    return list(dict.fromkeys(result))


def resolve_ipv4(host, link):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if address.version != 4:
            raise ValueError('校园订阅入口暂不支持 IPv6，请使用校园 IPv4 入口')
        return [str(address)]
    labels = host.rstrip('.').encode('idna').split(b'.')
    if not labels or any(not 0 < len(label) <= 63 for label in labels):
        raise ValueError('订阅服务域名无效')
    question = b''.join(bytes([len(label)]) + label for label in labels) + b'\0\0\1\0\1'
    if len(question) > 259:
        raise ValueError('订阅服务域名过长')
    query = struct.pack('!6H', secrets.randbelow(65536), 0x0100, 1, 0, 0, 0) + question
    for server in link.get('dns', [])[:3]:
        try:
            if ipaddress.ip_address(server).version != 4:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                bind_socket(sock, link)
                sock.connect((server, 53))
                sock.send(query)
                return _dns_answers(sock.recv(65535), query)
        except (OSError, ValueError):
            continue
    raise ValueError('无法通过校园物理网卡的 DNS 解析订阅域名；请检查该网卡 DNS，或使用管理员提供的校园 IPv4 订阅地址')


def connect(host, port, link, timeout=3):
    addresses = resolve_ipv4(host, link)
    failure = None
    for address in addresses[:3]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            bind_socket(sock, link)
            sock.connect((address, port))
            return sock
        except BaseException as exc:
            sock.close()
            if not isinstance(exc, OSError):
                raise
            failure = exc
    raise failure or OSError('校园订阅入口不可达')


class _HTTP(http.client.HTTPConnection):
    def __init__(self, host, link, **kwargs):
        super().__init__(host, **kwargs)
        self.link = link

    def connect(self):
        self.sock = connect(self.host, self.port, self.link, self.timeout)


class _HTTPS(http.client.HTTPSConnection):
    def __init__(self, host, link, **kwargs):
        super().__init__(host, **kwargs)
        self.link = link

    def connect(self):
        sock = connect(self.host, self.port, self.link, self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, link):
        super().__init__()
        self.link = link

    def http_open(self, request):
        return self.do_open(lambda host, **kwargs: _HTTP(host, self.link, **kwargs), request)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, link, context):
        super().__init__(context=context)
        self.link = link

    def https_open(self, request):
        return self.do_open(lambda host, **kwargs: _HTTPS(host, self.link, **kwargs),
                            request, context=self._context)


class _SameOrigin(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        original, target = urlsplit(request.full_url), urlsplit(newurl)
        def origin(parts):
            return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)
        if origin(original) != origin(target):
            raise ValueError('服务请求发生了不受信任的跨域跳转')
        return super().redirect_request(request, fp, code, message, headers, newurl)


class _PublicHTTPSProxy(urllib.request.ProxyHandler):
    """Use an existing proxy for this HTTPS API only, without global changes.

    The campus bypass list also includes the public panel domain on some PCs;
    that list must not force public subscription requests onto a broken direct
    path. Campus/private control traffic never reaches this handler.
    """
    def proxy_open(self, request, proxy, protocol):
        if request.type != 'https' or protocol != 'https':
            raise ValueError('订阅代理仅允许 HTTPS')
        parts = urlsplit(proxy if '://' in proxy else 'http://' + proxy)
        if parts.scheme != 'http' or not parts.hostname or parts.path not in ('', '/'):
            raise ValueError('当前 HTTPS 订阅需要 HTTP CONNECT 代理，请检查现有系统代理类型')
        if parts.username is not None:
            credential = unquote(parts.username) + ':' + unquote(parts.password or '')
            request.add_unredirected_header('Proxy-authorization', 'Basic ' + base64.b64encode(credential.encode()).decode())
        host = parts.hostname
        if ':' in host:
            host = '[' + host + ']'
        request.set_proxy(host + ':' + str(parts.port or 80), 'http')
        return None


def campus_opener(context=None):
    from .client_attachment import snapshot
    from .client_campus import campus_link
    link = campus_link(snapshot())
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SameOrigin(),
        _HTTPHandler(link), _HTTPSHandler(link, context or ssl.create_default_context()))


def subscription_links(attachment, host):
    """Candidate physical paths for control traffic; never authorize borrowing."""
    from .client_campus import CAMPUS_CIDRS
    try:
        campus_target = any(ipaddress.ip_address(host) in ipaddress.ip_network(cidr)
                            for cidr in CAMPUS_CIDRS)
    except ValueError:
        campus_target = False
    def rank(link):
        campus = any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr)
                     for address in link.get('addresses', []) for cidr in CAMPUS_CIDRS)
        metric = min((route['metric'] for route in link.get('defaults', [])), default=1000000)
        return (0 if campus_target and campus else 1, metric, str(link['id']))
    return sorted((link for link in attachment.get('links', [])
                   if link.get('connected') and link.get('addresses')), key=rank)


def subscription_opener(base_url, context=None, attachment=None):
    """Read/enroll over Wi-Fi, Ethernet or a hotspot without editing networking.

    Select a path with a credential-free TCP preflight. Never retry a submitted
    enrollment POST on another interface: a lost response can consume its token.
    """
    from .client_attachment import snapshot
    parts = urlsplit(base_url)
    try:
        private = ipaddress.ip_address(parts.hostname).is_private
    except ValueError:
        private = parts.hostname == 'localhost'
    # Public subscription control traffic may use the user's already enabled
    # HTTPS proxy. Never proxy a literal campus/private address, change proxy
    # settings, or fall back after sending a one-time enrollment request.
    proxy = urllib.request.getproxies().get('https') if parts.scheme == 'https' and not private else None
    if proxy:
        return urllib.request.build_opener(_PublicHTTPSProxy({'https': proxy}),
            _SameOrigin(), urllib.request.HTTPSHandler(context=context or ssl.create_default_context()))
    links = subscription_links(attachment if attachment is not None else snapshot(), parts.hostname)
    for link in links[:4]:
        try:
            with connect(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), link):
                pass
        except (OSError, ValueError):
            continue
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SameOrigin(),
            _HTTPHandler(link), _HTTPSHandler(link, context or ssl.create_default_context()))
    raise OSError('现有物理网络均无法到达订阅入口')
