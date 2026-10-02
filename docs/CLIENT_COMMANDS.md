# 借网、退网命令与面板教程（v0.3.1）

## 打开已经运行的客户端面板

新版 EXE 重复打开时会唤回已有桌面窗口；旧 Python 网页后台没有桌面窗口或托盘时，会打开它的浏览器面板。检测到已有后台后不重启服务、不修改网络。后台仍在启动或恢复中时仅重试显示并提示等待。

```powershell
.\ServerNetworkAssistClient.exe --show
```

`--show` 只打开已有面板；找不到后台时不会启动客户服务或执行网络恢复。源码环境可执行 `python scripts/show_client_ui.py`。只有原生桌面客户端提供托盘，旧网页后台不提供。

## 新客户原生 EXE：退出组网

2026-09-16：Titan 新客户窗口提供顶部红色“退出组网 · 恢复原网络”按钮。点击后保留窗口和订阅，停止客户借网并恢复它改变的网络配置。关闭原生窗口也执行恢复；旧浏览器标签关闭不具备这个保证。

```powershell
.\ServerNetworkAssistClient.exe --leave-network
```

已安装 CLI：`server-network-assist-client --disconnect`。后台使用自定义目录时，加 `--data <后台实际目录>`。Windows 需管理员身份，Ubuntu 需 sudo。客户端先做本地恢复，远端租约释放失败不会阻止本地退网。

`--disconnect` 与 `--leave-network` 等价。最小化进入通知区并保留借网，关闭原生窗口会退出。个人校园账号登录请点顶部个人登录按钮，先退网再打开认证页；热点/物理接入变化会提醒并自动退网，不自动重新加入。保留用户自己更改的代理及Clash配置，具体实测和限制见[Titan安全验收](../借网管理面板/handoffs/2026-09-16-Titan退网安全验收.md)。

Titan 桌面入口是 `SNA Customer Client`。打开后输入订阅地址、刷新节点、选择节点并启动借网；顶部按钮退出。应用分流区填可执行文件名，读取当前 Clash 的已有策略，然后应用所选策略。TUN 用于接管不读取系统代理的应用；系统代理只影响支持它的应用。

Titan 的 `fleet-titan` 已改为管理专用，以下旧部署教程仅供识别历史部署，不能用于停止当前 Titan 管理连接。详细实测和未通过项见 [Titan 原生客户端验收](../借网管理面板/handoffs/2026-09-16-Titan原生客户端验收.md)。

可以从任何具有 SSH 访问权限的 Windows / Ubuntu 管理机发起操作，但命令最终在目标客户端执行。新机器须先安装程序、配置出口和权限。当前没有 `server-network-assist borrow/leave --host ...` 这样的统一命令；下面按真实部署分别说明。

退出应在目标客户端执行恢复流程。不要为移除一台客户端而关闭整个共享出口。以下 SSH 命令供人在已配置 SSH 别名的管理机或出口服务器上执行；它们只把操作发送到指定客户端。

## d408 的旧独立部署

在管理机执行 `ssh -t d408` 进入客户端；或直接在 d408 终端操作。借网：

```bash
sudo /usr/local/sbin/d408-network enable
```

上一条成功后，启用开机借网：

```bash
sudo systemctl enable d408-network-boot.service
```

该入口同时安装借网所需的策略路由和 DNS，不能替换成仅启动 WireGuard 服务。它不自动启用本机 Mihomo 代理。

已将 `scripts/d408-stop-sharing.sh` 放到 d408 的 `/home/d408/stop-borrowing.sh`。客户端执行一个命令：

```bash
sudo bash /home/d408/stop-borrowing.sh
```

管理机或出口服务器执行一个命令（需要已有 `d408` SSH 别名，按提示输入该机 sudo 密码）：

```bash
ssh -t d408 'sudo bash /home/d408/stop-borrowing.sh'
```

脚本先备份服务与路由状态，再调用该机原有 `d408-network direct`，清理本方案策略路由、防绕行规则、隧道 DNS 和本机代理，最后停止并禁用 `d408-network-boot`、`wg-quick@wg-d408` 及两种本机代理服务。只针对 d408，脚本会校验主机名；不能把它套用于其他机器。直连之后能否访问公网取决于该机原网络是否已经认证。

检查状态：

```bash
sudo /usr/local/sbin/d408-network status
ip -4 route get 1.1.1.1
systemctl is-enabled d408-network-boot.service
```

借网应显示 `mode=tunnel`、公网走 `wg-d408`；退网应显示 `mode=direct`、公网经 `10.20.32.1 dev enp4s0`、启动服务 `disabled`。检查命令在服务 disabled 时返回非零属于正常语义。公网是否可用还取决于校园网认证。

以上流程保留 `c201-5080-wg → d408` 的 SSH 跳板访问，不关闭 5080 的管理 WireGuard，也不删除 SSH 的 ProxyJump。

退网成功后，d408 自己登录校园网：

```bash
python3 /home/d408/campus-login.py 202631030049 --service 0
```

按提示输入密码，`0` 表示校园网。若要先注销 d408 自己的校园网再借网，在仍走物理网络时执行以下命令，确认离线后再执行 `enable`：

```bash
python3 /home/d408/.local/share/sna-campus/netlogin.py logout
python3 /home/d408/.local/share/sna-campus/netlogin.py current-status --json
```

## Titan / Windows 桌面面板

先打开 Titan 桌面的“服务器网络助手”，确保后台运行。在 Titan 的 PowerShell 中退网并禁用自动启动：

