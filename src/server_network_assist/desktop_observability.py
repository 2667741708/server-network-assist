"""Bounded, nullable telemetry and a read-only official release check."""
from collections import deque
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, unquote

REPOSITORY = '2667741708/server-network-assist'
RELEASES = 'https://github.com/' + REPOSITORY + '/releases'


class TrafficSampler:
    def __init__(self, capacity=180):
        self.previous = {}
        self.history = deque(maxlen=capacity)

    def sample(self, tunnels, now=None, timestamp=None):
        now = time.monotonic() if now is None else now
        timestamp = int(time.time()) if timestamp is None else timestamp
        previous, self.previous = self.previous, {}
        totals = [0.0, 0.0]
        measurable = False
        for row in tunnels:
            row['sent_per_second'] = row['received_per_second'] = None
            if not row.get('active') or not row.get('telemetry'):
                continue
            name = row['name']
            sent, received = row.get('sent'), row.get('received')
            if not isinstance(sent, (int, float)) or not isinstance(received, (int, float)) or min(sent, received) < 0:
                continue
            self.previous[name] = (now, sent, received)
            old = previous.get(name)
            # A service restart or peer reset starts a new measurement segment.
            if not old or now <= old[0] or sent < old[1] or received < old[2]:
                continue
            elapsed = now - old[0]
            row['sent_per_second'] = (sent-old[1])/elapsed
            row['received_per_second'] = (received-old[2])/elapsed
            totals[0] += row['sent_per_second']
            totals[1] += row['received_per_second']
            measurable = True
        point = {'timestamp': timestamp, 'sent_per_second': totals[0] if measurable else None,
                 'received_per_second': totals[1] if measurable else None}
        self.history.append(point)
        return {**point, 'available': measurable, 'history': list(self.history),
                'scope': '本机具备遥测权限的活动 WireGuard 隧道；不代表整机网卡流量'}


class EventLog:
    def __init__(self, data, capacity=200):
        self.events = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.path = data / ('desktop-events-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(__import__('os').getpid()) + '-' + secrets.token_hex(3) + '.jsonl')

    def add(self, action, outcome, detail=''):
        event = {'timestamp': int(time.time()), 'action': action, 'outcome': outcome, 'detail': str(detail)[:1200]}
        with self.lock:
            self.events.append(event)
            # Diagnostics are optional: preserve controls even on a full disk.
            try:
                if not self.path.exists() or self.path.stat().st_size < 2_000_000:
                    with self.path.open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            except OSError:
                pass

    def read(self):
        with self.lock:
            return list(reversed(self.events))


def guidance(status):
    result = []
    if status.get('direct', {}).get('ok') and not status.get('system', {}).get('ok'):
        result.append({'code': 'system-proxy', 'severity': 'warning', 'title': '直连正常，系统应用探测失败',
                       'detail': '检查当前用户代理地址和 PAC。先保存备份，再修改或关闭手动代理；需要时在代理页恢复备份。'})
    if not status.get('direct', {}).get('ok'):
        result.append({'code': 'connectivity', 'severity': 'warning', 'title': '公网探测未通过',
                       'detail': '查看默认路由、WireGuard 握手和出口主机探测。探测站点失败不等于所有网站均不可达。多机共享失败后在共享页选择断开并恢复。'})
    for row in status.get('tunnels', []):
        if row.get('active') and not row.get('telemetry'):
            result.append({'code': 'telemetry', 'severity': 'info', 'title': row['name'] + ' 遥测不可读',
                           'detail': '使用管理员快捷方式；Ubuntu 的 wg 遥测需要适当权限。未知数值保持为空，不能按零流量解释。'})
        elif row.get('active') and (not row.get('handshake') or time.time() - row['handshake'] > 180):
            result.append({'code': 'handshake', 'severity': 'warning', 'title': row['name'] + ' 握手未更新',
                           'detail': '检查出口 UDP 端口、主机在线状态与对端配置；空闲隧道可能没有新握手，需要结合真实请求判断。'})
    if not result:
        result.append({'code': 'healthy', 'severity': 'info', 'title': '当前探测未发现明显故障',
                       'detail': '保留当前配置；更换网络共享方案前先记录出口与客户端，并查看最近操作记录。'})
    return result


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    return tuple(int(v) for v in match.groups()) if match else None


def release_check(current):
    result = {'current': current, 'latest': None, 'available': False, 'release_url': RELEASES,
              'assets': [], 'checked_at': int(time.time()),
              'install_mode': 'download-and-run-installer',
              'instructions': '从本项目正式 Release 下载对应系统安装包，关闭面板窗口后运行安装程序。保留现有数据目录；本面板不会执行下载内容。'}
    try:
        request = urllib.request.Request('https://api.github.com/repos/' + REPOSITORY + '/releases/latest',
                                         headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ServerNetworkAssist-Desktop'})
        with urllib.request.urlopen(request, timeout=12) as response:
            if urlsplit(response.url).hostname != 'api.github.com' or urlsplit(response.url).scheme != 'https':
                raise ValueError('更新来源发生不可信跳转')
            body = response.read(1024*1024+1)
            if len(body) > 1024*1024:
                raise ValueError('更新响应过大')
            release = json.loads(body)
        if release.get('draft') or release.get('prerelease'):
            raise ValueError('最新结果不是正式发行版')
        latest = str(release.get('tag_name', ''))
        official = RELEASES + '/tag/'
        url = str(release.get('html_url', ''))
        if not version_tuple(latest) or url != official + latest:
            raise ValueError('正式发行版地址或版本号无效')
        result.update(latest=latest, release_url=url,
                      available=bool(version_tuple(current) and version_tuple(latest) > version_tuple(current)))
        for asset in release.get('assets', []):
            url = str(asset.get('browser_download_url', ''))
            prefix = RELEASES + '/download/' + latest + '/'
            filename = unquote(url[len(prefix):]) if url.startswith(prefix) else ''
            if (filename and filename not in ('.', '..') and not any(c in filename for c in '/\\?#')
                    and urlsplit(url).netloc == 'github.com' and not urlsplit(url).query and not urlsplit(url).fragment):
                result['assets'].append({'name': str(asset.get('name', '')), 'url': url,
                                         'size': asset.get('size'), 'digest': asset.get('digest')})
    except urllib.error.HTTPError as exc:
        result['error'] = '尚无正式 GitHub Release；不能将源码提交当作可安装更新' if exc.code == 404 else f'更新检查失败：GitHub HTTP {exc.code}'
    except (OSError, ValueError, TypeError) as exc:
        result['error'] = '更新检查失败：' + str(exc)[:300]
    return result
