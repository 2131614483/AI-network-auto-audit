from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PAGES = ROOT / "desktop" / "src" / "components" / "pages"


def read_pages(*names: str) -> str:
    """读取「信息架构 v2」下按页拆分的渲染器源码。

    工作台内容已从单体 ``App.tsx`` 迁到 ``components/pages/*.tsx``，UI 契约必须跟着
    拆分后的文件走——这里拼接同组页面，契约本身（控件存在、文案存在）不打折。
    """

    return "\n".join((PAGES / name).read_text(encoding="utf-8") for name in names)


def test_desktop_shell_has_a_secure_electron_entrypoint() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    preload = (ROOT / "desktop" / "electron" / "preload.ts").read_text(encoding="utf-8")

    assert "BrowserWindow" in main
    assert "contextIsolation: true" in main
    assert "contextBridge.exposeInMainWorld" in preload
    assert "auditControl" in preload
    assert "normaliseIpcError" in preload
    assert "AUDIT_NETWORK_CAPTURE_DELAY_MS" in main
    assert "Math.min(30_000, Math.max(500" in main
    assert "function readableControlPlaneError" in main
    assert "策略网关尚未允许此操作" in main


def test_desktop_shell_exposes_only_enumerated_window_chrome_controls() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    preload = (ROOT / "desktop" / "electron" / "preload.ts").read_text(encoding="utf-8")
    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert 'type WindowControlAction = "minimize" | "toggle-maximize" | "close" | "query"' in main
    assert 'ipcMain.handle("audit:windowControl"' in main
    assert "windowControl" in preload
    assert "最小化窗口" in app
    assert "最大化窗口" in app
    assert "关闭窗口" in app


def test_desktop_wheel_never_triggers_global_zoom_and_window_starts_maximized() -> None:
    """滚轮只滚动内容：主进程禁用 visual-zoom 钩子，渲染器拦截 Ctrl+滚轮缩放。"""
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    renderer_entry = (ROOT / "desktop" / "src" / "main.tsx").read_text(encoding="utf-8")

    # setVisualZoomLevelLimits(1,1) 会导致滚轮完全无法滚动页面（Electron 已知问题），必须禁用
    assert "setVisualZoomLevelLimits" not in main
    assert "zoom-changed" not in main
    assert "MIN_ZOOM_FACTOR = 0.8" in main
    assert "window.maximize()" in main
    # 渲染器仅在 Ctrl 按下时拦截 wheel（捏合缩放），普通滚轮滚动不受影响
    assert 'if (event.ctrlKey) event.preventDefault();' in renderer_entry
    assert "{ passive: false }" in renderer_entry


def test_desktop_renderer_uses_component_library_and_stock_terminal_tokens() -> None:
    package = (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "desktop" / "src" / "styles" / "globals.css").read_text(encoding="utf-8")

    assert '"antd"' in package
    assert 'from "antd"' in app
    assert "--bg: #0d0e10" in css
    assert "--up: #f5222d" in css
    assert "--down: #14b143" in css
    assert ".index-bar" in css
    assert ".sidebar" in css
    # .ant-app 是 antd <App> 插入的包装 div，不补高度会打断 height:100% 链，
    # 导致 .content 滚动容器失效（滚轮滚不动长页面）。
    assert ".ant-app { height: 100%; }" in css


def test_desktop_knowledge_workbench_exposes_real_management_and_retrieval() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    app = read_pages(
        "KnowledgePage.tsx",
        "KnowledgeSearchPage.tsx",
        "KnowledgePendingPage.tsx",
        "KnowledgeRecyclePage.tsx",
    )

    for path in (
        "/api/v1/knowledge/stats",
        "/api/v1/knowledge/documents",
        "/api/v1/knowledge/adapters",
        "/api/v1/knowledge/batches",
        "/api/v1/knowledge/search",
        "/api/v1/knowledge/embed",
        "/api/v1/knowledge/recycle-bin",
        "/api/v1/knowledge/rich-media/pending",
        "/api/v1/knowledge/rich-media/extract",
        "/api/v1/knowledge/rich-media/retry",
    ):
        assert path in main
    assert "文档管理" in app
    assert "处理批次" in app
    assert "混合检索" in app
    assert "生成下一批向量" in app
    assert "知识回收站" in app
    assert "移入回收站" in app
    assert "恢复文档" in app
    assert "本地适配器" in app
    assert "等待本地运行时" in app
    assert "本地解析队列" in app
    assert "提交队列" in app
    assert "重新提交" in app


def test_desktop_has_cross_domain_operations_workbenches() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "/api/v1/ui/operations" in main
    for label in ("任务编排", "图谱总览", "审计工作台", "量化工作台", "AIOps 工作台"):
        assert label in app


