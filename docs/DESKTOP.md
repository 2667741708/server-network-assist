# 桌面面板

桌面 UI 采用 Framework7 9.1.3 的现成 iOS 组件，包含本地提供的样式、默认配色与 MIT 授权。外观按钮切换深浅模式并记住选择；长主机名、隧道名和代理地址自动换行。来源与复现步骤见 [UI 来源说明](UI_SOURCES.md)。

从 `0.2.0` 升级必须使用下面的完整离线包。新版桌面包含多机管理与加密依赖，旧版仅替换 UI 的脚本不适用于此次升级。

v0.3.0 使用 Framework7 官方 iOS 侧栏与菜单列表，提供连接概览、本机隧道、共享网络、主机与凭据、系统代理、诊断与恢复、后台与更新七个分区。桌面窗口能够独立添加多台主机和配置借网方案，不需要跳转服务器管理台；移动端使用可展开的同一导航菜单。

## 使用

- Windows 安装后，双击桌面上的 **服务器网络助手**。
- 查看直连公网、系统应用联网、隧道收发流量、最近握手、出口地址和原始默认网关。
- 点击“断开连接”会提示确认；断开后可以再次点击“连接”。只控制选中的已有 WireGuard 服务，不删除配置或私钥。
- “关闭手动代理”先备份当前用户配置，再关闭 Windows 手动代理。不删除 PAC，不修改代理软件的配置；代理软件重新启用系统代理时，应同时检查该软件的开关。
- 每 5 秒请求状态，也可手动刷新。速率按真实 WireGuard 计数差与时间差计算，首个样本或计数器重置时显示未知；后台保留最多 180 个采样。官方 Area Chart 展示速率历史，表格可查看完整数值。它不是整机所有网卡的流量。
- 关闭面板窗口不会断开隧道。重复打开快捷方式会复用本机后台；退出 Windows 登录后后台结束，下次双击快捷方式会重新启动。

窗口采用 Edge/Chrome 应用模式，有独立标题与应用图标，不需要常驻终端。没有兼容浏览器时，使用系统默认浏览器打开。系统托盘及状态通知取决于平台和当前图形会话；“后台与更新”展示实际能力，并提供 GitHub Release 检查、说明和下载入口。没有发布版本或检查失败时会明确提示；面板不提供 Clash 订阅或规则编辑。

## 独立配置多机网络共享

1. 在“主机与凭据”保存 SSH 密码或私钥。敏感内容加密保存在当前桌面实例的数据目录，保存成功后输入框清空。
2. 添加出口机和客户端的地址、SSH 用户、端口、凭据及可选跳板。先保存基本信息，再读取 SSH 指纹；通过服务器控制台等可信渠道核对，勾选确认并再次保存。
3. 在“共享网络”新建方案，选择一台出口机和至少一台客户端。Windows / Ubuntu 组合使用同一流程，实际可用性由探测与后端能力检查决定；Windows 出口需要 WinNAT。
4. 选择仅共享网络，或同时共享源机器的 HTTP / 混合代理端口（支持 HTTPS CONNECT）。仅共享网络保留客户端现有代理；不会绕过源机 VPN/TUN 的系统路由。
5. 保存方案。安装辅助程序前会确认目标操作，再探测各机 SSH、DNS、公网和辅助程序状态。
6. 点击“启用共享”并确认。后端保留管理路由、启用隧道、检查客户端出口，失败时尝试回退。运行中不能直接改写方案。
7. “断开并恢复原网络”恢复方案更改的路由和代理。若出现 `cleanup_pending`，保留方案并重试恢复，不能删除未清理状态。

源代理共享作用于 SSH 账号：Windows 用户代理；Ubuntu 新登录 shell、APT 和该用户已有的 GNOME 会话。它不是在所有系统服务中强制注入代理。共享代理端口目前不支持账号密码认证。

## 代理、诊断与恢复

“系统代理”支持编辑地址、绕过列表和启用状态，每次修改前生成备份。恢复时也先备份当前配置。Windows 使用当前用户 Internet Settings；Ubuntu 使用当前用户 GNOME 会话，无 GNOME 会话时显示不支持原因，不假装已修改整个系统。关闭手动代理不删除 PAC。

“诊断与恢复”读取本机网络状态、指导条目、操作记录和多机审计，支持导出 JSON。诊断本身不会断开网络；恢复必须在代理或共享方案入口确认。日志失败不阻止主操作。导出记录可能含主机地址，请自行选择分享范围。

