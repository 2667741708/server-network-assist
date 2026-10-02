# 路由保留验收

## TEST-NET-072：续租保留 peer、按套餐实际期限到期与每分钟替换风险模拟

关联 REQ-NET-066。5 项定向用例通过，覆盖无限期租约永不过期且续租不轮换 token、旧无限期租约原位迁移、有限租约保留 peer、有限套餐按实际期限到期，以及 OnlineClient 续租请求携带当前 token。源码 `compileall` 和 `git diff --check` 通过。另一次跨文件回归为 21 通过、5 失败：包含 `record_usage_by_device` 缺失、现有 OnlineClient 测试夹具与接口不匹配，以及一项 API 测试在进入续租逻辑前因 enrollment 返回 400 而失败；该轮不作为这 5 项定向用例的反证，也没有据此声称完整测试套件通过。

本地隔离脚本 `scripts/experiment_peer_rotation.py` 使用内存假中继命令执行器和 loopback UDP 探针，按 60 秒虚拟间隔模拟 10 次 peer 身份替换。10/10 次都出现 peer 暂缺窗口，模拟窗口为 107.07–110.86 ms；共 701 个探针落在脚本标记的缺 peer 区间。该计数是模拟分类结果，不是实际 WireGuard 丢包统计。相同 peer 的授权快照刷新产生 0 次模拟路由/WireGuard 修改，0 个额外模拟缺口。

**期限修正与部署证据（2026-10-02）**：有限套餐的 lease expiry 采用 grant/套餐实际到期时间，没有 30 天上限；新建月度计划也保留完整套餐期限。C201 上 67 个启用套餐已从 900 秒迁为实际期限。此前使用 `min(套餐有效期, 30 天)` 的迁移把一个 3650 天计划截短；纠正迁移将该计划从 2,592,000 秒改为 315,360,000 秒，其他 66 个计划未变。活动控制服务重启后为 active，HTTP 页面返回 200；后续 relay 同步服务退出码为 0。

**D321 实机验收（2026-10-02）**：通过 CLI 发起一次连接，操作返回 `succeeded`；状态为 `CONNECTED`，自动选中校园入口 `10.20.32.13:51910` 和 `source-physical`，未使用备用线路。状态探针为 `egress_verified=true`、公网 IP `183.198.109.16`。最新分层结果为公网出口 5/7、应用 1/4、校园私网 2/3 目标通过，因此源物理出口探针成功，但应用层仍有多个站点失败。套餐授权到期为 2026-10-24 06:15:36 UTC，D321 lease expiry 与之吻合。控制服务于 14:24:59 CST 重启后，D321 仍为 `CONNECTED`，没有重置 peer 或路由。

D321 CLI 报告的 `source_public` 累计接收/发送计数在 14:13:51–14:25:11 CST 间由 25,257,728 / 3,547,220 字节增长到 53,392,692 / 10,111,296 字节（增量 28,134,964 / 6,564,076）。这是客户端报告的计数增长，不等于 relay 端计数。

**后续重新验收（2026-10-02）**：在上述历史成功之后，只发起了一次新的 D321 连接操作（operation ID `2f0ffbda5ca54ad099318b1a44eb22f6`，自动选择入口，没有强制切换线路）。操作最终因未完成 WireGuard 握手而失败，客户端报告已恢复原网络；只读复查状态为 `IDLE`、`connected=false`、无活动操作，未建立当前借网连接。调用等待超时后通过状态与事件查询确认结果，没有重放连接命令。故较早的成功记录仍是历史证据，本次最新验收结论为失败于握手层。

**自动断连窗口复核（2026-10-02）**：D321 本机日志中另有两次自动恢复窗口，约为 13:38–13:40 和 14:24–14:26 CST。两次期间借网路径公网探针均为 0/7；WireGuard health 仍出现新鲜握手时间及 RX/TX 计数增长；校园隧道接口 `pnx3f1a954168bb` 上对 `223.5.5.5`、`1.1.1.1` 的 DNS 请求持续超时，客户端随后各重试两次并回滚到 `IDLE`。回滚后的物理上行公网探针也分别为 0/7。证据表明当时失败发生在握手建立之后的 DNS/转发/出口可达链路，不能支持“peer 过期或被轮换导致握手中断”这一解释；但现有客户端日志还不能区分故障在 D321 隧道内 DNS 路径、C201 转发/出口，还是上游 DNS/校园公网。当前状态复查仍是 `IDLE`、未连接；`status` 返回的较早公网探针成功记录是历史值。

**可达性计数缺口**：同一时段 CLI 的 `reachability_monitor.total_incidents` 仍为 0、无 active incident，尽管事件日志记录了上述两次探测失败、重连和回滚。因此目前不能用该计数表示断网次数，日志系统的事件归并/incident 计数仍需修正。当前借网未连接时，C201 的只读路由表也不能代表断连窗口中的瞬时 peer 路由，不能据此单独判定源端根因。

**再次实际连接验收（2026-10-02 15:44 CST）**：只发起一次 CLI `connect`，operation ID `9df46d42b87e4814a546011cc719b081`。Reliable SSH 等待超时后没有重发；只读状态查询确认该操作最终 `failed`，D321 已回到 `IDLE`、`connected=false`，事务清理完成。客户端日志记录 `WindowsBackend.start_tunnel.failed`，耗时 92.9 秒，失败栈落在 `client_tunnel.wait_handshake`；操作使用自动选择的 `10.20.32.13:51910`，租约地址 `10.213.40.46/32`。私网管理入口可达和 C201 UDP 端口有监听都不能证明该 UDP 握手包抵达并被正确处理。失败后物理上行公网探针为 0/7。当前无法区分校园 UDP 路径/主机 VPN-TUN 干扰/C201 防火墙或 peer 安装状态；缺少 C201 root 侧握手时间、计数器或抓包证据，因此本次实机验收失败且根因仍未定。

**仍待独立复核**：未取得 relay 侧当前 WireGuard 握手时间及服务器收发计数器的第二个样本。relay 状态文件需要 root 权限，当前 Reliable SSH 只读能力未能读取；因此尚未证实服务端计数器持续增长，也不把控制面 lease 或客户端计数单独视作完整双端计量验收。隔离模拟仍不等同于真实 peer 替换丢包，也不能断言历史断网的现场根因。

**其他测试限制**：`test_client_store.py` + `test_client_relay.py` 全文件运行结果为 28 通过、6 失败；失败集中在已有未提交夹具/旧断言（缺少 `record_usage_by_device`、过期 nft/TC 断言及旧清理假执行器），没有计入定向通过数。API 测试补设隔离用 `SNA_DIRECTORY_URL` 后仍有 8 项因现有未提交测试与当前接口不匹配而失败（包含缺少 `set_grant_egress`、预期状态码/续租响应不一致）；relay-agent 测试的旧 `FakeManager` 未实现 `expire()`。这些外围测试均未作为本需求的通过证据。

## TEST-NET-071：线上复制路径实机核验与客户端源 IP 订阅验收

REQ-NET-064 / REQ-NET-065。两者都是**跑在真实生产/真实客户端上的验收**，不是夹具。

**A. `tools/admin-copy-live-check/check-copy.cjs`（新增工具）**：默认对三个生产管理入口跑，也可传入任意 URL 覆盖。关键手法有两处，都是踩过坑才定下来的：

- `#address-dialog` 是 `#workspace` 的**兄弟节点**，不在登录门之后 —— 直接 `showModal()` 就能像真实用户那样点到页面自己的 `#copy`，**全程无需管理员凭据**。仅断言 `dialog.open` 不够（Tabler `.modal{display:none}` 曾让 `open === true` 却 0×0），故同时打印按钮的 `getBoundingClientRect()` 尺寸。
- **两条剪贴板路径都要捕获**：包装 `navigator.clipboard.writeText` + 监听 `execCommand` 触发的 `copy` 事件，并打印**尝试序列**。只监听 `copy` 会在安全上下文漏掉主路径，只包装 `writeText` 会在明文入口漏掉兜底。

结果（2026-09-30，发布后）：三入口 served js 均为 `ec8a56ee…`；9182 与 9180（非安全上下文，`navigator.clipboard === 'undefined'`）走 `execCommand`，**472/472 完整一致**；公网 HTTPS 无权限时 `clipboard.writeText → NotAllowedError → execCommand`，**472/472**；授权后走 `writeText` 且 **OS 剪贴板回读一致**。全入口 `#message` = 「订阅地址已复制。」、输入框未变、零页面错误。合成地址，**不含任何真实令牌**。

第三行是重点：它证明**兜底链在真实页面上真的接得住** —— 修复前这里没有兜底，`writeText` 被拒就等于什么都没复制。也说明"HTTPS 入口没问题"是错觉。

**B. 客户端源 IP 订阅验收（本机 whm-save）**：`subscription-add --file`（令牌不走 argv，临时文件注册后即删）→ `route-preview`（`ambiguous:false`）→ `subscription-select --confirm`（CLI 有防误切护栏，**须显式 `--confirm`**）→ `refresh` → `connect`。入网后按 `status` 的 `connection_plan` / `source_link_probe` / `probes` / 真实下载四项分别取证，不以 `connected:true` 单独结论。表见 [REQ-NET-065](requirements_traceability.md)。

