"""``packages.paths`` 必须同时满足开发布局与安装版布局。

安装版把代码放在只读的资源目录，日志与工件改为写 ``AUDIT_NETWORK_DATA_DIR``；
开发、CI 与既有 PowerShell 脚本不设该变量，必须仍落到 ``<项目>/.data``。
这两条都不能漂移，否则表现为"本机能跑、装完写不进去"。
"""

from __future__ import annotations

import os

from packages.paths import DATA_DIR_ENV, data_root, project_root


def test_default_data_root_matches_legacy_layout(monkeypatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert data_root() == project_root() / ".data"


def test_data_root_honours_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    assert data_root() == tmp_path


def test_blank_env_override_falls_back(monkeypatch) -> None:
    # 启动器未配置时可能传入空串，不能因此把数据写到当前目录。
    monkeypatch.setenv(DATA_DIR_ENV, "   ")
    assert data_root() == project_root() / ".data"


def test_project_root_holds_the_packages() -> None:
    assert (project_root() / "packages" / "paths.py").is_file()
    assert os.path.isdir(project_root() / "migrations")
