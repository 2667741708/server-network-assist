---
title: "借网与退网操作指南：Windows、Ubuntu 命令和面板流程"
description: "从指定客户端借网，到停止借网、禁用自启、恢复物理网络：整理 Titan 与 d408 的真实命令、面板步骤和流程图，并说明如何保留 SSH 跳板访问。"
pubDatetime: 2026-09-11T08:00:00Z
author: "2667741708"
project: "server-network-assist"
articleType: "实操教程"
testedVersion: "0.3.1"
verifiedAt: "2026-09-11"
featured: true
draft: false
tags: [Windows, Ubuntu, WireGuard, 使用教程]
---

借网是让客户端通过另一台机器访问公网；退网则恢复客户端自己的网络。**临时断开、禁用开机借网、注销校园网账号，是三个不同的操作。** 这篇文章把命令和面板入口放在一起，便于按实际部署选择。

这里使用两台已部署机器作为案例：Titan 是 Windows 原生客户端，d408 是 Ubuntu 客户端。可以从任何具有 SSH 访问权限的管理机发起操作，但命令最终在目标客户端执行。首次使用本项目的读者，请先完成“用面板首次配置”一节，不能把案例路径直接套到新机器。

> 适用范围：网络助手 v0.3.1，以及案例中已安装的配套脚本。命令依据项目代码和 d408 实际管理脚本核对；此前已完成 Titan 借网、d408 退网与校园网认证验证。本次文章发布不再次切换它们的网络，也不表示所有硬件与系统组合都已实测。

## 目录

## 先选对操作入口

| 你的部署 | 应使用的入口 | 操作范围 |
| --- | --- | --- |
| 桌面创建的共享方案 | 原桌面实例的“共享网络” | 所选方案中的全部客户端 |
| Titan 的已有隧道 | “本机隧道”或本文的桌面 API 脚本 | Titan 的指定隧道服务 |
| d408 的旧独立部署 | `d408-network` 与退出脚本 | d408 的借网路由、服务和代理 |

“本机”指**运行面板后台的机器**。即使你在自己的电脑浏览器打开 Titan 面板，“本机隧道”操作的仍是 Titan。

当前没有 `server-network-assist borrow/leave --host ...` 这样的统一命令。方案的维护任务可能重新拉起隧道，所以由共享方案管理的网络应在原方案中启停。

## 流程图：从借网到恢复原网络

![借网流程：确认目标和管理通道，检查配置及源代理选择，启用借网，验证路由与公网；失败时恢复并查看诊断。](/projects/images/borrow-network-flow.svg)

![退网流程：确认影响范围，选择方案恢复或客户端专用入口，停止借网并处理自启，验证物理路由和 SSH；通过后按需登录校园网，失败时保留恢复记录。](/projects/images/restore-network-flow.svg)

两张图均可点击放大。下面的命令与步骤是流程图的文字版本；成功标准是路由、启动状态和实际访问均符合预期。

## d408：借网与开机启动

先在已配置 SSH 别名的管理机进入目标客户端。Windows PowerShell 和 Ubuntu 终端均可执行：

```text
ssh -t d408
```

进入 d408 后，借网：

```bash
sudo /usr/local/sbin/d408-network enable
```

上一条成功后，再启用开机借网：

```bash
sudo systemctl enable d408-network-boot.service
```

该机的脚本同时配置策略路由、DNS 和管理通信规则。仅启动 `wg-quick` 不足以完成这个部署的借网流程。这个入口也不会自动开启本机 Mihomo 代理。

若要先注销客户端自己的校园网账号，应在**仍走物理网络时**执行 `netlogin.py logout`，查询确认离线后再借网。已经借网时不要注销，否则认证请求可能经共享出口操作到出口机的会话。

## d408：退网、禁用自启、保留跳板

在 d408 终端执行：

```bash
sudo bash /home/d408/stop-borrowing.sh
```

或者从管理机直接指定 d408 退网，按提示输入目标机的 sudo 密码：

```text
ssh -t d408 'sudo bash /home/d408/stop-borrowing.sh'
```

