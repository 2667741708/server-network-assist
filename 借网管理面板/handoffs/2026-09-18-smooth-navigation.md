# Smooth Navigation 本地实施记录

2026-09-18，REQ-UI-NAV-001 / TEST-UI-NAV-001。

已为 Tabler 订阅管理、Framework7 客户窗口及 Angular 协作台接入同一导航封装，加入安全 GET 意图/视口预取、并发加载、去重/取消、历史与滚动恢复及 120 ms 动效。服务端只共享当前运行的只读代理探测；API no-store 和所有原业务鉴权保持。

94 项 Python 回归、2 项 Angular 用例、5 组隔离浏览器测试、限定导航 lint、生产构建及 wheel 包资源检查通过。匹配延迟 fixture 首次列表 p50 533.6 → 311.4 ms；热视图减少动态效果约 15 ms，正常动效约 49 ms。数据不是线上实测。

没有部署/服务重启、真实订阅变更、操作员网络调整、真实借网或 Windows EXE 发行。无 JS 仅可读 shell/普通文档链接；实时业务仍需 JS。初始 Angular 包约 1.02 MB，超过 1 MB warning budget。

详细代码、文件清单、原理、原始指标和瓶颈见 [实施报告](../../docs/SMOOTH_NAVIGATION_IMPLEMENTATION.md)。
