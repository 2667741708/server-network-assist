# 0001. WireGuard 组网节点的底层出口与自动回退策略

- **Status**: accepted
- **Date**: 2026-09-21
- **Deciders**: 项目所有者、Server Network Assist 维护者

## Context

本项目中的服务器和工作站可能同时具备多种网络能力：

- 有线、校园 Wi-Fi、手机热点等真实物理网络；
- WireGuard Hub 组网，例如 `10.201.250.0/24`；
- 管理辅助隧道，例如 `fleet-titan`；
- 客户借网隧道；
- Meta/Mihomo/TUN 等代理或透明转发网络。

WireGuard 节点的虚拟身份、密钥和虚拟地址应保持稳定，不应因为物理网卡、DHCP 地址、NAT 公网地址或借网状态变化而改变。WireGuard Endpoint 的底层公网数据包仍需要经过一条真实可用的外层路径。

过去的局部修复曾把 Hub 公网 Endpoint 永久绑定到某个物理网关，或永久加入某条管理辅助隧道。这能解决单次故障，但会引入新的耦合：

- 切换 Wi-Fi、热点或有线网络后，旧 `/32` 路由可能失效；
- 停止借网或停止管理辅助隧道后，Hub 也随之断开；
- Windows 服务仍显示 `Running`，但 WireGuard 已长期没有握手；
- 不同服务器各自维护一次性脚本，行为和恢复边界不一致；
- 管理隧道、客户隧道和 Hub 隧道之间可能形成递归路由或错误依赖。

以 d321-titan 为例，Hub 身份为 `10.201.250.50`，Hub 公网 Endpoint 为 `140.143.202.144:51820`。2026-09-21 曾出现 Endpoint 被固定到旧有线网关，随后临时改为经 `fleet-titan` 和 C201-4090 到达 Hub。后者已经恢复连接，但属于回退路径，不应成为所有节点的永久默认。

## Decision

所有通过 WireGuard 加入组网的服务器和工作站，默认采用“真实网络直连优先、受控源网自动回退、恢复后自动回切”的分层策略。

### 1. 不变的虚拟身份

以下内容与实际出网方式解耦：

- WireGuard 私钥与公钥；
- Hub 中的虚拟地址；
- Peer 身份和 ACL；
- SSH 别名所指向的虚拟地址；
- Hub 内部服务地址。

更换 Wi-Fi、有线网络、手机热点、DHCP 地址或公网 NAT 地址时，不得重新分配 WireGuard 虚拟地址。

### 2. 默认路径：当前真实网络

在没有客户全隧道接管的普通状态下，不为 Hub Endpoint 创建永久、固定下一跳的 `/32` 路由。由操作系统根据当前有效物理默认路由选择：

```text
WireGuard Endpoint
  → 当前有效的 WLAN / 有线 / 手机热点
  → 公网 Hub
```

Windows 和 Linux 的 WireGuard 漫游能力负责适应公网 NAT 地址变化。节点切换物理网络后，应能在不修改 WireGuard 虚拟配置的情况下重新握手。

### 3. 借网期间的直连保护

当客户借网隧道安装 `0.0.0.0/1`、`128.0.0.0/1` 或其他全局路由时，路径控制器应先尝试保护 Hub Endpoint 的物理直连：

- 动态发现当前真实物理默认路由；
- 排除 WireGuard、Wintun、Meta、TAP、ZeroTier、Hyper-V 等虚拟接口；
- 仅创建由路径控制器拥有的临时 Endpoint `/32` 路由；
- 物理网卡、网关或 DHCP 地址变化后自动重建；
- 借网退出时删除该临时路由，不遗留永久下一跳。

不得把安装时观察到的某个网关永久写入配置。

### 4. 备用路径：受控源网

如果真实物理路径连续无法完成 WireGuard 握手，而管理辅助隧道和源网网关均健康，允许把 Hub Endpoint 的单一 `/32` 临时切换到管理辅助隧道：