**未覆盖**：客户端 `usage` 计量（下载 36.6 MB 后仍为 0，未定位，记为待观察）；30 天会话 soak；境外站点不可达是 `source-physical` 的设计结果，未列为缺陷。

## TEST-NET-070：订阅控制台 v2 浏览器夹具改写与变异体反证

REQ-NET-063 / ERR-NET-041。原 `tests/subscription_admin_ui_browser.cjs` 是 09-18 的 v1 时代断言（引用 `#result`、`#customer-address-dialog`、`#result-close`、`#result-title`、`#manager`、`#nav-*`、各表单 `[name=confirmation]` 等 v2 已不存在的元素），对 v2 HTML 必然失败。本次整体改写为 v2 真实 ID 与行为：`#address-dialog` 家族、登录 `remember:30` 且全程只登录一次、生成成功文案「生成成功，完整地址已显示。」、客户行按 `subscription_addresses[id].status`（ready/used/expired/not-recorded）门控的四个按钮、`expireAddress` 的 `window.confirm`、批量结果写入 `sessionStorage['batchResults']`。

覆盖：匿名→登录→生成→弹窗**可见**→txt 导出→**复制按钮真的写入剪贴板**→重认证重放→客户行按钮门控→查看已存地址→销毁→批量签发 3 行（2 成功 1 失败）→批量 txt/CSV 导出→访问日志→1440/1024/390px 无水平溢出且弹窗在视口内→localStorage 为空且 sessionStorage 仅 `batchResults`、凭据不落盘→退出登录。全部请求由 `page.route` 拦截，`pageerror` 零容忍。结果：**通过**。

**剪贴板断言（ERR-NET-043 追加）**：夹具源 `http://admin-fixture.test` **本身就是非安全上下文**，与校园 9182 / WG 9180 同条件，故能真实复现缺陷。断言先自检 `isSecureContext === false` 且 `typeof navigator.clipboard === 'undefined'`（否则这条测试什么也证明不了），再监听浏览器**真实触发的 `copy` 事件**读取剪贴板内容并比对完整地址 —— 验的是"副作用真的发生了"，不是"弹了句提示"。

**变异体反证**（本次新增方法，证明断言不是摆设）：对"修复前"代码构造变异体分别运行同一夹具，断言均在对应变异体上变红 —— ① 弹窗类名回到 `modal modal-blur` → `dialog must not be display:none`；② `onResult` 置 `null`（丢回放结果）→ 重认证后弹窗未打开；③ 22rem 内联宽度写回 + 删除 `.reissue-field` → `No horizontal page overflow at 390px`；④ `copyText` 还原为无兜底逻辑 → `window.__copied` 为 `null`。详见 [ERR-NET-041](error_traceability.md) / [ERR-NET-043](error_traceability.md)。

**线上实机复验（2026-09-30，发布后，`tools/admin-copy-live-check/check-copy.cjs`）**：`REQ-NET-064` 一节的"拦截替换 js"限制已解除 —— 修复已发布（三入口 served js 均为 `ec8a56ee…`），复验直接跑**已发布状态**。三个入口 + 两条路径全绿：9182/9180 走 `execCommand`（472/472）；公网 HTTPS 无权限时走 `writeText → NotAllowedError → execCommand`（472/472，**兜底链在真实页面跑通**），授权后走 `writeText` 且 **OS 剪贴板回读一致**。该工具同页打印 `isSecureContext` 与 `typeof navigator.clipboard`，避免"在安全上下文里测了个寂寞"。

**未覆盖**：线上真实管理员交互登录与按钮点击（需管理员凭据）；线上弹窗可见性以静态元素 + 免登录 headless 实测 + 用户手验为准。见 [OPS-NET-064](handoffs/2026-09-30-订阅地址弹窗不可见与地址导出.md) §9.1。

## TEST-NET-069：订阅控制台线上三入口验收与回滚演练

REQ-NET-057 / REQ-NET-058 / REQ-NET-062 / OPS-NET-063。切换后只读探针：进程 active/running（PID 2005103）、`ExecMainStatus=0`、journal 无异常；`/subscriptions.html` 标题「源网订阅管理」；`subscriptions.js`/`subscriptions.css`/`tabler.min.css` 均 200；`/api/session` 返回未认证且 `version=2.0`；匿名 `/api/client-service` 返回 **401 + `Cache-Control: no-store`**；`device_access` 表与两个索引已建（0 行）；`subscription_addresses` 25 行前后未变。

三入口均实测且互不替代：校园 `http://10.20.32.13:9182/subscriptions.html` 200；WG `http://10.201.250.1:9180/subscription-admin/subscriptions.html` 200 且 JS 200；公网 `https://whm12.art/subscription-admin/subscriptions.html` 200，标题一致。

**回滚演练双向完成**：移走 v2 drop-in 后 WorkingDirectory 回到 `commercial-readiness-v3-20260928`，服务实际返回的 `subscriptions.html` sha256 = `81d4f2b4…` 与旧文件逐字节一致、标题回到「澜桥 · 订阅管理」；移回后返回 sha256 = `ca9c0258…`、标题「源网订阅管理」、服务 active。数据库为纯增量变更，两向演练均未动数据。

**未执行**：带管理员凭据的真实交互登录、30 天 Cookie 的 Max-Age 实测、按钮点击与 CSRF 重认证弹窗的线上实测、长会话 soak、反代入口下的真实客户端 IP 语义验证。不得以本验收宣称上述项目已通过。见 [OPS-NET-063](handoffs/2026-09-30-订阅管理控制台v2上线.md)。

## TEST-NET-068：订阅控制台 v2 隔离生命周期验证

REQ-NET-057 / REQ-NET-058 / REQ-NET-059 / REQ-NET-060 / REQ-NET-061。在 4090 以新发布目录 + 隔离数据目录 `/tmp/sna-v2-pretest` 起服务于 127.0.0.1:9185，跑 21 项生命周期检查，**21/21 全通过**：匿名会话 → 登录(remember=30) → 预设三项下发 → 源网快照 → 生成地址 → 归档状态 `ready` → 批量签发 3/3 → 重复客户名整批拒绝(400) → 查看地址 → 销毁 → 销毁后归档状态消失 → 补发 → 归档恢复 `ready` → 延长 24h → `subscription-preview` 可用 → 访问日志含 preview 记录且带来源 IP。

同一批检查在本机隔离服务上亦 21/21 通过。预检期间一次 SSH 超时（退出码 255）经查为 `nohup ... &` 挂住会话，服务本身正常启动，非代码缺陷。

**未覆盖**：真实管理员交互登录、30 天会话长期有效性、批量签发在真实生产的施用、反代后 `remote_ip` 语义。见 [OPS-NET-063](handoffs/2026-09-30-订阅管理控制台v2上线.md)。

## TEST-NET-067：稳定性与暂态监测分类

14新增/263完整回归及12集中构建检查通过；只读查询持续超时不拆隧道，传输/公网故障有限重试、有效租约/新鲜握手前提、授权拒绝/保护失败立即恢复、独立用户恢复优先。真实新版连接与五分钟观察不得由mock代替，结果见native-connect-019.json/native-stability-soak-019.json。见 [OPS-NET-062](handoffs/2026-09-19-网卡查询超时误断线修复.md) / REQ-NET-056。

## TEST-NET-066：入网优化集中回归与只读连接基准

新增快速检测绑定/回退/失败、后台补测、租约续期及生产 PS AST 单次查询核验；最终集中验收和构建以 artifacts/code-acceptance.json 为准。实际既有源代理隧道的快速HTTPS163ms、HTTP200，未变更网络。当前校园认证/源代理成功由用户现场确认；无新版完整重新入网计时。见 [OPS-NET-061](handoffs/2026-09-19-入网耗时优化.md) / REQ-NET-055。

## TEST-NET-065：后台日志与可逆恢复集中验收

REQ-NET-053 / REQ-NET-054 / OPS-NET-060。客户端240项及12项集中离线检查、源日志41项和16项针对性检查通过；网络清理使用生产PS AST/mocks，恢复快捷方式真创建校验。已安装0.1.18的回环恢复接口实测通过，保持idle及已有账号/订阅；直连百度/腾讯/必应200，本机Clash GitHub/Gemini200、GPT403。不启动新借网、不切换校园运营商；不能替代失败源代理真实出网验收。完整依赖离线安装与单文件CheckOnly核验见 [OPS-NET-060](handoffs/2026-09-19-后台日志与可逆网络恢复.md)。

## TEST-NET-064：D321普通客户端持续真实连接

REQ-NET-051 / ERR-NET-034 / OPS-NET-059。现有普通纯享入网0.7.0/20260917-subscription-fold通过其真实按钮后台处理入网，原生窗口show成功；不是鼠标点击验收，未更新EXE。另一LanBridge 0.1.8防泄漏规则阻断该网卡，显式退出它后恢复。随后约34分钟同一隧道，跨三次自然续租：直连1261.6秒源API/5080 SSH各579成功、百度HTTPS116成功；代理响应头360.7秒百度/GitHub/GPT/Gemini各36为200、源API/5080 SSH各179成功。两段状态253次无退出/错误，客户端及源机计数无重置、源机采样无peer缺失。短期限整页下载curl28已有200，作为测量误报保留，不算断网。结束保留D321连接，不修改本机网络。A001、源机代理、Wi-Fi切换、客户端重启、全部校园互通矩阵和应用登录未执行本轮测试；周期20秒未复现，不能宣称全部修复。原始证据artifacts/d321-pure-soak-20260918。

