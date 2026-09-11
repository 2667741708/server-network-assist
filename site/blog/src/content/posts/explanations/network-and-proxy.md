---
title: "共享网络时，要不要同时共享源机器的代理？"
description: "分清 WireGuard 网络出口与 HTTP/HTTPS 应用代理，再决定 Windows、Ubuntu 客户端采用哪一种共享方式。"
pubDatetime: 2026-09-10T11:00:00Z
author: "2667741708"
project: "server-network-assist"
articleType: "原理解说"
draft: false
tags: [网络共享, Windows, Ubuntu]
---

让另一台机器提供网络出口，与让应用使用它的 HTTP/HTTPS 代理，是两个可以分别配置的选择。

网络助手通过 WireGuard 建立网络路径，并提供可选的源代理中继。这篇文章解释选择的含义；具体安装条件和验证边界以[跨平台共享指南](https://github.com/2667741708/server-network-assist/blob/a830b2c/docs/SHARING.md)为准，不把功能实现等同于所有系统组合已经实测。

## 先确定需要共享哪一层

| 需求 | 配置选择 | 对客户端的影响 |
| --- | --- | --- |
| 使用源机器提供的网络出口 | 共享网络 | 通过 WireGuard 使用出口；客户端原代理设置保留 |
| 应用还需要源机器的 HTTP/HTTPS 代理 | 同时共享源代理 | 通过隧道中继访问指定代理，并按系统应用相应设置 |

“不共享源代理”不等于强制绕过源机器的 VPN 或 TUN。如果源机器的系统路由已被 VPN 接管，下游网络仍可能经过该路由。它也不会自动关闭客户端原有代理。

## 系统不同，设置生效的范围也不同

Windows 的系统代理设置作用于对应用户。Ubuntu 的相关配置涉及新登录 shell、APT 和对应用户已登录的 GNOME 会话。应用也可能拥有自己的代理设置，因此应在目标应用里单独验证。

这个功能不会复制源机器的订阅、PAC 或认证信息。选择共享前，核对代理实际监听的 IPv4 地址、端口及访问条件。

## 操作后，如何确认选择符合预期

先确认 WireGuard 路径与公网探测，再测试真正需要联网的应用。断开方案后，检查本方案修改的路由和代理是否恢复。涉及 Windows WinNAT、已有 Docker/WSL 网络等条件时，先查阅共享指南中的限制。

想排查“隧道看似正常，Windows 应用仍失败”，可以从[直连与系统应用的两项探测](/projects/posts/tutorials/windows-proxy-diagnosis/)开始。
