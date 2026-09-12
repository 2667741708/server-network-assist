# 为 Decap CMS 配置生产 GitHub OAuth

本仓库的文章存储在 GitHub，编辑器使用 Decap CMS。静态页面本身不能安全保存
GitHub Client Secret，因此生产环境需要一个只负责 OAuth 握手的服务端 proxy。
仓库已经提供 `services/decap-oauth/decap_oauth_proxy.py`，云端部署为
`127.0.0.1:9190`，由 Caddy 以 `/projects/oauth/` 暴露。

## 基础原理

```text
Decap /projects/admin/
        │ 打开同源 OAuth popup
        ▼
Caddy /projects/oauth/auth ──► GitHub 授权页
        ▲                         │ callback: code + state
        │                         ▼
Caddy /projects/oauth/callback ◄─ OAuth proxy
        │ 服务器端用 Client Secret 换 token，并验证 GitHub 账号与仓库 push 权限
        └──────── postMessage(token) ───────► Decap
```

`state` 同时放在短期 HMAC 签名值和 `HttpOnly` cookie 中，防止伪造回调；Client
Secret、state secret 和 GitHub token 都只存在服务端，不能进入静态构建产物。
GitHub 的 OAuth Web Application Flow 是“授权码 → 服务端换 token → API 调用”；
Decap 的 GitHub backend 随后用这个 token 读取、提交文章和创建编辑工作流分支。

## 一、在 GitHub 建立 OAuth App

1. 进入 `Settings -> Developer settings -> OAuth Apps -> New OAuth App`。
2. 填写：
   - Application name：`network-assist-cms`
   - Homepage URL：`https://whm12.art/projects/`
   - Authorization callback URL：`https://whm12.art/projects/oauth/callback`
3. 点击创建后记录：
   - `Client ID`
   - `Client Secret`（只显示一次，请立即保存）

不要启用 callback 通配符；生产回调地址必须精确匹配。仓库是公开仓库时使用
`public_repo,user` scope；如果以后改为私有仓库，服务端改用 `repo,user`。

## 二、在 cloud 部署 OAuth proxy

把 `services/decap-oauth/decap_oauth_proxy.py` 放到 cloud 的
`/opt/decap-oauth/`，把 `deploy/decap-oauth.service` 放入
`/etc/systemd/system/`。创建专用用户和秘密环境文件：

```bash
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin decap-oauth
sudo install -d -o decap-oauth -g decap-oauth -m 0750 /opt/decap-oauth
sudo install -o root -g decap-oauth -m 0750 decap_oauth_proxy.py /opt/decap-oauth/decap_oauth_proxy.py
sudo install -o root -g root -m 0644 decap-oauth.service /etc/systemd/system/decap-oauth.service
sudo sh -c 'umask 077; printf "GITHUB_OAUTH_ID=你的ClientID\nGITHUB_OAUTH_SECRET=你的ClientSecret\nOAUTH_PUBLIC_BASE_URL=https://whm12.art/projects/oauth\nCMS_ORIGIN=https://whm12.art\nOAUTH_STATE_SECRET=%s\nGITHUB_OAUTH_SCOPE=public_repo,user\nGITHUB_REPO=2667741708/server-network-assist\nOAUTH_BIND=127.0.0.1\nOAUTH_PORT=9190\n" "$(openssl rand -hex 32)" > /etc/decap-oauth.env'
sudo chmod 0640 /etc/decap-oauth.env
sudo systemctl daemon-reload
sudo systemctl enable --now decap-oauth.service
curl --fail http://127.0.0.1:9190/healthz
```

实际部署时应使用安全输入方式填写 `GITHUB_OAUTH_ID` 和
`GITHUB_OAUTH_SECRET`，不要把它们放在 Git、聊天记录、命令历史或博客的
`.env` 中。服务启动后必须得到 `{"ok":true}`；如果是 `503`，先修正环境文件。

Caddy 需要在静态 `/projects/*` 路由之前增加：

```caddyfile
handle_path /projects/oauth/* {
    reverse_proxy 127.0.0.1:9190
}
```

修改前执行 `caddy validate`，然后只 reload Caddy。不要把 9190 直接绑定公网。

## 三、部署端配置（与 `prepare-cms.mjs` 对齐）

在 `server-network-assist/site/blog` 下复制 `.env.example` 为 `.env`，按实际认证服务填写：

```dotenv
BLOG_REPO=2667741708/server-network-assist
BLOG_BRANCH=main
BLOG_SITE_URL=https://whm12.art/projects/
BLOG_DISPLAY_URL=https://whm12.art/projects/
BLOG_OAUTH_BASE_URL=https://whm12.art
BLOG_OAUTH_ENDPOINT=projects/oauth/auth
```

然后执行：

```bash
npm run prepare:cms
```

`prepare-cms` 只把公开的站点与 backend 配置字段写入 `public/admin/config.json`。Client Secret 仅配置在认证服务器，不放入本目录的静态文件。`base_url` 保持为纯 origin，`auth_endpoint` 才包含 `/projects/oauth/auth` 路径，这样 Decap 的 popup 来源校验仍与博客同源。

## 四、建议的生产验收顺序

1. 先部署 proxy，确认 `/healthz` 返回 `{"ok":true}`，并确认 9190 仅监听 `127.0.0.1`。
2. 配置 Caddy 并 reload，确认 `https://whm12.art/projects/oauth/healthz` 返回 `200`。
3. 设置博客 `.env`，运行 `npm run prepare:cms` 和 `npm run build`，确认 `oauthConfigured` 为 `true`、`backend.base_url` 为 `https://whm12.art`。
4. 发布静态构建，打开 `https://whm12.art/projects/admin/`，点击登录并完成 GitHub 授权。
5. 创建一篇保留为草稿的文章，确认 GitHub 出现 `cms/...` 分支或 PR；再删除测试稿。
6. 浏览器开发者工具中确认没有把 Client Secret、token 或 state 打印到控制台。

### 常见故障定位

- 直接跳到 GitHub 登录页：通常是正常的，说明 `/auth` 已工作；完成 GitHub 授权即可。
- `redirect_uri_mismatch`：GitHub OAuth App 的 callback 必须精确写成 `https://whm12.art/projects/oauth/callback`。
- `OAuth 状态校验失败`：检查浏览器是否阻止了 Secure cookie、是否重复使用旧 popup；关闭旧窗口后重新点击登录。
- `没有仓库写入权限`：当前 GitHub 账号不是 `2667741708/server-network-assist` 的可写成员。
- 后台显示未接通登录：重新构建并发布 `public/admin/config.json`；这不是 Caddy OAuth proxy 的健康检查。
