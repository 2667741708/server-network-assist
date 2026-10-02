# 商业出口策略

## 两种出口

| 策略 | 转发路径 | 使用条件 |
| --- | --- | --- |
| `physical`（默认） | 客户专用 WireGuard → 按客户 `/32` 和入接口匹配的专用路由表 → 源机物理网关 | 源机宽带正常登录；外网代理由客户自己的 Clash 决定，源机代理可关闭 |
| `source_proxy`（按订阅明确启用） | 客户专用 WireGuard → 源机原策略路由 → 源机代理/TUN | 源机 Mihomo 服务和对应 TUN 同时运行；客户可不开本机代理 |

两类客户可同时使用同一商业中继，分别配置出口接口。物理出口并不赋予源机自己的进程代理能力，也不承诺不运行客户代理时可访问所有境外网站。物理模式当前只支持 IPv4；IPv6 客户转发拒绝。

本轮物理出口实现用于 Linux/Ubuntu源端，Windows客户经 D321 实机测试。Windows源端的独立物理出口策略尚未实现，不能直接套用 Linux 命令。

## 管理网页

1. 源网登记一次物理接口、所在网段的实际网关及公共 IPv4 DNS；4090 已配置 `enp4s0` / `10.20.32.1`。
2. 选择源网、客户和套餐，直接生成订阅。未勾选代理选项时，始终走物理出口，4090 开着代理也不会改变它。
3. 如某个新订阅需要源网代理：先看“4090 源机代理”状态；未就绪时输入管理员凭据点击“启动 4090 Mihomo / TUN”，再为本次订阅勾选对应源机代理选项。未就绪选项禁用，后台也拒绝签发代理授权。
4. 如现有订阅需要代理：在“商业出口配置”选择该客户线路 → 修改客户线路出口 → 选择源机代理。接口自动填写 `Meta`，保存后旧租约撤销，客户退出并重新入网。恢复物理出口也只修改该授权。

代理进程和订阅授权是两个开关。启动代理只调用源端现有 `mihomo.service`，不改变其他客户出口、规则模式或配置文件。代理停止后，新代理租约和续租被拒绝，代理节点标为不可用，中继下一次同步撤销其转发；不会偷偷切换成物理模式。物理订阅不依赖该状态。服务/TUN 检查不等于所有网站都可达。

当前管理按钮控制本机 4090；远程源机未接入代理状态检测时拒绝代理授权，不能误把 4090 的状态当成其他源机状态。IPv4 物理订阅仍可配置其他已部署源机。

独立 `/subscriptions.html` 页也提供“商业出口配置”，可分别修改源网和已有客户线路。修改均要求管理员重新认证。客户响应只公开出口类型和 DNS，不公开源机网关、路由表及管理凭据。

公网实际入口：[源网订阅管理](https://whm12.art/subscription-admin/subscriptions.html)。cloud 协作台与商业源端后台不同，出口配置在这个源端管理入口完成。

## CLI

指定实际服务数据库，勿对另一份空数据库操作：

```bash
export SNA_CLIENT_SERVICE_DB=/home/a/.local/share/server-network-assist-commercial/data/commercial-service.sqlite3
server-network-assist-service-admin grant list
server-network-assist-service-admin source-proxy status
server-network-assist-service-admin source-proxy start
server-network-assist-service-admin grant set-egress "授权ID" --egress-mode physical --egress-interface enp4s0 --egress-gateway 10.20.32.1 --dns 223.5.5.5,1.1.1.1
```

返回源机代理出口（也会撤销旧租约，需重新入网）：

```bash
server-network-assist-service-admin grant set-egress "授权ID" --egress-mode source_proxy --egress-interface Meta --dns 223.5.5.5,1.1.1.1
```

签发新订阅默认物理；仅加 `--proxy-source` 的源网使用源机代理，且要求当前源机代理就绪：

```bash
server-network-assist-service-admin subscription generate --name "客户A" --quota-gb 10 --source c201-4090-commercial --base-url http://10.20.32.13:9182 --output customer-a.txt
server-network-assist-service-admin subscription generate --name "客户B" --quota-gb 50 --source c201-4090-commercial --proxy-source c201-4090-commercial --base-url http://10.20.32.13:9182 --output customer-b.txt
```

源网 JSON 除原字段外可包含：

```json
{
  "egress_mode": "physical",
  "egress_interface": "enp4s0",
  "egress_gateway": "10.20.32.1",
  "proxy_interface": "Meta",
  "dns": "223.5.5.5,1.1.1.1"
}
```

这是字段示例，不能单独用它创建源网；完整文件还需源网 ID、名称、Endpoint、公钥、客户地址池和商业 WireGuard 接口。新源网缺省策略为 physical；未填写网关时仅在主路由表有唯一物理出口且接口匹配时读取它，不能把源端 TUN 当成物理出口。旧源网配置必须先补齐物理出口，签发不会静默沿用旧代理默认。

4090 本轮将其商业 UDP 51910 入口的 9 条旧授权转为物理出口。因为其 DNS 已是公共 IPv4、AllowedIPs 已是 IPv4，保留了已签发租约；其他旧入口未修改。一般网页切换仍撤销旧租约，以保证客户端 DNS 与策略一致。

## 所有权与失败处理

- 修改订阅不修改主路由表默认出口、源机 DNS、源机 Clash/TUN开关、原管理 VPN、客户端热点和校园路径。只有显式“启动源机代理”动作启动已有服务。
- 物理出口使用优先级 `100`、来源 `/32`、入接口限定的策略规则。独立表编号由租约 ID 计算；写入前检查接口、网关所属子网、已有规则/路由以及冲突。
- 专用表先安装终止性 `unreachable default`，再安装物理默认路由和来源规则。物理链路失效时不回落到源端 TUN。防火墙同时限制客户只能走选定出口，并拒绝物理客户的 `198.18.0.0/15` fake-IP 目的地址。
- 客户源地址只允许已授权的 WireGuard peer；防火墙拒绝源机本机访问、管理网段、客户间横向访问及错误出口。客户自己对校园私网的直连仍在客户物理接口上完成。
- 持久化所有权后执行变更，失败撤销 peer 并记录 `recovery_required`。退网、到期和额度用完删除该客户规则、明确路由、限速过滤器和授权元素；不全表 flush。
- 文件锁防止 CLI 与定时中继互相覆盖。重启软件后重新恢复已授权 peer、专用路由、nftables及限速。控制端失联或计量上报失败仍执行本地租约到期/额度检查；撤销生效延迟受中继轮询和请求超时影响，不是零毫秒。
- 物理模式使用公共 IPv4 DNS，拒绝私网、fake-IP和IPv6 DNS。客户自己的 Clash 仍可选择规则/全局/直连/TUN；不兼容的 IPv6-only 机场节点不能由本 IPv4 服务保证可用。

## 验收方式

先完成代码及测试脚本，再执行隔离回归和真实 Linux namespace 验收：

```bash
sudo python3 scripts/verify_commercial_egress_netns.py --source-tree src
```

脚本使用专门创建的临时 namespace、真实 WireGuard、nftables、NAT、TC和策略路由，测试模拟 TUN 捕获时旁路、TUN消失、链路丢失、软件重建、计量、额度撤销和 owned 路由清理。只变更临时 namespace，不改宿主机网络。真实公网/机场、Windows GUI、校园双向服务和软件重启另行记录，不能用 namespace 通过替代现网通过。
