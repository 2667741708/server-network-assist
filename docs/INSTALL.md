# 安装与升级

## 支持范围

- 管理节点：Windows 或 Linux，Python 3.11+
- 被管理主机：OpenSSH Server
- 出口机及 Linux 客户端：systemd、WireGuard、`iproute2`、`iptables`、`sudo`
- Windows 客户端：原生 WireGuard、PowerShell 5.1+、已提权管理员 SSH；无需 WSL，见 [Windows 安装](WINDOWS.md)
- 当前仅管理 IPv4 公网借助；暂不自动修改 nftables-only、防火墙管理器或 NetworkManager 配置

## 普通用户安装

从 GitHub Release 安装固定版本：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install https://github.com/2667741708/server-network-assist/releases/download/v0.1.0/server_network_assist-0.1.0-py3-none-any.whl
server-network-assist init --data ./data
server-network-assist serve --data ./data
```

从源码安装：

```bash
git clone https://github.com/2667741708/server-network-assist.git
cd server-network-assist
python3 -m venv .venv
. .venv/bin/activate
pip install .
server-network-assist init --data ./data
server-network-assist serve --data ./data
```

初始化会生成：

- `credentials.json`：管理员密码和恢复密钥的单向摘要
- `master.key`：凭据库加密主密钥，必须离线备份且不可提交 Git
- `initial-login.json`：一次性初始账号交付文件
- `panel.sqlite3`、`console.sqlite3`：会话、审计、主机、凭据和借网方案

## systemd 安装

仓库提供 `deploy/server-network-assist.service` 作为模板。建议创建专用系统用户，将虚拟环境安装在 `/opt/server-network-assist/venv`，数据放在 `/var/lib/server-network-assist`，服务只监听 `127.0.0.1:9180`。

```bash
sudo useradd --system --home /var/lib/server-network-assist --shell /usr/sbin/nologin server-network-assist
sudo install -d -o server-network-assist -g server-network-assist -m 0700 /var/lib/server-network-assist
sudo install -d -o root -g root -m 0755 /opt/server-network-assist
sudo python3 -m venv /opt/server-network-assist/venv
sudo /opt/server-network-assist/venv/bin/pip install .
sudo -u server-network-assist /opt/server-network-assist/venv/bin/server-network-assist init --data /var/lib/server-network-assist
sudo install -m 0644 deploy/server-network-assist.service /etc/systemd/system/server-network-assist.service
sudo systemctl daemon-reload
sudo systemctl enable --now server-network-assist
```

## HTTPS

反向代理必须保持 WebSocket 升级头，并把浏览器看到的精确来源写入 `/etc/server-network-assist.env`：

```ini
PANEL_ORIGIN=https://network.example.com
```

不要把管理台直接监听在公网地址。推荐先通过 WireGuard/Tailscale/内网访问，再叠加 HTTPS。

若与现有站点共用域名，可以让服务继续监听 `127.0.0.1:9180`，由反向代理把 `/network-assist/` 转发给它，并在环境文件中增加：

```ini
PANEL_ORIGIN=https://example.com
PANEL_BASE_PATH=/network-assist
PANEL_ALLOWED_ORIGINS=http://10.201.250.1:9180
```

浏览器入口为 `https://example.com/network-assist/`。路径前缀不要带结尾斜杠，反向代理需要在转发前移除该前缀，并保留 WebSocket 升级头。不要用公网 IP 上的明文 HTTP 传输管理员密码、SSH 私钥或 Codex 对话。

`PANEL_ALLOWED_ORIGINS` 可用英文逗号列出可信的 WireGuard/Tailscale/LAN 入口。HTTP 私网入口使用密码登录，通行密钥仍只对主 `PANEL_ORIGIN` 开放。推荐让 Caddy/Nginx 监听私网地址并转发至 `127.0.0.1:9180`，不要让应用监听所有公网网卡。

## 升级

1. 备份整个数据目录，尤其是 `master.key` 和两个 SQLite 文件。
2. 停止服务。
3. 在新虚拟环境安装新版本，先阅读 CHANGELOG。
4. 启动后检查 `/api/session`、登录、主机读取和审计日志。
5. 在测试客户端验证借网启用与断开，再对生产主机操作。

降级前同样必须备份。数据库尚未承诺跨大版本向后兼容。
