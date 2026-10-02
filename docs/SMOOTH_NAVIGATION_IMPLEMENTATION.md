# Smooth Navigation 实施与验收记录

日期：2026-09-18。实际仓库：`D:\文件\网络登录服务器管理\server-network-assist`。

已实现统一导航、安全预取和并行数据加载，并生成可复用 skill。隔离网络条件下，订阅客户列表首次可用中位时间从 **533.6 ms 降至 311.4 ms（减少 41.6%）**。已加载工作区的内容可见时间约一帧；正常动效模式约三帧。代码、打包资产和本地验证完成；没有发布线上、重启服务、修改真实订阅或调整操作员网络。

## 1. 定位结果与实现选择

| 层 | 文件/入口 | 原有机制与处理 |
| --- | --- | --- |
| 源端订阅管理 | `src/server_network_assist/subscription_admin_ui/subscriptions.html`, `subscriptions.js`, `dashboard.js` | 原生 JS + Tabler；保留 DOM，三个工作区统一导航 |
| 客户端窗口 | `src/server_network_assist/client_ui/index.html`, `client.js` | Framework7；折叠订阅/帮助/通知设置经过同一控制器，保留框架展开机制 |
| 协作台 | `frontend/src/app/app.ts`, `app.html`, `commercial.component.ts` | Angular 22，原有 signal 选页；接入类型化适配器，商业编辑器首次访问后保持挂载 |
| 管理 HTTP/API | `src/server_network_assist/app.py` | aiohttp；鉴权在只读共享探测之前，两个订阅读接口并发加载 |
| 客户 HTTP/API | `src/server_network_assist/client.py` | loopback HTTP handler；静态资产条件缓存，查询参数深链，API 仍鉴权/no-store |
| 发行 | `scripts/build_frontend.py`, `pyproject.toml` 的 package-data | 沿用既有布局；新增增量打包脚本，避免删除用户已有 UI 文件 |

不是 Astro 项目，因此没有引入 Astro。保留了工作区中已有的 `frontend/shared/smooth-interactions.*` 交互代码及其他未提交改动。Git 的完整 diff 包含本次任务之前或同时存在的变更，不能把全部 diff 计作本次新增。

## 2. Skill 内容和复用

仓库内：`skills/smooth-navigation/SKILL.md` 和 `references/acceptance.md`。完整正文同时交付为 `Smooth-Navigation-Skill.md` 和 `smooth-navigation-skill.zip`。

Skill 覆盖框架选择、统一链接、共享元素、hover/focus/viewport 预取、目标页面/数据准备、受限缓存/SWR、局部状态、滚动与焦点、事件生命周期、减少动态效果、失败/no-JS 回退、去重/取消、服务端优化、性能预算和验收。它要求使用现有框架机制，明确禁止预取/自动重放副作用操作，并区分显示缓存与授权状态。

默认预算：100–160 ms 动画；本项目 120 ms；80 ms 快照准备兜底；70 ms 意图防抖；最多一个预取组/两个并行 API；八条小型内存记录；1.5 s 新鲜窗口；共享导航 JS <=15 KiB、CSS <=2 KiB；热导航内容可见 p50 <=50 ms/p95 <=100 ms。

## 3. 实现原理与关键代码

### 导航和共享元素

统一引擎位于 `subscription_admin_ui/smooth-navigation.js`，同时被 Tabler、Framework7 和 Angular 使用。菜单采用真实 href，URL `?view=...` 可刷新和深链。History API 保留已有 state，按历史条目保存真正滚动容器的坐标；前进/后退不再创建新条目。记录只含视图、历史 ID 和坐标，不含凭据或业务数据。

```js
const workspaceNavigation = SmoothNavigation.create({
  views: ['customers', 'generate', 'egress'], initial: 'customers',
  render: renderWorkspace, canNavigate: () => authenticated,
  prefetch: async () => { if (!busy) await subscriptionReads.read('client-service'); }
});
```

控制器委托处理内部视图链接，程序化按钮/卡片使用同一个 navigate 方法。外链、下载、修饰键点击不被拦截。返回协作台是明确 allowlist 的同源文档链接，保留浏览器原生导航，可在意图出现时预取其公开 shell；不跨不同管理认证上下文替换整份 DOM。

