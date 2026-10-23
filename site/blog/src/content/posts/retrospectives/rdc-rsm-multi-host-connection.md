---
title: "RDC 与 RSM 六台主机接入复盘：安装、配对、代理和真实验收"
description: "记录 Windows 与 Ubuntu 六台主机接入 RDC、RSM 的成功路径，分清设备配对、ChatGPT OAuth、服务身份与全盘范围，并解释官方命令之外补上的环境配置。"
pubDatetime: 2026-10-23T16:00:00Z
author: "2667741708"
project: "remote-server-manage"
articleType: "故障复盘"
testedVersion: "RSM Agent 0.6.1 / Desktop Commander 0.3.0"
verifiedAt: "2026-10-22 六台接入与读写执行；2026-10-24 整理历史证据"
featured: true
draft: false
tags: [RSM, RDC, MCP, Windows, Ubuntu, 代理, 故障复盘]
---

## 先回答：为什么复制官方命令失败，调整后却成功了

官方命令启动的是程序。要让远程控制真正可用，还需要这台机器的终端能执行它、下载和运行阶段都能联网、设备配对到正确账户、后台进程持续运行，以及 ChatGPT 获得该账户的授权。

我们没有用一条“特殊命令”替代所有官方流程。实际补上的分别是 **Windows 启动方式、每台机器自己的网络出口、后台进程的环境、独立账户配置，以及最后的读写执行验收**。这些问题发生在不同层，不能全部叫“安装失败”。

本文记录 2026 年 10 月 22 日成功接入的历史状态，按 10 月 24 日整理的证据撰写，不代表阅读时每台机器都在线。示例不包含私人邮箱、设备凭据、真实配对码或内网地址。

