# 项目博客创作后台

## 已采用的现成界面

- AstroPaper 官方主题 6.1.0，来源 https://github.com/satnaing/astro-paper ，初始导入提交 `35cfa7f`，MIT 许可证保留在 `site/blog/LICENSE`。使用原有首页、文章、归档、搜索、明暗主题、图片查看组件。适配中文、项目关系与 `/projects/` 基路径，不另造博客视觉主题。
- Decap CMS `3.16.2`，本地代理 `decap-server 3.11.2`，MIT，版本锁定于 package-lock.json。编辑器原始构建及第三方声明由 `prepare:cms` 复制到本地，运行时无需 CDN。

## 编辑流程

在 `/projects/admin/` 选择实操教程、项目介绍、故障复盘、实验记录、原理解说之一，再选择关联项目。每种集合自动提供对应正文结构。填写目标摘要、正文、作者、日期、标签、适用版本和验证范围；通过原生媒体库上传图片，并在正文中插入图片与图注。

`draft: true` 保证正式构建不呈现草稿。GitHub backend 使用 `publish_mode: editorial_workflow`，保存创建 `cms/<集合>/<slug>` 分支，批准发布才合并到 main。正式发布前还要关闭“保留为草稿”，否则合并后文章仍保持隐藏。代码直接提交的文章同样遵守 draft 字段。

项目目录包括 25 个公开仓库；项目和文章是一对多关系。只有实际编写的两篇文章进入文章列表，未写的项目保留仓库入口，不自动生成虚假教程。旧 `/projects/<repo>/` 路径重定向到 `/projects/project/<repo>/`。

## 本地命令

在 `site/blog` 执行：

```text
npm ci
npm run dev
npm run cms
npm run build
npm run build:preview
```

`cms` 在仓库根运行官方代理、监听 127.0.0.1:8081，使用 Git 模式，必须有可用 Git 仓库和本地提交身份。不要在有未提交代码的开发分支中试验草稿切换；建议使用独立内容工作树。自动化验证已经使用完全隔离的临时 Git 仓库，测试文章从未写入主仓库。

开发服务器可以渲染 draft；`build:preview` 显式包含草稿，用于独立预览部署，不能覆盖正式站。`build` 排除草稿和未到发布时间的文章，输出 `site/blog/dist`，包括 admin、图片和 Pagefind 索引。主站 `/projects/*` 去掉前缀后映射 dist，保留域名根目录的其他应用。

## GitHub 登录和发布接入

生产登录采用仓库内的 `services/decap-oauth/` proxy：云端只监听
`127.0.0.1:9190`，Caddy 在现有 `whm12.art` HTTPS 站点下转发
`/projects/oauth/*`。GitHub OAuth App 的精确 callback 是
`https://whm12.art/projects/oauth/callback`；它不属于静态
`/projects/admin/` 页面。完整部署步骤见 [GITHUB_OAUTH_SETUP.md](../site/blog/GITHUB_OAUTH_SETUP.md)。

构建变量：`BLOG_OAUTH_BASE_URL=https://whm12.art`，
`BLOG_OAUTH_ENDPOINT=projects/oauth/auth`。Client ID/secret 和 state secret
保留在 OAuth service，绝不写入静态配置。backend 固定为
`2667741708/server-network-assist` 的 main 分支。未配置时，线上后台明确显示
尚未接通登录，前台照常可读。

发布预览还需要 CI 对 CMS 草稿分支构建 `build:preview`，并通过 Decap 的 deploy preview 状态返回独立预览 URL。静态 CMS 右栏预览不等于 AstroPaper 完整主题预览；本轮尚未接通远程分支预览服务。

官方资料：

- https://decapcms.org/docs/editorial-workflows/
- https://decapcms.org/docs/decap-proxy/
- https://decapcms.org/docs/github-backend/
- https://decapcms.org/docs/external-oauth-clients/

## 实际验证与 CSP

`node frontend/blog-cms-smoke.cjs` 启动短生命周期隔离代理和静态服务，用真实 Chromium 执行选择项目、创建模板文章、上传图片、编辑正文、保存、重新打开，核对 Git 草稿分支内容与 main 未包含测试稿。进一步检查 390/1440 宽文章、图片加载、整页横向溢出和 Pagefind 搜索。证据在 `artifacts/blog-cms-check/`，服务结束后自动退出。

生产构建通过 Astro check（0 错误、0 警告）、65 个生成页面和 2 篇文章搜索索引。仅后台需要 `script-src 'unsafe-eval'`：Decap 的 AJV 运行时编译 JSON schema，缺少会阻断初始化；后台 `connect-src` 还必须允许 `blob:`，否则媒体保存被 CSP 阻断。普通页面无需 JS unsafe-eval；Pagefind 的 WebAssembly 可使用更窄的 `wasm-unsafe-eval`。

`node frontend/blog-build-verify.cjs` 接着读取同一批真实 CMS 草稿，在隔离主题副本中分别执行 production/preview 构建。五类模板均已逐一保存、重新打开；正式文章路由、首页、项目页、归档、RSS 与 sitemap 均排除这些草稿，预览构建包含全部五篇。1440/390 宽度下检查新草稿正文、无横向溢出，并保存截图。上传的 91,675 字节图片由实际 Git 草稿分支读取核对。修复了 public-folder 图片路径被 Astro `image()` 误识别为本地导入的问题。

CMS 测试也执行 CSP 反证：移除后台 `unsafe-eval` 后初始化失败，恢复后完整编辑流程通过；证据保存在 `csp-negative.json`。编辑器尚未保存时可能短暂请求尚不存在的公开媒体路径，测试在保存并重开后强制核对图片 HTTP 200。官方本地代理的无部署预览查询可能返回 404，不能据此声称远程分支预览已经可用。

GitHub Actions 新增独立 blog job，执行正式构建、真实 CMS 浏览器验证、隔离草稿构建和截图，上传验证证据与正式站点产物。未完成项必须单独验收：生产 OAuth 登录、发布 PR 与远程独立预览、线上版本核对。当前通过的本地草稿闭环不能替代这些步骤。
