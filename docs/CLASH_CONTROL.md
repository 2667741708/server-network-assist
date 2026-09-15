# 远端 Clash / Mihomo 控制

管理台的“代理与 TUN”页面用于控制主机上已经安装并运行的 Clash Verge Rev、Clash 或 Mihomo。选择目标机器后，可以读取核心状态、切换规则/全局/直连模式、选择策略组和节点、控制 TUN，并在 Windows 上启停当前 SSH 用户的系统代理。

## 前置条件

- 目标机器已加入管理台，SSH 可达，并安装 Python 3。
- Clash/Mihomo 已启动，配置文件位于 Clash Verge Rev、`~/.config/mihomo/config.yaml` 或 `~/.config/clash/config.yaml` 的常用位置。
- Controller 只能监听目标机器本机，例如 `external-controller: 127.0.0.1:9097`。Windows Clash Verge Rev 的本机命名管道也受支持。管理台拒绝连接公开监听的 Controller。
- TUN 的实际启用仍取决于目标机器权限、驱动和 Clash 核心配置。

## 操作流程

1. 登录管理台，打开“代理与 TUN”，选择目标机器并刷新状态。
2. 选择规则、全局或直连模式。全局模式仍需在“策略组与节点”中选择实际节点。
3. Windows 可开启系统代理，使遵循 WinINET 代理设置的应用使用本机 mixed-port。Ubuntu 服务器的 SSH 会话不能代表桌面用户修改 GNOME 系统代理，应使用 TUN 或在用户图形会话中设置。
4. 开启 TUN 前确认目标机器仍有 SSH 管理路径。切换会触发二次管理员验证。
5. 添加规则时使用 Mihomo 规则格式，例如 `DOMAIN-SUFFIX,openai.com,节点选择`。新规则写到当前活动配置顶部并立即热加载。

模式、TUN 和规则写入前都会生成带时间戳的 `.sna-backup-*` 配置备份。热加载失败时程序恢复原配置并尝试重新加载。系统代理修改也会保存 `.sna-proxy-backup-*.json`。策略组节点选择通过运行中的 Controller 完成，不改写 YAML。

Clash Verge Rev 的订阅更新可能重新生成活动配置，从而覆盖直接加入活动配置的规则。需要长期保留的规则应同时加入 Clash Verge Rev 的 Merge/Script 配置；本页面当前不管理订阅地址、订阅凭据或 Merge 脚本。