## TEST-NET-063：代理订阅和独立下线集中验收

REQ-NET-052 / OPS-NET-058。226项客户端及12项离线构建检查通过，管理器27项（固定页脚、默认唯一勾选源网无需额外高亮、不可用代理拒绝、明确proxy_source_ids），服务API6项（physical不受代理未就绪影响、远程状态未知、旧授权无source_id时按实际代理接口检测）。生产控制API HTTP200，代理授权source_proxy/Meta，本源Mihomo/TUN只读就绪。订阅预导入未申请租约或启动隧道，当前选择及旧订阅保留。本机管理器0.2.1已安装，实际窗口出口按钮可见。澜桥0.1.16完整离线包已交付且单文件CheckOnly验证通过，客户端管理员确认被取消，新版未安装。新代理实际出网及校园下线未测试，待用户确认。

用户暂停前0.1.15移动Wi-Fi实测301.1秒100次采样，借网状态/国内公网/源API无失败；隧道CF trace出口183.198.110.58与源物理出口一致。不证明计费归属或长期无断流。校园0连接在暂停消息前提交，随后安全退出并恢复移动认证，未执行该轮5分钟采样。最新要求移动和校园均不再测试。

## TEST-NET-062：中继元数据续期与真实借网连续性

REQ-NET-051 / ERR-NET-034 / OPS-NET-057。100项相关回归及1项同策略内核修复测试通过；元数据更新零内核命令，计量保留，相同策略仍修复peer。生产两个客户自然续期ID不变、计量递增，Mihomo/WG未重启。D408只读420.8秒，411源端TCP、137百度HTTPS均成功且跨自然续期。D321只读391.1秒232TCP成功，但78HTTPS失败（DNS超时，无公网成功基线），普通测试窗口未入网，不能宣称其周期故障消失。A001及源代理长期实测未执行，本机网络未动。

## TEST-NET-061：周期断线排查、稳定续租与复用额度会话

REQ-NET-051 / OPS-NET-056。客户端225项回归、12项集体验收及完整离线依赖打包通过：ID轮换不拆隧道，真实契约变化保留保护，跨20分钟模拟额度查询仅认证一次，诊断文件失败不阻止暂停。服务端14项Store/API测试，远程临时数据库4次续租ID/计量连续通过；仅控制源码窄部署，API/SSH TCP和Mihomo/WG激活记录保持。报告周期约5分钟，现场租约约13分钟，不能据此声称报告故障已彻底解决。未运行本机客户端或实施网络变更，未验收故障机器长时Wi-Fi/Clash、实际应用及双向TCP；详见handoff。

## TEST-NET-060：普通纯享入网自动源网状态与不限IPv4候选

REQ-NET-050 / OPS-NET-055。130项Python回归通过，覆盖192.168/172/100.64/10任意前缀实际可达性、IPv6默认存在时只读查询、查询不触发注销/租约/隧道/退网、同端点不同授权不混淆、所有权退出恢复。JavaScript逻辑与真实DOM隔离浏览器两套通过，启动/添加自动查询、节流、出口及耗时可读、节点滚动和默认窗口按钮可见，保留旧操作工作流。冻结源码与完整离线ZIP核验见artifacts/client-auto-source-20260918。未运行本机客户端、未部署D321、未进行真实入网/TUN及Ubuntu/IPv6验收。

## TEST-NET-059：澜桥新增account-traffic学校计费接口

REQ-NET-049 / ERR-NET-033 / OPS-NET-054。216项Python回归和12项collective检查通过，包含学校混合GB+MB原文/1024近似换算、零/未知/不限量不猜0、不同服务范围不相加、运营商独立余额未返回、两个已知套餐GET、查询CAS不登录服务、同源HTTP升级HTTPS/越源停止/正确Accept、账号额度与当前在线账号分开、缓存失败旧值陈旧、失败暂停自动CAS重试/手动恢复、同账号更换密码generation隔离。计费栏420/540/760px原文、周期、多条范围和长值完整显示通过；既有四服务选择/保存/指定登录及订阅/8态布局继续通过。真实只读学校查询读取60 GB总额度和53 GB 862.59 MB剩余，当前前后中国移动在线，userId/service/userIp/authenticationTime均未变化，无serviceLogin/logout/路由操作；仅查询CAS会话。源码冻结80项/28独立模块、官方依赖签名、完整ZIP CRC和单文件摘要/桌面副本验证通过。新的原生托盘悬停、冻结UI借网、切换其他服务/账号、多范围实机及按包/账单免流归属均未验收。

## TEST-NET-058：澜桥余额来源、三路径与托盘提示

REQ-NET-049 / ERR-NET-033 / OPS-NET-053。207项Python回归通过，新增余额明确单位/零/缺失/冲突、不固定60GB、不泄露响应账号密码、实际运营商而非保存选择、账户不匹配/变更、旧值失败标陈旧、迟到查询丢弃、查询运行中退出不等待、未知认证不假报离线、only owned WG leased peer transfer、不计管理VPN、Windows托盘127 UTF-16限制。DOM两套、订阅浏览器、Wi-Fi运营商工作流、计费栏浏览器及8态布局通过；420/540/760宽度长文本完整且无页面水平溢出。代码统一验收12项PASS，冻结27独立模块/77源文件，离线依赖签名、ZIP CRC/摘要及单文件包内置摘要通过。当前真实校园认证接口只读结构探测确认两处accountInfo为空，未登录或修改网络；真实剩余GB、是否扣校园60GB、实际运营商套餐余额、原生托盘悬停、冻结新版借网均未验收。

## TEST-NET-057：桌面订阅与源网登记、迁移

REQ-NET-048 / OPS-NET-052。本地72项相关回归通过，生产包副本叠加窄候选9项隔离测试通过：生成/找回、DPAPI、租约撤销、URL/身份/套餐/用量/账期保留、旧中继最终用量归属、历史不可重启、源网停签发与删除引用保护、过期版本/请求重放/CSRF/匿名鉴权、地址池耗尽事务回滚、中继ID冲突、长源网/客户/URL完整视图及水平/垂直滚动。EXE内置Python/Tcl/Tk，当前用户稳定目录安装、桌面摘要与冻结GUI启动通过。真实API鉴权读取1源网/27客户、匿名401与revision字段通过；发布前后客户/源网/身份及路由、规则、代理/WG/中继timer摘要相同，没有修改真实客户或操作员网络。真实双源机借网、客户端重握手、公网转发、校园服务双向TCP与干净机器安装尚未验收；不能用隔离库代替实机多源网通过。

## TEST-NET-056：单文件安装与校园网服务Wi-Fi双网卡抓包

REQ-NET-047 / OPS-NET-051。185代码回归、冻结源码/资源、完整依赖签名、嵌入ZIP及9payload自检、本机实际安装/桌面摘要和冻结GUI启动通过。实际服务0校园网保持在线，独立physical测试订阅两轮借网；Edge无代理百度/腾讯/阿里云/B站/微软/源API均200，本机Clash GitHub/Wikipedia200，Gemini首页200但未验对话，GPT403未正常使用。Windowscurl系统连接本地WGIP且公网183.198.110.58匹配源enp4s0；WLAN11531/WG10607 IPv4头部包，向源UDP51910隧道3530包，机场节点和网站内层经过WG。已有公网管理直连及校园认证/DNS例外仍存在，不称所有公网包隧道；零计费、干净机器依赖安装、GUI点击借网、ClashTUN切换未验。最终路由/DNS/NRPT/firewall/管理端点/Clash恢复、原中国移动登录恢复，临时客户停用。证据artifacts/local-campus0-acceptance-0.1.12.json及对应handoff。

## TEST-NET-055：授权本机Wi-Fi真实会话与恢复

REQ-NET-046 / ERR-NET-032 / OPS-NET-050。185代码回归和0.1.11冻结源码/离线ZIP通过。真实iYanDa两轮修复后Session借网成功：近期握手/计数增长、百度/腾讯/阿里云200、普通无代理系统请求200、物理WLAN公网直出WinError10013；隧道公网IP183.198.110.58与源enp4s0匹配。Clash保持rule/TUN关闭且绑定WG，Wikipedia200，GPT403仅可达；校园源API/SSH前中后均可达，路由/DNS/NRPT/防火墙profile/管理端点及Clash文档恢复，owned状态无残留。临时测试客户停用，旧客户/原订阅不变。本轮明确授权本机测试，不取消REQ-NET-003；冻结GUI点击、Clash TUN切换和校园计费未测。证据见对应handoff及artifacts/local-wifi-acceptance-0.1.11.json。

## TEST-NET-054：澜桥全部IPv4候选与物理源端证明

REQ-NET-045 / ERR-NET-031 / OPS-NET-049。175项Python回归通过；16项新增覆盖多类物理IPv4、旧窄配置兼容、源端直达无校园账号、认证后仍隔离停止、默认网络优先、无效地址与本机/网关精确直连。0.1.10冻结71源码/26独立模块、GUI资源及无旧客户端包核验通过，完整离线依赖签名、ZIP CRC及桌面摘要一致。未执行客户端/安装/校园认证/隧道，不能称真实Wi-Fi入网已通过；本机网络未修改。见对应handoff及campus-borrow-client-next/artifacts/releases/offline-package.json。

