from __future__ import annotations

import importlib.util

from test_flow_asset_migration import PROTOTYPE_ROOT


def _load_viewer_builder():
    spec = importlib.util.spec_from_file_location(
        "audit_report_legacy_viewer", PROTOTYPE_ROOT / "tools" / "build_legacy_viewer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_viewer_is_built_from_the_legacy_runtime_and_accepts_display_snapshots() -> None:
    viewer_builder = _load_viewer_builder()
    viewer = viewer_builder.build_viewer()
    legacy_template = viewer_builder._load_legacy_builder().TEMPLATE
    legacy_runtime = legacy_template[legacy_template.index("<script>\n(() => {") : legacy_template.index("</body>")]

    assert "window.__AUDIT_RENDER_BUNDLE_KEY" in viewer
    assert "legacy-dag@1.0.0" in viewer
    assert "Compatibility with the first migration snapshot" in viewer
    assert 'if (!dag.generated) dag.generated = "2026-09-15T00:00:00";' in viewer
    assert "window.__DAG = window.__AUDIT_NORMALIZE_DAG(window.__DAG);" in viewer
    assert 'id="bundleFile"' in viewer
    assert "requestAnimationFrame(render)" in viewer
    assert 'id="btnPlay"' in viewer
    assert 'id="btnStep"' in viewer
    assert 'id="btnZoomIn"' in viewer
    assert "stage.style.zoom" in viewer
    assert 'id="stage"' in viewer
    assert 'id="panel"' in viewer
    assert "数据正在端口间传递（数据包）" in viewer
    assert legacy_runtime in viewer
