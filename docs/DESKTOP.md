# 桌面面板

桌面 UI 采用 Framework7 9.1.3 的现成 iOS 组件，包含本地提供的样式、默认配色与 MIT 授权。外观按钮切换深浅模式并记住选择；长主机名、隧道名和代理地址自动换行。来源与复现步骤见 [UI 来源说明](UI_SOURCES.md)。

已有 `0.2.0` Windows 安装可使用 `scripts/prepare_desktop_ui_update.py` 准备只含桌面 UI 与静态资源路由的更新包，再在目标机器以管理员运行 `update_desktop_ui.ps1 -Source <暂存目录> -ExpectedHostname <已核对的主机名>`。更新前备份旧文件，只重启桌面面板任务，保留 WireGuard 连接；完成后重新打开原桌面快捷方式。完整程序升级仍按下面的安装方法操作。

v0.2.0 提供一个面向本机网络的桌面窗口。它直接读取 Windows WireGuard 服务或 Ubuntu 的 `wg-quick@` 服务，适合已经有工作隧道、需要日常查看状态和连接开关的电脑。多主机配置、SSH 管理和创建新借网方案仍使用原有服务器管理台。

## 使用

- Windows 安装后，双击桌面上的 **服务器网络助手**。
- 查看直连公网、系统应用联网、隧道收发流量、最近握手、出口地址和原始默认网关。
- 点击“断开连接”会提示确认；断开后可以再次点击“连接”。只控制选中的已有 WireGuard 服务，不删除配置或私钥。
- “关闭手动代理”先备份当前用户配置，再关闭 Windows 手动代理。不删除 PAC，不修改代理软件的配置；代理软件重新启用系统代理时，应同时检查该软件的开关。
- 每 30 秒自动更新，也可手动刷新。流量为隧道当前运行周期的累计值，不是实时速率。
- 关闭面板窗口不会断开隧道。重复打开快捷方式会复用本机后台；退出 Windows 登录后后台结束，下次双击快捷方式会重新启动。

窗口采用 Edge/Chrome 应用模式，有独立标题与应用图标，不需要常驻终端；它不是完整的 Clash 客户端，没有订阅、规则编辑或系统托盘菜单。没有兼容浏览器时，使用系统默认浏览器打开。

## Windows 离线安装

准备 Windows 官方 embeddable Python 3.11+ 解压目录（包含 `python.exe`、`pythonw.exe`、`python3xx.zip`）和本项目构建的 wheel。在管理员 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_desktop.ps1 -RuntimeSource C:\Tools\Python313 -Wheel .\dist\server_network_assist-0.2.0-py3-none-any.whl
```

安装程序只复制解释器文件及标准库，不复制 Python 用户凭据或已有第三方包。桌面面板仅使用 Python 标准库，因此安装和启动无需从网络下载依赖。已有管理台的依赖安装方式保持不变。

安装位置：`C:\ProgramData\ServerNetworkAssist\desktop\0.2.0`。程序文件只允许 Administrators/SYSTEM 修改，普通用户仅可读取和运行。使用独立 Python 路径配置，不加载用户的 Python 搜索路径。

后台任务 `ServerNetworkAssist-Desktop` 使用安装时当前用户的交互登录令牌及管理员权限，按需启动。任务不保存登录密码、不设置定时唤醒，也不会开机自动修改网络。启动器和窗口以普通桌面会话运行；连接开关通过受限本机 API 调用后台。

状态、代理备份和浏览器专用配置保存在当前用户的 Local AppData 下的 `ServerNetworkAssist` 文件夹。权限显式授予当前用户 SID、Administrators 和 SYSTEM，使普通窗口能够访问管理员后台生成的状态，同时不向其他用户开放令牌。

## 源码或普通 Python 环境

```powershell
python -m pip install .
server-network-assist-desktop
```

普通方式会按当前用户权限运行。没有管理员权限时，Windows 隧道连接开关不可用；需要控制网络时使用上面的管理员安装程序。Ubuntu/Linux 也可运行该命令，状态读取与连接开关取决于当前账户权限和 `ip`、`wg`、`systemctl` 可用性。

## 升级与移除

使用新 wheel 重新运行安装程序，并指定对应 `-Version`。用户数据和 WireGuard 配置保留。安装器会停止自己的后台任务、替换程序并重新启动；同版本重装跳过内容相同的解释器文件，避免无意义地覆盖正在使用的 DLL。升级前关闭面板窗口。

移除桌面面板时，可以删除桌面快捷方式，在 Windows 任务计划程序中停止并删除 `ServerNetworkAssist-Desktop`，再移除桌面程序安装目录。不要删除上级 `ServerNetworkAssist` 中其他辅助程序的状态或备份；WireGuard 隧道服务也无需移除。

## 安全与验证

HTTP 后台只监听 `127.0.0.1` 的随机端口。状态和操作 API 都需要随机令牌；写操作额外核对 Origin。启动 URL 的令牌片段会立即从地址中清除，仅留在当前窗口会话。连接操作只接受实际存在的 WireGuard 服务，不能传入任意系统服务或 shell 命令。

回归检查包括桌面 API 认证、跨来源操作拒绝、未知服务拒绝、单后台复用、桌面/手机布局，以及模拟隧道的连接、断开和取消操作。Windows 实机验证包含受限桌面会话打开窗口、管理员后台状态读取、当前活动隧道的幂等连接；不会为界面测试主动断开工作隧道。
