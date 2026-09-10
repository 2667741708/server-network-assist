# Windows 与 Ubuntu

日常本机连接开关和桌面快捷方式见 [桌面面板指南](DESKTOP.md)。

| 功能 | Windows | Ubuntu/Linux |
| --- | --- | --- |
| 管理台 | Python 3.11+ | Python 3.11+ |
| SSH / DNS / 直连公网探测 | 原生 PowerShell | sh / getent / curl 或 wget |
| 系统代理诊断 | 比较直连与当前 SSH 用户的系统代理请求 | 当前探测绕过代理 |
| 借网客户端 | WireGuard Windows 服务 | wg-quick / systemd |
| 借网出口机 | 原生 WireGuard / WinNAT（须无已有 NAT 冲突） | iptables / IPv4 转发 |
| 可选 HTTP/HTTPS 代理共享 | 隧道内 portproxy 中继；客户端修改 SSH 用户系统代理 | 标准库中继；客户端设置新登录 shell、APT、已登录 GNOME 会话 |

新增四种共享方向的前提、代理作用范围及验证边界见 [SHARING.md](SHARING.md)。这不等于全部系统版本均支持 WinNAT，或四种组合都已经做过实机验收。

Windows 探测先识别原生系统；即便 PATH 中存在 WSL 的 sh，也不会把 Windows 网络诊断送进 WSL。管理台通过 SFTP 上传临时探测脚本，执行后删除；探测不修改路由或代理。SSH 用户与桌面用户不同时，系统代理结果仅代表 SSH 用户，不能推断所有用户的浏览器状态。

## Windows 管理台安装

在源码目录使用 PowerShell，逐条执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\server-network-assist.exe init --data .\data
.\.venv\Scripts\server-network-assist.exe serve --data .\data --bind 127.0.0.1 --port 9180
```

Windows 支持尚未发布到旧的 v0.1.0 Release；使用当前源码或包含这些变更的新构建。管理台运行不要求管理员；远程安装与控制 Windows 网络辅助程序需要已经提权的管理员 SSH 会话。

## Windows 客户端

安装官方 [WireGuard for Windows](https://www.wireguard.com/install/)，并启用 OpenSSH Server 和 SFTP。管理台中添加 Windows 原生 SSH 地址，核对指纹，点击“安装辅助程序”。辅助程序保存在 `C:\ProgramData\ServerNetworkAssist`，目录权限限定为 Administrators 和 SYSTEM，私钥不会传回管理台。

也可在管理员 PowerShell 中手工安装：

```powershell
New-Item -ItemType Directory -Path 'C:\ProgramData\ServerNetworkAssist' -Force
icacls.exe C:\ProgramData\ServerNetworkAssist /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
Copy-Item -LiteralPath '.\src\server_network_assist\windows_helper.ps1' -Destination 'C:\ProgramData\ServerNetworkAssist\windows_helper.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\ServerNetworkAssist\windows_helper.ps1 bootstrap
```

选择符合前提的 Ubuntu/Linux 或 Windows 作为出口，Windows 作为客户端。辅助程序使用两个 IPv4 `/1` 路由，不删除原默认路由；先保存 endpoint 和 SSH 控制路由，再启用原生 WireGuard 服务。Windows 的 DNS 仅配置在隧道网卡上，使用 `223.5.5.5` 和 `1.1.1.1`；物理网卡 DNS 保留。现有 `/1` 全隧道路由会阻止启用新方案，避免与已有 VPN 争抢公网路由。

启用前注册 120 秒回退计划任务；复检成功才取消。启用维护时每分钟检查隧道来源地址的公网 TCP 连通性，连续 6 次失败停用并恢复；开机维护先恢复控制路由再启动服务。关闭维护时重启后不会自动启动隧道。实现使用官方 [Windows tunnel service 接口](https://github.com/WireGuard/wireguard-windows/blob/master/docs/enterprise.md)。

紧急回退（管理员 PowerShell）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\ServerNetworkAssist\windows_helper.ps1 status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\ServerNetworkAssist\windows_helper.ps1 disable PROFILE_ID
```

仅删除该方案创建的路由、服务和计划任务。清理失败时保留恢复记录，修复错误后可重试；不要手工删除状态目录。

## Windows 浏览器无法联网，但 WSL 可以

先比较“直连公网”和“系统应用联网”。若直连正常、系统应用失败，项目显示系统代理故障，不再把它当作隧道断网。常见原因是 Windows 仍指向本地代理端口，但代理上游失败；端口能连接也不等于代理可用。

在 Windows“设置 → 网络和 Internet → 代理”检查手动代理与 PAC。已有原生 WireGuard 出口时，可关闭发生故障的手动代理，并关闭代理客户端保存的系统代理开关。代理设置按用户生效；浏览器有单独代理扩展时，还需检查扩展。项目只报告此类故障，不会在普通探测中改写用户代理设置。

## 验证

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check_windows_scripts.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test_windows_helper.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

此前桌面/客户端版本验证包含 Titan 原生 Windows 的真实网络请求、helper bootstrap、密钥生成、配置生成、现有隧道冲突拒绝及停用；Ubuntu 实机直连探测；模拟部分路由创建失败和清理失败时的恢复记录。未在 Titan 上切换现有工作隧道，也未做重启验收。新增 Windows 出口和代理共享的测试范围另见 [SHARING.md](SHARING.md#验收记录与建议)，不要将此前证据视为新功能实机验收。