```text
节点
  → 管理辅助 WireGuard
  → 源网服务器
  → 源网物理出口 NAT
  → 公网 Hub
```

备用路径必须满足：

- 只包含 Hub Endpoint 的精确 `/32`，不得把普通公网默认路由混入管理隧道；
- 源网网关显式允许该 Endpoint 和端口，并执行转发/NAT；
- 不依赖客户借网隧道自身，避免隧道递归；
- 备用入口、接口和所有权写入状态文件；
- 管理辅助隧道停止时立即撤销备用路由，回到真实网络探测；
- 真实网络连续恢复后自动回切，不永久依赖源网。

### 5. 状态机与防抖

统一路径控制器至少包含以下状态：

```text
DIRECT_PROBING
  ├─ 握手成功 → DIRECT_HEALTHY
  └─ 连续失败 → FALLBACK_PROBING

FALLBACK_PROBING
  ├─ 源网握手成功 → FALLBACK_ACTIVE
  └─ 源网不可用 → DEGRADED

FALLBACK_ACTIVE
  ├─ 定期探测真实网络
  ├─ 真实网络连续成功 → DIRECT_HEALTHY
  └─ 源网失效 → DIRECT_PROBING / DEGRADED

DEGRADED
  └─ 任一路径恢复 → 对应健康状态
```

建议默认阈值：

- 不能只看服务是否为 `Running`；必须读取最新 WireGuard 握手时间；
- 至少连续 3 次失败，且持续 60–120 秒，才从直连切到备用；
- 至少连续 3 次真实网络探测成功，才从备用回切直连；
- 服务重启设 30 分钟冷却，避免网络不可用时反复重启；
- 路由切换后验证实际选路、最新握手和 Hub 内目标连通性；
- TCP/HTTPS 普通上网成功不能替代 WireGuard UDP 握手证据。

### 6. 路由所有权与恢复

路径控制器只允许修改自己明确记录为 owned 的资源：

- Hub Endpoint 临时 `/32` 路由；
- 本控制器创建的恢复任务和状态文件；
- 明确属于目标 Hub 的 WireGuard 服务。

禁止清理或重写：

- 用户未知来源的历史路由；
- WSL、Docker、Hyper-V、ZeroTier 的路由；
- 客户借网客户端拥有的隧道和恢复记录；
- 其他 WireGuard Hub 或管理隧道；
- 系统默认网关、DNS 或代理设置。

### 7. 失败时的主操作原则

- 健康检查、状态文件和日志属于辅助功能，写入失败不得阻断现有健康隧道；
- 路由修改失败时保留原可用路径，并报告 `recovery_required`；
- 新路径未验证成功前，不删除最后一条已验证路径；
- 远程变更必须具有定时回退或本地恢复入口；
- 不允许仅凭计划任务成功退出码宣称组网恢复。

## Current transition state

截至 2026-09-21：

- d321-titan 已通过 `fleet-titan → C201-4090 → 140 Hub` 恢复 Hub 握手；
- `hmw20` SSH 别名已完成真实登录验证；
- 该配置是已验证的备用路径，不是本 ADR 规定的最终默认模式；
- 在统一控制器落地前，不应把 d321 的过渡配置复制到其他节点；
- C201-4090 的 `wg-fleet-gateway` 已具备对 Hub Endpoint UDP/51820 的精确直连转发规则，可作为备用路径执行端。

## Required implementation changes

### A. 新增统一策略模块

在 `src/server_network_assist/` 新增独立模块，例如：

```text
wireguard_underlay.py
```

职责：

- 描述 Hub Endpoint、Hub 接口、可选管理辅助接口；
- 保存状态机、失败计数、切换时间、最近握手和 owned 路由；
- 生成平台无关的“期望路径”；
- 不直接包含 Windows PowerShell 或 Linux iproute2 命令。

建议核心接口：