纯 DOM/state 提交由 View Transitions 包裹，快照中不等待数据。新请求慢时，目的视图与其实际旧数据/加载状态立即显示。连续点击跳过旧动画，以 generation 保证只提交最新目的地。快照失败或超过 80 ms 走同一纯提交兜底。减少动态效果或 API 不支持时直接提交。

可见工作区标题使用相同的 `navigation-heading` 名称，现有导航栏/标签栏保持连续；隐藏节点不参与快照。Framework7 折叠项使用自身 120 ms 动画，避免再叠加整页动画，展开保持当前位置。Angular 通过同步 detectChanges 提交视图，并保留商业表单草稿；隐藏组件取消其读订阅，回来重新检查/读取安全数据。

### 请求提前、去重和取消

```js
const reads = SmoothNavigation.createReadCache({
  allowed: ['client-service', 'client-service/dashboard'],
  load: (path, signal) => request(path, undefined, signal), maxAge: 1500
});
const [service, snapshot] = await Promise.all([
  api('client-service'), api('client-service/dashboard')
]);
```

管理页在 session 验证后并行取配置与仪表盘，以预载 snapshot 直接渲染。Angular 菜单的 hover/focus/viewport 会提前准备商业订阅数据；相同安全 GET 在同一认证 epoch 内共享任务。限制预取并发和条数，并在后台、Save-Data/2g 条件下跳过。切换局部视图不重复下载文档和已加载框架。

每次写操作前后、登出及 401/403 均使缓存失效。取消过期读请求，并拒绝它在新 epoch 回填。返回复制过的 payload，防止 UI 修改缓存对象。写操作不预取、不重试、不合并；沿用既有 CSRF、Origin、令牌、确认凭据和忙碌保护，并为 Angular 商业操作补上显式 busy guard。模糊超时保留原有锁定与“核对后再操作”提示。

SWR 是引擎可选的显示专用能力，已测试旧值立即返回、随后更新；本项目没有将它用于实时代理就绪、客户端源网可用性或仪表盘授权/计量判断。这些前台/周期读取强制 fresh。失败保留旧显示时仍锁定相关修改，不能让旧缓存恢复权限。

客户窗口启动令牌只保存于当前来源、当前标签的 sessionStorage，以支持去掉地址片段后的刷新；不写入 URL、history 或共享数据缓存，401/403 删除。若标签存储不可用，则保留原启动片段。管理端密码/确认凭据不进入 Web Storage，仍按原流程清空。

### 服务端

```python
proxy, defaults = await asyncio.gather(
    navigation_reads.get('proxy', lambda: asyncio.to_thread(proxy_status)),
    asyncio.to_thread(physical_defaults))
value = await asyncio.to_thread(snapshot)
```

源网列表每份响应只读取一次。独立代理与物理出口检查并行，SQLite/档案读取放到线程中，减少对 aiohttp 主事件循环的阻塞。配置接口与仪表盘同时请求时，共享当前正在运行的全局代理只读探测；结果完成即移除，下一次读取重新探测。任何写操作前后分离旧探测槽位，防止随后请求沿用旧状态；不为代理就绪增加跨请求 TTL。

机密/API 响应继续 `no-store`。只给公开静态 JS/CSS/字体/图片增加 `max-age=0, must-revalidate` 和 ETag 条件重用；版本无关的文件不会被长期冻结。aiohttp 使用 FileResponse 自带验证器，客户端静态 handler 使用内容摘要。订阅读接口增加 Server-Timing。所有 JSON 字段、签名/nonce、账期、计费和租约业务语义保持原接口契约。

## 4. 匹配的性能验证

测试为 Chromium 隔离拦截，不接触真实客户。固定 session 延迟 50 ms、每个数据 API 200 ms；同一视口、数据 fixture（空客户列表）、浏览器和网络条件。每组五个冷上下文、25 次热跳转。首次内容指仪表盘列表计数真实渲染，不是只有静态标题；热导航指点击到目标 DOM 可见后的下一帧。

