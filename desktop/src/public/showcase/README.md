# showcase · 主控台内嵌的可视化视图

这些页面原先各自是一个**独立 Electron 窗口**（`electron/main.ts` 的
`createAuditBrainWindow` / `createKnowledgeNebulaWindow` / `createFlowCanvasWindow`）。
现在它们以 iframe 承载在主控台的「可视化与演示」导航组里 —— **一个窗口、一个入口**。

| 文件 | 数据来源 | 说明 |
|---|---|---|
| `audit-brain.html` | 零后端 | 插件树状组网 + 判断分支 + 数据接口流 |
| `flow-canvas-demo.html` + `.js` | 零后端 | ComfyUI 式数据流播放演示 |
| `flow-canvas-audit/` | **真实数据** | `flow-canvas-audit.data.js` 由同目录 `build_data.py` 从 UCI `audit_risk.csv`（776×27）生成，页面逐行确定性重算 |
| `flow-canvas-audit-pro/` | — | **只有数据生成器，没有页面**（`flow-canvas-audit-pro.data.js` 当前无任何引用）。保留为数据源 |
| `plugin-flow-showcase.html` | 零后端 | 插件数据流全景展示 |

`knowledge-nebula.html`（知识星云）**不在本目录** —— 它是承接真实接口的功能视图，
留在 `public/` 根目录，但挂在同一个导航组里。

## 改动须知（三条都在代码里有对应说明）

1. **这是静态资源，不是 Python 包。** `electron-vite` 把 `src/public/**` 原样复制到
   `out/renderer/**`。目录里的 `build_data.py` / `_*.py` 是一次性数据生成脚本，
   因此 `pyproject.toml` 的 ruff `exclude` 包含本目录（保证 `ruff check .` 与 CI 口径一致）。
2. **iframe 里没有 preload**，`window.auditControl` 不存在。需要后端数据的视图要经
   postMessage 借用父窗口：桥的父侧在 `App.tsx`（`audit-network-embed`），
   `knowledge-nebula.html` 里有对应的 shim。**不要**为此打开
   `nodeIntegrationInSubFrames` —— 那会把 preload 注入每个子框架。
3. **原来的独立窗口函数与截图链路仍然保留**在 `electron/main.ts`
   （`AUDIT_NETWORK_OPEN_*` / `AUDIT_NETWORK_CAPTURE_*` 自动化截图依赖它们），
   只是主控台不再把"开新窗口"当作默认入口。

## iframe 路径约定

`App.tsx` 的 `SHOWCASE_VIEWS` 一律用**相对路径**（`./showcase/xxx.html`）：
开发态（`ELECTRON_RENDERER_URL`）与生产态（`file://.../renderer/index.html`）都能解析，
也天然排除了远程地址。`renderer/index.html` 中**不得**加入 `<base>` 标签，否则会破坏该约定。
