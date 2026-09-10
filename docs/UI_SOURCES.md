# UI 来源与设计师候选

本次 UI 使用已有的 Framework7 iOS 主题与官方组件示例。项目内容、路由和业务事件由本项目接入；不另行设计颜色、阴影、圆角或视觉系统。Framework7 是第三方开源实现，并非 Apple 官方组件。

- [Framework7 官方模板](https://framework7.io/templates/)
- [官方页面骨架](https://framework7.io/docs/app-layout)
- [官方分组列表与媒体列表](https://framework7.io/docs/list-view)
- [官方卡片](https://framework7.io/docs/cards)
- [MIT 授权](https://github.com/framework7io/framework7/blob/master/LICENSE)

## 可核查的设计师候选

以下为 2026-09-10 查询页面的评分快照，评分会变化，不代表全球排名；没有联系、下单或复制其付费作品。

| 候选 | 平台记录 | 适用方向与边界 |
| --- | --- | --- |
| [Ella S.](https://www.upwork.com/services/product/design-user-friendly-ux-ui-design-for-web-and-saas-dashboards-dashboard-designer-1793929642299259596) | Upwork 5.0 / 5，297 条全部项目评价，Top Rated；该具体服务 1 条评价 | SaaS、B2B、控制台；不能据此认定她提供了可直接复用的 Apple 风格源码 |
| [Dhruv / vdhruvik](https://es.fiverr.com/vdhruvik/create-your-website-ui-ux-design-in-figma) | Fiverr 页面索引显示 5.0 / 5，251 条评价，Top Rated；以进入个人页时的实时数字为准 | 网站、应用和 Dashboard 的 Figma 设计；交付设计稿不等于已经开发好的组件 |

Framework7 的作者 Vladimir Kharlampidi 提供了可运行的 iOS 风格组件与模板，因此实际集成选用该开源实现。GitHub Star 仅反映关注度，不作为设计师客户评分。

## 集成与复现

Titan 桌面面板使用 `desktop_ui/` 中的现成 iOS 样式；多主机管理台仍使用 Angular Material。项目总览也使用 Framework7 列表与卡片，长文沿用原文章布局。

`docs/vendor/framework7/` 保存固定版本、MIT 授权、npm 包完整性和文件摘要。`frontend/framework7-theme.cjs` 使用官方库的默认参数生成默认色板，未自定义视觉参数；将色板保存为外部 CSS，避免运行时插入内联样式。桌面运行时只加载本地文件。

```text
python scripts/vendor_framework7.py
node frontend/framework7-theme.cjs
python scripts/sync_ui_assets.py
```

`desktop.css` / `hub.css` 只处理文档滚动、长文本换行、原生对话框承载和键盘可访问性；按钮、配色、圆角、阴影、列表和卡片来自上游实现。
