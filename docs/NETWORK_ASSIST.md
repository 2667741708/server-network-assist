# 网络借助：设计、启用与回退

## 数据路径

客户端建立 WireGuard 隧道到出口机。客户端公网路由使用两个 `/1` 前缀覆盖默认路由，避免直接删除原默认路由；出口机开启 IPv4 转发，并使用带唯一注释的 iptables 规则做 MASQUERADE。

管理台不会接收 WireGuard 私钥。每台主机上的 root helper 本地生成私钥，只把公钥返回给管理台。

## 启用事务

1. 确认出口机当前通过公网探测。
2. 确认全部节点已安装 helper。
3. 每台主机本地生成密钥并返回公钥。
4. 下发受校验的 WireGuard 配置。
5. 先启用出口机，再逐台启用客户端。
6. 客户端启用时启动 120 秒 failsafe。
7. 管理台重新验证客户端 SSH 与公网；成功后取消 failsafe，失败则立即断开。

## 控制面保活

客户端切换默认公网前，会为以下目标保留原路由：

- 出口 endpoint
- 当前客户端的 SSH 地址
- 跳板链上的每个地址
- 用户在高级配置中填写的额外 CIDR

这里的目标只应用于 IPv4。域名 endpoint 会在切换前解析，产生的临时路由在断开时删除。

## 维护与自动断开

启用维护后，systemd timer 每 60 秒检查一次接口与隧道公网。连续 3 次失败会尝试重建接口；连续 6 次失败会停用方案并删除临时路由。重启后 timer 会根据保存的 desired 状态恢复检查。

## 手工安装 helper

在每台参与借网的主机上，以该主机将供面板使用的 SSH 账号执行：

```bash
sudo install -o root -g root -m 0755 src/server_network_assist/network_assist_helper.py /usr/local/sbin/server-network-assist-helper
sudo /usr/local/sbin/server-network-assist-helper bootstrap
```

`bootstrap` 检查 `wg`、`wg-quick`、`ip`、`iptables` 和 `systemctl`，安装维护 unit，并为当前 `SUDO_USER` 写入只允许调用该 helper 的 sudoers 条目。请检查 `/etc/sudoers.d/server-network-assist-*` 是否只对应专用运维账号。

面板里的“安装辅助程序”仅适用于已经允许该 SSH 账号执行非交互 `sudo install` 的环境；更严格的环境应采用上述手工安装。

## 紧急回退

如果面板不可用，在客户端和出口机分别执行：

```bash
sudo /usr/local/sbin/server-network-assist-helper status
sudo /usr/local/sbin/server-network-assist-helper disable PROFILE_ID
```

然后用 `ip route`、`wg show` 和 `systemctl list-timers 'server-network-assist-*'` 验证。不要直接删除状态目录；helper 需要其中的临时路由清单来完成回退。
