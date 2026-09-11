# 安全模型

## 信任边界

- 浏览器只接触会话 Cookie、CSRF token、主机非敏感元数据和一次性终端票据。
- Web 进程能解密 SSH 凭据，因此管理节点本身属于高信任系统。
- WireGuard 私钥仅存在于各远端主机的 root-only 目录，不回传管理台。
- 远端 root helper 是唯一可修改路由、防火墙、转发和 WireGuard 配置的组件。

## 已实现控制

- scrypt 密码摘要；Fernet 加密 SSH 私钥与密码
- `HttpOnly`、`SameSite=Strict` 会话 Cookie；HTTPS 时添加 `Secure`
- POST 请求使用同源检查和 CSRF token
- SSH 主机公钥固定，不允许静默接受未知指纹
- 凭据 API 不返回密文或明文；审计过滤敏感字段
- 终端票据 30 秒过期、绑定会话且只能使用一次
- 高风险操作需要 5 分钟内重新验证密码
- Codex 消息只发送到所选 SSH 主机上的现有 Codex CLI；不把远端 Codex 登录凭据传回管理台
- Codex 对话仅允许 `read-only` 或 `workspace-write` 沙箱，不提供绕过审批与沙箱的开关
- 非 loopback 监听必须配置精确公开 Origin
- systemd 服务模板启用 `NoNewPrivileges`、`ProtectSystem` 和专用可写目录

## 部署者责任

- 通过 VPN 或可信内网限制管理入口，并使用 HTTPS
- 对 `master.key` 和 SQLite 文件做加密备份
- 为面板创建专用 SSH 账号和专用私钥
- Codex 会话正文保存在管理台 SQLite 中；可能包含代码或内部信息，备份与截图需按敏感资料处理
- 人工核对 SSH 指纹与 sudoers 内容
- 使用主机防火墙限制 WireGuard UDP 端口来源
- 在可现场恢复的主机上先验证启用和断开

## 非目标

本项目不是零信任堡垒机、企业 PAM、通用 VPN 编排器或多租户控制平面。当前为单管理员模型，不提供细粒度 RBAC。