| 指标 | 优化前 | 优化后 |
| --- | ---: | ---: |
| 首次有用列表，减少动态效果，p50 | 533.6 ms | 311.4 ms |
| 首次有用列表，减少动态效果，p95（五次样本） | 723.9 ms | 320.0 ms |
| 热跳转内容可见，减少动态效果，p50 | 12.8 ms | 15.5 ms |
| 热跳转内容可见，减少动态效果，p95 | 15.2 ms | 16.2 ms |
| 首次有用列表，正常动效，p50 | 535.0 ms | 313.3 ms |
| 热跳转内容可见，正常动效，p50 | 12.2 ms | 48.7 ms |
| 热跳转内容可见，正常动效，p95 | 14.9 ms | 50.1 ms |
| 每个上下文首次数据 GET 数量 | 2 | 2（并行） |
| 五次工作区热跳转新增数据 GET | 0 | 0 |

原有本地工作区切换已经很快；正常 View Transition 的快照准备增加了约 35 ms，换来连续视觉，未假称所有导航指标均变快。减少动态效果时保持约一帧。主要改进来自首次数据串行链被移除。

安全预取缓存：后测五个上下文每个实际记录一次缓存命中、两次 miss，热跳转不增加 API 请求。Angular 的实际生产构建浏览器测试在点击前预取商业数据，两次访问仅一次 client-service GET，刷新后再请求一次，草稿保持；最近一次完整点击到已加载内容的浏览器操作计时约 75 ms（包括自动化点击过程，不能与上表 DOM 计时直接配对）。无该部分匹配的前测，不声称其前后提升比例。

真实 loopback HTTP 验证静态文件条件重复访问返回 304/空体，API 无缓存且令牌鉴权有效。隔离 aiohttp 服务验证两个并发管理接口只做一次代理探测，完成后的新请求重新探测；匿名读取在任何探测开始前返回 401。

原始指标：`navigation-before.json`, `navigation-after.json`, `navigation-before-motion.json`, `navigation-after-motion.json`，汇总 `navigation-metrics-summary.json`。这些是人为延迟 fixture，不能当作线上 SLA；五次冷样本的 p95 也不是统计置信结论。

## 5. 检查结果

| 检查 | 结果 |
| --- | --- |
| `npm run lint:navigation` | 通过：变更 JS 语法、全应用 TypeScript、共享源格式。仓库没有现成 ESLint 配置，不宣称全仓库 ESLint 通过 |
| 订阅仪表盘/档案 API/客户基础 Python 回归 | 52 passed |
| 新导航/协作台 API/在线客户端/保存订阅 Python 回归 | 42 passed，含新请求共享/取消/缓存/鉴权用例 |
| Angular `npm test -- --watch=false` | 2 passed；测试 DOM 环境有 canvas 未实现提示，无失败 |
| 5 组浏览器 fixture | 通过：管理页、仪表盘、超时恢复、Framework7、导航内核与真实 Angular 构建 |
| Skill quick_validate（UTF-8 模式） | 通过 |
| Angular production build | 通过；初始包约 1.02 MB，1 MB warning budget 超约 22 kB，低于 1.5 MB error budget |
| Python wheel | 构建通过；检查包含共享导航 JS/CSS 和 navigation_reads 模块 |

浏览器覆盖前进/后退、查询深链/刷新、滚动坐标刷新恢复、草稿、修饰键、Save-Data、去重、epoch 失效、显示专用 SWR、缺失/失败动画 API、80 ms 兜底与快速点击晚回调、缺失导航资产回退、no-JS 普通链接、源网不可用、重复 Start、超时 POST 不重放、旧数据锁定以及登出期间过期 snapshot 丢弃。原有管理测试覆盖 1440/1024/390px、30 客户长文本和地址窗口。测试等待实际可见的新状态，Framework7 确认按钮限定当前 modal-in，避免匹配已关闭但尚在退场的旧弹窗。

初次验证曾因磁盘空间/默认临时目录失败；改用项目内独立测试临时目录后回归通过。Python `-m build` 不可直接用（环境模块不可执行），改用 pip wheel，无需新增依赖。

