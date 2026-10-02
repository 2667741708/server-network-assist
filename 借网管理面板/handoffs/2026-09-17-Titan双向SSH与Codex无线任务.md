# OPS-NET-011：双向 SSH 授权与 Titan Codex 无线目标任务

关联 REQ-NET-010 / REQ-NET-011 / TEST-NET-019。

## 双向授权

用户明确授权两端 SSH 访问。各端本地生成独立 Ed25519 私钥，私钥不复制、不打印；只交换对应公钥。授权到各端现有 Windows OpenSSH 的 administrators_authorized_keys，保留原授权内容及时间戳备份，文件 ACL 限 SYSTEM / Administrators。公钥附 no-agent-forwarding,no-X11-forwarding。已有 sshd 均运行，本次没有修改防火墙、sshd 配置、路由、DNS、系统代理或 Wi-Fi，没有重启 sshd。

本机私钥 `C:/Users/hmw20/.ssh/id_ed25519_d321_titan`，指纹 `SHA256:PebA9BODR2zs+xVLDBASpI4j+nx7MG1QNLm8TM8qang`；d321 私钥 `C:/Users/86133/.ssh/id_ed25519_whm_save`，指纹 `SHA256:wqT0jwNeJYHGxkT+v3xqZV8yUxKzyCM2QqGt75THEb4`。文档不存私钥内容。

现有默认 SSH 配置不变，专用配置 `~/.ssh/sna-peer.config` 使用 IdentitiesOnly 和 StrictHostKeyChecking，主机公钥经既有可信管理路径读取并固定到专用 known_hosts。

## 实测与命令

d321 经校园 4090 跳板访问本机已有 ZeroTier 地址，免密 `whoami` 返回 `whm-save\hmw20`。再从这条可信 SSH 会话在本机执行反向 SSH，使用本机新私钥经 4090 到 d321 校园地址，返回 `desktop-td6b9gn\d321-titan`。验证的是新密钥双方真实认证，没有跨机器传私钥。

本机当前已验证路径：

```powershell
ssh -F "$env:USERPROFILE\.ssh\sna-peer.config" d321-via-4090
```

本机接入可与 d321 互通的校园网后的直连入口（追加实测通过）：

```powershell
ssh -F "$env:USERPROFILE\.ssh\sna-peer.config" d321-campus
```

d321 当前已验证路径：

```powershell
ssh -F C:\Users\86133\.ssh\sna-peer.config whm-save
```

以后本机获得校园 IPv4 后，可使用固定的本机主机公钥别名：

```powershell
ssh -i C:\Users\86133\.ssh\id_ed25519_whm_save -o HostKeyAlias=whm-save-campus -o UserKnownHostsFile=C:\Users\86133\.ssh\known_hosts_whm_save -o StrictHostKeyChecking=yes hmw20@<本机校园IPv4>
```

当前 d321 对本机 WireGuard 10.201.250.40 的 TCP 建连探测虽成功，真实 SSH banner 超时；fleet-titan 只允许 10.203.49.0/24，不能据 TCP 探测说管理地址可登录。本次没有为修复此路径更改 WireGuard 或路由。

追加实测：用户手动连接 iYanDa-Dormitory，本机校园 IPv4 为 10.126.63.249/17，d321 校园 IPv4 为 10.20.31.134。专用 whm-save 别名已改为本机校园地址，保留严格主机公钥检查（HostKeyAlias=whm-save-campus）。d321 直接免密认证返回 whm-save\\hmw20；再在该可信会话中使用本机专用私钥直连 d321-campus，返回 desktop-td6b9gn\\d321-titan。两次实际 SSH 均不经过 cloud、ZeroTier 或 WireGuard。未故意断开本机公网做测试；任意 Wi-Fi 不保证校园互通，DHCP 地址变化后需更新专用 HostName。

d321 公钥的本机副本为 C:/Users/hmw20/.ssh/d321-dedicated.pub；已经加入本机现有 SSH 授权。公钥不是私钥，不能用它直接认证；本机访问 d321 使用本机自己的 id_ed25519_d321_titan 私钥。

## Codex 任务

服务器源码快照：`C:/Users/86133/sna-wifi-goal-20260917`。只打包源代码、测试与设计约束，没有客户凭据、订阅 URL 或私钥。任务存于 TASK.md，独立目录供 Codex 修改，不自动提交、不覆盖本机工作树。

手动任务 `SNA-Codex-Wifi-20260917` 在 d321 已登录的 Session 1 执行 Python 无窗口启动器；没有开机触发器。它启动真正的 `codex exec`，用当前已登录 ChatGPT 身份、现有 Clash 127.0.0.1:7897 和进程级 HTTP_PROXY/HTTPS_PROXY，不修改全局代理。PowerShell 启动器此前提前退出，改用 Python subprocess 捕获 stdin/stdout/stderr，避免原生命令 stderr 中断主操作。

