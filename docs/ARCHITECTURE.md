# 架构与开发

## 组件

```text
Browser (Angular Material + Xterm.js)
            │ HTTPS / same-origin WebSocket
            ▼
aiohttp control plane ── SQLite + encrypted SSH vault
            │ AsyncSSH, pinned host keys, optional jump chain
            ▼
Managed Linux hosts
  └─ root helper: WireGuard / routes / iptables / systemd timers
```

前端与后端同源发布。生产构建输出复制到 Python 包的 `server_network_assist/ui`，因此运行时不依赖 Node.js 或外部 CDN。

## 目录

- `frontend/`：Angular 22 standalone components
- `src/server_network_assist/app.py`：HTTP、WebSocket、认证和 SSH 编排
- `src/server_network_assist/network_assist.py`：方案校验、探测与运行时配置
- `src/server_network_assist/network_assist_helper.py`：远端 root helper
- `tests/`：数据模型与 API 集成测试
- `deploy/`：systemd 服务模板
- `docs/`：安装、使用、安全和借网说明

## 构建

前端依赖使用 `package-lock.json` 固定。运行 `npm ci`、`npm run build` 后，用 `python scripts/build_frontend.py` 更新随 Python wheel 分发的静态资源。

任何修改网络 helper 的变更都应增加回退测试，并在隔离虚拟机中验证：启用成功、客户端失联、出口断网、重启维护、手工 disable 和原默认路由恢复。
