# 客户借网中继执行层

该组件运行在 Linux WireGuard 出口机，以单个 WireGuard peer 为最小授权、计量、限速和撤销单位。控制面只能提交固定 JSON；执行层不会接收脚本或任意命令。

## 安全边界

- WireGuard `allowed-ips` 将客户限定为一个 IPv4 `/32`。
- nftables 只允许客户从指定公网出口转发并做 NAT，禁止访问管理网段。
- 同一 WireGuard 客户接口上的客户不能互访。
- `tc` 按目标客户地址限制下行，按源客户地址 policing 上行。
- `wg show <interface> transfer` 的单 peer 接收与发送字节增量组成真实中继用量。首次采样只建立基线；WireGuard 重启导致计数器归零时不会产生虚假的巨大用量。
- 到期、额度耗尽和服务端撤销都会删除 WireGuard peer、限速规则和授权地址。

## 安装

中继机需要 `wireguard-tools`、`iproute2`、`nftables` 和 systemd。安装项目后执行：

```bash
sudo python3 scripts/install_client_relay.py --wireguard-interface wg-customer --interval 60
```

安装器只允许在 Linux root 下运行。systemd 定时任务每分钟采样并执行到期/额度核对，状态写入 `/var/lib/server-network-assist-relay`，权限为 root-only。

## 策略格式

```json
{
  "peer_id": "lease-01HXYZ",
  "public_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
  "address": "100.64.77.2/32",
  "interface": "wg-customer",
  "egress_interface": "eth0",
  "customer_subnet": "100.64.77.0/24",
  "management_subnets": ["10.0.0.0/8", "172.16.0.0/12"],
  "download_bps": 20000000,
  "upload_bps": 5000000,
  "quota_bytes": 107374182400,
  "expires_at": 1798761600,
  "enabled": true
}
```

字段以外的内容会被拒绝；接口名、网络、peer 公钥、速率与时间都经过验证。

## 运维命令

```bash
sudo python3 -m server_network_assist.client_relay apply --policy /root/lease.json
sudo python3 -m server_network_assist.client_relay status
sudo python3 -m server_network_assist.client_relay measure --interface wg-customer
sudo python3 -m server_network_assist.client_relay reconcile
sudo python3 -m server_network_assist.client_relay reconcile --desired /root/desired-policies.json
sudo python3 -m server_network_assist.client_relay revoke --peer-id lease-01HXYZ
```

`apply` 失败时会尝试撤销该 peer，并留下 `recovery_required` 状态和追加式事件日志。`revoke` 的清理步骤采用尽力执行；任一步失败同样留下恢复状态，不能把部分清理误报为成功。

`measure` 和 `status` 的每个 peer 会给出 WireGuard 的绝对 `last_received`、`last_sent`，以及本次 `delta_received`、`delta_sent`、`delta_bytes`。控制面应以本次增量写入不可变用量账本。`reconcile --desired` 接受策略数组：缺失的活动 peer 会被即时撤销，新增或变更的策略会被应用。

## 当前执行模型

用量是中继看到的隧道字节，包括协议承载的 IP 流量，并非移动运营商账单口径。额度判断在每次定时采样后发生，最多会超出一个采样周期内传输的字节。若需要更小超额窗口，应缩短定时间隔，并结合较低的线路速率。状态文件不得作为唯一账单档案；商业控制面应另外保存不可变计量记录和对账汇总。
