#!/usr/bin/env python3
"""Generate the complete CLI reference from argparse help without running actions."""
import argparse
from pathlib import Path
import subprocess
import sys

from server_network_assist.control_cli import parser
from server_network_assist.client_service_admin import build_parser

ROOT = Path(__file__).resolve().parents[1]


def walk(value):
    yield value
    for action in value._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from walk(child)


def main():
    intro = '''# 命令大全

4090 源网的系统代理、终端代理、规则/全局/直连、TUN、核心服务、自启、回退及验收命令见 [4090代理命令大全](4090代理命令大全.md)。该文档按 4090 实际配置核对，区分当前可用命令和尚未开启的 Controller API。

源端完整停止与恢复当前代理状态，见 [4090代理退出与恢复教程](4090代理退出与恢复教程.md)：先安排自动恢复、暂停 gateway-health 检查，再停核心，避免守护重启造成测试误判；含客户端自主代理的实际实验与流程图。

命令以当前源码的 `--help` 为准。Windows 使用 PowerShell，Linux 使用 Bash；不要将 Bash 续行命令直接粘贴到 PowerShell。安装后可用短命令；未安装时可用对应的 `python -m server_network_assist.<模块>`。

## 借网与退网快捷命令

在保存了主机与借网方案的管理服务端执行：

```bash
server-network-assist-ctl hosts
server-network-assist-ctl profiles
server-network-assist-ctl probe --host all
server-network-assist-ctl profile-enable --profile "方案名称或ID"
server-network-assist-ctl profile-disable --profile "方案名称或ID"
```

`profile-disable` 恢复原有网络并停用该借网方案。管理 WireGuard 跳板与借网隧道应分别配置，不要停止跳板隧道。

Windows 客户机仅退出现有 `fleet-titan` 借网且禁用独立自动启动：

```powershell
Stop-Service -Name 'WireGuardTunnel$fleet-titan'
Set-Service -Name 'WireGuardTunnel$fleet-titan' -StartupType Disabled
```

Windows 恢复这个已有借网配置：

```powershell
Set-Service -Name 'WireGuardTunnel$fleet-titan' -StartupType Automatic
Start-Service -Name 'WireGuardTunnel$fleet-titan'
```

以上服务名只适用于实际安装了该隧道的机器。商业客户专用隧道使用 `sna...` 名称，由客户端负责租约与退网。

## 商业订阅签发

管理网页：商业订阅 → 登记已部署源网 → 选择一个或多个源网 → 填写客户名称与速度 → 点击 10 GB / 50 GB / 100 GB / 无限 GB 按钮 → 复制地址给客户。

有效期为签发起 30 天，流量额度按 GiB（1024³ 字节）计算，开户令牌 24 小时内一次性使用。无限流量仍可限速。服务端未部署中继时，签发订阅不代表实际可借网。

4090 命令的数据库选项需使用实际路径：

```bash
export SNA_CLIENT_SERVICE_DB=/home/a/.local/share/server-network-assist-commercial/data/commercial-service.sqlite3
server-network-assist-service-admin source list
server-network-assist-service-admin source save --file source.json
server-network-assist-service-admin source-proxy status
server-network-assist-service-admin source-proxy start
server-network-assist-service-admin subscription generate --name "客户A" --quota-gb 10 --source "源网ID" --base-url http://10.20.32.13:9182 --output customer-a.txt
server-network-assist-service-admin subscription generate --name "客户B" --quota-gb 50 --source "源网ID" --base-url http://10.20.32.13:9182 --output customer-b.txt
server-network-assist-service-admin subscription generate --name "客户C" --quota-gb 100 --source "源网ID" --base-url http://10.20.32.13:9182 --output customer-c.txt
server-network-assist-service-admin subscription generate --name "客户D" --quota-gb unlimited --source "源网ID1" --source "源网ID2" --base-url http://10.20.32.13:9182 --output customer-d.txt
server-network-assist-service-admin subscription generate --name "代理客户" --quota-gb 50 --source "源网ID" --proxy-source "源网ID" --base-url http://10.20.32.13:9182 --output customer-proxy.txt
server-network-assist-service-admin customer suspend "客户ID"
server-network-assist-service-admin customer resume "客户ID"
server-network-assist-service-admin customer revoke "客户ID"
server-network-assist-service-admin grant revoke "授权ID"
server-network-assist-service-admin grant set-egress "授权ID" --egress-mode physical --egress-interface enp4s0 --egress-gateway 10.20.32.1 --dns 223.5.5.5,1.1.1.1
server-network-assist-service-admin grant set-egress "授权ID" --egress-mode source_proxy --egress-interface Meta --dns 223.5.5.5,1.1.1.1
server-network-assist-service-admin lease revoke "租约ID"
server-network-assist-service-admin usage show --customer "客户ID"
```

订阅文件含凭据，不要提交 GitHub。源网 JSON 包含 name、endpoint、relay_public_key、address_pool、relay_interface、egress_interface；不包含源机 SSH 凭据。客户地址池不要与管理网段重叠。支持 egress_mode、egress_gateway 和 dns，详见 [商业出口策略](docs/COMMERCIAL_EGRESS.md)。修改客户线路出口会撤销旧租约，客户需退网后重新入网。

## 客户端与安装

```powershell
server-network-assist-client
server-network-assist-client --device-id
server-network-assist-client --serve --data 'C:\\客户数据目录'
```

Windows 独立客户安装（管理员 PowerShell）：

```powershell
.\\scripts\\install_client_package.ps1 -RuntimeSource '嵌入式Python目录' -PackageArchive 'desktop-packages.zip' -PackageSha256 '构建包SHA256' -Version '0.7.0'
```

Linux 安装：`python3 scripts/install_client_linux.py --help`。首次输入管理员给的 `http://校园私有IP:9182/#enroll=令牌` 或公网 HTTPS 地址。客户端后台读取地址，再选择节点启动借网。服务到期或租约过期会退出客户隧道。Wi-Fi 名称仅是提示；源网服务的真实可达性才是能否接入的依据。当前没有自动搜索和连接 Wi-Fi 的实现。

## 源机部署和本机作为源网

Linux 源机必须部署 WireGuard、IP 转发、NAT、商业中继计量与限速。准备专用接口与地址池，避免修改管理跳板。

```bash
sudo python3 scripts/bootstrap_commercial_source.py --wheel dist/server_network_assist-0.7.0-py3-none-any.whl --owner a --endpoint 10.20.32.13:51910
sudo python3 scripts/install_client_relay.py --control-url http://127.0.0.1:9182 --token-file /etc/server-network-assist-relay.token --wireguard-interface sna-commercial --relay-id sna-commercial --interval 10
sudo systemctl status server-network-assist-relay.timer
sudo journalctl -u server-network-assist-relay.service --no-pager -n 50
```

`bootstrap_commercial_source.py` 面向现有 4090 用户服务部署，需 root，首次运行会创建独立的 `sna-commercial` 接口；不能直接套用到其他路径或重复覆盖已有配置。

Windows 本机成为源网：先有可用公网出口，安装 WireGuard/WinNAT 中继，给专用接口配置局域网 UDP 端口、公钥、客户地址池；再在管理网页登记源网，并让订阅选择该节点。

```powershell
.\\scripts\\install_windows_client_relay.ps1 -Help
python -m server_network_assist.windows_client_relay --help
```

Windows 中继目前不支持按客户可靠执行限速，不能作为已验证的完整限速商业源机；真实限速套餐优先使用 Linux 中继。本机加入管理台主机列表也不等同于完成中继部署。

## 校园认证与诊断

```powershell
python netlogin.py current-status --json
python netlogin.py logout
Get-NetRoute -AddressFamily IPv4
Get-DnsClientServerAddress
& 'C:\\Program Files\\WireGuard\\wg.exe' show
curl.exe -I https://whm12.art/
```

安全登录使用 `netlogin.py login-stdin` 从标准输入接收 username/password/service JSON，不把口令写到命令行、文档或 Git。0=校园网，1=移动。借网客户无需自行完成运营商认证，但必须能到达源机的校园局域网端口。

## 全部 CLI 参数

以下内容自动从当前实现生成，涵盖管理、借网、Clash、套餐、客户、节点、租约、签名订阅和中继命令。
'''
    sections = [intro]
    for tree in (parser(), build_parser()):
        for item in walk(tree):
            sections.append('\n### ' + item.prog + '\n\n```text\n' + item.format_help() + '```\n')
    commands = [('cli', []),('cli',['init']),('cli',['serve']),('cli',['import-ssh']),
                ('client',[]),('desktop',[]),('client_admin',['keygen']),('client_admin',['sign']),
                ('client_relay',[]),('client_relay',['apply']),('client_relay',['revoke']),
                ('client_relay',['measure']),('client_relay',['status']),('client_relay',['reconcile']),
                ('client_relay_agent',[]),('windows_client_relay',[])]
    for module, args in commands:
        argv = [sys.executable,'-m','server_network_assist.' + module,*args,'--help']
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', env=__import__('os').environ | {'PYTHONIOENCODING':'utf-8'})
        if result.returncode:
            raise RuntimeError(result.stderr)
        title=('python -m server_network_assist.' + module + ' ' + ' '.join(args)).rstrip()
        sections.append('\n### ' + title + '\n\n```text\n' + result.stdout + '```\n')
    (ROOT / '命令大全.md').write_text(''.join(sections), encoding='utf-8')


if __name__ == '__main__':
    main()
