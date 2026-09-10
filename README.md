# Server Network Assist

一个面向小型实验室、工作室和家庭机房的自托管服务器网络协作台。它通过你明确添加的 SSH 主机执行连通性探测，让 Windows 或 Ubuntu/Linux 客户端经联网的 Ubuntu/Linux 出口机使用 WireGuard 访问公网；Windows 使用原生网络栈，无需 WSL。断开后恢复原有路由，继续使用校园网认证、热点或本机网络。

> 当前版本为 `0.2.0-alpha`。网络切换属于高风险运维操作，请先在可现场恢复的测试机上验证。不要把管理台直接暴露到公网。

## 功能

- SSH 主机清单、分组、跳板链和严格主机指纹锁定
- SSH、DNS、HTTPS 公网连通性分层探测
- 一台出口机向 1–32 台客户端提供可逆 WireGuard 借网
- 自定义 UDP 端口、隧道网段和控制面保留路由
- 120 秒切换确认窗口，复检失败自动回退
- systemd 定时维护；连续故障后停用隧道并恢复原路由
- Xterm.js 网页终端，支持可选 tmux 持久会话
- 本地加密凭据库、CSRF/Origin 防护、会话撤销、审计日志与 WebAuthn 通行密钥
- Angular Material 响应式界面，桌面、平板和手机均可使用
- 本机桌面面板：独立窗口、桌面快捷方式、隧道连接开关、握手与流量、Windows 系统代理诊断

## 桌面面板

安装后运行 `server-network-assist-desktop`。Windows 可通过安装程序创建“服务器网络助手”桌面快捷方式，双击即开；支持直接控制已经安装的 WireGuard 隧道。窗口关闭不会断开网络。面板所有资源本地提供，无需 WSL，也无需额外桌面运行库。

完整安装、升级及使用方式见 [桌面面板指南](docs/DESKTOP.md)。

## 快速开始

要求：Windows 或 Linux 管理节点、Python 3.11+。被管理主机需要 SSH。Ubuntu/Linux 借网节点需要 systemd、WireGuard、`iproute2` 和 `iptables`；Windows 客户端需要 WireGuard for Windows、PowerShell 5.1+ 和管理员 SSH 账号。Windows 出口机暂不支持。

Windows 原生安装、代理故障定位与回退见 [Windows 与 Ubuntu 兼容说明](docs/WINDOWS.md)。下方 `v0.1.0` 发布包是旧版；本次 Windows 支持需要从当前源码安装。

直接安装 `v0.1.0` 预发行 wheel：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install https://github.com/2667741708/server-network-assist/releases/download/v0.1.0/server_network_assist-0.1.0-py3-none-any.whl
server-network-assist init --data ./data
server-network-assist serve --data ./data --bind 127.0.0.1 --port 9180
```

或者从源码安装：

```bash
git clone https://github.com/2667741708/server-network-assist.git
cd server-network-assist
python3 -m venv .venv
. .venv/bin/activate
pip install .
server-network-assist init --data ./data
server-network-assist serve --data ./data --bind 127.0.0.1 --port 9180
```

首次账号保存在 `data/initial-login.json`，权限应保持为仅当前用户可读。登录并安全保存恢复密钥后，建议删除这个一次性交付文件。

浏览器访问 `http://127.0.0.1:9180`。生产环境请使用 Caddy、Nginx 或其他反向代理提供 HTTPS，并设置精确的 `PANEL_ORIGIN`；通行密钥只在固定 HTTPS 来源下启用。

## 使用顺序

1. 在“主机与凭据”中添加加密凭据和 SSH 主机。
2. 读取 SSH 主机公钥，在可信渠道核对 SHA-256 指纹后保存。
3. 在“网络借助”中探测全部主机，选出公网可用的出口机。
4. 在出口机和客户端安装辅助程序，创建方案并选择端口、客户端和保留路由。
5. 启用后等待客户端通过 SSH 与公网复检；失败会自动回退。
6. 点击“断开并恢复原网络”，删除借网隧道和临时路由。

完整说明见：

- [安装与升级](docs/INSTALL.md)
- [使用手册](docs/USER_GUIDE.md)
- [网络借助原理与回退](docs/NETWORK_ASSIST.md)
- [安全模型](docs/SECURITY.md)
- [架构与开发](docs/ARCHITECTURE.md)
- [English README](README_EN.md)

## 开发

```bash
cd frontend
npm ci
npm test -- --watch=false
npm run build
cd ..
python scripts/build_frontend.py
python -m unittest discover -s tests -v
```

前端使用 Angular 22、Angular Material/CDK 与 Xterm.js；后端使用 aiohttp、AsyncSSH、SQLite、cryptography 和 WebAuthn。所有生产前端资源均随 Python 包本地提供，不依赖公共 CDN。

## 安全披露与贡献

请阅读 [SECURITY.md](SECURITY.md) 和 [CONTRIBUTING.md](CONTRIBUTING.md)。不要在 Issue、截图或日志中提交真实私钥、密码、恢复密钥、Cookie、内部 IP 清单或 SSH 配置。

## 许可证

本项目采用 [MIT License](LICENSE)。第三方组件及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
