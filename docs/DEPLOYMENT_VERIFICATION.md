# Cloud 博客部署验收

2026-09-11 09:08（Asia/Shanghai），独立部署智能体通过 Reliable SSH 在已核验的 `cloud` / `VM-0-12-ubuntu` 执行。上线地址：[项目博客](https://whm12.art/projects/)、[项目目录](https://whm12.art/projects/catalog/)、[编辑后台](https://whm12.art/projects/admin/)。

| 检查 | 结果 |
| --- | --- |
| 权限与身份 | SSH 为 ubuntu；策略 unrestricted；`sudo -n /usr/bin/id -un` 返回 root |
| 构建 | 采用博客验证智能体完成的 AstroPaper production 构建；25 个项目、2 篇正文 |
| 安装前校验 | manifest v2 的文件清单及全部 SHA256；路径边界与符号链接检查；Caddy 原配置 SHA 防止覆盖并发变更 |
| Caddy dry-run | `caddy validate` 通过后才切换发布目录 |
| 公网完整性 | 227 个发布文件逐一从 HTTPS 下载，SHA256 全部匹配，包括图片、编辑器延迟加载 JS 与 Pagefind 索引 |
| 原站点 | `/` 上线前后均 HTTP 200；`/network-resilience/` HTTP 200；保留既有 LingTour 反向代理与原教程配置 |
| 服务 | `caddy.service` active，仅 reload，未重启业务服务 |
| 部署回滚测试 | 云端 Ubuntu 临时目录执行 9 项测试全部通过；模拟公网校验失败，确认旧 Caddy 配置及旧发布符号链接恢复。此测试未触碰线上配置 |
| CSP | 普通博客仅为 Pagefind 放行 `wasm-unsafe-eval`；Decap 专属 `unsafe-eval` 和 `connect-src blob:`。内联脚本使用构建内容 SHA256 白名单 |

发布目录：`/var/www/whm-projects/releases/20260911-090809-3576316`。

回滚备份：`/var/backups/whm-projects/20260911-090809-3576316`，包含部署前 Caddyfile 与 previous.json。首次部署没有旧博客 release；回滚时恢复原 Caddy 路由并撤销 current 符号链接。

配置 SHA256：

- 部署前 Caddyfile：`085690c46848fbe0a6825d899d963908e28730bafcd7e1e49935021491f13757`
- 部署后 Caddyfile：`2dad44862dcae8d971d8e0cbf92dad8ed066f7cf6128834b9bda7a60dbd796bc`
- 发布 manifest：`8aba2da57e7948822ae7726fd748880f5a651db8cd6d45d5a0f663adb4d30fc0`

GitHub OAuth App 尚未创建。生产后台应明确显示配置缺失，不能进行真实 GitHub 登录或发布；隔离 CMS 的草稿、图片、预览测试不能替代这项生产验收。接入步骤见 [BLOG_STUDIO.md](BLOG_STUDIO.md)。

复用与验证命令：

```text
python scripts/prepare_project_deployment.py
python -m unittest discover -s tests -p test_project_deployment.py -v
```

准备脚本仅打包已完成的 `site/blog/dist`。每次部署仍需重新获取当前 Caddyfile SHA，并先运行发布脚本的 `--dry-run`。发布脚本在完整公网校验失败时自动恢复配置与 current 链接。
