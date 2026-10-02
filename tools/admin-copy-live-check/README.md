# admin-copy-live-check

线上（部署后）验证订阅管理面板的**「复制地址」按钮在真实环境下真的把完整地址写进剪贴板**。

## 为什么需要它

管理面板有三个入口，其中两个是**明文 HTTP**：

| 入口 | 地址 | 上下文 |
|---|---|---|
| 校园直连 | `http://10.20.32.13:9182/subscriptions.html` | 非安全上下文 |
| WireGuard | `http://10.201.250.1:9180/subscription-admin/subscriptions.html` | 非安全上下文 |
| 公网 | `https://whm12.art/subscription-admin/subscriptions.html` | 安全上下文 |

`navigator.clipboard` **只在安全上下文存在**（HTTPS 或 localhost）。在 9182 / 9180 上它是 `undefined`，
所以任何"只调用 `navigator.clipboard.writeText`"的实现在这两个入口上**什么都不会发生**——
旧版本正是如此：只提示"请手动复制"，从未真正碰过剪贴板。
`document.execCommand('copy')` 在非安全上下文仍然可用（`queryCommandSupported('copy') === true`）。

一个只在 localhost 上跑过的浏览器测试**抓不到这类缺陷**，因为 localhost 是安全上下文。
本工具跑的三个入口里有两个是明文 HTTP，因此能真实复现该条件。

## 用法

```bash
node tools/admin-copy-live-check/check-copy.cjs                       # 三个生产入口
node tools/admin-copy-live-check/check-copy.cjs http://10.20.32.13:9182/subscriptions.html
```

参数会替换默认入口列表；`secure` 由 URL 协议自动推断（`https:` 即安全上下文）。

需要 `frontend/node_modules/playwright`（与 `tests/*.cjs` 同一依赖）。

## 测什么

用一段**合成的**地址（472 字符，不含任何真实开户令牌）：

1. 打印 `isSecureContext` 与 `typeof navigator.clipboard`，确认当前上下文符合预期；
2. 同时挂上两条捕获：包装 `navigator.clipboard.writeText`，并监听 `execCommand` 触发的 `copy` 事件，
   记录**尝试顺序**（`attempts`）与**最终路径**（`terminal path`）；
3. 点击页面**自己的** `#copy` 按钮；
4. 断言：剪贴板文本 === 完整合成地址、`#message` === `订阅地址已复制。`、输入框未被改动、无页面错误；
5. 安全上下文额外要求**优先尝试** `clipboard.writeText`（被拒后回退 `execCommand` 属正确行为，不算失败）；
6. 第二轮：给安全上下文**授予剪贴板权限**再跑一次，确认主路径本身可用，并**回读操作系统剪贴板**比对。

## 两个实现要点

- **不碰管理员凭据。** `#address-dialog` 是 `#workspace` 的**兄弟节点**，不在登录门之后。
  直接 `showModal()` 打开它，就能像真实用户那样点到 `#copy`，全程无需任何管理员密码。
  （仅断言 `dialog.open` 是不够的——曾经 Tabler 的 `.modal{display:none}` 让 `open === true` 但实际 0×0，
  所以这里另外打印按钮的 `getBoundingClientRect()` 尺寸。）
- **两条路径都要捕获。** 只监听 `copy` 事件会在安全上下文漏掉 `writeText`；
  只包装 `writeText` 会在明文入口漏掉 `execCommand`。

## 期望结果

```
=== campus-9182 ===      terminal path: execCommand             472/472 MATCH
=== wg-9180 ===          terminal path: execCommand             472/472 MATCH
=== public-tls ===       attempts: clipboard.writeText -> writeText-REJECTED: NotAllowedError -> execCommand
                         terminal path: execCommand             472/472 MATCH
=== public-tls (clipboard permission GRANTED) ===
                         terminal path: clipboard.writeText ✅  OS clipboard readback matches (472 chars)
=== RESULT: OK ===
```

第三段那条 `writeText-REJECTED -> execCommand` 是**兜底链在真实页面上跑通**的证据：
无头环境没有剪贴板权限，`writeText` 被拒，回退接管并成功。修复前这里没有回退，结果就是"什么都没复制"。

退出码非 0 即表示有空缺。

## 相关记录

- `借网管理面板/error_traceability.md` — ERR-NET-043（明文 HTTP 下复制按钮失效）
- `借网管理面板/requirements_traceability.md` — REQ-NET-064
- `tests/subscription_admin_ui_browser.cjs` — 离线回归夹具（不联网，断言非安全上下文下的复制）