退出脚本会先备份，再调用原有恢复流程，处理借网策略路由、DNS、本机代理和防绕行规则，并停止、禁用相关开机服务。[查看退出脚本源码](https://github.com/2667741708/server-network-assist/blob/main/scripts/d408-stop-sharing.sh)。脚本校验 d408 主机名，不适用于其他机器。

**退出的是 d408 的公网借网，不是删除管理网络。** 保留 `c201-5080-wg → d408` 的 SSH 跳板访问，不关闭 5080 的管理 WireGuard，也不删除 SSH 配置中的 `ProxyJump`。

检查结果：

```bash
sudo /usr/local/sbin/d408-network status
ip -4 route get 1.1.1.1
systemctl is-enabled d408-network-boot.service
```

| 检查项 | 借网后 | 退网后 |
| --- | --- | --- |
| 模式 | `mode=tunnel` | `mode=direct` |
| 公网路由 | 走 `wg-d408` | 走物理网卡 `enp4s0` 与原网关 |
| 开机借网 | 启用后为 `enabled` | `disabled` |
| SSH 跳板 | 可访问目标客户端 | 仍可访问目标客户端 |

`is-enabled` 在服务禁用时会返回非零退出码，这不表示退网失败。公网探测失败则还需要检查校园网认证，不能只根据路由判断已经能上网。

## Titan：暂停借网与恢复原配置

先打开 Titan 桌面的“服务器网络助手”，保证后台运行。在 Titan 的 PowerShell 中，退出借网并禁用自动启动：

```powershell
& 'C:\ProgramData\ServerNetworkAssist\desktop\0.3.1\python.exe' 'C:\ProgramData\ServerNetworkAssist\campus-recovery\local_sharing.py' pause-sharing fleet-titan
```

恢复先前的启动方式和运行状态：

```powershell
& 'C:\ProgramData\ServerNetworkAssist\desktop\0.3.1\python.exe' 'C:\ProgramData\ServerNetworkAssist\campus-recovery\local_sharing.py' restore-startup fleet-titan
```

`restore-startup` 依赖上一次暂停时保存的恢复记录。原先为“自动启动且运行”时才会恢复到该状态；它不是无条件开启自启的命令。没有恢复记录时会拒绝猜测恢复。如果只是点击过“临时断开”，在面板点击“连接”即可。

从其他管理机远程退网，可使用已经配置好的 `d321-titan` SSH 别名：

```text
ssh d321-titan 'C:/ProgramData/ServerNetworkAssist/desktop/0.3.1/python.exe C:/ProgramData/ServerNetworkAssist/campus-recovery/local_sharing.py pause-sharing fleet-titan'
```

远程恢复时，将 `pause-sharing` 换成 `restore-startup`。SSH 用户需有权访问同一桌面数据目录；后续升级按实际版本修改 Python 路径。[查看本机操作脚本](https://github.com/2667741708/server-network-assist/blob/main/scripts/local_sharing.py)。

本机服务暂停只处理隧道拥有的路由。旧脚本额外添加的路由、代理或自动任务仍需对应恢复流程，不应盲目删除路由表。

## 其他客户端：命令的适用条件

如果目标机已有仅由本机服务管理的 WireGuard 隧道，没有额外维护任务或路由，并且已准备项目源码、依赖与有权限的桌面后台，可在目标机仓库目录选择执行：

```text
python scripts/local_sharing.py pause-sharing 实际隧道名
```

```text
python scripts/local_sharing.py restore-startup 实际隧道名
```

Ubuntu 按实际环境使用 `python3`。隧道名从目标机面板读取，非默认数据目录追加 `--data 绝对路径`。本机脚本不会自动新建出口配置，也不会关闭方案维护任务。新机器首次借网应使用下面的完整配置流程。

## 用面板首次配置 Windows / Ubuntu 共享

1. 打开“主机与凭据”，保存 SSH 凭据，添加出口机与客户端的地址、用户、端口和必要跳板。
2. 读取 SSH 指纹，通过可信渠道核对后确认、保存。两端都需要安装辅助程序和修改网络所需的管理员 / sudo 权限。
3. 进入“共享网络”，新建方案，选择出口机和客户端，填写客户端可达的出口地址、UDP 端口；隧道网段可留空自动分配。
4. 核对“仍走原网络的 CIDR”，按实际拓扑保留 SSH 跳板、出口端点与必要管理网络的路由。
5. 选择“仅共享网络，不共享源代理”，或“共享网络和源 HTTP/HTTPS 代理”。后者填写源代理 IPv4 和端口。前者保留客户端现有代理，不会绕过源机 VPN/TUN 的系统路由。
6. “保存方案”后“安装辅助程序”，完成主机探测，再核对目标列表并点击“启用共享”。
7. 等待后端检查完成，查看诊断，并在客户端实际打开网页或访问所需服务。

Windows 与 Ubuntu 使用同一配置流程；Windows 作为出口需要 WinNAT 等后端能力，是否可用以目标机探测结果为准。

## 用面板退网与再次借网

| 场景 | 退网入口 | 再次借网入口 |
| --- | --- | --- |
| 桌面创建的共享方案 | “共享网络”选原方案 → “断开并恢复原网络” | 同一方案 → “启用共享” |
| Titan 已有隧道 | “本机隧道” → “停止借网并禁用自动启动” | “恢复原借网服务配置” |
| d408 旧独立部署 | 本文专用退出脚本 | 本文 `enable` 及开机设置 |

**方案退网影响其中全部客户端。** v0.3.1 没有从运行中的多客户端方案单独移除一台的按钮。需要逐台独立启停时，首次配置就每台建独立方案，同一出口使用不同 UDP 端口、不重叠网段。旧多客户端方案需先整体恢复再拆分，不直接改写运行中的方案。

出现 `cleanup_pending` 或恢复失败时，保留方案与恢复记录，查看诊断后重试。其他管理台创建的方案应在原管理库恢复。关闭面板窗口不会退网，“临时断开”也不会禁用下次自启。

## 退网后，用自己的校园网账号上网

先确认公网路由回到物理网卡、借网相关任务不再运行，并核对代理状态。d408 已部署交互式登录入口：

```bash
python3 /home/d408/campus-login.py 你的校园网账号 --service 0
```

把账号占位符换成自己的账号，按提示输入密码；`0` 表示校园网。密码不应写进命令历史或公开文章。[登录包装脚本](https://github.com/2667741708/server-network-assist/blob/main/scripts/d408-campus-login.py)最终调用原有 `netlogin.py`。

v0.3.1 面板也提供“本机隧道 → 恢复物理网络后登录校园网”。它只登录面板后台所在机器，并要求管理员已接入登录脚本、路由与代理检查满足条件。登录后核对账号、运营商和实际公网访问。

**桌面后台自启、隧道开机借网、校园网自动登录是三项独立设置。** 恢复其中一项不表示其余两项也开启。

## 继续阅读

- [项目的全部文章](/projects/project/server-network-assist/)
- [网络共享与源代理有什么区别](/projects/posts/explanations/network-and-proxy/)
- [恢复范围与校园网登录限制](https://github.com/2667741708/server-network-assist/blob/main/docs/STOP_SHARING.md)
- [项目安装与使用文档](https://github.com/2667741708/server-network-assist)