## TEST-NET-053：澜桥运营商、DPAPI及认证后API闸门

REQ-NET-044 / ERR-NET-030 / OPS-NET-048。159项Python通过，含四服务选择和目标复核、真实WindowsDPAPI重开、不公开密码、旧配置默认0、无效服务保持账号、10.30/10.80/10.126源网仍不可达时禁止继续、认证后API恢复及借网中禁止切换账号。两组DOM、既有订阅浏览器和新Wi-Fi浏览器通过，含默认持久保存、手动登录先保存、非秘密回填、变更运营商需密码、扫描/权限错误与390px长值可读。

本机仅只读：WLAN扫描18+条且iYanDa已连接、IPv4为10.80.62.217、无自定义routing覆盖、物理源API session可达。未运行Netlogin登录或logout、未启动澜桥、安装组件或修改网络。0.1.9 EXE GUI/图标/版本、70源文件/26独立模块无旧包、ZIP9负载摘要及官方组件签名通过，桌面ZIP摘要一致。实机认证/握手/分流/Clash共存/退网仍未验收。

## TEST-NET-052：导航保留、冻结资源及真实查询对照

REQ-NET-043 / OPS-NET-047。34项Python及五组隔离浏览器通过；覆盖历史/状态/草稿、GET合并失效、动画降级及POST不重试。20次减少动画预热回访中位36ms/P95 37ms为fixture结果。4090同一只读256万上报数据五次对照，完整快照对象相同，查询11,516.87→3.09ms；三入口鉴权、匿名401、no-store及资源通过，合同及受保护网络摘要不变。最终EXE73项源清单与共享资源、ZIP校验通过；操作员PC真实运行、D321 GUI、真实入网/退网/TUN未执行。

## TEST-NET-051：丝滑微交互与客户端回归

REQ-NET-042 / OPS-NET-046。`smooth_interactions_browser.cjs`通过原生动画、减少动态效果、API缺失、快速最后目标、80ms截图看门狗、一次提交、并发忙碌清理和相同文本不重写。一次本地采样反馈0.3ms、DOM提交95.5ms。既有服务端管理/仪表盘/请求恢复、客户端catalog/Framework7、Next订阅浏览器回归均通过；均为隔离fixture，未启动真实客户端、修改网络、部署或重打包EXE。见[实施记录](handoffs/2026-09-18-丝滑微交互技能与客户端.md)。

## TEST-NET-050：请求超时恢复与后台刷新交错

REQ-NET-041 / ERR-NET-029 / OPS-NET-045。新增 subscription_request_recovery_browser.cjs 隔离 Chromium 测试通过：等待连接、响应体停滞均恢复按钮；POST超时仅提交一次，修改/导出保持锁定直至成功刷新；后台等待时可切换和退出，退出后迟到快照不得恢复客户列表。既有订阅管理、仪表盘两组浏览器测试通过。仅模拟API，无真实开户或启动代理。

4090仅替换两份JS，校园本机API与10.201.250.1管理入口均逐字节匹配候选；匿名session未登录/dashboard401。前后IPv4路由全表、规则、后端摘要、Mihomo/商业WG/中继timer与商业API进程标识一致，无服务重启。内置浏览器控制连接不可用，未查看用户页面的具体未完成请求；截图具体请求及用户Ctrl+F5后实际点击仍待反馈。

## TEST-NET-049：管理入口归属与D321订阅服务路径

OPS-NET-044 / REQ-NET-003 / REQ-NET-040。实际Caddy确认域名/WG管理代理共用4090后台；两入口鉴权读21客户、三个最新UI资产匹配。D321校园页面200、匿名订阅401；域名普通路径证书域名不匹配，客户端传输TLS提前关闭；管理WG超时。D321绑定服务仍为校园API。无兑换、客户重启、入网或网络修改；具体TLS拦截责任、完整GUI入网与转发未验证。见[核验记录](handoffs/2026-09-18-管理入口与校园订阅核验.md)。

## TEST-NET-048：订阅自定义速度与服务期限

REQ-NET-040 / OPS-NET-043。65项后台测试、两组隔离浏览器操作及4090候选8项API测试通过，覆盖永久/半年/一年/日期、精确速度/单向不限速、无效输入回滚、租约到期/撤销及原地址/消费保留。线上本地与域名鉴权读取21客户、匿名401及资产一致通过；网络服务/路由/规则及客户合同摘要相同，5080/D408/D321的TCP22保持可达。真实新套餐入网、重启和全校园双向矩阵未验收。见[实施记录](handoffs/2026-09-18-订阅自定义速度与期限.md)。

## TEST-NET-046：全部10.*候选与校园实路保护

67项接入/GUI/LAN回归通过，覆盖10/8多个范围、未知172/192出口、新增范围物理不可达、第二10.*热点与IPv6。EXE70源文件/12冻结模块/6UI一致性通过，未运行客户EXE或修改操作员网络。实际新增客户认证、UDP与公网复测待反馈，最小包只含EXE和说明，见OPS-NET-041。

## TEST-NET-045：10.30校园接入兼容

65项接入/只读GUI/LAN传输回归通过。新版EXE核验70份源码、12冻结模块、6UI资源及GUI图标通过；不在本机执行入网。支持授权10.30/16且要求物理源服务可达，未知另一出口和IPv6仍拦截。10.30客户真实认证、UDP握手及网站访问待用户验证。详见OPS-NET-040。

## TEST-NET-047：D321 澜桥 source_proxy 入网与恢复

0.1.7 独立核心与 0.1.8 打包 EXE 的实际入网/退网均通过：GitHub、GitHub API、Gemini、百度均 200；普通系统路由 GitHub/Gemini 200，ChatGPT 403 含验证挑战头，专用检测同轮曾为 200。登录与模型调用未验证。Mihomo 同时记录源端节点选择。D321 校园 API 和管理 SSH 在入网/退网均可达，4090 校园直连 D321 SSH 可达；0.1.8 单次退网且 owned 网络记录清理成功。146 项客户端回归通过，覆盖退出等待正在执行的健康检查且不被新 tick 抢占、防火墙掩码/有效状态与 fake-DNS 隔离；完整离线包文件校验与隔离安装回归通过。客户本机 Clash 当前自动选 TUIC 节点，附加检测 TLS EOF，日志 context deadline exceeded；全部节点共存未通过。GUI 点击、另一台目标机、全校园双向矩阵、重启未验证。未改操作员网络和源端代理配置。见 OPS-NET-042 与 campus-borrow-client-next/artifacts/d321-proxy-exe-0.1.8-20260918.json。

## TEST-NET-044：接入拒绝原因和原网络保护

57项attachment/access_gui/lan_transport回归通过：未收录IPv4与校园IPv4上的IPv6默认出口分别诊断；订阅读取成功/失败均保留原拒绝原因；开始入网被拒绝时无账号注销、租约、隧道安装或接入快照修改。不改本机网络。未构建新EXE、未更新客户、未验证截图客户网段，见 OPS-NET-039。

## TEST-NET-043：4090 透明 TLS 域名恢复

修复后透明 TUN 和显式 HTTP 代理均访问 GitHub/Gemini 返回 200、TLS 校验通过；ChatGPT 403 含 cf-mitigated: challenge，OpenAI API 根路径 421，均 TLS 通过，但登录和模型调用未验收。IPv4规则、主路由和中继防火墙修复前后一致，4090 到 D321/5080/D408 的校园 SSH 均可达。客户有近期握手、双向计量，但客户浏览器复测待反馈。未修改操作员本机网络，未验证 ECH、QUIC、IPv6及全节点双向矩阵。详见 handoffs/2026-09-18-源网代理DNS与透明TLS修复.md。

## TEST-NET-042：完整离线安装包与安全前置拒绝

REQ-NET-034 / OPS-NET-036。142 项相关 Python 回归通过，1 项符号链接测试因本机创建权限不足跳过。覆盖缺少 wireguard.exe/wg.exe 时个人账号处理、租约申请及隧道安装之前拒绝，依赖完整性/篡改拒绝、安装路径与当前用户便携版配置沿用，以及既有订阅、流量、接入、退出与恢复逻辑。

EXE 核验 70 份源码摘要、12 个冻结模块、6 份 UI 资源、品牌图标和 GUI 子系统通过。ZIP 共 84 项，83 份文件摘要一致，无用户配置；WireGuard 与 WebView2 官方签名 Valid。实际运行 Install.exe 的 --self-check，仅初始化 Tcl、核验冻结 Tk/helper 与包内文件：installation_performed=false、network_operations=false。核实 python313.dll、Python.Runtime.dll、WebView2Loader.dll 与 VCRUNTIME140.dll 已冻结。Windows PowerShell 5 仅解析两份 helper 成功，未执行。

没有启动客户程序、执行依赖安装器/helper、创建隧道或修改本机网络；未在全新 Windows 客户机执行系统组件安装及实际校园入网。既有界面未变，此轮不宣称新的实机 GUI/联网验收通过。

