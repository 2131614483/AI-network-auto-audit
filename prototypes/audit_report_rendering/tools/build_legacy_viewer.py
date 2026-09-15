"""Build the JSON viewer from the existing deterministic DAG page template.

The source renderer remains untouched.  This builder creates an isolated viewer
whose stage styling, layout, cards, controls and animation runtime are byte-for-
byte inherited from its template; the only additions are a local JSON picker and
the bundle-to-display bootstrap.
"""
from __future__ import annotations

import html
import importlib.util
import json
from pathlib import Path
from typing import Any

PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROTOTYPE_ROOT.parents[1]
FLOW_BUILDER = REPOSITORY_ROOT / "审计项目案例" / "_flow_render_builder" / "build_dag_pages.py"
OUTPUT = PROTOTYPE_ROOT / "viewer" / "index.html"


def _load_legacy_builder() -> Any:
    spec = importlib.util.spec_from_file_location("legacy_case_dag_viewer", FLOW_BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法读取既有页面模板：{FLOW_BUILDER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_page_payload(specification: dict[str, Any]) -> dict[str, Any]:
    """Mirror the edge normalization performed by the existing HTML generator."""
    payload = json.loads(json.dumps(specification, ensure_ascii=False))
    payload["nodes"] = [
        dict(node, kpis=[list(kpi) for kpi in node.get("kpis", [])], rows=[list(row) for row in node.get("rows", [])])
        for node in payload["nodes"]
    ]
    payload["edges"] = [
        {"from": edge["from_"] if "from_" in edge else edge["from"], "to": edge["to"], "label": edge["label"], "virtual": edge.get("virtual", False)}
        for edge in payload["edges"]
    ]
    payload["stats"] = [list(stat) for stat in payload["stats"]]
    payload["generated"] = "2026-09-15T00:00:00"
    return payload


def _preload_script(default_dag: dict[str, Any]) -> str:
    default_json = json.dumps(default_dag, ensure_ascii=False, separators=(",", ":"))
    return f"""window.__AUDIT_RENDER_BUNDLE_KEY = "audit_report_render_bundle_selected_v1";
(function () {{
  var fallback = {default_json};
  var bundle = null;
  try {{
    var saved = localStorage.getItem(window.__AUDIT_RENDER_BUNDLE_KEY);
    if (saved) {{
      var candidate = JSON.parse(saved);
      if (candidate && candidate.contract_id === "audit-report-render-bundle" &&
          candidate.contract_version === "1.0.0" && candidate.presentation &&
          candidate.presentation.renderer === "legacy-dag@1.0.0" &&
          candidate.presentation.legacy_dag) {{
        /* Compatibility with the first migration snapshot, whose internal
           builder spelling was from_ rather than the page payload's from. */
        candidate.presentation.legacy_dag.edges = (candidate.presentation.legacy_dag.edges || []).map(function (edge) {{
          if (edge.from || !edge.from_) return edge;
          var upgraded = {{}};
          Object.keys(edge).forEach(function (key) {{ if (key !== "from_") upgraded[key] = edge[key]; }});
          upgraded.from = edge.from_;
          return upgraded;
        }});
        bundle = candidate;
      }}
    }}
  }} catch (error) {{ localStorage.removeItem(window.__AUDIT_RENDER_BUNDLE_KEY); }}
  window.__AUDIT_NORMALIZE_DAG = function (dag) {{
    if (!dag || !Array.isArray(dag.edges)) return dag;
    if (!dag.generated) dag.generated = "2026-09-15T00:00:00";
    dag.edges = dag.edges.map(function (edge) {{
      if (edge.from || !edge.from_) return edge;
      var upgraded = {{}};
      Object.keys(edge).forEach(function (key) {{ if (key !== "from_") upgraded[key] = edge[key]; }});
      upgraded.from = edge.from_;
      return upgraded;
    }});
    return dag;
  }};
  window.__AUDIT_RENDER_BUNDLE = bundle;
  window.__DAG = window.__AUDIT_NORMALIZE_DAG(bundle ? bundle.presentation.legacy_dag : fallback);
  var fileName = bundle ? bundle.report.id + ".render.json" : "内置示例（基础版）";
  document.getElementById("bundleState").textContent = "当前：" + fileName;
  document.getElementById("viewerTitle").textContent = window.__DAG.title;
  document.getElementById("sub").textContent = window.__DAG.sub;
}})();"""


def _importer_script() -> str:
    return """(function () {
  var picker = document.getElementById("bundleFile");
  var state = document.getElementById("bundleState");
  picker.addEventListener("change", function () {
    var file = picker.files && picker.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var bundle = JSON.parse(String(reader.result));
        var dag = bundle && bundle.presentation && bundle.presentation.legacy_dag;
        if (!bundle || bundle.contract_id !== "audit-report-render-bundle" ||
            bundle.contract_version !== "1.0.0" || !dag ||
            bundle.presentation.renderer !== "legacy-dag@1.0.0" ||
            !Array.isArray(dag.nodes) || !Array.isArray(dag.edges)) {
          throw new Error("请选择包含 legacy-dag 展示快照的 audit-report-render-bundle@1.0.0 文件");
        }
        localStorage.removeItem("audit_dag_draft_" + dag.plan);
        localStorage.setItem(window.__AUDIT_RENDER_BUNDLE_KEY, JSON.stringify(bundle));
        window.location.reload();
      } catch (error) {
        state.textContent = "导入失败：" + error.message;
        picker.value = "";
      }
    };
    reader.readAsText(file, "utf-8");
  });
  document.getElementById("bundleReset").addEventListener("click", function () {
    localStorage.removeItem(window.__AUDIT_RENDER_BUNDLE_KEY);
    window.location.reload();
  });
})();"""


def _zoom_script() -> str:
    return """(function () {
  var stage = document.getElementById("stage");
  var stageWrap = document.querySelector(".stage-wrap");
  var label = document.getElementById("zoomLabel");
  var zoom = 1;
  function apply(next) {
    zoom = Math.max(0.5, Math.min(1.8, Math.round(next * 100) / 100));
    stage.style.zoom = String(zoom);
    label.textContent = Math.round(zoom * 100) + "%";
  }
  document.getElementById("btnZoomOut").addEventListener("click", function () { apply(zoom - 0.1); });
  document.getElementById("btnZoomIn").addEventListener("click", function () { apply(zoom + 0.1); });
  document.getElementById("btnZoomReset").addEventListener("click", function () { apply(1); });
  stageWrap.addEventListener("wheel", function (event) {
    if (!event.ctrlKey) return;
    event.preventDefault();
    apply(zoom + (event.deltaY < 0 ? 0.1 : -0.1));
  }, { passive: false });
  window.__AUDIT_RENDER_ZOOM = { get: function () { return zoom; }, set: apply };
})();"""


def build_viewer() -> str:
    builder = _load_legacy_builder()
    default_dag = _legacy_page_payload(builder.spec_base())
    page = builder.TEMPLATE
    page = page.replace("@@TITLE@@", html.escape(default_dag["title"]))
    page = page.replace("@@SUB@@", html.escape(default_dag["sub"]))
    page = page.replace("<h1>" + html.escape(default_dag["title"]) + "</h1>", '<h1 id="viewerTitle"></h1>')
    picker = """
    <label class=\"bundle-picker\">导入渲染 JSON<input id=\"bundleFile\" type=\"file\" accept=\"application/json,.json\" /></label>
    <button class=\"bundle-reset\" id=\"bundleReset\" type=\"button\">恢复示例</button>
    <span class=\"bundle-state\" id=\"bundleState\"></span>"""
    page = page.replace('<span class="sub" id="sub">' + html.escape(default_dag["sub"]) + "</span>", '<span class="sub" id="sub"></span>' + picker)
    page = page.replace(
        "</style>",
        """  .bundle-picker, .bundle-reset { background: #12233a; color: #cfe0f5; border: 1px solid #23476e;
    border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 11px; white-space: nowrap; }
  .bundle-picker:hover, .bundle-reset:hover { background: #1a3f66; }
  .bundle-picker input { display: none; }
  .bundle-state { color: #6b7a90; font: 10.5px Consolas, monospace; white-space: nowrap; }
</style>""",
        1,
    )
    page = page.replace("window.__DAG = @@DAG_JSON@@;", _preload_script(default_dag), 1)
    page = page.replace(
        "  } catch (e) {}\n})();\n</script>\n<script>\n(() => {",
        "  } catch (e) {}\n})();\n</script>\n<script>\nwindow.__DAG = window.__AUDIT_NORMALIZE_DAG(window.__DAG);\n</script>\n<script>\n(() => {",
        1,
    )
    page = page.replace(
        '<button id="btnRestart">↺ 重播</button>',
        '<button id="btnRestart">↺ 重播</button>\n    <button id="btnZoomOut" title="缩小画布">− 缩小</button>\n    <button id="btnZoomReset" title="重置画布缩放">100%</button>\n    <button id="btnZoomIn" title="放大画布">＋ 放大</button>\n    <span id="zoomLabel" style="font-family:Consolas,monospace;color:#8b97a8">100%</span>',
        1,
    )
    page = page.replace(
        "</body>",
        "<script>" + _importer_script() + "</script>\n<script>" + _zoom_script() + "</script>\n</body>",
        1,
    )
    return page


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_viewer(), encoding="utf-8")
    print(f"已生成：{OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
