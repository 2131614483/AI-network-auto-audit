# 审计报告 JSON 渲染原型

这是一个隔离试点，不修改现有审计报告、桌面画布、插件目录或运行时拓扑。

目标是把每份报告的“来源 → 审计程序 → 发现/排除 → 结论”导出为一个可校验、可追溯、可离线导入的 JSON 文件。通用查看器只读取该 JSON；后续展示不再为每份报告编写专用网页。

## 当前试点

- 契约：`schemas/audit-report-render-bundle.schema.json`
- 导出器：`tools/build_bundle.py`
- 首个对象：黔岭酒业高难度案例（只读原有 `audit_evidence.json` 与客户资料目录）
- 通用查看器：`viewer/index.html`

生成首个 bundle：

```text
python prototypes/audit_report_rendering/tools/build_bundle.py
```

生成结果写入本原型目录的 `fixtures/`。查看器可直接打开，或作为本机静态页访问；选择该 JSON 即可查看。

## 已有资产的只读迁移

`tools/migrate_flow_assets.py` 会读取 `审计项目案例/_flow_render_builder/build_dag_pages.py` 中已经验收的三份 DAG 规格，输出到 `fixtures/migrated/`：

- `qianling-2025-base.render.json`
- `qianling-2025-experiment.render.json`
- `qianling-2025-hard.render.json`

迁移保留既有的节点、连线、层级、虚拟工件池和报告引用；每个 bundle 还带有 `presentation.legacy_dag` 展示快照，完整保留原页面的节点证据卡、KPI、统计条和动画时间线所需数据。每个 bundle 同时记录原构建规格、Markdown、证据工件和源文件清单的 SHA-256。它不反向写入 `_flow_render/`，因此原截图、交互页和报告均保持原状。

## 统一查看器

`tools/build_legacy_viewer.py` 从既有确定性页面模板生成 `viewer/index.html`。因此，查看器与原页面使用同一套深色布局、分层 SVG、数据包沿边动画、播放/单步/时间轴、节点状态、证据卡和“AI 全自动 / 人修改 + AI 微调”工作台；它只新增顶部的“导入渲染 JSON”和“恢复示例”控件。

```text
python prototypes/audit_report_rendering/tools/build_legacy_viewer.py
python -m http.server 4173 --bind 127.0.0.1 --directory prototypes/audit_report_rendering
```

然后打开 `http://127.0.0.1:4173/viewer/`，导入 `fixtures/migrated/` 中任一文件。导入内容仅保存在浏览器本地存储；“恢复示例”会清除所选 bundle 并回到基础版示例。

根目录的 `fixtures/qianling-2025-hard.render.json` 是最初的“证据优先”单案样例；需要与既有三页渲染资产一一对拍时，应优先导入 `fixtures/migrated/` 下的三个文件。

## 设计边界

- bundle 是历史报告的展示快照，不是 `TopologyRelease`，不会发布、修改或执行任何插件。
- 原始资料不被嵌入 bundle；只记录相对路径、字节数和 SHA-256。
- bundle 记录报告、证据 JSON 与源文件清单的哈希。报告或证据变化后必须重新生成。
- 当前查看器是本地静态页面；若未来接入桌面/API，读取必须经过 Policy Gateway 并携带租户与 Trace ID。