def test_desktop_aiops_governance_exposes_only_lineage_and_human_canary_review() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "/api/v1/aiops/incidents" in main
    assert "aiops\\/incidents\\/[0-9a-f-]{36}\\/lineage" in main
    assert "aiops\\/executions\\/[0-9a-f-]{36}\\/verify" in main
    assert "事故模拟证据链" in app
    assert "AIOps 事故" in app
    assert "核验通过" in app
    assert "记录模拟回滚" in app
    assert "不连接基础设施" in app


def test_desktop_graph_workbench_uses_bounded_force_visualization() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")
    chart = (ROOT / "desktop" / "src" / "components" / "GraphExplorer.tsx").read_text(encoding="utf-8")
    assert "/api/v1/graph/visualization" in main
    assert "GraphExplorer" in app
    assert "type: \"graph\"" in chart
    # 稠密图仍走受预算约束的 force 布局；稀疏图（≤ SPARSE_LAYOUT_MAX_NODES）改用显式
    # 坐标 —— force 在 1–2 个节点时会塌成画布正中一小坨，页面看起来像空的。
    assert 'layout: sparseTopology ? "none" : "force"' in chart
    assert "sparseLayoutPositions" in chart
    assert "focus: \"adjacency\"" in chart


def test_desktop_graph_workbench_shows_multigraph_governance_without_direct_execution() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "desktop" / "src" / "styles" / "globals.css").read_text(encoding="utf-8")

    for path in ("/api/v1/graph/routes", "/api/v1/graph/governance", "/api/v1/graph/conflicts"):
        assert path in main
    assert "多级图谱治理" in app
    assert "受预算路由" in app
    assert "冲突收件箱" in app
    assert "版本历史" in app
    assert ".graph-governance-grid" in css


def test_desktop_graph_extraction_proposals_use_only_exact_declared_paths() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    app = read_pages("GraphExtractPage.tsx")

    for path in (
        "/api/v1/graph/extractions/preview",
        "/api/v1/graph/extractions/propose",
        "/api/v1/graph/extractions/proposals",
    ):
        assert path in main
    assert "extractions\\/proposals\\/[0-9a-f-]{36}" in main
    assert "从最近文档生成提案" in app
    assert ">放行</Button>" in app
    assert ">驳回</Button>" in app


def test_desktop_plugin_topology_workbench_is_plan_only_and_uses_multi_axis_catalog() -> None:
    app = read_pages("PluginsPage.tsx")
    topology = (ROOT / "desktop" / "src" / "model" / "pluginTopology.ts").read_text(encoding="utf-8")

    assert "插件拓扑工作台" in app
    assert "生成不可执行计划" in app
    assert "topologyGraphForAxis" in app
    assert "createTopologyPlan" in app
    assert 'mode: "plan_only"' in topology
    assert "business_domain" in topology
    assert "capability_family" in topology
    assert "runtime_pool" in topology
    assert "governance_zone" in topology


def test_desktop_uses_one_bounded_scroll_surface_for_every_workspace_view() -> None:
    """All views render inside ``.content``, so a single wheel surface is sufficient.

    信息架构 v2 起总览页的 view key 由 ``overview`` 改为 ``hub``，其余 key 沿用。
    """

    app = (ROOT / "desktop" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "desktop" / "src" / "styles" / "globals.css").read_text(encoding="utf-8")

    for view in ("hub", "operations", "knowledge", "graph", "plugins", "audit", "quant", "aiops", "approvals", "policy"):
        assert f'"{view}"' in app
    assert "<Layout.Content className=\"content\">" in app
    assert ".app-shell { height: 100%;" in css
    assert ".app-shell > .ant-layout { flex: 1 1 auto; min-height: 0; overflow: hidden; }" in css
    assert ".content { flex: 1 1 auto; min-width: 0; min-height: 0; overflow: auto;" in css
    assert "overscroll-behavior: contain" in css


def test_desktop_knowledge_folder_import_keeps_relative_paths_inside_main_process() -> None:
    main = (ROOT / "desktop" / "electron" / "main.ts").read_text(encoding="utf-8")
    preload = (ROOT / "desktop" / "electron" / "preload.ts").read_text(encoding="utf-8")
    app = read_pages("KnowledgePage.tsx")

    assert "openDirectory" in main
    assert "knowledgeSelections" in main
    assert "relativePath" in main
    assert "MAX_KNOWLEDGE_SELECTION_FILES" in main
    assert "isAllowedControlPlanePath" in main
    assert "documents\\/[0-9a-f-]{36}\\/retire" in main
    assert "recycle-bin\\/[0-9a-f-]{36}\\/restore" in main
    assert "selectKnowledgeFolder" in preload
    assert "selectKnowledgeFolder" in app
    assert "选择文件夹" in app