```powershell
& 'C:\ProgramData\ServerNetworkAssist\desktop\0.3.1\python.exe' 'C:\ProgramData\ServerNetworkAssist\campus-recovery\local_sharing.py' pause-sharing fleet-titan
```

恢复先前借网及启动配置：

```powershell
& 'C:\ProgramData\ServerNetworkAssist\desktop\0.3.1\python.exe' 'C:\ProgramData\ServerNetworkAssist\campus-recovery\local_sharing.py' restore-startup fleet-titan
```

脚本通过运行中的面板 API 执行，密码和面板令牌不会写进命令行。操作与 UI 共用锁与恢复记录。恢复记录原本为自动启动且运行时，才会恢复到自动启动且运行；没有暂停时保存的记录则拒绝恢复。如果只是“临时断开”，在面板点击“连接”即可。

管理机可通过已配置的 `d321-titan` SSH 别名远程执行。SSH 用户须有权读取同一桌面数据目录。使用 Windows 可识别的正斜杠路径，也便于从 Bash 发起：

```text
ssh d321-titan 'C:/ProgramData/ServerNetworkAssist/desktop/0.3.1/python.exe C:/ProgramData/ServerNetworkAssist/campus-recovery/local_sharing.py pause-sharing fleet-titan'
```

远程恢复：

```text
ssh d321-titan 'C:/ProgramData/ServerNetworkAssist/desktop/0.3.1/python.exe C:/ProgramData/ServerNetworkAssist/campus-recovery/local_sharing.py restore-startup fleet-titan'
```

如先前使用独立校园网账号上网，应先在物理网络下执行 `netlogin.py logout` 并确认离线，再恢复借网。不要在已借网时执行校园网注销，以免操作到共享出口会话。

## 其他 Windows / Ubuntu 客户端

首次接入使用下方“共享网络”流程。对于仅由本机 WireGuard 服务管理、没有额外维护任务或路由的隧道，可在目标机仓库目录、安装了项目依赖的 Python 环境运行 [local_sharing.py](../scripts/local_sharing.py)。保持同一用户的桌面后台运行，并具备网络管理权限。按需要选择其中一条：

```text
python scripts/local_sharing.py pause-sharing 实际隧道名
python scripts/local_sharing.py restore-startup 实际隧道名
```

分别表示暂停并禁用自启、恢复保存的启动与运行状态。隧道名从目标机“本机隧道”读取；Ubuntu 按实际环境使用 `python3`。不同数据目录追加 `--data 绝对路径`。脚本需随仓库准备，不能假定所有机器都使用 Titan 的安装路径。

由共享方案管理的隧道应在原方案中启停，避免维护任务重新拉起服务。本机暂停不会清理旧脚本额外路由、代理或其他自动任务；d408 必须使用专用流程。

## 面板操作教程

### 首次配置多机借网

1. 打开“服务器网络助手” → “主机与凭据”，保存 SSH 凭据，添加出口机和客户端的地址、用户、端口及必要跳板。
2. 读取 SSH 指纹，通过可信渠道核对后确认并保存。目标主机需要安装辅助程序和修改网络所需的管理员 / sudo 权限。
3. 进入“共享网络”，新建方案，选择出口机和客户端，填写客户端可达的出口地址、出口 UDP 端口；隧道网段可留空自动分配。
4. 核对“仍走原网络的 CIDR”，保留 SSH 跳板、出口端点与必要管理网络路由，按实际拓扑填写。
5. “是否共享源机器代理”选择“仅共享网络，不共享源代理”，或“共享网络和源 HTTP/HTTPS 代理”。后者需填写源代理 IPv4 和端口。前者保留客户端现有代理，也不会绕过源机 VPN/TUN 的系统路由。
6. 保存方案 → “安装辅助程序”，完成主机探测 → 核对目标列表 → “启用共享”并确认。
7. 等待后端连通性检查完成，在诊断中检查错误，并验证目标客户端实际公网访问；不只看隧道已连接。

### 退网与再次借网

| 操作对象 | 退网 | 再次借网 |
| --- | --- | --- |
| 当前桌面创建的共享方案 | “共享网络”选择方案 → “断开并恢复原网络” | 选择同一方案 → “启用共享” |
| Titan 已有 fleet-titan | “本机隧道” → “停止借网并禁用自动启动” | “恢复原借网服务配置” |
| d408 旧独立部署 | 本文 d408 退出脚本 | 本文 d408 enable 命令及启动设置 |

“本机隧道”始终控制运行面板后台的机器：在自己的电脑浏览器打开 Titan 面板，操作的仍是 Titan，不能据此控制 d408。

“断开并恢复原网络”影响所选方案内的全部客户端。当前没有从运行中的多客户端方案单独移除一台的按钮。如果要求每台独立启停，首次配置时每台建立独立方案，同一出口使用不同 UDP 端口和互不重叠的网段。旧多客户端方案需先整体恢复，再拆分配置。

恢复失败或出现 `cleanup_pending` 时，保留方案并重试恢复。旧管理台创建的方案应在原管理库恢复。临时断开不禁用自启；关闭面板窗口也不会退网。

退网不等于校园网认证。满足条件时可用“本机隧道 → 恢复物理网络后登录校园网”，此入口同样只登录面板后台所在机器。后台自启、隧道开机借网和校园网自动登录是独立设置。完整边界见 [恢复原网络与校园网登录](STOP_SHARING.md)。
