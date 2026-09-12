---
title: "快速疑问：为什么能 SSH 到 WG 服务器，却不能直接打开 Cloud 面板？"
description: "用一条 Cloud → WireGuard → 5080 → d408 的真实跳板链，分清本机路由、SSH ProxyJump 和远程网络管理面板的访问方式。"
pubDatetime: 2026-09-12T06:30:00Z
author: "2667741708"
project: "server-network-assist"
articleType: "快速疑问"
testedVersion: "以当前仓库 main 分支为准"
verifiedAt: "2026-09-12"
draft: false
tags: [快速疑问, SSH, WireGuard, 网络诊断, Windows]
---

这是网络管理里最容易混淆的一类问题：本机明明没有 WireGuard 网卡，却可以执行 `ssh c201-5080-wg`，甚至还能继续登录 d408；但浏览器直接打开 `http://10.201.250.1:9180` 却超时。

关键在于：**SSH 的跳板链不是本机 IP 路由。** SSH 可以先连接一台本机可达的服务器，再让那台服务器代替本机访问下一跳。

## 先给结论

本次检查得到的状态是：

| 问题                                   | 结论                                          |
| -------------------------------------- | --------------------------------------------- |
| Cloud 的 WireGuard Hub 是否正常        | 正常，Cloud 的 `wg0` 使用 `10.201.250.1/24`   |
| Windows 本机是否是 WireGuard Peer      | 不是；本机没有 WireGuard 程序、网卡或对应服务 |
| 本机是否有到 `10.201.250.1` 的专用路由 | 没有；目标会落到本机 WLAN 默认网关            |
| Cloud 面板是否正常                     | 正常，Cloud 本机可以访问 `10.201.250.1:9180`  |
| 本机能否直接访问 `10.201.250.1:9180`   | 不能，直接 TCP 探测失败                       |

所以“Cloud 组网正常”和“本机没有加入组网”可以同时成立。

## 为什么 `ssh c201-5080-wg` 可以成功

本机 SSH 配置中的核心关系是：

```text
Host c201-5080-wg
    HostName 10.201.250.20
    ProxyJump cloud

Host cloud
    HostName <Cloud 公网 SSH 地址>
```

OpenSSH 实际建立的是下面这条连接：

```text
本机
  └─ WLAN → Cloud 公网 SSH
                └─ Cloud wg0 → 10.201.250.20:22
```

本机并没有把 `10.201.250.20` 当作自己的直连目标。`ProxyJump` 会让 Cloud 打开到 `10.201.250.20:22` 的 TCP 通道，再把 SSH 数据流转发回来。

终端中出现：

```text
Last login: ... from 10.201.250.1
```

说明 5080 看到的 SSH 来源是 Cloud 的 WireGuard 地址 `10.201.250.1`，不是 Windows 本机地址。

## 为什么连接 d408 还要再经过 5080

本机的 `d408` 别名继续写了一层跳板：

```text
Host d408
    HostName 10.20.32.14
    ProxyJump c201-5080-wg
```

因此完整链路是：

```text
Windows 本机
  → Cloud 公网 SSH
  → Cloud WireGuard
  → c201-5080
  → 校园网地址 10.20.32.14
  → d408
```

这也是为什么 `ssh d408` 能成功，而本机的 `Get-NetRoute` 里不需要出现到 `10.20.32.14` 的路由。

需要注意，`ssh d408-lan` 是另一条路径：它不使用跳板，直接从本机校园网连接 d408。

## 网络管理面板当前检查结果

2026-09-12 对本机面板的主导航进行了逐页检查：

| 页面       | 结果     | 说明                                           |
| ---------- | -------- | ---------------------------------------------- |
| 总览       | 正常     | 能显示主机数量、可达状态和需关注项             |
| 主机       | 正常     | 能显示 6 台主机、地址和跳板路径                |
| 网络借助   | 正常     | “探测全部主机”实测完成                         |
| 终端       | 正常     | 选择主机后才能连接，未选择时按钮禁用是预期行为 |
| Codex 对话 | 页面正常 | Cloud 当前未检测到 Codex CLI                   |
| 浏览器     | 页面正常 | Cloud 当前未检测到 Chrome/Edge                 |
| 安全       | 正常     | 密码、恢复密钥和登录设备入口可显示             |
| 审计       | 正常     | 能显示登录、探测和会话记录                     |