当前状态：d408 的 TEST-NET-003 服务端口验证、TEST-NET-005 公网路径与 HTTPS 请求、TEST-NET-006 已有跳板读取均通过；其余完整验收未完成。记录每次测试的机器、时间、命令、输出、结果和剩余限制。

| 编号 | 场景 | 成功标准 |
|---|---|---|
| TEST-NET-001 | 借网前后对比原网络配置 | 原内网路线、网卡与原网关保留，主表没有借网全局路由污染 |
| TEST-NET-002 | 同子网访问 | SSH/RDP 等实际服务连接成功，回包走原网卡 |
| TEST-NET-003 | 校园跨子网访问 | 本机 10.80.62.217 与 d408 10.20.32.14 服务互通，抓包确认双向路线 |
| TEST-NET-004 | 未列出来源的原网入站连接 | 回包按原网络路由发送，不误入借网隧道 |
| TEST-NET-005 | 公网借网与端点保护 | 公网走指定源网；端点不递归；实际出口与目标源网一致 |
| TEST-NET-006 | 原管理连接 | c201-5080-wg 跳板和管理专用隧道持续可用 |
| TEST-NET-007 | 重启、重连和自动维护 | 规则不重复，路由不被维护任务改回，冲突方案不会同时接管公网 |
| TEST-NET-008 | 退网与跨平台 | Ubuntu/Windows 分别验证，只撤销本方案改动，恢复 DNS/代理及原出口 |
| TEST-NET-009 | 校园组网双向互通矩阵 | 已确认可互访的机器对，在借网、代理/TUN、退网、续租、重连后验证双向关键 TCP 服务；独立验证跳板内层和外层入口，未测试项不能记为通过 |
| TEST-NET-010 | 客户端软件重启 | 重启客户后台而非操作系统；客户 PID 变化、已有 TUN 进程保持，校园服务、续租、HTTPS 与规则不重复通过 |
| TEST-NET-011 | 源机无握手失败回退 | 不把服务 active 当作连接成功；握手超时撤销新客户隧道及 DNS，保留原借网/TUN/校园路径；回退失败如实报错 |

## 已用于故障定位的命令

TEST-NET-009 当前新增结果：408/5080/4090 六方向 TCP 22 SSH banner 实测全部通过，均走 enp4s0；408 实际退网、重连、TUN 与续租通过。原 cloud → c201-5080-wg → d408 跳板关闭旧连接池后再次身份核验通过。完整组网、Windows TUN 和开机场景未验收。[证据与实现](handoffs/2026-09-16-校园互通与TUN兼容.md)。

本机 Windows：

```powershell
Get-NetIPAddress -AddressFamily IPv4
Find-NetRoute -RemoteIPAddress 10.20.32.14
Test-NetConnection -ComputerName 10.20.32.14 -Port 22
Test-NetConnection -ComputerName 10.20.32.14 -Port 3389
```

d408 Ubuntu：以下命令在 d408 执行，不是在本机 PowerShell 执行。AI 远程读取使用 Reliable SSH MCP。

```bash
ip -4 route show table all
ip -4 rule show
ip route get 10.80.62.217 from 10.20.32.14
ss -lnt
nmcli -f ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.routes connection show 'Wired connection 1'
```

修复后的回程应为经 10.20.32.1、dev enp4s0，而非 na1ae90b4a36。单条 route get 正确仍不足以证明端到端互通；还需服务连接、防火墙和必要时抓包验证。

## 2026-09-16 结果

- 本机到 d408 22、3389：TcpTestSucceeded=True；未测试 RDP 图形登录认证。
- d408 到本机回程：via 10.20.32.1 dev enp4s0。
- 公网 1.1.1.1：dev wg-d408 table 20480；WireGuard 握手正常。
- d408 使用 curl --noproxy '*' 请求 https://www.baidu.com：HTTP 200。
- 新版维护 timer：disabled；新方案再次 enable：被部署的冲突检查拒绝，未重新建立隧道。
- `python -m unittest discover -s tests -p test_route_conflicts.py`：5 项通过。
- `python -m unittest discover -s tests -p test_network_assist.py`：8 项通过。
- 重启、完整退网/恢复、Windows、并发操作和未知来源网段验收：未执行。

## 2026-09-16 客户软件重启追加验收

下列结果替代上方历史记录中“408 软件重启未执行”的状态，不代表操作系统重启或 Windows 已验收。

- TEST-NET-010：408 连续两次真实后台重启通过，后台 PID `140317 → 143407 → 143483`，Clash TUN PID `138590` 保持；每次校园 TCP 22、GitHub/API HTTP 200、续租、规则不重复通过，boot ID 不变。
- TEST-NET-008/009：部署握手保护后，408 实际退网、重连、TUN 开启、续租通过；退网后无客户 WG/TUN/自有规则/fake-IP DNS 遗留，跳板与校园 SSH 保留。
- 回归测试：握手保护、分流、TUN 生命周期、Windows 配置所有权共 18 项通过；客户签名订阅与在线租约生命周期 17 项通过。测试使用模拟器，不改本机网卡配置。
- TEST-NET-011：408 使用一个 TEST-NET 路由及不可达的 UDP 源口做真实失败试验；握手超时被拒绝，新建 WG 和 DNS 被清理，原有借网、TUN PID、校园 TCP 22 保留。日志 `server-only-handshake-rollback-0916/output.log`，exit 0。
- Windows：用户报告本机测试后断网，验收中止并禁止继续本机网络变更；不能记为成功。当前无活动客户隧道，Clash TUN 仍开；客户测试后台仍 Running，停用时管理员授权被取消。测试账号已在 4090 撤销。
- 所有操作系统关机/开机恢复、Windows TUN/重启、未知校园来源和完整组网矩阵：未验收。
- [实施与故障记录](handoffs/2026-09-16-客户端软件重启验收.md)。

## 2026-09-16 Titan追加验收

- TEST-NET-012：CLI保留窗口、实际WM_CLOSE、终止客户进程后的guard清理；客户隧道和自有状态清理，fleet-titan保留。测试自身模式/TUN改动由测试脚本撤销后，原网络基线一致。
- TEST-NET-013：Titan网卡16短暂禁用，finally启用并有独立SYSTEM恢复，禁用前至少35秒余量；断开期间active/recovering=false、自有文件清空、新错误事件，隐藏窗口被客户端显示；恢复后不自动借网。d57042制品重复实测；596c82及最终99e6b9制品另补验崩溃恢复，原实验哈希保留。
- TEST-NET-014：校园0和移动1真实登录；移动6模式百度/GitHub/API成功、指定应用HTTP出口成功，最后移动logout且HTTPS复核offline。校园0及借网自有TUIC国际超时不记通过。
- TEST-NET-015：三台Linux IPv4/IPv6规则/路由前后一致；5080/4090/408/Titan四台双向TCP22成功，437不可达，五台全矩阵未完成。
- [完整范围、文件SHA256和未完成项](handoffs/2026-09-16-Titan退网安全验收.md)。78项回归为模拟测试，独立audit只读复核源码/EXE及已有实际测试记录。

## 2026-09-17 本机 GUI 更新追加核验

- TEST-NET-016 / REQ-NET-008：物理校园接入且个人账号离线、个人账号在线/未知、热点四种只读 GUI 检查模拟通过；不创建租约、不安装隧道、不调用退网。GET接口认证、后台隐藏控制台、无窗口 EXE 退网错误提示等共97项相关回归通过。
- Node mockDOM 执行当前客户端 JavaScript：节点弹窗、安全文本、Mbps 单位和连接确认/取消通过，不发真实网络请求。
- 独立 check_audit：最终 EXE PE Subsystem=2，56项 manifest、包入口和29项目子模块及9项资源与源码一致，SHA256 `d6c2d527a106a761dca874c5207bfb7b7e87997b7fa45465ab3c40ff9c703312`，29,367,124字节。
- 本机只复制安装器语法检查及实际执行通过，installed=true、client_started=false、old_backend_stopped=false、network_fingerprint_unchanged=true。未运行新GUI、未操作本机借网、Wi-Fi、代理或网络服务。
- 4090测试订阅只读查询：尚未绑定、配额/上下行限速均null，2026-10-16 23:44:50 +08到期。此次未创建租约，未撤销任何现有订阅。
- 当前部署的可信校园IPv4范围仍为已验证的10.20.0.0/16，未配置IPv6或其他校园网段保守拒绝。本机实际 GUI 显示、热点切换恢复和代理/TUN真实组合未执行，不能据隔离回归声称全部兼容。
- [具体实现、使用方式及回滚](handoffs/2026-09-17-本机接入保护GUI更新.md)。
- 闪窗追加只读核验：历史事件证实物理快照循环约4.169秒，00:07:30后停止；30秒观察未捕获可见PowerShell窗口，用户确认当前不再出现。事件400不证明可见窗口，窗口采样可能漏短时事件。[独立审计](handoffs/2026-09-17-PowerShell闪窗独立审计.md)。

## TEST-NET-017：付费入网客户端精简UI