## 6. 本次直接改动文件清单

新增：

- `skills/smooth-navigation/SKILL.md`
- `skills/smooth-navigation/references/acceptance.md`
- `src/server_network_assist/subscription_admin_ui/smooth-navigation.js`
- `src/server_network_assist/subscription_admin_ui/smooth-navigation.css`
- `src/server_network_assist/navigation_reads.py`
- `frontend/src/app/smooth-navigation.ts`
- `frontend/prepare-navigation.cjs`
- `frontend/lint-navigation.cjs`
- `scripts/package_smooth_navigation.py`
- `scripts/measure_smooth_navigation.cjs`
- `tests/test_smooth_navigation.py`
- `tests/smooth_navigation_browser.cjs`
- `docs/SMOOTH_NAVIGATION_IMPLEMENTATION.md`
- `借网管理面板/handoffs/2026-09-18-smooth-navigation.md`

修改：

- `frontend/package.json`（预构建资产同步与限定 lint）
- `frontend/src/index.html`（共用脚本/样式、no-JS 提示）
- `frontend/src/app/app.ts`, `app.html`（统一选页、真实链接、历史/焦点、保留商业组件）
- `frontend/src/app/api.service.ts`（安全 GET 预取/去重、写前后失效）
- `frontend/src/app/commercial.component.ts`（隐藏读消费退出、再次激活读取、草稿与忙碌保护）
- `frontend/src/styles.scss`（链接沿用菜单样式）
- `src/server_network_assist/app.py`（并发只读探测/离线阻塞、静态缓存、计时）
- `src/server_network_assist/client.py`（共享资产路由、静态验证器、查询深链）
- `src/server_network_assist/client_ui/client.js`, `index.html`（设置导航、强制新鲜读、标签令牌刷新）
- `src/server_network_assist/subscription_admin_ui/subscriptions.js`, `subscriptions.html`, `dashboard.js`（导航、并行加载、预载 snapshot）
- `tests/subscription_admin_ui_browser.cjs`, `subscription_request_recovery_browser.cjs`, `client_simple_ui_browser.cjs`（真实导航资产与状态等待）
- `借网管理面板/requirements_traceability.md`, `test_reference.md`（对应需求/验收记录）

生成/同步：`frontend/public/smooth-navigation.*`、`frontend/dist/frontend/browser/`，以及 `src/server_network_assist/ui/` 中更新的 Angular index/main/styles、订阅 HTML/JS/dashboard 和共享导航文件。增量打包保留旧资源，没有清理其他工作的输出。原 `frontend/angular.json` 的试验性改动已移除。没有改动 Astro 博客、其他 Next 客户原型或网络出口/计费功能。

## 7. 剩余瓶颈和边界

- Angular 初始包仍有终端、浏览器和代理等既有模块，约 1.02 MB raw/218 kB estimated transfer，仍超过 warning budget；后续如需进一步下降，应单独评估模块懒加载。
- 实时代理就绪、源网可用性和客户计量依赖探测/数据库/真实网络，缓存不能掩盖失败或放宽授权。高客户量分页/轻量详情接口属于额外契约设计，本次未更改。
- 界面工作区热切换本身原来已接近一帧；跨文档返回协作台仍需要浏览器正常下载/启动，其过渡效果取决于浏览器支持，不等于零网络耗时。
- 无 JS 可读 shell 和普通文档链接可用；实时认证/订阅表单没有新增 SSR 业务端点，仍需要 JavaScript。原有公共页面 SEO 内容和 metadata 未修改。
- 未部署、未进行线上真实延迟/浏览器矩阵、Windows EXE 重新发行或真实客户借网/支付验证。现有测试和构建不能替代这些验收。

参考：框架的 [Angular View Transitions](https://angular.dev/guide/animations/route-animations)、浏览器 [同文档 View Transitions](https://developer.chrome.com/docs/web-platform/view-transitions/same-document)、[aiohttp 响应与 FileResponse](https://docs.aiohttp.org/en/stable/web_reference.html)。具体实现采用上述机制的现有框架等价方案，并以本地测试而非文档作为结果证据。
