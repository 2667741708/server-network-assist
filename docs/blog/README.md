# 网络手记：项目博客

直接打开 `index.html` 即可阅读，交互示意不调用真实网络 API。无框架、无需构建、无 CDN 或外部字体依赖。

部署时保留 `docs/blog/` 与 `docs/images/` 的相对位置。例如把整个 `docs` 目录部署到静态托管，文章入口为 `/blog/`；GitHub Pages 可选择 `main` 分支的 `/docs`，访问 `/server-network-assist/blog/`。这里只提供可部署文件，未自动开通 Pages 或绑定域名。

本地 HTTP 预览（从仓库根目录）：

```text
python -m http.server 9191 --bind 127.0.0.1 --directory docs
```

访问 `http://127.0.0.1:9191/blog/`。源码位置：`index.html`（文章）、`style.css`（排版）、`blog.js`（主题、目录、图像放大、复制和路径演示）。图片复用 `../images/`，须连同该目录部署。若浏览器限制本地文件的剪贴板 API，复制按钮会选中代码并提示手动复制。

文章采用本项目原创内容和样式，参考链接见页尾“排版与行文参考”：Josh W. Comeau 的交互讲解、Overreacted 的标题/日期/短摘要组织、Paul Graham 的正文优先。未复制第三方源码或品牌素材。

共享能力和限制以 [SHARING.md](../SHARING.md) 为准；界面图均使用标明的演示数据。
