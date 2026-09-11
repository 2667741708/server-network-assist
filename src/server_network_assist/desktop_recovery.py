"""Local service pause with reversible startup policy; no guessed route deletion."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


class LocalRecovery:
    def __init__(self, data, windows, run, native):
        self.folder = Path(data) / 'tunnel-recovery'
        self.windows, self.run, self.native = windows, run, native

    def path(self, name):
        return self.folder / (hashlib.sha256(name.encode()).hexdigest() + '.json')

    def records(self):
        if not self.folder.exists():
            return []
        return [json.loads(p.read_text(encoding='utf-8')) for p in self.folder.glob('*.json')]

    def save(self, path, value):
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        if not self.windows:
            temporary.chmod(0o600)
        temporary.replace(path)

    def change(self, name, restore=False):
        state = self.native()
        if not state.get('elevated'):
            raise ValueError('停止借网或恢复服务配置需要管理员权限')
        tunnel = next((t for t in state['tunnels'] if t['name'] == name), None)
        if not tunnel:
            raise ValueError('找不到此本机隧道，请刷新后重试')
        path = self.path(name)
        record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if restore and not record:
            raise ValueError('没有该隧道的启动配置备份，不能猜测恢复')
        if not restore and (not record or record.get('state') == 'restored'):
            mode = tunnel.get('start_mode')
            allowed = ('Automatic', 'Manual', 'Disabled') if self.windows else ('enabled', 'disabled')
            if mode not in allowed:
                raise ValueError('无法安全记录此隧道的启动方式，请先检查系统服务配置')
            record = dict(name=name, start_mode=mode, delayed=bool(tunnel.get('delayed')),
                          was_active=bool(tunnel['active']), created_at=int(time.time()))
        record['state'] = 'restoring' if restore else 'pausing'
        self.save(path, record)  # A durable backup is required before the first mutation.
        try:
            if self.windows:
                args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                        'Bypass', '-File', str(Path(__file__).with_name('desktop_network.ps1')),
                        '-Action', 'restore-startup' if restore else 'pause', '-Tunnel', name]
                if restore:
                    args += ['-StartMode', record['start_mode'], '-Delayed', str(int(record['delayed'])),
                             '-WasActive', str(int(record['was_active']))]
                self.run(args)
            else:
                unit = f'wg-quick@{name}.service'
                if restore:
                    self.run(['systemctl', 'unmask', '--', unit])
                    self.run(['systemctl', 'enable' if record['start_mode'] == 'enabled' else 'disable', '--', unit])
                    self.run(['systemctl', 'start' if record['was_active'] else 'stop', '--', unit])
                else:
                    self.run(['systemctl', 'mask', '--now', '--', unit])
            actual = next(t for t in self.native()['tunnels'] if t['name'] == name)
            wanted_mode = record['start_mode'] if restore else ('Disabled' if self.windows else 'masked')
            wanted_active = record['was_active'] if restore else False
            if actual.get('start_mode') != wanted_mode or actual['active'] != wanted_active:
                raise RuntimeError('服务状态复查未达到目标')
            if restore and self.windows and bool(actual.get('delayed')) != record['delayed']:
                raise RuntimeError('服务延迟启动配置复查未达到目标')
            record['state'] = 'restored' if restore else 'paused'
        except Exception as exc:
            record['state'] = 'restore_failed' if restore else 'pause_failed'
            self.save(path, record)
            raise RuntimeError('服务操作未完成；启动配置备份已保留，请检查诊断并重试或恢复配置') from exc
        self.save(path, record)
        return {'ok': True, 'recovery': record,
                'scope': '仅处理此本机隧道服务与其拥有的路由；旧脚本额外路由、代理及登录任务需单独核对'}


class CampusLogin:
    def __init__(self, data, native):
        self.data, self.native = Path(data), native

    def script(self):
        path = Path(os.environ.get('SNA_NETLOGIN_SCRIPT', self.data / 'netlogin.py'))
        if not path.is_absolute() or not path.is_file() or path.name.lower() != 'netlogin.py':
            raise ValueError('尚未安装校园网脚本：请将已核验的 netlogin.py 放入面板数据目录，或配置 SNA_NETLOGIN_SCRIPT 绝对路径')
        return path

    def status(self):
        try:
            self.script()
            return {'configured': True}
        except ValueError:
            return {'configured': False}

    def execute(self, args, payload=None):
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')}
        env['PYTHONIOENCODING'] = 'utf-8'
        try:
            result = subprocess.run([sys.executable, str(self.script()), *args], input=payload,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180,
                env=env, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            response = json.loads(result.stdout)
            if not isinstance(response, dict):
                raise ValueError()
            return result.returncode, response
        except subprocess.TimeoutExpired:
            raise ValueError('校园网脚本超时；请查询当前校园网状态后再决定是否重试') from None
        except Exception:
            raise ValueError('校园网脚本执行失败或响应格式无效；未记录凭据或脚本输出') from None

    def current(self):
        self.stopped_state()
        code, response = self.execute(['current-status', '--json'])
        summary = response.get('summary')
        if code or not isinstance(summary, dict):
            raise ValueError('校园网状态查询未返回有效摘要；未记录原始认证会话')
        # Deliberately omit session IDs, device inventories, redirects and errors.
        return {'state': str(summary.get('state', 'unknown'))[:80],
                'online': summary.get('online') is True,
                'account': str(summary.get('userId') or summary.get('userName') or '')[:128],
                'service': str(summary.get('service') or '')[:128],
                'ip': str(summary.get('userIp') or '')[:64],
                'internet_online': response.get('internetOnline') if isinstance(response.get('internetOnline'), bool) else None}

    def stopped_state(self):
        state = self.native()
        tunnels = state['tunnels']
        if not isinstance(tunnels, list):
            raise ValueError('本机隧道状态无效，暂不能确认物理网络')
        for tunnel in tunnels:
            if tunnel.get('active') is not False or tunnel.get('service_state') not in ('Stopped', 'inactive', 'failed'):
                raise ValueError('本机仍有运行中、过渡中或状态未知的 WireGuard 服务，请等待完全停止后重试')
        return state

    def login(self, value):
        self.script()
        if value.get('physical_network_confirmed') is not True:
            raise ValueError('请先确认已恢复本机物理网络和代理设置')
        state = self.stopped_state()
        if state.get('proxy', {}).get('enabled') or state.get('proxy', {}).get('pac'):
            raise ValueError('当前网络代理仍然启用，请检查并关闭后再登录')
        user, password, service = value.get('username'), value.get('password'), str(value.get('service', '0'))
        if not isinstance(user, str) or not user.strip() or not isinstance(password, str) or not password or service not in ('0', '1', '2', '3'):
            raise ValueError('请填写账号、密码并选择运营商')
        payload = json.dumps(dict(username=user.strip(), password=password, service=service))
        code, response = self.execute(['login-stdin'], payload)
        if code or response.get('ok') is not True:
            raise ValueError('校园网认证未成功，请核对物理网络、账号、密码和运营商；未记录凭据或脚本输出')
        try:
            current = self.current()
        except ValueError:
            return {'ok': True, 'verified': False, 'message': '认证请求已完成，但当前账号复核失败。请点击查询当前校园网状态，不要重复登录。'}
        wanted_service = {'0': '校园网', '1': '中国移动', '2': '中国联通', '3': '中国电信'}[service]
        verified = current['online'] and current['account'] == user.strip() and current.get('service') == wanted_service
        message = ('已核对当前校园网账号与运营商均符合选择。公网是否可达请查看连接概览。' if verified else
                   '认证请求已完成，但未确认当前出口的账号与运营商符合选择；可能已有其他账号或运营商在线，请核对下方状态。')
        return {'ok': True, 'verified': verified, 'current': current, 'message': message}