已确认 CLI 会话 `01a0ab46-2abf-7a23-bda8-dd2a35c85245` 收到任务并开始真实读取 AGENTS、设计约束与源码；不是仅创建进程。模型列表刷新曾报告子进程超时，未阻止随后模型执行。任务要求原生 create_goal 若工具可用，否则持续执行目标；目前没有证据确认该 CLI 提供了原生持久 goal 工具，不能称原生 goal 已登记。

目标包括真实校园无线识别、物理网络/TLS/订阅可达性验证、GUI 入网/退出、路由/DNS/代理恢复、校园与管理路径保护、热点和个人校园账号优先。SSID 仅作提示，不以名称盲目信任网络，不扩张到所有私网，不关闭防火墙、不跳过 TLS。

禁止任务修改本机 WHM-SAVE 的网络配置或在其他服务器做网络变更；新 SSH 授权不是本机网络测试授权。无线真实验收尚未完成，读取源码不等于功能完成。阶段状态在 artifacts/TASK_STATUS.md，事件 codex-events.jsonl、错误 codex-stderr.log、最终 codex-final.md；这些文件可能在任务进行中尚未生成。

回退授权时只移除带本次专用公钥的行及本次生成的专用配置/known_hosts/密钥，不覆盖其他授权；无需改路由或停管理隧道。暂停任务只停止该手动任务，不能结束既有 Codex App 或其他 CLI 进程。

## 新目标：d321 Codex 操作本机原生 GUI

用户另行明确要求 d321 Codex CLI 操作本机客户端 GUI 完成订阅及入网；这项新授权只覆盖客户客户端的显式操作，不授权任意更改本机 Wi-Fi、DNS、系统代理、其他 VPN 或自动退出个人校园账号。旧 SNA-Codex-Wifi-20260917 任务已暂停，避免与新目标争抢 d321 网络。

新任务 SNA-Codex-Local-Gui-20260917，目录 C:/Users/86133/sna-local-gui-goal-20260917，CLI 会话 01a0ab56-bdf5-7f31-9a82-303917929804。CLI 已通过严格校验的校园 SSH 启动本机新 EXE、查看真实窗口截图并尝试输入订阅；不是仅创建进程。动作桥只允许该 EXE 所有者窗口的查看、输入、滚动、点击及只读状态核验，要求前台和坐标边界检查，不以客户 API/CLI 代替 GUI 注册及连接。

本机实际无线网段追加为已验证的 10.126.0.0/17；物理接口绑定的源服务请求返回 200，校园认证入口 TLS 验证通过并返回 302。相关 38 项回归通过。新 EXE SHA256 为 7D0E87EBBF7AF9CCD58134638DA6BC4BE00A8EE405E9DE6B0C145343CA22508B，已按哈希隔离安装并由 d321 CLI 启动。不能据网络可达及回归声称全部热点/TUN 组合已完成。

本机个人校园账号当前已在线，客户端应拒绝接管；已向用户请求是否允许退出个人账号，未得到答复前 PERMISSION.json 保持 campus_logout_allowed=false。

追加 01:59 +08 实测：窗口工具未声明 DPI 感知，GetWindowRect 的逻辑坐标与 CopyFromScreen / 鼠标物理坐标混用，截图包含背景并裁掉窗口右侧，输入误选页面文字。窗口检查及输入助手均改为 SetThreadDpiAwarenessContext(-4)，失败即停止；截图与输入使用物理像素，输入前比对当前窗口与快照尺寸，不一致必须重新查看。完整窗口为 859×1202；只调整工具坐标，没有改系统显示缩放或客户端网络。

d321 CLI 01:57:08 通过真实 GUI 输入订阅，随后截图确认已选择 C201-4090 商业源网，剩余流量不限额，到期 2026/10/17，下载/上传不限速。01:58:36 CLI 及 01:58:55 经授权管理员 SSH 的独立只读核验均确认 configured=true、active=false、owned=false、recovering=false；本机个人认证状态 online。4090 服务端实际存在绑定设备 label=whm-save，当前客户活动租约为空，未产生借网连接。CLI 于 01:59:22 正常退出（exit_code=0），明确报告等待账号退出授权，没有声称目标完成；恢复下一阶段须用户答复，不可将进程退出或等待超时当作重新入网授权。