## Windows 离线安装

准备 Windows x64 官方 embeddable Python 3.13 解压目录（包含 `python.exe`、`pythonw.exe`、`python313.zip`）。在普通 CPython 3.13 x64 的隔离构建环境安装 `.[desktop]` 和 `packaging`，再运行 `python scripts/build_desktop_release.py --output artifacts/desktop-release-v030`。该命令生成完整的 `desktop-packages.zip` 和包含 SHA256 的 `manifest.json`，输出目录必须不存在。在目标机器的管理员 PowerShell 中运行，SHA256 从经过核对的清单读取：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_desktop.ps1 -RuntimeSource C:\Tools\Python313 -PackageArchive .\desktop-packages.zip -PackageSha256 <清单中的SHA256> -Version 0.3.0
```

v0.2 的本机状态面板可使用标准库运行；v0.3 的独立多机管理增加了 SSH、加密凭据等项目依赖，因此完整升级必须同时准备依赖，不能只替换 UI 文件。推荐在普通 Python 环境使用下面的完整安装命令；嵌入式离线安装应按安装器的依赖参数准备依赖包，不能复用旧版“无需第三方依赖”的假设。

安装位置：`C:\ProgramData\ServerNetworkAssist\desktop\0.3.0`。程序文件只允许 Administrators/SYSTEM 修改，普通用户仅可读取和运行。使用独立 Python 路径配置，不加载用户的 Python 搜索路径。

后台任务 `ServerNetworkAssist-Desktop` 使用安装时当前用户的交互登录令牌及管理员权限，登录时或按需启动。任务不保存登录密码、不设置定时唤醒，也不会开机自动修改网络。启动器和窗口以普通桌面会话运行；连接开关通过受限本机 API 调用后台。

状态、代理备份和浏览器专用配置保存在当前用户的 Local AppData 下的 `ServerNetworkAssist` 文件夹。权限显式授予当前用户 SID、Administrators 和 SYSTEM，使普通窗口能够访问管理员后台生成的状态，同时不向其他用户开放令牌。

## 源码或普通 Python 环境

```powershell
python -m pip install ".[desktop]"
server-network-assist-desktop
```

普通方式会按当前用户权限运行。没有管理员权限时，Windows 隧道连接开关不可用；需要控制网络时使用上面的管理员安装程序。Ubuntu/Linux 也可运行该命令，状态读取与连接开关取决于当前账户权限和 `ip`、`wg`、`systemctl` 可用性。

Ubuntu 图形会话中可运行 `python scripts/install_desktop_linux.py --autostart`，生成应用菜单、桌面快捷方式和登录启动入口。GNOME 托盘显示取决于系统 AppIndicator 支持；无图形会话的服务器没有原生托盘。

## 升级与移除

使用新完整离线包重新运行安装程序，并指定对应 `-Version`。用户数据和 WireGuard 配置保留。安装器先准备新目录、检查导入与版本，再切换自己的后台任务；健康检查失败时恢复旧任务。已有版本目录会拒绝覆盖，避免修改正在使用的 DLL。升级前关闭面板窗口。登录时启动后台和原生托盘，不自动启停共享方案。

移除桌面面板时，可以删除桌面快捷方式，在 Windows 任务计划程序中停止并删除 `ServerNetworkAssist-Desktop` 和 `ServerNetworkAssist-Tray`，再移除桌面程序安装目录。不要删除上级 `ServerNetworkAssist` 中其他辅助程序的状态或备份；WireGuard 隧道服务也无需移除。

## 安全与验证

HTTP 后台只监听 `127.0.0.1` 的随机端口。状态和操作 API 都需要随机令牌；写操作额外核对 Origin。启动 URL 的令牌片段会立即从地址中清除，仅留在当前窗口会话。连接操作只接受实际存在的 WireGuard 服务，不能传入任意系统服务或 shell 命令。

回归检查包括桌面 API 认证、跨来源操作拒绝、未知服务拒绝、单后台复用。`node frontend/desktop-e2e.cjs` 启动真实隔离后台并在浏览器层模拟网络变更，验证桌面/手机导航、完整主机与方案流程、指纹确认、取消操作、代理保存与恢复、诊断导出、更新、错误显示和长值可读性。截图使用测试数据，不能作为 Titan 实机联网验证的替代；UI 测试不会主动断开工作隧道。