```python
class UnderlayPolicy:
    def observe(self) -> UnderlayObservation: ...
    def decide(self, observation) -> UnderlayDecision: ...
    def apply(self, decision) -> UnderlayResult: ...
    def recover(self) -> UnderlayResult: ...
```

### B. 新增 Windows 执行助手

新增或扩展 Windows helper，例如：

```text
src/server_network_assist/windows_wireguard_underlay.ps1
```

职责：

- 枚举真实物理默认路由；
- 创建和删除 owned Endpoint `/32`；
- 查询 WireGuard 最新握手；
- 切换到管理辅助隧道的精确 AllowedIPs；
- 回切时恢复原管理隧道 AllowedIPs；
- 以原子状态文件记录修改前后值；
- 不修改客户隧道以外的代理、DNS 和系统默认路由。

当前 d321 上的 `C:\ProgramData\WireGuardHub\hub-route-health.ps1` 应被视为临时实例，最终由安装器生成或由统一 helper 替代，不能继续手工漂移。

### C. 扩展 Linux 执行助手

在 Linux helper 或独立脚本中提供：

- Endpoint 精确直连规则；
- 管理辅助子网的源地址校验；
- nftables/iptables 转发和 NAT；
- 策略路由 mark 与专用路由表；
- 幂等 apply/remove/status；
- 规则所有权标记与回滚。

现有 `/usr/local/sbin/wg-fleet-gateway` 中针对 Hub Endpoint 的规则应迁移为模板化配置，不能继续把公网 IP、接口、网关和端口硬编码为单机常量。

### D. 修改客户端隧道安装流程

修改：

- `src/server_network_assist/client_tunnel.py`
- `src/server_network_assist/client_clash_coexist.py`
- 与 Windows/Linux 安装、续租和退网有关的 helper

要求：

1. 安装客户全隧道路由前，先调用 UnderlayPolicy 保护 Hub Endpoint；
2. 客户隧道握手成功后，不得覆盖管理 Hub 的 owned 路由；
3. 退网时只删除本次客户隧道资源，并通知 UnderlayPolicy 回到正常直连；
4. 客户租约续期、重启或崩溃恢复不能把 Hub Endpoint 静默吸入客户隧道；
5. 管理 Hub 是否健康应作为入网后验收项，而不是只检查客户公网是否可用。

### E. 修改通用网络编排

修改：

- `src/server_network_assist/network_assist.py`
- `src/server_network_assist/network_assist_helper.py`
- `src/server_network_assist/windows_helper.ps1`

要求：

- 网络方案显式声明 `management_endpoints`；
- 运行时配置自动生成 Endpoint 保留和回退策略；
- 跨 Windows/Linux 使用相同状态语义；
- 禁止在方案中保存安装时探测到的永久默认网关；
- disable/recover 恢复到策略控制器接管的默认状态，而不是恢复陈旧网关。

### F. 修改安装器与计划任务

修改 Windows/Linux 安装脚本，使每个节点安装统一健康服务：

- 开机启动；
- 网络变化事件触发；
- 低频定时兜底；
- 单实例锁；
- 失败退避；
- 日志失败不阻断路由恢复；
- 卸载时恢复 owned 路由和原 AllowedIPs。

Windows 不应仅依赖一分钟轮询。应同时订阅网络配置变化事件，轮询只作为漏事件后的兜底。

### G. 管理模型与配置格式

配置中新增类似结构：

```json
{
  "hub_interface": "hub-140",
  "hub_endpoint": "140.143.202.144:51820",
  "virtual_address": "10.201.250.50/32",
  "path_policy": "direct_then_fallback",
  "fallback": {
    "interface": "fleet-titan",
    "gateway_node": "c201-4090",
    "allowed_endpoint": "140.143.202.144/32"
  },
  "thresholds": {
    "direct_failures": 3,
    "direct_recovery_successes": 3,
    "stale_handshake_seconds": 120,
    "restart_cooldown_seconds": 1800
  }
}
```

