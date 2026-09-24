# legacy-pages · 单文件演示页的历史版本（已归档）

这些是可视化演示页在**产品化之前的源文件与历史版本**。它们曾经散落在仓库根目录
（`audit-brain/`、`plugin-flow-showcase/`），使"到底哪一份是正解"变得含糊。

| 归档文件 | 原位置 | 与产品的关系 |
|---|---|---|
| `audit-brain-src.html` | `audit-brain/index.html` | 与 `desktop/src/public/showcase/audit-brain.html` **逐字节相同**（61344 字节，`cmp` 验证通过）。产品加载的是 `desktop/src/public/` 那一份 |
| `audit-brain-v1.html` | `audit-brain/_backup/index-v1.html` | 更早的版本（37 KB），仅存档 |
| `plugin-flow-showcase-v1.html` | `plugin-flow-showcase/_backup/index-v1.html` | 更早的版本（36 KB），仅存档 |

`plugin-flow-showcase/index.html` 本身**不是重复**，已移入产品：
`desktop/src/public/showcase/plugin-flow-showcase.html`。

## 与产品的关系

两处产品级入口（都挂在主控台「可视化与演示」导航组，单窗口内以 iframe 承载）：

- `desktop/src/public/showcase/audit-brain.html`
- `desktop/src/public/showcase/plugin-flow-showcase.html`

本目录只做存档，**不参与构建、不被引用**。