当前唯一明确的主机级异常是 `d321-titan` 探测返回 `ChannelOpenError`。这不是面板导航故障，而是该主机的 SSH 通道没有成功打开。

修改密码、删除、撤销会话、启用借网和退出登录等按钮会改变状态，本次只检查页面和无副作用的探测按钮，没有执行这些操作。

## 在另一台电脑上使用现有面板

### 方案一：给另一台电脑配置独立 WireGuard Peer

为新电脑生成独立的 WireGuard 密钥和地址，例如新的 `10.201.250.x/32`，把新电脑的公钥登记到 Cloud，再在新电脑导入配置。

连接成功后，直接访问：

```text
http://10.201.250.1:9180
```

每台电脑都应使用自己的 Peer。不要复用手机配置，也不要复制另一台电脑的私钥。

### 方案二：不安装 WireGuard，只通过 Cloud 建立 SSH 转发

另一台电脑只要能使用 `ssh cloud`，就可以建立本地端口转发：

```powershell
ssh -N -L 19180:10.201.250.1:9180 cloud
```

然后在浏览器访问：

```text
http://127.0.0.1:19180
```

这条路径是：

```text
另一台电脑 → Cloud SSH → Cloud 的 10.201.250.1:9180
```

这种方式不要求另一台电脑拥有 WireGuard 路由，也不需要把面板端口暴露到公网。

## 如果要在另一台电脑安装一套面板

项目源码入口是 [server-network-assist GitHub 仓库](https://github.com/2667741708/server-network-assist)。Windows 管理节点可以按下面顺序安装：

```powershell
git clone https://github.com/2667741708/server-network-assist.git
cd server-network-assist
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\server-network-assist.exe init --data .\data
.\.venv\Scripts\server-network-assist.exe serve --data .\data --bind 127.0.0.1 --port 9180
```

随后打开：

```text
http://127.0.0.1:9180
```

首次登录信息在 `data/initial-login.json`。登录后应安全保存恢复密钥，再删除这个一次性交付文件。

安装后进入“主机与凭据”，逐台添加 Cloud、跳板机和目标服务器，核对 SSH 主机指纹，再到“网络借助”执行探测。不要把 `cloud.pem`、其他私钥、恢复密钥或整个数据目录提交到 GitHub。

如果需要独立桌面窗口，在源码目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install ".[desktop]"
.\.venv\Scripts\server-network-assist-desktop.exe
```

完整安装与离线桌面包说明见[安装与升级](https://github.com/2667741708/server-network-assist/blob/main/docs/INSTALL.md)、[使用手册](https://github.com/2667741708/server-network-assist/blob/main/docs/USER_GUIDE.md)和[桌面面板指南](https://github.com/2667741708/server-network-assist/blob/main/docs/DESKTOP.md)。旧版 `v0.1.0` 发布包不包含当前多机 Windows 和桌面能力，使用前应以当前仓库和文档为准。

## 最后记住三件事

1. `ProxyJump` 成功，只能证明跳板链成功，不能证明本机已经加入 WireGuard。
2. 直接访问 `10.201.250.1:9180` 需要本机拥有 WireGuard 路由，或改用 SSH 端口转发。
3. 管理面板不要直接暴露到公网；优先使用 WireGuard、Tailscale、SSH 转发，正式部署再配置 HTTPS。

继续阅读：[网络共享与源代理有什么区别](/projects/posts/explanations/network-and-proxy/)、[借网与恢复原网络的实操流程](/projects/posts/tutorials/borrow-and-restore-network/)、[server-network-assist 项目页](/projects/project/server-network-assist/)。