遇到具体报错，可直接阅读配套的[安装与代理排错手册](/projects/posts/tutorials/rdc-rsm-install-proxy-troubleshooting/)。首次使用 RSM，请以[当前安装页](https://mcp.whm12.art/host/start)显示的发行版本与命令为准。

## 目录

## RDC、RSM 和 Cloud Host 各自是什么

| 名称 | 谁提供连接入口 | 目标机器上运行什么 | 本次用途 |
| --- | --- | --- | --- |
| RDC · Remote Desktop Commander | Desktop Commander 官方服务 | 官方 remote 设备进程 | 将自己的电脑连接到官方远程工具 |
| RSM · Remote Server Manage | 自建 HTTPS MCP 服务 | 自建 RSM Agent，按需加载本地工具引擎 | 账户隔离、设备共享、别名与受控远程任务 |
| 旧 Cloud Host | 早期网关与独立 OpenAI 隧道 | Cloud 网关，再沿已授权管理路线访问主机 | 另一条历史管理入口；不能冒充 RSM 的验收结果 |

RSM 使用 Desktop Commander 的本地工具引擎，不等于 RSM 账户自动登录了 RDC，也不等于两者共享远程凭据。引擎负责本机文件和命令能力，RSM 自己负责设备账户、任务和授权。

这里至少有三种身份：ChatGPT 账户、RDC/RSM 服务账户、Agent 的操作系统账户。显示名相同，也不能证明它们是同一个登录身份。

## 两条网络链路，解释“浏览器能开为什么还失败”

```text
RDC：
ChatGPT ──授权调用──→ RDC 官方服务 ←──设备主动联网── 目标电脑的 remote 进程

RSM：
ChatGPT ── HTTPS / OAuth ──→ mcp.whm12.art
Agent   ── 出站 HTTPS    ──→ mcp.whm12.art
域名入口 ── Cloudflare 边缘 ── 已连接的 Tunnel ── Cloud 上的 RSM 应用
```

上图 RSM 公网入口的实际请求方向是：**ChatGPT → Cloudflare 边缘 → 已建立的 Tunnel → Cloud 上的 RSM 应用**。Cloud 的 `cloudflared` 主动向边缘建立连接，设备 Agent 再主动访问这个 HTTPS 地址。

你的浏览器、npm、Node 进程、Windows 计划任务、Linux systemd 服务，以及 OpenAI 的服务端请求，不一定使用同一个出口。只证明浏览器能打开登录页，不能证明其他链路也通。

给目标电脑设置出站代理，解决的是它访问外部服务的问题；让 ChatGPT 访问自建 MCP，则还需要服务端入口、HTTPS 和授权发现正确。两件事分别验证。

## 六台机器实际成功到什么程度

以下为本次历史验收的运行身份，RDC 与 RSM 一致。“全盘范围”仍受这个身份的系统权限约束。

| 主机别名 | 系统与接入方式 | 当时有效的网络处理 | 实际身份 / 文件范围 |
| --- | --- | --- | --- |
| Cloud | Ubuntu；已有 RDC，RSM 系统服务 | 保留可用出口；RSM 公网入口由独立 Cloudflare Tunnel 提供 | root；`/` |
| d408 | Ubuntu；独立 RDC 配置、RSM 系统服务 | 本机代理可用；也出现过间歇断网，恢复后再验收 | root；`/` |
| 本机 / Titan | Windows；独立 RDC 配置、RSM 后台任务 | 显式配置该机现有回环代理；修复任务的脚本启动策略 | SYSTEM；C、D、E、F |
| hmw20 / whm-save | Windows；独立配置和后台任务 | 显式使用该机现有回环代理；管理访问改用已验证的热点路线 | SYSTEM；C、D |
| c201-5080 | Ubuntu；普通用户服务 | 直连证书链验证失败，经 d408 的可用代理返回 200；保留校验 | `c201`；`/`，受用户权限约束 |
| c201-4090 | Ubuntu；普通用户服务，新完成 RSM 配对 | 当时直连和指定代理均返回 200；使用已验证路线 | `a`；`/`，受用户权限约束 |

**两台 c201 没有验收为 root。** `/` 表示允许从根目录寻址，不会让普通用户跨过系统 ACL、所有者权限或 sudo。LXC 当时所有已知管理路线均不可达，未安装、未计入成功的六台。

管理机为了远程部署使用过 SSH、跳板和已有 WireGuard 管理网。这些是运维访问路径，普通用户安装 Agent 并主动出站连接 RSM，不需要照抄我们的内网拓扑。

### Cloud：保留原网关，单独建立 RSM 入口

旧 Cloud Host 的 OpenAI 隧道与 RSM 的 Cloudflare Tunnel 是两个独立服务。RSM 使用 `https://mcp.whm12.art/host/mcp`，登录页是 `/host/login`，登录页本身不是 MCP 工具端点。

保留原有所有者和设备身份后，通过正常设备共享把 Cloud 授权给目标 RSM 账户。随后在 ChatGPT 重新完成该账户的 OAuth；没有复制另一个用户的 Token。

### d408：服务在线，不保证每次请求都成功

这台机器完成了 root 身份下的命令执行和专用标记文件读写。一次 RSM 写入因网络中断进入 `indeterminate`，即结果不确定，不能据此断言“肯定没执行”。

当时先沿已有管理通道确认目标标记文件不存在，再确认 HTTPS 恢复，然后仅对这个限定的验收文件提交一次新任务并读回。原失败记录保留，没有盲目重放业务命令。

### 本机与 hmw20：把桌面环境显式交给后台

我们给后台任务设置了自己的工作目录、Node/引擎路径、代理环境和独立配置目录。本机还修复了 PowerShell 任务没有指定进程级执行策略的问题；浏览器能联网不会自动替任务配置这些内容。

经用户明确授权，这两台整机管理实例运行于 SYSTEM。这个身份权限很高，是本次特定部署的选择；普通开发者应先以自己的系统用户运行，按需要再提升权限。

### c201-5080：修的是有效出口，未关闭证书检查

同一目标 HTTPS 地址，直连得到证书链错误，使用已验证的代理得到 HTTP 200。另一个旧代理地址拒绝连接，还有一个地址超时，因此不能只看“曾经配置过 proxy”就认为它仍有效。

保留可用代理后 Agent 恢复连接。证据只支持不同路径表现不同，不能单凭这一结果判断具体中间设备或责任方。

### c201-4090：沿普通用户权限完成新设备接入

准备运行时与完整工具引擎后，生成新配对请求，由目标用户在正常网页中确认。配置写入该用户的私密目录后启动 Agent，再核对账户内的设备列表和实际任务。

这台机器不是把另一台设备的 `device.json` 复制过来。每台设备有自己的身份和凭据；目录名、别名和凭据是不同的东西。

## RDC 成功路径：官方命令之后还要做什么

### 1. 在要被控制的机器启动设备进程

官方入口为：

```bash
npx @wonderwhy-er/desktop-commander@latest remote
```

Windows PowerShell 若拦截 `npx.ps1`，使用随 Node/npm 提供的 CMD 入口：

```powershell
npx.cmd @wonderwhy-er/desktop-commander@latest remote
```

不需要自己创建 `npx.ps1`。若是下载或联网错误，接着检查[下载与运行阶段的代理区别](/projects/posts/tutorials/rdc-rsm-install-proxy-troubleshooting/#下载和运行是两个联网阶段)。本次验证的引擎为 0.3.0；`@latest` 会变化，历史结论不自动覆盖未来版本。

### 2. 在验证页面核对账户和设备码

终端会提供一次性代码及验证链接。在浏览器确认当前登录的是预期 RDC 账户，再核对代码并完成 `Verify device`。如果没有自动跳转，手动打开终端给出的完整链接，不要复制旧截图里的过期码。

这次出现过后台旧进程与另一账户共用本地配置、会话被替换的情况。我们为第二个账户建立独立配置与进程，保留原账户，而不是覆盖它的凭据。

### 3. 在 ChatGPT 连接 RDC，并保持设备进程运行

设备配对把电脑放进 RDC 账户；ChatGPT 的 OAuth 则允许 ChatGPT 使用该服务账户的能力。官方说明明确要求设备 Agent 保持运行，关闭终端会失去访问。

需要常驻时，再配置系统服务或计划任务，并从实际启动身份核对环境。我们的管理部署使用了已核对版本的本地 Node 和引擎入口，避免每次重启都临时下载 `@latest`；这是减少运行期依赖，不是跳过授权。

## RSM 成功路径：设备配对和 ChatGPT 授权分别完成

### 1. 从 RSM 站点获取匹配版本的 Agent

Windows 下载 [RSM ZIP](https://mcp.whm12.art/host/downloads/rsm-windows-x64.zip) 后先完整解压，再在目录中运行：

```powershell
.\rsm.cmd --help
.\rsm.cmd auth login
```

Linux 便携包在解压目录运行：

```bash
python3 -S agent_runner.py auth login
```

这是本次 0.6.1 路径。该版本可能先询问服务器地址、名称、目录和权限，出现 `RSM HTTPS server [...]` 是等待输入，不代表失败。先完成这些问题才会生成设备码。后续新版的零提问 `connect` 流程不能当作旧版已经具备的功能；以发行页和 `--help` 为准。

新机器使用自己的默认配置；已有后台实例必须明确使用原来的 `--config`。完整引擎如未安装，可在原配置下先运行 `setup-engine`，停止旧进程后再启动；它增加工具能力，不自动扩大设备授权。

### 2. 在 RSM 配对页核对账户、权限与路径

登录自己的 RSM 账户，输入终端代码，在页面检查目标电脑与读、写、执行权限。RSM 密码、ChatGPT 密码、Google 密码不互通；使用第三方邮箱作为用户名不等于接入该邮箱的登录系统。

本次通过正常配对/共享页面给同一 RSM 账户接入六台主机，保留原五台的所有者和身份，新机器单独配对。多个账户共享一台设备，应走共享授权，避免反复创建相同物理机的重复记录。

### 3. 保持 Agent 在线，再连接 ChatGPT

在 RSM 账户中先确认设备在线；之后在 ChatGPT 的插件或连接器设置中，使用站点提供的 RSM MCP 地址并完成 OAuth。与网页授权有关的地址不能只替换域名，`issuer`、资源地址和回调必须与服务配置匹配。

这次批准的是 `devices:read`、`devices:exec`、`devices:manage`、`files:read`、`files:write` 五项范围。它们是明确授权的结果，不是任何连接器都应默认申请的权限。

### 4. 验证当前连接实际属于谁

早期界面存在几条相同显示名的 RSM 连接，其中两条实际属于旧账户。我们保留旧连接，完成正确账户的新 OAuth，并设置能辨认的连接昵称。

真正判断身份应调用 `who_am_i`，再调用 `device_list`。仅看“Julia 的 RSM”这类标题或 `Primary` 文案，不能证明后台用户或当前请求使用的连接。

## 最后验收：在线标记只是第一步

每台至少检查三件事：执行 `whoami`（Linux 可用 `id`）并核对 `hostname`，写入一个独立测试文件，再读回核对。先指定设备别名及 ID，确认路径在授权范围内，再做写入；不要让验收命令操作业务文件。

```text
请使用 RSM 的指定账户连接。
先调用 who_am_i 和 device_list，核对目标设备。
仅在我选定的设备和测试目录操作：
1. 执行 whoami/id 与 hostname，报告系统身份；
2. 新建一个不覆盖现有文件的 rsm-acceptance-日期.txt；
3. 写入固定测试文字并读回，逐项报告结果与任务 ID。
```

本次 **RDC 六台均完成命令、写入、读回**。RSM 则由正确的真实 ChatGPT OAuth 连接对六台提交三项检查，共 **18 项有效检查通过**，并用实际任务记录复核。维护者 SSH 或后台集成测试另行记录，不拿它们替代 ChatGPT 连接验收。

## 实际权限由多层共同决定

```text
ChatGPT 已授予的 OAuth 范围
    ∩ RSM 账户对该设备的授权
    ∩ Agent 本地的操作许可和文件目录
    ∩ Agent 操作系统账户能够做的事
```

这是理解“为什么目录已经全盘却仍写不进去”的关键。shell 执行权限也不能被目录白名单当作完整沙箱；授予命令执行，意味着允许该系统账户启动程序。

整机控制服务与后来建立的 CI Runner 也不同：前者按用户明确授权管理服务器，后者使用专用普通账户和隔离环境测试可信代码。不能为了让测试方便，把 CI 一并改成生产 root/SYSTEM。

## 仍然不能保证什么

一次读写执行成功，不等于长期网络稳定；d408 的间歇失败、Cloudflare 的短暂断线都有保留记录。依赖另一台电脑的代理时，那台电脑休眠、退出登录或关闭代理，也会影响后台连接。

这篇文章是成功连接的复盘，不是对尚未发布的 RSM 新版进行上线声明。macOS、原生 ARM、交互安装等独立发布验收，也不能由这六台主机的连接记录替代。

## 继续阅读与资料

- [安装与代理排错手册：按报错所在层处理](/projects/posts/tutorials/rdc-rsm-install-proxy-troubleshooting/)
- [借网与退网操作指南](/projects/posts/tutorials/borrow-and-restore-network/)
- [RSM 当前安装与连接说明](https://mcp.whm12.art/host/start)
- [RDC 官方仓库与快速开始](https://github.com/desktop-commander/remote-desktop-commander)
- [OpenAI：MCP OAuth 与授权发现](https://developers.openai.com/plugins/build/auth)
- [OpenAI：Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

本地整理依据为 2026-10-22 的逐机接入、Cloudflare 1033、六台设备最终报告及实际 OAuth 任务验收；只公开结论与可复用方法，不上传原始账户截图、Token、验证码或私密配置。
