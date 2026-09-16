# Windows 商业中继

Windows 商业中继让 Windows 电脑充当客户出口。它从商业控制面读取短期租约，把客户公钥加入指定 WireGuard 隧道，使用 WinNAT 转发流量，并从 WireGuard 内核累计计数器上报真实收发字节。服务端撤销、租约到期或额度耗尽后，中继会删除对应 peer 和防火墙规则；默认轮询间隔为 10 秒。

## 能力边界

| 能力 | Windows 中继状态 |
| --- | --- |
| WireGuard peer 授权和 `/32` 地址限制 | 强制执行 |
| WinNAT 出口 | 强制执行；发现不属于本项目的现有 WinNAT 时拒绝修改 |
| WireGuard 内核真实流量计量 | 支持 |
| 服务端撤销、到期和额度停用 | 支持，下一次同步生效 |
| 客户子网和管理网隔离 | Windows 防火墙规则 |
| 每个客户独立上传/下载限速 | **不支持** |

Windows 没有受项目支持的原生、可靠、按 WireGuard peer 限速器。控制面套餐中的速率仍会保留，但 Windows 中继会明确返回 `download_rate_limit: unsupported` 和 `upload_rate_limit: unsupported`，不会显示为已限速。需要强制限速的商业线路应使用 Linux 中继的 `tc`。

## 安装

先安装 WireGuard for Windows 和本项目 Python 包，以管理员 PowerShell 准备仅 SYSTEM 与 Administrators 可读取的中继令牌文件，然后运行：

```powershell
.\scripts\install_windows_client_relay.ps1 `
  -ControlUrl 'https://service.example.com' `
  -TokenFile 'C:\ProgramData\ServerNetworkAssist\relay.token' `
  -RelayId 'windows-relay-01' `
  -WireGuardInterface 'wg-customer' `
  -Interval 10
```

安装器创建以 SYSTEM 管理员身份开机运行的 `ServerNetworkAssist-CommercialRelay` 计划任务，并限制数据目录和令牌文件 ACL。后台进程持续运行，每 10 秒同步一次；请求仅接受 HTTPS，拒绝控制面重定向。

## 撤销验证

服务端撤销线路或租约后，等待一个同步周期，再在管理员 PowerShell 查看：

```powershell
& "$env:ProgramFiles\WireGuard\wg.exe" show wg-customer peers
Get-NetFirewallRule -Name 'SNA-Commercial-*'
```

被撤销公钥必须从第一条命令消失，对应 `SNA-Commercial-<租约 ID>-*` 规则也必须消失。中继状态文件位于 `C:\ProgramData\ServerNetworkAssist\commercial-relay\state.json`，其中的速率能力必须显示为 `unsupported`。
