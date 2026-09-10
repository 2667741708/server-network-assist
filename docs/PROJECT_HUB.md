# 域名下的统一项目博客

统一入口：<https://whm12.art/projects/>。独立项目地址：`https://whm12.art/projects/<slug>/`。

域名首页继续提供原来的灵山服务；`/network-resilience/` 等已有页面保持原地址。项目中心只使用 `/projects/` 路由。

## 内容组织

- `site/projects/catalog.json`：公开 GitHub 仓库元数据快照。同步只读取 `2667741708` 的公开仓库，不读取私有项目或导出访问令牌。Fork 明确标注。
- `site/projects/editorial.json`：人工维护的标题、摘要、分类、精选项目和文章映射，不会被同步覆盖。
- `site/projects/hub.css` / `hub.js`：首页及速览页样式、搜索、分类、文章类型、Fork 筛选和主题切换。
- `docs/vendor/framework7/`：已有的 Framework7 iOS 组件样式、默认色板、授权与完整性记录；不需要外部 CDN。
- `docs/projects/`：生成的完整静态站点。不要直接修改生成内容。
- `docs/blog/`：Server Network Assist 的长文源文件，构建时复制到该项目独立目录，图片一起发布。

已有完整教程的项目进入精选区；其他公开仓库先有项目速览页、README/Release/Issue 入口，之后可以在同一固定地址补充实操长文。空简介不编造功能，Fork 不作为原创成果介绍。

## 新增项目

公开新仓库后，在仓库根目录运行：

```text
python scripts/build_project_hub.py --sync
```

构建会生成新页面、索引和 sitemap，并清除已经不在公开清单中的旧页面。GitHub API 查询失败时不会覆盖原清单；无需把 GitHub Token 放到前端或 cloud 网站目录。

在 `site/projects/editorial.json` 中按仓库名增加配置，例如：

```json
{
  "my-project": {
    "slug": "my-project",
    "title": "我的项目：从问题到实现",
    "summary": "用一两句话说明它解决的问题。",
    "category": "开发工具",
    "kind": "项目速览",
    "guide_url": "https://whm12.art/my-project-guide/"
  }
}
```

可用 `slug` 自定义独立地址（小写字母、数字、连字符或下划线，不重复），`guide_url` 指向已有完整教程。教程准备好后把 `kind` 改为“完整教程”，需要推荐时设置 `featured: true`。默认 slug 为仓库名的小写，头尾连字符会规范化；已发布项目尽量保持 slug 不变，否则旧链接需另外配置重定向。

新增同站托管长文时，在构建器中按 `article` 标识增加本地文章资源复制逻辑，沿用当前 `network-assist` 示例。不要从互联网上复制未检查的脚本进入站点。

不联网重新构建：`python scripts/build_project_hub.py`。

## 本地检查

```text
python -m http.server 9191 --bind 127.0.0.1 --directory docs
```

浏览 `http://127.0.0.1:9191/projects/`。首页和项目速览即使禁用 JavaScript 也保留所有正文和链接。搜索筛选条件写入 URL，可以直接分享筛选结果。静态页面可部署到 Caddy、Nginx 或 GitHub Pages；当前 canonical URL 面向 `whm12.art/projects/`。

## cloud 部署

可先运行 `python scripts/prepare_project_deployment.py`，在 `artifacts/project-deployment/` 生成仅含静态站点和部署脚本的暂存包。

只上传 `docs/projects/` 生成目录和 `scripts/deploy_project_hub.py`，不要上传整个工作目录或私有配置。使用既有可信 `cloud` SSH 身份。

1. 检查 cloud 主机身份和当前 `/etc/caddy/Caddyfile`，记录其 SHA-256。
2. 上传静态构建与部署脚本到 `/home/ubuntu/` 下独立暂存目录。
3. 先运行部署脚本的 `--dry-run`：它验证静态资源清单、域名、配置摘要和 Caddy 语法。
4. 使用同一参数去掉 `--dry-run`，完成发布。

远端命令格式：

```text
sudo python3 /home/ubuntu/<upload>/deploy_project_hub.py --source /home/ubuntu/<upload>/site --expected-config-sha256 <reviewed-sha256> --dry-run
```

发布目录为 `/var/www/whm-projects/releases/<release>/`，当前版本通过 `current` 符号链接原子切换。Caddy 仅增加命名的项目路由，校验后 reload；原配置和上一个版本信息保存到 `/var/backups/whm-projects/<release>/`。上线健康检查失败时恢复配置和原链接。不会重启灵山服务或修改 DNS。

以后新增项目需要重新同步、构建、检查并部署。当前没有配置定时任务，不会在无人检查时自动把新文章发布到公网。
