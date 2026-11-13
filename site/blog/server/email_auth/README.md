# 博客邮箱登录与内容网关

Google 使用 OAuth 授权码、PKCE、state、nonce 和 Google 签名校验；QQ 邮箱使用 10 分钟有效、最多尝试 5 次、只能使用一次的邮件验证码。允许登录的地址只放在服务器配置中。

浏览器仅持有 8 小时的 Secure / HttpOnly 会话 Cookie。GitHub 凭据留在服务器，接口仅允许修改指定仓库 main 分支中六类博客目录的 Markdown 文章和 uploads 目录的 PNG、JPEG、GIF、WebP 图片。写操作校验同源请求和 CSRF，保存校验文件 SHA 防止覆盖。

部署配置位于 `/etc/blog-email-auth/config.json`，必须指定 database、secret_file、github_token_file、smtp_config_file、google_client_file、qq_email、google_email。所有密钥文件只允许服务账号读取。不要将配置、数据库、OAuth JSON 或 SMTP 密码提交到 Git。

Google Web 客户端的回调必须为 `https://whm12.art/projects/email-auth/google/callback`，仅申请 openid、email、profile。将控制台下载的客户端 JSON 保存到配置指定的位置；服务会自动识别，无需把密钥写入前端。

运行服务：`python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 9191 --no-access-log --no-proxy-headers`。Caddy 保留 `/projects/email-auth/` 前缀转发到该端口，并覆盖 X-Real-IP 为真实客户端地址。只对外开放 HTTPS。

前端从 `/projects/admin/` 检查会话，未登录跳转统一登录页；`?login=github` 可继续使用原有 GitHub 登录。通过邮箱登录使用简单发布模式，关闭“保留为草稿”后保存才能显示在公开网站。

验证：在本目录安装 requirements.txt 和 pytest，执行 `python -m pytest -q test_app.py`。测试使用临时数据库、模拟仓库和模拟邮件，不发送真实邮件、不更改真实仓库。