- 98项pytest隔离回归通过，新增Windows身份whoami不弹控制台且SID路径保持一致用例；原HTTP首页测试改为检查“入网客户端”。
- Node mockDOM通过：弹窗安全转义、隐藏节点地址、Mbps、取消不入网、确认后连接、未知状态/离线目录显式退出、其他操作中的排队退出。
- 使用真实Framework7本地资产和headless Chromium拦截全部页面请求，10场景通过：未订阅、读取订阅不连接、取消确认、选择第二节点并入网、目录离线时退出、初次状态读取失败时退出、恢复未完成时退出、390px长节点和SSID全文可读、异常顶层提醒及付费文案、订阅请求等待中排队退出。未请求实际客户端或服务器，未改网络。证据`artifacts/paid-client-ui-browser-result.json`及3张截图。
- 独立check_audit核验最终EXE SHA256 `b6d047c704678bd1039554ef2f7f817993ec8823f5274ca9585a0c1d3269bf48`，29,365,332字节，Subsystem=2；56项manifest、包入口、29子模块、EXE入口及9资源均与源码一致。确认580x820窗口、whoami隐藏、退出排队及双标题兼容。
- 实际只复制安装成功：client_started=false、old_backend_stopped=false、network_fingerprint_unchanged=true，桌面“入网客户端”指向按新hash隔离的程序。沿用原gui-protected-20260917配置目录，未重绑订阅或启动入网。
- [使用方式、参考图、模拟截图、绘制提示与范围限制](handoffs/2026-09-17-付费入网客户端界面精简.md)。此项不替代公网/热点/代理/TUN真实验收。

## TEST-NET-018：d321 真实原生 GUI 与错误诊断

- 25 项相关 pytest 回归通过；10 个 Framework7 模拟场景采用真实生产 CSP，字体加载检查通过。
- 实际 Session 1 原生窗口键盘提交订阅、查看授权节点、鼠标确认入网及退出；没有用客户 CLI/API 完成注册或连接。最终 32D291 制品远端哈希相同、字体正常、GUI 入网后 GitHub API HTTPS 200。
- 最终 GUI 退出后 01:22:23 +08 核对路由指定字段、DNS 和 WinINET 代理基线相同，管理 fleet-titan 保留，客户自有活跃/恢复/隧道记录消失；安全 CLI 回退任务此轮在触发前移除。
- 校园 Wi-Fi、切真实热点、所有 TUN 与应用组合未验证；有限指纹不能证明全部配置无缺陷。证据见 OPS-NET-010。

## TEST-NET-019：双向 SSH 新密钥与 Codex 任务启动

- d321 新私钥经 4090 到本机，BatchMode 及主机公钥严格校验，whoami 确认 hmw20；从此本机会话使用本机新私钥经 4090 到 d321，确认 d321-titan。私钥未跨机器传输。
- 本机 sshd 原已运行，本次无网络字段或服务配置变更。追加：本机用户手动接入校园 Wi-Fi 后，d321→10.126.63.249 和本机→10.20.31.134 均严格校验主机公钥并用专用密钥免密认证成功；不经过跳板、ZeroTier 或 WG。未故意断公网测试。
- Codex CLI Python 启动器取得新 CLI 会话，模型实际读取 TASK.md、AGENTS 与设计约束。执行开始不等于无线目标完成，不声称原生 goal 工具已登记。见 OPS-NET-011。
- 新 CLI 会话实际通过 SSH 在本机启动新客户端并查看原生窗口截图；修复助手 DPI 坐标混用后，真实 GUI 完成订阅输入并选中 C201-4090 商业源网，显示不限额、2026/10/17 到期、上下行不限速。
- 授权管理员 SSH 只读核验 configured=true、active/owned/recovering=false、个人账号 online；4090 存在 whm-save 绑定设备且当前客户活动租约为空。不可读的客户记录必须报告上下文错误，不可默认为不存在。
- 01:59:35 GUI 启动前后选定路由字段、DNS 列表、四个 WinINET 字段均相同；4 个窗口助手语法检查通过。不是完整 OS 配置安全证明。
- CLI 已正常退出并保存等待授权状态；个人账号退出授权待用户答复，本机客户入网尚未执行，完整目标未完成。见 OPS-NET-011。
- 后续用户授权退出个人账号，但本机原校园 IP 不可达；本机实际物理快照为热点，仍未进行 GUI 入网，未放宽保护或代替用户切 Wi-Fi。
- 用户另行要求“纯享入网”命名并安装到 d321：48 项相关回归通过，新制品 97B3749F 安装及桌面快捷方式目标/哈希核对通过，安装前后选定网络指纹相同，未启动客户端或停旧后台。本命名制品实际 GUI 入网未验证。

## TEST-NET-020：WiFi 扫描与校园物理订阅路径

- 109 项相关 pytest 回归、Node mockDOM 与 10 个 Framework7 headless 场景通过；覆盖校园 WiFi/有线、热点拒绝、DNS/接口绑定、源网 Endpoint 预解析、网络变化、TLS 和退出恢复。无线长名称换行、文本安全及列表滚动通过。
- 本机实际只读扫描成功，当前热点地址 192.168.43.140、5 条无线/1 条已连接；没有进行真实订阅或入网。旧 12 秒系统查询曾超时，优化后检查成功。
- 新 EXE 静态来源和关键内嵌代码核验通过；只复制安装保持选定路由/DNS/代理指纹相同，没有启动客户端或结束旧后台。详见 [OPS-NET-012](handoffs/2026-09-17-WiFi扫描与校园订阅物理路径.md)。
- 真实校园无线入网、其他 TUN 和系统重启未执行；当前热点检查不用于归因历史校园无线故障。

## TEST-NET-021：热点订阅与实际源网管理入口

- 60 项相关 pytest 通过；追加代理绕过场景后 31 项传输/只读检测回归通过。覆盖热点允许读取、双网卡私有入口优先、开户 POST 不跨路径重试、公网 HTTPS 代理、私网禁止代理、原目标 TLS、严格跳转和保持个人网络。
- 10 个 Framework7 客户 UI 模拟场景通过。独立订阅管理页面的登录、默认公网地址、手动节点/套餐选择、签发前再次验证及新 CSRF、地址全文、390px 长名称换行、客户列表滚动、凭据清空及退出通过；全部请求为模拟，不签发真实客户。
- 本机热点 192.168.43.140 实际只读 API 收到 401 正常鉴权响应，签发页面可读；校园源网 10.20.32.13:9182 不可达。匿名 401 证明 API 传输可达，不能代替真实开户成功。证据 artifacts/public-subscription-readonly-20260917.json。
- 源网服务器真实密钥登录、公网来源拒绝、CSRF、正确源网列表、云端/源网 Cookie 隔离和退出验证通过；没有生成测试客户或租约。初始密码哈希不匹配，密钥匹配，不将初始密码当作当前密码。
- 最终 EXE SHA256 ef192137845d7a38f8d28b35d2a890633008b911046880fbb8e7717d1934cc87，29,384,293 字节，Subsystem=2；58 项 manifest、5 个关键模块及 UI/PS 资源静态核验通过。只复制安装成功，选定路由/DNS/代理指纹相同，不启动客户端、不结束旧后台。
- 公网及现有 WG 管理入口 HTTP 检查通过；浏览器控制通道失败，打开请求排队，未确认本机真实浏览器呈现。真实新版本开户/入网、Wi-Fi 切换、UDP 握手及重启未执行。详见 OPS-NET-013。
- 后续用户截图确认管理页在内置浏览器真实呈现；自动控制连接仍失败。当前源网密钥可用，当前密码无可恢复明文；密码重置等待明确授权。

## TEST-NET-022：客户订阅地址持久找回及补发

- 21 项相关 Python 测试通过；4090 最终候选包 11 项新测试通过。覆盖加密存储及重新打开数据库找回、存档失败原子回滚、旧客户补发保留套餐/授权/账期、旧令牌失效、满额设备/停用/授权到期拒绝、错误密钥以及管理员/CSRF/Origin/近期验证保护。
- 390px 隔离 UI 通过完整地址自动显示/全选、刷新找回、显式确认补发、客户数量保持、设备满额按钮不被忙碌状态恢复误启用、凭据清空及零浏览器持久化存储。
- 实际 WG 管理入口客户列表/令牌状态/查看操作到达 4090 商业源网后端；历史摘要地址返回无法还原，符合预期。公网新页面返回 200。现有 10 个客户、套餐、授权、账期锚点及令牌与部署前备份逐行一致，未真实开户或补发。校园 IPv4 路由与受保护系统网络服务的 InvocationID/MainPID/状态一致。见 OPS-NET-014。
- 未执行真实客户补发、真实本机开户注册或入网；不以隔离测试宣称真实借网通过。

## TEST-NET-023：当前校园账号 Logout 与已注册身份刷新

- 84 项相关 pytest 通过：tests/test_client_campus_logout.py、test_client_online.py、test_client_access_gui.py、test_client_attachment.py、test_client_ui_activation.py、test_client_handshake_guard.py、test_client_lan_transport.py。独立 basetemp 避免原管理员测试临时目录权限问题，不改 ACL。
- 12 个隔离 Framework7 场景通过：包括已注册刷新不发送 enrollment POST、Logout 取消无 POST、确认一次 POST且不自动 connect；390px 长文本换行及无线列表滚动可读，无实际服务请求。
- 真实只读认证状态 online，物理源网 TCP 9182 可达，令牌已使用且存在设备/无租约。未执行 Logout、修改网络或开始借网。
- 最终 EXE 59 项 manifest、7 个冻结模块及 UI/PS 静态一致性验证通过，安装后桌面目标哈希一致；选定路由/DNS/代理指纹不变，未启动新版或终止旧后台。详见 OPS-NET-015。

