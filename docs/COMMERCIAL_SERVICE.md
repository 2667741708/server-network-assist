# 商业借网服务：部署与运营手册

本模块在客户纯享版之上提供在线开户、套餐授权、短期租约、服务端即时撤销、真实流量计量和中继限速。客户只拿到 HTTPS 订阅地址，不会看到源网服务器的 SSH 地址、账号、私钥或管理接口。

## 管理边界

- **套餐**规定租约时长、设备数、总流量和上下行速率。
- **线路**对应可用的受控出口；线路记录中不要保存可直接登录源网服务器的明文凭据。
- **授权**连接客户、套餐和允许使用的线路。
- **租约**是客户端在线申请的短期凭证。暂停客户、撤销授权或超过流量额度后，服务端拒绝续租并终止活动租约。
- **计量**以中继实际转发的接收、发送字节为准；客户端显示值只能用于提示，不能作为计费依据。
- **限速**必须在中继数据面执行，不能依赖客户端自觉限速。

本软件提供技术控制能力。上线收费前，运营者仍应确认其购买的运营商产品是否允许向第三方转售、共享或提供中继服务。

## 初始化

管理员命令默认使用 `data/client-service.sqlite3`，也可以设置环境变量：

```powershell
$env:SNA_CLIENT_SERVICE_DB='D:\service-data\client-service.sqlite3'
```

命令的普通输出是 JSON，完整订阅 token、访问 token、API 密钥和私钥会被替换为 `[REDACTED]`。签发后如需把订阅地址交给客户，应通过管理 API 的专用交付流程完成，不要从终端日志复制秘密。

## 创建套餐和客户

创建 30 天套餐，最多两台设备，每次租约 15 分钟，月流量 100 GiB，下行 50 Mbit/s、上行 10 Mbit/s：

```powershell
python -m server_network_assist.client_service_admin plan create --id monthly-100g --name '100G 月套餐' --quota-bytes 107374182400 --download-bps 50000000 --upload-bps 10000000 --max-devices 2 --lease-seconds 900
```

查看配置：

```powershell
python -m server_network_assist.client_service_admin plan list
```

## 在线开户和授权

开户只保存业务所需的客户标识。敏感联系方式应遵循最小化原则：

```powershell
python -m server_network_assist.client_service_admin customer create --id customer-001 --name '客户 001' --plan monthly-100g
```

随后创建受控出口。公钥、地址和网卡名必须换成真实中继配置：

```powershell
python -m server_network_assist.client_service_admin line create --id grant-mobile-a --customer customer-001 --name '移动出口 A' --endpoint relay.example.com:51820 --tunnel customer-mobile-a --relay-public-key '<WireGuard 公钥>' --allocated-address '10.203.1.2/32' --relay-interface 'wg-customer' --egress-interface 'eth0'
```

授权一个套餐，可重复 `--line` 允许多条线路。`--expires-at` 是 Unix 秒时间戳；不提供时采用套餐或服务端策略：

```powershell
python -m server_network_assist.client_service_admin grant issue --customer customer-001 --name '移动出口 A' --endpoint relay.example.com:443 --tunnel customer-mobile-a --expires-at 1798761600
```

生成一次性开户注册令牌并写入权限受限文件。终端只显示文件位置，令牌本身不会进入普通日志：

```powershell
python -m server_network_assist.client_service_admin customer enrollment-token customer-001 --ttl 900 --output 'D:\service-data\delivery\customer-001.txt'
```

查询客户和授权：

```powershell
python -m server_network_assist.client_service_admin customer list
python -m server_network_assist.client_service_admin grant list --customer customer-001
```

## 暂停与即时撤销

临时欠费或风控可暂停客户。暂停操作应在同一事务中让该客户的活动租约失效，并触发中继关闭对应会话：

```powershell
python -m server_network_assist.client_service_admin customer suspend customer-001 --reason '欠费暂停'
```

恢复客户只允许其重新申请租约，不应复活旧租约：

```powershell
python -m server_network_assist.client_service_admin customer resume customer-001 --reason '款项确认'
```

永久撤销客户或仅撤销某项授权：

```powershell
python -m server_network_assist.client_service_admin customer revoke customer-001 --reason '合同终止'
python -m server_network_assist.client_service_admin grant revoke grant-001 --reason '套餐变更'
```

紧急终止单个活动租约：

```powershell
python -m server_network_assist.client_service_admin lease revoke lease-001 --reason '异常流量'
```

“即时撤销”的含义是：新连接立即被拒绝，现有中继会话在控制平面通知到达或下一次短周期校验时关闭。租约应保持较短，并让数据面主动接收撤销事件，不能等到长效订阅自然过期。

## 控制平面与中继部署

公网入口必须由 HTTPS 反向代理保护。为中继生成至少 32 字节的随机令牌，并仅通过服务环境变量提供：

```powershell
$env:SNA_RELAY_TOKENS='{"wg-customer":"<随机中继令牌>"}'
$env:SNA_RELAY_CUSTOMER_SUBNET='10.203.0.0/16'
$env:SNA_RELAY_MANAGEMENT_SUBNETS='10.201.0.0/16,192.168.0.0/16'
server-network-assist serve --data 'D:\service-data'
```

客户接口位于 `/client/v1/`，中继接口位于 `/relay/v1/`。设备注册令牌只可使用一次；注册后每个请求都必须带设备 ID、时间戳、随机数和 Ed25519 签名，随机数不可重放。管理台会话凭据不适用于这两组接口。

在 Linux 中继上将同一个令牌保存为仅 root 可读文件，然后安装 10 秒一次的同步任务：

```text
sudo python3 scripts/install_client_relay.py --control-url https://service.example.com --token-file /etc/server-network-assist/relay.token --relay-id wg-customer --wireguard-interface wg-customer --interval 10
```

每轮同步会先读取 WireGuard 内核累计字节并上报，再取得活动租约的期望状态；被服务端撤销、过期或超额的租约会在下一轮从 WireGuard、nftables 和 `tc` 中移除。因此默认撤销生效时间不超过约 10 秒，加上一次 HTTPS 请求时间。

## 设备、租约与真实用量

```powershell
python -m server_network_assist.client_service_admin device list --customer customer-001
python -m server_network_assist.client_service_admin lease list --customer customer-001 --active-only
python -m server_network_assist.client_service_admin usage show --customer customer-001
```

计量记录至少应包含客户、授权、租约、线路、时间窗口、接收字节和发送字节。服务端以单调递增计数器聚合，重复上报必须幂等；断线后的最终计数也要持久化。套餐额度应明确采用上传、下载之和还是分别统计，本实现按转发总字节判断总额度。

限速值使用 bit/s。数据面为每个活动设备分别应用下载和上传令牌桶；套餐总流量仍按客户名下所有设备汇总。若商业套餐要求多设备共享一个总带宽上限，应在出口层再叠加客户级整形规则。

## 建议的日常核对

1. 查询活动租约与中继会话数是否一致。
2. 核对计量最后上报时间，停止上报的活动租约应告警并关闭。
3. 检查超额、过期、暂停和撤销客户是否仍存在活动会话。
4. 抽查限速后的实测速率，允许协议开销造成少量误差。
5. 定期备份数据库和审计记录；备份中不得写入可直接使用的明文 token 或私钥。

客户支持人员可以查看脱敏后的客户、设备、租约和用量。创建线路、修改套餐、恢复客户和导出订阅凭证应仅授予管理员。