只读核验需管理员 SSH 上下文，因为客户记录受 ACL 保护；本地普通进程曾出现 PermissionError，其缺失判断不能作为未配置证据。verify 助手新增管理员上下文检查，避免把不可读记录误判不存在。4 个本机窗口助手语法检查通过。

01:59:35 对 GUI 启动前基线核对，routes_equal、dns_equal、wininet_proxy_equal 均 true；范围为选定路由字段、DNS 服务器列表及四个 WinINET 字段，不证明全部 OS 配置无缺陷。证据为 artifacts/whm-native-subscription-confirmed-20260917.png、artifacts/local-gui-current-audit-20260917.json、artifacts/local-gui-network-baseline-20260917.json 和远端 artifacts/TASK_STATUS.md、codex-exit.json。截图无订阅 URL，审计只输出白名单状态，不输出凭据。

本机入网尚未执行，完整目标仍未完成；没有自动退出个人校园账号，没有故意断公网，没有放宽保护规则。

## 个人账号退出明确授权后的继续阶段

用户随后明确回复允许退出本机个人校园账号，再由 d321 Codex 通过 GUI 入网。两端 PERMISSION.json 已按此答复记为 true；只针对本次显式操作，不改变生产客户端“个人账号优先”规则，也不授权任意改 Wi-Fi、其他 VPN、系统代理或 DNS。预置基线保留。

独立脚本 authorized_whm_campus_logout.py 在 WHM-SAVE 检查明确授权和当前物理校园接入，仅对本机物理认证会话使用原 netlogin.py 已有 /eportal/network/offline JSON 端点；绑定物理网卡、固定已验证门户地址、严格校验证书，不使用密码，不打印 sessionId。此脚本不是客户端自动功能，认证 unknown 时停止，不做账号所有设备下线。

客户专用 8 分钟 safety-exit 只作用于本次隔离数据目录，成功完成实际 HTTPS 和校园双向 SSH 核验后删除；不删除已有管理隧道。脚本及 7 个相关 PS 助手语法检查通过，退出脚本及恢复启动器 py_compile 通过。先探测直连 SSH，再单独上传恢复提示与启动器，再单独启动。

手动任务 SNA-Codex-Local-Gui-Resume-20260917 于 02:10:23 恢复同一 CLI 会话；已收到 thread.started / turn.started，未新增开机触发器，保留第一阶段事件及退出记录。第二阶段要求实际查看窗口并点击确认，不允许客户 CLI/API 代替 GUI 连接；当前尚待执行结果，不据启动认定入网成功。

后续真实执行停点：Codex 已布置安全任务并启动一次性退出 helper，但原 WHM-SAVE 校园地址不再可达，不能独立确认退出结果；没有点击 GUI 入网，没有用 API/CLI 代替。独立本机只读物理快照显示已切到热点，IPv4 为 192.168.43.140，IPv4/IPv6 默认出口均为热点，校园服务请求超时。保留热点优先，不自动切回 Wi-Fi、不放宽校园判断；本机 GUI 入网须用户手动接回校园网络后继续，不能称完整目标完成。

## 用户指定的新名称与 d321 安装

用户要求将付费客户软件命名为“纯享入网”，区别于旧桌面入口。窗口标题、页面标题、托盘标题/通知统一改名，旧“入网客户端”和“借网客户端”的窗口激活兼容仍保留。独立安装目录为 C:/Users/86133/AppData/Local/Programs/PureNetworkClient/gui-97B3749FB046，保留既有 gui-window-test-20260917 客户数据目录，不重新绑定订阅。

48 项相关 HTTP、重复打开/恢复保护及校园接入回归通过。新 EXE SHA256 97B3749FB04647B454494D68F4BB334B67ADB9801943B515134D9FD38C29ACEC，29,395,603 字节；经 Reliable SSH 上传，安装后哈希一致。桌面 C:/Users/86133/Desktop/纯享入网.lnk 已创建并核对指向此 EXE及原客户数据。

安装报告 installed=true、old_backend_stopped=false、client_started=false、network_fingerprint_unchanged=true；安装不启动/停止应用或网络服务。旧 SNA Customer Client 指向 ProgramData 的 0.7.1 客户 EXE，服务器网络助手是 0.3.1 管理桌面程序；此次不删除这两个旧入口。SSH 继承 USERPROFILE 可能为过期值，GetFolderPath(Desktop) 可能为空，安装使用已核实的 DesktopDirectory/ProgramsDirectory/DataDirectory，不依赖这些过期环境变量。

纯享入网命名制品的实际 GUI 启动和入网未执行；本次验证是安装、快捷方式、哈希及选定网络指纹，不能把早先其他哈希的真实 GUI 入网记录当作本制品测试。安装报告在远端 artifacts/pure-install-result.json。