## TEST-NET-024：一键自动 Logout 与未认证源网路径复核

- 最终 117 项相关 pytest 通过，含 test_client_one_click_connect.py。覆盖 Logout → 下线验证 → 租约 → 隧道顺序；已下线不注销；无授权/订阅失败/源网不可达时不注销；注销失败/网卡变化/下线未知/注销后 TCP 被拦截时无租约和隧道；注销后租约或安装失败报告个人账号已下线并清理；已有连接拒绝再次执行。
- 12 个隔离 Framework7 场景及 catalog VM 检查通过，直接 Start 无二次确认，连续点击只有一次 connect POST，保持独立 Logout 确认、离线退出、长文本及排队恢复。未访问实际源网或注销个人账号。
- 真实只读 03:07:37 UTC 的 WLAN 物理 TCP 9182 可达和匿名公网 API 401，仅代表当时状态；用户提供 Logout 后 ping 超时及订阅 TLS 失败，未做同步未认证 TCP/UDP 对照。校园访问控制推断未标为已验证。
- 新 EXE 59 项 manifest、7 个冻结模块及 UI/PS 静态一致性验证通过，只复制安装与桌面目标二次哈希一致，路由/DNS/代理指纹一致，不启动新版/终止旧后台。真实本机自动 Logout/借网和有线未认证可达性未验收。见 OPS-NET-016。

## TEST-NET-025：D321 有线 GUI 入网与控制运行时

- CLI 在 D321 交互会话实际读取并执行 computer-use；窗口可发现/激活，截图恢复仍失败，纯 accessibility 无可操作按钮。官方浏览器运行时清单为空，新的 GUI Start 未执行。
- 初始既有租约及近期 WG 握手不是本次点击证据，随后发生后台检查异常自动退网。12:41:56 独立审计 active=false，无租约；有线校园 TCP 9182 可达，fleet-titan Running，真实无 HTTP 代理 HTTPS 失败。
- 临时管理 CONNECT 只用于 CLI 模型访问，不计公网借网验收；未调整系统代理/Clash/校园认证或操作员本机网络。总体未通过；稳定续租、无线、其他平台及重启未验证。详见 [OPS-NET-017](handoffs/2026-09-17-D321有线GUI入网验收.md)。

## TEST-NET-026：品牌窗口和 Clash 自主代理

90 项相关 Python 回归与 Framework7 headless 场景通过，覆盖窗口桥接、外部页面拒绝、关闭恢复、离线 Controller、配置重生成、内核绑定丢失和 DNS 字段升级恢复。61 文件 manifest、10 冻结模块、windowed PE 与品牌图标核验。只复制安装不改选定网络指纹。本机三节点 136/183/407 ms、d321 三节点 479/562/892 ms；d321 Clash HTTPS 200，校园路径及管理隧道保留。DoH 223.5.5.5 与 1.1.1.1 返回合法 DNS 回答。新版实际 TUN/Verge 重启、原生桌面截图、跨平台及系统重启未独立验收；详见 [OPS-NET-018](handoffs/2026-09-17-商业窗口与Clash自主选择.md)。

## TEST-NET-027：D321 最终 EXE 入网和持续代理

52 项相关 pytest 与两个隔离 GUI 脚本通过。最终 EXE 7A29A5A4BA10 通过原生窗口 Start 发起入网。规则/全局/直连 × TUN 开/关：百度和 GitHub 全部 HTTPS 200，4090:9182 和 5080:22 全部可达。模式切换由真实控制器执行，网络请求严格验证 TLS；GUI Start/Leave 为实际鼠标点击。新版 Leave 后路由、DNS、系统代理与基线均相等，管理隧道保留。无线、所有机场协议、全部主机双向互通、系统重启不在本次通过范围。证据与限制见 [OPS-NET-019](handoffs/2026-09-17-D321入网按钮与Clash持续共存修复.md)。
## TEST-NET-028：D321 无本机 Mihomo / 系统代理真实联网

当前 D321 Mihomo/Verge GUI 进程均不存在，系统代理关闭。显式禁用所有 HTTP 代理和 curlrc 的 Google、YouTube、ChatGPT、百度 HTTPS 均 200，TLS 验证正常。清除代理变量的实际 Codex CLI 返回 SOURCE_NETWORK_OK，退出码 0。租约、握手、校园源网服务、管理 WG 和 4090→D321 校园 SSH 保留。YouTube 视频播放、无线和系统重启未执行；服务 Auto 启动类型未更改。见 [OPS-NET-020](handoffs/2026-09-17-D321停用本机Mihomo验证源网出口.md)。
## TEST-NET-029：4090 实际代理路径及 D321 校园双向 SSH

本机代理关闭且无 Mihomo，绑定客户 WG 地址的三项固定端口请求与源端 journal 逐项匹配，记录机场节点；转发、Meta 路由与 NAT 规则一致。YouTube/ChatGPT 200，Google 正常重定向及独立 204；首页另一次下载超时明确保留。4090/5080/D408 与 D321 校园双向真实 SSH 全部通过；D437 的 SSH/邻居不可达，4090 也无法连接，不计通过。网络配置未修改。见 [OPS-NET-021](handoffs/2026-09-17-4090实际代理路径与校园互通核验.md)。

## TEST-NET-030：源端完整停代理时客户机场无法独立维持出口

第一轮被 health 守护10秒内恢复，后续模型完成不计源端无代理成功。第二轮暂停守护，journal证明核心15:10:13–15:11:28一直停止，40次采样Meta不存在、配置/nft不变；D321真实本地核心及7897监听仍存在，三站HTTPS均超时，模型20秒无回复。源端/守护恢复及D321原关闭代理状态恢复后，无HTTP代理ChatGPT200/TLS正常、实际Codex回复SOURCE_NETWORK_OK退出0，租约/管理/校园三台SSH保留。源端默认解析TLS异常、显式代理403及客户端机场恢复后不稳定单列，不伪称全通过。见 [OPS-NET-022](handoffs/2026-09-17-4090停用恢复与客户端代理出口测试.md)。
# TEST-NET-031：商业出口策略统一验收

关联 REQ-NET-023 / OPS-NET-023。完成实现后依次执行出口契约/数据库迁移/API/CLI/中继回归、管理 UI 构建与浏览器检查、Linux临时 namespace 的真实 WG/NAT/路由/限速/撤销。4090 + D321 公网及客户端自身 Clash 的实机测试单独记录，不用隔离结果代替。操作员本机网络不作测试。结果见实施记录。

106 项相关回归、390px浏览器 fixture、Angular构建、13 项真实 namespace 检查及 4090停止核心/TUN后 D321自己的代理/模型访问通过；源端已上线，原策略及代理状态保持。Google首轮超时后重试通过；D437不可达、IPv6及Windows源端物理策略未算通过。详见 [OPS-NET-023](handoffs/2026-09-17-商业物理出口与客户自主代理.md)。

# TEST-NET-032：默认物理出口和代理就绪门禁

关联 REQ-NET-024 / OPS-NET-024。120 项联合回归通过；另新增 API 场景核验代理失效后节点不可用、中继不下发策略、续租拒绝，对应 API 文件 4 项通过，合计 121 个相关场景通过。390px fixture 验证默认不勾选代理、未就绪禁用、显式启动携带当前 CSRF、凭据清理、启动不修改线路、仅选中节点代理授权及长文本。Angular 生产构建通过（超警告预算约19.25kB，未到错误阈值）。实际 4090 root-owned helper/受限 sudoers 及真实鉴权 API 的启动动作成功且授权模式不变；公网 HTTPS 已返回新版 UI。9 条商业授权 physical、2 个在线客户 physical；主路由和已有非 owned 策略规则与基线相同，D321↔4090/5080/D408 SSH及 D321无代理百度 HTTPS 200 通过。D437仍不可达，不算通过。代理停止门禁为隔离测试，未在本轮现网停用核心；真实冷启动、远程其他源机控制、Windows源端物理策略及IPv6未验收。操作员本机网络未改。

## TEST-NET-033：通知设置不影响网络

关联 REQ-NET-025 / OPS-NET-025。53 项后端回归通过，包含持久化、无效参数、保存失败、关闭 balloon 后错误窗口保留且合并、接口鉴权及不调用网络退出。Framework7 fixture 点击 toggle 和页面重载通过，未触发入网/退网。D321 EXE 哈希及安装报告验证通过，原连接未中断，网络指纹不变。新版原生 GUI 和新客户实机连接未执行，不能用隔离 UI 结果代替。

## TEST-NET-034：多订阅不覆盖身份及安全切换

关联 REQ-NET-026 / OPS-NET-026。普通版 84 项回归及 Framework7 fixture 通过；Next 全部 119 项回归及独立 GUI fixture 通过。迁移及页面隐私、注册去重、旧身份恢复/释放先于选择、恢复失败阻止、备用删除不停止、当前删除保留其他项、同 SID 显式导入、损坏列表不覆盖、390px长文本核验。两侧快捷方式及 EXE 哈希验证通过，普通版路由/DNS/代理指纹不变；Next 的 windowed PE 与独立包及源清单验证通过。本机 Next 找回一份旧身份且当前身份未改变。新版原生窗口和真实多订阅入网切换未执行，服务器网络未变。
# TEST-NET-035：网址/订阅码兼容及 D321 历史列表

