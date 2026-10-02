# WireGuard 稳定 peer 续租与替换模拟

日期：2026-10-02
仓库：`server-network-assist`，分支 `commercial-admin-v2`
本地代码与 C201 活动控制面已修正；C201 的套餐期限数据已迁移。D321 较早一次真实借网成功，后续重验未完成 WireGuard 握手并回滚；当前及本轮 CLI 复验记录见下文。没有重置源端 WireGuard 数据面。

## 目标

同一有效设备/线路续租时不换承载数据的 WireGuard peer。有限套餐的租约使用套餐/授权的实际到期时间，不额外设置 30 天上限；无限期套餐保持永不过期，不需要周期续租，也不轮换 bearer token。显式续租请求沿用原 token、lease ID 和 peer。套餐/设备/客户/线路显式撤销仍会立即失效，中继离线截止继续独立保护失联情形。中继只在配置差异或实际状态缺失时修复 peer/路由。用每分钟轮换 peer 的隔离模拟，观察删除旧 peer 到添加新 peer 之间是否存在传输空窗。

## 实现

- `src/server_network_assist/client_store.py`：有限 WG lease 续租时保留原 lease ID 和 counters，只轮换 token digest 并更新期限；旧 token 立即失效。续租检查客户撤销、设备、套餐、线路状态及有效期。有限 grant 的 lease expiry 直接采用实际 grant 到期时间；套餐创建不再拒绝超过 30 天的期限，新建月度订阅也保存完整套餐期限。无限期 grant 使用永不过期的内部期限，不设置人为上限；不需要周期续租，收到续租请求时校验当前 token 并原样返回，不轮换 token、lease ID 或 peer。显式撤销仍生效，relay `offline_deadline` 独立保留。
- `src/server_network_assist/client_relay.py`：比较数据面字段，授权元数据刷新只持久化本地状态；稳定配置不会重发 WG peer 或客户路由。对账读取实际 AllowedIPs/路由，只修复缺失项；数据面政策变化时保留同一 WG 身份，避免无关删除/重建。
- 中继签名快照携带的 `offline_deadline` 继续独立有效，控制面长时间不可达时中继仍可按本地截止撤销 peer。

## 验证结果

- 定向用例 5/5 通过：有限套餐超过 30 天按实际到期日、3650 天计划可创建、无限期套餐永久有效且显式续租不轮换 token、旧无限期租约原位延长期限且保留身份、OnlineClient 续租携带当前 token。另一次跨文件回归为 21 通过、5 失败，分别涉及缺少 `record_usage_by_device`、OnlineClient 旧夹具与当前接口不匹配，以及一项 API 测试在进入续租逻辑前 enrollment 返回 400；没有据此声称完整套件通过。`compileall` 与 `git diff --check` 通过。
- 隔离模拟在内存假中继执行器中每条命令加入 10 ms 延迟，以 60 秒虚拟间隔更换 10 次 peer 身份：10 次都形成暂缺窗口，范围 107.07–110.86 ms。701 个 loopback UDP 探针被脚本分类为落在模拟缺 peer 窗口内；这不是实际 WireGuard 丢包观测。
- 相同 peer 的授权快照刷新：模拟路由/WireGuard 写入 0 次，额外模拟缺口 0 次。
- `test_client_store.py` 与 `test_client_relay.py` 全文件运行 28 通过、6 失败，失败为现存未提交夹具/旧断言问题。API 测试补设隔离用 `SNA_DIRECTORY_URL` 后仍有 8 项因现有未提交测试与当前接口不匹配而失败；relay-agent 的旧 fake 未实现 `expire()`。详见 `test_reference.md` 的 TEST-NET-072。

## 未完成与边界

