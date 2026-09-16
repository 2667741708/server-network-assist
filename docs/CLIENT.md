# 借网客户端（客户纯享版）

该客户端面向获授权使用个人购买宽带出口的设备。它与学校免费校园网额度无关，线路名称可以标记为“个人购买的中国移动校园宽带出口”。使用者只能更新订阅、检测线路、启动借网和退出借网，不能访问 SSH、跳板机、管理面板或源网服务器控制接口。

## 当前可交付能力

- Windows 和 Ubuntu 共用同一 Python 客户端与 Framework7 界面。
- HTTPS 配置地址携带固定的 Ed25519 公钥；订阅正文被修改、过期、未绑定设备或跨域跳转时拒绝使用。
- 显示匿名线路名称、可用性、上下行速度上限、流量额度、已用流量和到期时间。
- TCP 接入延迟检测；只允许启动签名订阅中列出且本机已经安装的 WireGuard 隧道。
- 启动另一条线路前停止同一订阅中正在运行的其他线路。
- 退出借网后执行直连 HTTPS 探测，明确提示原网络是否恢复。
- 客户端使用独立的回环地址接口，不包含管理端 `/api/fleet/*`、终端、凭据或代理管理能力。

限速、月流量计量、到期停用和客户端隔离必须在接入中继执行。客户端显示的额度不能替代服务端的 `tc`/nftables/WireGuard peer 策略。

## 管理员签发订阅

生成一次 Ed25519 签名密钥：

```powershell
server-network-assist-client-admin keygen --private-key .\private\client-subscription.key
```

命令输出公钥。私钥只能放在管理端，不能进入客户端、静态站点或 Git 仓库。

准备 `payload.json`：

先让客户在客户端订阅区域复制“当前设备编号”，或在客户机器运行：

```powershell
server-network-assist-client --device-id
```

把该值原样写入 `device_id`。订阅复制到另一台机器时会被拒绝。

```json
{
  "schema_version": 1,
  "version": 1,
  "subscription_id": "sub-customer-001",
  "device_id": "device-customer-001",
  "nonce": "replace-with-random-value",
  "issued_at": 1789516800,
  "expires_at": 1789517400,
  "customer": {"display_name": "移动宽带用户"},
  "lines": [
    {
      "id": "mobile-line-a",
      "name": "个人购买的中国移动校园宽带出口",
      "tunnel": "sna-customer-a",
      "endpoint": "relay.example.com:51820",
      "available": true,
      "download_bps": 2500000,
      "upload_bps": 625000,
      "quota_bytes": 107374182400,
      "used_bytes": 0
    }
  ]
}
```

签名：

```powershell
server-network-assist-client-admin sign --private-key .\private\client-subscription.key --payload .\payload.json --output .\subscription.json
```

将 `subscription.json` 放到 HTTPS 地址。给客户的配置地址格式如下，`#key=` 后填写 keygen 输出的公钥：

```text
https://example.com/subscriptions/high-entropy-token.json#key=BASE64URL_PUBLIC_KEY
```

URL 的片段部分不会发送给服务器。客户端只用它验证下载内容。

## Windows 安装和启动

先安装同版本 Server Network Assist 离线桌面运行时，再用管理员 PowerShell 运行：

```powershell
.\scripts\install_client.ps1 -Version 0.6.0
```

安装器创建“借网客户端”桌面快捷方式和登录启动任务。后台以提升权限运行，界面只监听 `127.0.0.1`，本机随机令牌通过 URL 片段交给页面后立即从地址栏清除。

开发环境可分别启动后台和打开界面：

```powershell
server-network-assist-client --serve --data "$env:LOCALAPPDATA\ServerNetworkAssistClient"
```

```powershell
server-network-assist-client --data "$env:LOCALAPPDATA\ServerNetworkAssistClient"
```

## Ubuntu 运行说明

Ubuntu 使用相同入口和订阅协议。全局安装当前 Python 包后，以 root 身份为指定桌面用户安装后台和快捷方式：

```bash
sudo python3 scripts/install_client_linux.py --user "$USER"
```

安装器创建仅监听本机的 root systemd 服务，使其可以切换 WireGuard 服务；随机界面令牌只交给指定桌面用户。订阅、签名缓存和活动线路记录位于 `/var/lib/server-network-assist-client/<uid>`，普通用户不能直接修改。需要登录桌面后自动打开界面时增加 `--autostart-ui`。

## 当前限制

- 线路 WireGuard 配置必须预先由管理员安全安装。当前订阅不会下发私钥或执行任意配置脚本。
- 当前版本是签名静态订阅 MVP；完整商业控制面仍需实现在线设备注册、短期 lease、即时撤销、服务端 peer 生命周期、计量和限速。
- 校园局域网指纹和 Wi-Fi 自动选择尚未用于自动借网。SSID 不能作为唯一判断条件。