REQ-NET-027。102 项相关 Python 测试通过，涵盖 HTTPS/内网 HTTP 跨格式解析、畸形/超长/不支持版本、端点限制、身份/租约不变、跨格式去重及开户路径。Framework7 隔离浏览器场景通过，先添加网址，再以订阅码添加备用项，确认连接保持且切换需人工确认。真实 D321 GUI 部署结果见 OPS-NET-027；本机未启动 EXE 或修改网络配置。
# TEST-NET-036：添加订阅首屏可见

REQ-NET-028。既有 Framework7 隔离浏览器用例验证添加区为主页面第一个元素，580×820 和 390×844 首屏可见添加按钮，保留原有添加、切换、移除、退网及长文本场景。没有访问真实订阅服务或在本机运行 EXE；本轮未更新 D321。分发打包和校验见 OPS-NET-028。

后续折叠版：隔离用例验证默认 aria-expanded=false、展开/折叠可操作且不会发送 POST；展开后切换及移除保持可用。D321 真实 GUI 部署见 OPS-NET-029，此项只验证 GUI 升级及未入网网络状态，不能代替真实网络连接验收。

## TEST-NET-041：普通客户端 Sol 界面与安全交付

REQ-NET-033 / OPS-NET-034。129项相关Python回归、两套隔离UI fixture及独立离线预览检查通过，覆盖订阅码/网址、历史切换/删除/折叠、重复连接抑制、排队退出、离线与未知状态可退出、通知持久化、不可用节点禁用、文本转义/长值滚动、生产CSP、本机统计与服务端套餐区分。580×820主操作首屏可见；420×560、390×844与900×900无整页水平溢出、退出可达。

EXE静态核验69份摘要、6份UI资源、10个冻结模块、图标与GUI子系统通过，不执行客户端。79项ZIP内容/摘要核验通过，无用户数据。本机文件安装前后路由/DNS/代理只读指纹相同，client_started=false、old_backend_stopped=false。没有真实入网测试、D321部署或全新Windows人工安装向导验收；不能用fixture替代实际校园Wi-Fi/TUN联网测试。详见 OPS-NET-034。

## TEST-NET-040：客户归档/回收站与标签备注

REQ-NET-032 / OPS-NET-033。81 项相关后台测试通过，标签/备注不影响活跃租约，归档/回收站停用撤销，恢复仍停用，旧启用接口拒绝绕过，保留身份/原地址/历史用量，输入限制、版本冲突和客户隔离通过。两套浏览器 fixture 含标签筛选/备注展开、HTML文本不执行、归档隐藏/回收站/恢复以及已有业务工作流通过，三宽度无整页溢出。4090候选隔离数据库运行时通过；真实 public 鉴权读取12客户、新字段与CLI一致、九资产一致通过。只重启商业控制API，路由、规则、源代理/WG/中继指纹及身份摘要保持；没有处理真实客户或测试本机网络。部署与回滚证据见 OPS-NET-033。

## TEST-NET-039：Sol 现成组件 UI 和核心工作流

地址就近查看追加：两套 fixture 再次通过；1440/1024/390px 确认弹窗在视口内，打开不跳页、关闭保留查询位置，Esc 清空输入凭据和地址。查看结果无需刷新全列表。新版公开资产一致及真实鉴权读取12客户通过；部署不重启服务、不改路由或真实订阅。证据与备份见 OPS-NET-032 同日补充。

REQ-NET-031 / OPS-NET-032。已通过：两套浏览器 fixture 覆盖默认客户管理和无修改请求的三工作区导航、生成、受保护查看/补发、180 天/无限额度与不限速修改、逐设备停用/恢复、源机启动和源/线路出口编辑；消费保留、代理就绪门禁、旧数据锁定、退出清空和浏览器凭据不存储。1440/1024/390px 无整页水平溢出，长值完整读取，宽表仅自身滚动；本地 Tabler 固定资产，无脚本错误或意外外部请求，桌面与390截图审查通过。后台相关 69 项回归通过。真实 public 登录读取12客户、Cookie/CSRF代理、匿名401、九份发布文件一致及控制CLI客户数一致通过；源端路由/策略规则/API与网络服务指纹发布前后相同，没有服务重启。真实客户修改只在 fixture 验证，线上仅只读验证。详细证据和两处布局修复见 OPS-NET-032。

## TEST-NET-038：订阅原址变更与服务端一致仪表盘

REQ-NET-030 / OPS-NET-031。69 项后台联合回归通过，覆盖 URL/设备/账期/用量不变、独立套餐、180 天/无限量、期限缩短和停用撤销、源代理门禁/切换、旧身份恢复过期服务、一次性地址恢复、版本冲突/越权字段/事务回滚、跨日计数与速率方向、鉴权/CSRF/fresh/no-store、新 CLI show/update 和 stale 错误。管理页与仪表盘两份隔离浏览器用例通过，包含修改载荷、筛选、代理就绪、390px 完整文字/横向表格、数据失败锁定、退出清空和无持久化凭据。

4090 部署候选临时数据库验证通过；真实鉴权新 API 读取 12 客户，匿名 401，静态文件字节一致、订阅和设备身份指纹保持；IPv4 路由全表/ip rule 指纹及 Mihomo/商业 WG/中继 timer 状态一致。公网鉴权读取与 cookie/CSRF 代理通过，Caddy 校验/reload、错误 Origin 403 和 WG 管理入口通过。没有修改真实客户授权、操作者本机网络或执行 GUI 重入网，不能用读取与 fixture 代替真实客户出口变更后的联网测试。

## TEST-NET-037：安装文件隔离与订阅本机统计

REQ-NET-029 / OPS-NET-030。115 项联合 Python 回归通过，覆盖历史身份、订阅格式、恢复和新增安装路径/文件校验/旧配置保留/同版本文件不覆盖、LocalAppData 显式导入同用户 ProgramData 历史、流量差量与持久化、计数器重置、跨日、不同订阅、损坏文件保留、统计失败不影响退出及 owned transfer 校验。Framework7 隔离 GUI 场景通过，新增今日、累计及下行/上行单位校验，保留首屏添加、折叠历史、切换、移除和退出流程。Windows PowerShell 5 解析安装 helper 通过。

已构建客户端 EXE 和 Install.exe，并校验完整 ZIP 文件摘要、两份 EXE 为 GUI subsystem=2。Install.exe 无窗口自检验证冻结 Tcl 8.6.15、Tk DLL 和 helper 资源，全程不执行安装、快捷方式或网络操作。尚未执行新客户 Windows 的人工目录选择、桌面选项、真实 ACL/快捷方式及入网流量验收；不能以 mock 和资源检查代替实机网络验收。

## TEST-UI-NAV-001：导航、读缓存和副作用隔离

REQ-UI-NAV-001。94 项 Python、2 项 Angular、5 组隔离浏览器场景通过，覆盖历史/刷新/深链/滚动/草稿、预取去重/取消/失效、显示专用 SWR、减少动态效果、失败动画/资产、no-JS 普通链接、超时不重放与旧 snapshot 锁定。lint/生产构建/wheel 资源检查通过。匹配人工延迟 fixture 首次列表 533.6 → 311.4 ms；热跳转约 15 ms（减少动态效果）/49 ms（正常动效）。未线上实测、未部署、未真实借网或变更操作员网络。详见 handoffs/2026-09-18-smooth-navigation.md 及 docs/SMOOTH_NAVIGATION_IMPLEMENTATION.md。

## TEST-NET-073：D321 CLI 入网与 WireGuard 握手复验（未通过）

关联 REQ-NET-066。2026-10-02，D321 的 `d321-titan-jump` 在连接尝试前后均可达。C201 的 Reliable SSH `lan` 路由到 `10.20.32.13:22` 超时，备用 `zn` 路由身份探测成功。C201 用户级 `sna-commercial.service` 为 active，UDP `51910` 监听 `0.0.0.0` 与 `::`；这些只证明控制服务和 UDP socket 状态。

D321 的 `check-source` 操作 `e153ac036e5b4ded827741e7ed20d4d2` 成功后，只发起一次 `connect --json --timeout 180`。连接操作 `bb7f50df264d437e8c1983ad119a3ee5` 被接受，最终失败，客户端事件报告“源网未完成 WireGuard 握手”，并回到 `IDLE` / `connected=false`。调用等待超时后通过该操作的事件记录确认失败，没有重发。失败后的 `check-source` `8587fbc2ff264272bd041d2628c9ef66` 成功，D321 管理跳板仍可达；这不构成借网成功或公网出口验收。

C201 SSH 账户执行 `wg show all latest-handshakes` 收到 `Operation not permitted`，无法观察源端 peer 最近握手和收发计数。当前只能确认失败在握手阶段，不能区分校园 UDP 传输、本机 VPN/TUN、源端防火墙或 peer 配置。根因待有权限的源端握手/计数证据或受控抓包；本轮没有再次重试，也没有修改 C201 数据面。完整记录见 `handoffs/2026-10-02-WireGuard稳定peer续租与替换模拟.md`。