- 每分钟替换风险模拟没有运行真实 WireGuard，不能估算生产丢包或认定历史断网根因。D321 实际验收有一次历史成功、一次较新的握手失败，详见下文。
- C201 已将 67 个启用套餐从 900 秒迁到套餐实际有效期。原迁移把一个 3650 天套餐截成 30 天；纠正迁移已把该行从 2,592,000 秒改为 315,360,000 秒，其余 66 行保持实际期限。活动控制服务已重启并返回 HTTP 200；relay 后续同步任务以退出码 0 完成。
- D321 于 2026-10-02 成功建立 `source-physical` 借网连接，自动选择校园私网入口 `10.20.32.13:51910`，无备用线路；CLI 报告 `egress_verified=true`，公网探针确认出口 IP `183.198.109.16`。租约 expiry 与 grant 实际到期时间一致（2026-10-24 06:15:36 UTC）。控制服务于 14:24:59 CST 重启后，D321 仍显示 `CONNECTED`，没有重置 peer 或路由。
- D321 CLI 的 `source_public` 累计计数在 14:13:51–14:25:11 CST 间由接收/发送 25,257,728 / 3,547,220 字节增长到 53,392,692 / 10,111,296 字节（增量 28,134,964 / 6,564,076）。最新一轮分层探针为出口 5/7、应用 1/4、校园私网 2/3；因此源物理出口探针成功，但应用层仍有多个站点失败。
- 随后最新一轮 D321 重验（operation ID `2f0ffbda5ca54ad099318b1a44eb22f6`）未完成 WireGuard 握手，客户端提示已恢复原网络。只读复查为 `IDLE`、`connected=false`、无活动操作；这次没有形成借网连接。等待调用超时后查询了操作状态和事件，没有重放连接。
- 复核 D321 本机历史服务日志，另有两次自动断连/恢复窗口：13:38–13:40 与 14:24–14:26 CST。两次借网公网探针 0/7，但 WireGuard health 的握手年龄有刷新且 RX/TX 计数增加；隧道接口 `pnx3f1a954168bb` 向 `223.5.5.5`、`1.1.1.1` 的 DNS 请求持续超时，自动重试两次后客户端回滚至 `IDLE`。回滚后的物理上行公网探针也为 0/7。当前证据不支持“peer 过期/轮换导致握手中断”，但不足以在客户端隧道 DNS、C201 转发/出口与上游 DNS/校园公网之间定责。
- 当前只读 CLI 状态为 `IDLE`、未连接；`reachability_monitor.total_incidents` 仍为 0 且无 active incident，与事件日志记录的两次故障不一致。因此该计数目前不能用来统计断网次数，需要修复事件归并/incident 计数。C201 当前非借网时的路由表不能代替故障时 peer 路由快照，不能据此定为源端根因。
- 在此后又只发起一次 CLI 连接验收，operation ID `9df46d42b87e4814a546011cc719b081`。Reliable SSH 调用等待超时后没有重发；后续只读状态/事件确认操作失败并已回滚，D321 为 `IDLE`、`connected=false`。客户端日志的 `WindowsBackend.start_tunnel.failed` 用时 92.9 秒，异常位于 `client_tunnel.wait_handshake`；自动选择的目标仍为 `10.20.32.13:51910`，租约地址 `10.213.40.46/32`。私网管理地址可达、C201 有 UDP 监听，均不足以证明握手 UDP 抵达并完成处理。回滚后 D321 物理上行探针 0/7。尚不能在校园 UDP 路径/本机 VPN-TUN/源端防火墙或 peer 状态间定责；缺少 C201 root 侧握手和收发计数或抓包证据。本次验收失败，没有再次重试。
- 这些是 D321 CLI 报告的客户端 `source_public` 计数。relay 状态文件受 root 权限保护，当前只读 SSH 能力未能取得 relay 侧实时握手/计数器第二样本，因此完整双端计数验收仍待补证；不得把客户端计数单独当作中继端计量证据，也不得把控制面 lease 或单次公网探针描述为全部网络验收通过。

## 2026-10-02 本轮追加 CLI 实机复验

- 用户要求检查 `d321-titan-jump` 与 C201 路径，并确认能否通过命令接入。D321 的 campus 跳板在操作前后都能完成身份探测。C201 的 Reliable SSH `lan` 路由连接 `10.20.32.13:22` 超时；按工具建议探测后，备用 `zn` 路由成功，确认仍是 `a-MS-7E06`。所以这次 C201 检查通过备用管理路由完成，没有把 `lan` 超时判断成主机不可达。
- D321 `check-source` 预检操作 `e153ac036e5b4ded827741e7ed20d4d2` 成功。此结果仅代表预检操作成功。
- 随后只发起一次 `LanBridgeCLI.exe connect --json --timeout 180`。操作 `bb7f50df264d437e8c1983ad119a3ee5` 被服务接受并运行，最终 `operation.failed`，消息为“源网未完成 WireGuard 握手”。CLI 状态回到 `IDLE`、`connected=false`，事件记为“源网连接失败，已恢复原网络”。Reliable SSH 调用等待超过 60 秒后超时；随后通过原 operation/event 查询确认失败，没有重放连接。
- 失败后 D321 campus 跳板仍可访问，第二次 `check-source` 操作 `8587fbc2ff264272bd041d2628c9ef66` 也成功。当前证据证明管理路径与控制面预检可用，但本轮没有形成 WireGuard 数据面连接，也没有公网借网验收通过。
- C201 上 `sna-commercial.service` 为 active，UDP `51910` 在 `0.0.0.0` 与 `::` 上监听。对 `wg show all latest-handshakes` 的只读请求因 SSH 账户权限不足，在 `wg-fleet`、`sna-commercial`、`wg0`、`wg-d408` 均返回 `Operation not permitted`。端口监听和控制服务 active 都不能证明 peer 已收到/接受握手；因此不能在校园 UDP 路径、本机 VPN/TUN、源端防火墙或 peer 状态之间确认根因。准确归因还需要有权限的 C201 WireGuard 握手/收发计数或受控抓包证据。
- 本轮没有再次重试，也没有对 C201 WireGuard 数据面做修改。
