# 命令行控制

`server-network-assist-ctl` 与管理面板使用同一数据目录、SSH 凭据、主机指纹和借网方案。它适合通过终端或自动化脚本执行操作。命令应在安装管理台的服务器上运行；默认读取 `/var/lib/server-network-assist`，其他位置使用 `--data` 和 `--master-key` 指定。

## 查看服务器和客户端

```bash
sudo server-network-assist-ctl hosts
sudo server-network-assist-ctl profiles
sudo server-network-assist-ctl probe --host all
```

`hosts` 会列出每台主机在各借网方案中的 `server` 或 `client` 角色。命令接受主机/方案的显示名称或 ID。加 `--json` 可获得适合脚本处理的完整 JSON。

## 创建、启用与退出借网

```bash
sudo server-network-assist-ctl profile-create --name titan-borrow --server c201-5080 --client titan --endpoint 10.20.32.12 --preserve-route 10.20.0.0/16
sudo server-network-assist-ctl profile-enable --profile titan-borrow
sudo server-network-assist-ctl profile-disable --profile titan-borrow
```

创建方案时，`--server` 是能够访问公网的源网服务器，`--client` 是借网客户端；可重复写多个 `--client`。默认仅共享网络。共享源机 HTTP/HTTPS 代理时增加：

```bash
--proxy-mode share --proxy-host 127.0.0.1 --proxy-port 7897
```

首次使用一台主机前，可执行 `helper-install --host 主机名`。启用和退出操作会调用与面板相同的验证、路由保留、失败回退和恢复逻辑。

## Clash、系统代理和 TUN

```bash
sudo server-network-assist-ctl clash-status --host titan
sudo server-network-assist-ctl clash-mode --host titan --mode rule
sudo server-network-assist-ctl clash-mode --host titan --mode global
sudo server-network-assist-ctl clash-proxy-select --host titan --group "🚀 节点选择" --name "香港 A"
sudo server-network-assist-ctl clash-system-proxy --host titan --value on
sudo server-network-assist-ctl clash-tun --host titan --value on
sudo server-network-assist-ctl clash-rule-add --host titan --rule "DOMAIN-SUFFIX,openai.com,🚀 节点选择"
```

这些命令仍通过 SSH 在目标机器本机访问 Clash Controller。配置备份、规则校验、Controller 回环限制和失败恢复与网页操作一致。CLI 拥有管理服务器数据目录权限的人等同于管理台管理员，因此应限制系统账号和 master key 的读取权限。