不得在该配置中保存 WireGuard 私钥、SSH 密码或订阅令牌。

## Verification requirements

实现完成前不能只做静态配置检查。至少覆盖以下矩阵：

| 场景 | 预期结果 |
| --- | --- |
| 有线直连可用 | Hub 走有线，虚拟地址不变 |
| 切换到 Wi-Fi | 自动重新握手，无需改配置 |
| 切换到手机热点 | 自动重新握手，Hub 学习新公网 Endpoint |
| 借网开启但物理 UDP 可用 | Hub 保持物理直连 |
| 物理 UDP 被封锁 | 自动切到受控源网备用路径 |
| 物理 UDP 恢复 | 防抖后回切真实网络 |
| 停止借网 | Hub 不依赖客户隧道，仍保持或恢复直连 |
| 停止管理辅助隧道 | 撤销备用 `/32`，进入直连探测，不遗留黑洞路由 |
| 重启节点 | 状态恢复，虚拟地址和密钥不变 |
| 源网重启 | 节点先尝试直连，备用恢复后可再次切换 |
| Hub 暂时离线 | 不循环重启、不破坏默认网络 |
| 客户退网 | 只清理客户资源，Hub 与管理隧道不受影响 |

每项验收必须记录：

- 物理接口与网关；
- Endpoint 的实际选路；
- WireGuard 最新握手；
- Hub 内双向 TCP/SSH；
- 切换前后 owned 路由；
- 客户公网、管理通道和原网络恢复结果。

## Rollout order

1. 先实现纯观察模式，只报告当前路径和建议决策，不改网络。
2. 在隔离 Windows 节点验证直连漫游。
3. 在测试源网验证备用转发和 NAT。
4. 在 d321-titan 启用自动状态机，替换当前过渡脚本。
5. 完成有线、Wi-Fi、手机热点、借网启停和重启矩阵。
6. 再推广至其他 Windows/Linux Peer。
7. 删除已被统一控制器接管的单机硬编码任务和过期 `/32` 路由。

## Consequences

- Positive: WireGuard 虚拟地址与实际网络解耦，节点可在有线、Wi-Fi和热点之间漫游。
- Positive: 真实网络可用时不依赖源网，源网只承担受控备用职责。
- Positive: 借网启停不再决定管理 Hub 是否可用。
- Positive: Endpoint 路由、恢复和证据格式在所有节点统一。
- Negative: 需要维护一个跨平台路径状态机，并处理网络变化、防抖和回滚。
- Negative: 备用路径要求源网提前配置精确转发和 NAT。
- Negative: 可靠验证必须包含真实网络切换，单元测试不能替代现场验收。
- Neutral: WireGuard 密钥、虚拟地址和现有 Hub 拓扑无需改变。

## Alternatives considered

### 永久绑定物理网关

拒绝。网关、接口和 DHCP 地址会变化，移动节点容易产生黑洞路由。

### 永久绑定源网管理隧道

拒绝作为默认方案。虽然当前 d321 已验证可用，但会让 Hub 永久依赖源网，与停止借网后继续组网的目标冲突。

### 让 Hub Endpoint 随客户全隧道走

拒绝作为无条件行为。可能产生递归依赖，客户退网、租约失效或源网故障会同时破坏管理 Hub。

### 只依赖 WireGuard 服务状态

拒绝。`Running` 不代表发生过近期握手，也不能证明 Hub 内服务可达。

## References

- `docs/ARCHITECTURE.md`
- `docs/SHARING.md`
- `docs/CLIENT_RELAY.md`
- `借网管理面板/handoffs/2026-09-16-新客户端迁移.md`
- `借网管理面板/handoffs/2026-09-16-校园互通与TUN兼容.md`
- `借网管理面板/handoffs/2026-09-17-Titan双向SSH与Codex无线任务.md`
