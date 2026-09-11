# 为 Decap CMS 配置 GitHub OAuth App

下面按本仓库当前 `prepare-cms.mjs` 的配置方式进行设置，避免你每次手工改源码。

## 一、在 GitHub 建立 OAuth App

1. 进入 `Settings -> Developer settings -> OAuth Apps -> New OAuth App`。
2. 填写：
   - Application name：`network-assist-cms`
   - Homepage URL：`https://whm12.art/projects/`
   - Authorization callback URL：填写所部署的 Decap 兼容 OAuth 服务提供的准确回调地址。
3. 点击创建后记录：
   - `Client ID`
   - `Client Secret`（只显示一次，请立即保存）

> 当前仓库只提供静态编辑后台，没有实现 `/projects/auth/callback` 或 `/auth` 认证接口。请先按 [Decap 官方 OAuth 服务列表](https://decapcms.org/docs/external-oauth-clients/) 部署认证服务，再依据该服务的文档填写回调。不能把静态页面地址作为回调，也不能仅填写下面的构建变量就认为登录已接通。

## 二、部署端配置（与你现有脚本对齐）

在 `server-network-assist/site/blog` 下复制 `.env.example` 为 `.env`，按实际认证服务填写：

```dotenv
BLOG_REPO=2667741708/server-network-assist
BLOG_BRANCH=main
BLOG_SITE_URL=https://你的域名/projects/
BLOG_DISPLAY_URL=https://你的域名/projects/
BLOG_OAUTH_BASE_URL=https://你的域名
BLOG_OAUTH_ENDPOINT=auth
```

然后执行：

```bash
npm run prepare:cms
```

`prepare-cms` 只把公开的站点与 backend 配置字段写入 `public/admin/config.json`。Client Secret 仅配置在认证服务器，不放入本目录的静态文件。前端按 `BLOG_OAUTH_BASE_URL` 是否存在决定显示登录入口；这只是配置状态，不是认证服务健康检查。

## 三、建议的部署顺序

1. 先在本地完成一轮文章提交流程验证（当前仓库支持本地 `npm run cms` + `local_backend` 预演）。
2. 在生产机部署静态站点和部署端点后，设置同名环境变量。
3. 再次触发构建或 `npm run build`，确认 `public/admin/config.json` 中 `oauthConfigured` 为 `true` 且 `backend.base_url` 已写入。
4. 打开 `/projects/admin/` 验证 GitHub 登录与草稿保存。
