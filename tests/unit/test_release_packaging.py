"""The distributable must not inherit developer paths or replace old artifacts."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def packaging():
    script = Path(__file__).resolve().parents[2] / "scripts/package-windows.py"
    spec = importlib.util.spec_from_file_location("release_packaging", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_developer_path_hooks_and_private_config_are_excluded(packaging):
    names = ["__editable__.audit.pth", "__editable___audit_finder.py", "_virtualenv.pth",
             ".env", ".env.bak", "__pycache__", "runtime.py", "LICENSE", ".env.example"]
    excluded = packaging._ignore("fixture", names)
    assert set(names[:6]) <= excluded
    assert not {"runtime.py", "LICENSE", ".env.example"} & excluded


def test_packaged_resources_include_license_and_dependency_inventory(packaging):
    assert {"LICENSE", "requirements.txt", "uv.lock"} <= set(packaging.APP_FILES)


def test_existing_stage_is_preserved(packaging, tmp_path):
    directory = tmp_path / "app"
    directory.mkdir()
    marker = directory / "previous-release.txt"
    marker.write_text("retained")
    with pytest.raises((FileExistsError, SystemExit)):
        packaging.stage_app(tmp_path)
    assert marker.read_text() == "retained"


def test_offline_guard_loads_from_a_windows_path_with_spaces(packaging, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable for real release guard probe")
    guard = tmp_path / "release tools" / "offline-guard.cjs"
    guard.parent.mkdir()
    shutil.copy2(Path(packaging.__file__).with_name("offline-build-guard.cjs"), guard)
    probe = subprocess.run([node, "-e", "try { require('node:http').get('http://127.0.0.1:9'); "
                            "process.exit(2); } catch(e) { if(!e.message.includes('Network access disabled')) throw e; }"],
                           env={**os.environ, "NODE_OPTIONS": packaging.offline_node_options(guard)},
                           capture_output=True, text=True, timeout=15)
    assert probe.returncode == 0, probe.stderr


def test_manifest_reads_a_real_chinese_git_subject_as_utf8(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/make-release-manifest.py"
    spec = importlib.util.spec_from_file_location("release_manifest", script)
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subject = "测试发布：中文提交不依赖系统代码页"
    subprocess.run(["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                    "commit", "--allow-empty", "-m", subject], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(manifest, "PROJECT_ROOT", tmp_path)
    assert manifest.git("log", "-1", "--pretty=%s") == subject


def test_release_app_cannot_inherit_stale_desktop_output(packaging, tmp_path):
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    (desktop / "out").mkdir()
    (desktop / "out/old.js").write_text("stale developer build")
    (desktop / "package.json").write_text(json.dumps({"name": "audit-test", "version": "0.1.0",
        "main": "./out/main/index.js", "dependencies": {"react": "19"}, "devDependencies": {"electron": "44"}}))
    output = tmp_path / "fresh"
    for relative in ["main/index.js", "preload/index.js", "renderer/index.html"]:
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("current compiled output")
    target = tmp_path / "release-app"
    packaging.prepare_desktop_app(desktop, output, target)
    assert (target / "out/main/index.js").read_text() == "current compiled output"
    assert not (target / "out/old.js").exists()
    assert (desktop / "out/old.js").read_text() == "stale developer build"
    metadata = json.loads((target / "package.json").read_text())
    assert not metadata.get("dependencies") and not metadata.get("devDependencies")
    with pytest.raises(FileExistsError):
        packaging.prepare_desktop_app(desktop, output, target)
