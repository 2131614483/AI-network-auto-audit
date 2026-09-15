"""Run the AI-composed large workflow with full capture, without clobbering anything.

Wraps the project's own composer driver (`.data/_ai_compose_demo.py`) so that:

* ``STAGING`` points at this archive instead of ``.data/isolated-ai-composed-100``
  — that directory holds an earlier run's per-node artifacts and must never be
  overwritten (长期保存，不许删除);
* ``DB`` stays on the driver's own target (``audit_network``).  The full
  pytest regression is running against ``audit_network_test`` at the same time;
  pointing this run there would make both interfere.  The run is purely
  additive (new ``plan_key``), which is also what the driver was written to do,
  and it means the run shows up in the desktop app's run feed.

Everything else — the goal, the pinned main chain, the AI composition, the
in-memory policy allow-list — is exactly the project's own driver, unmodified.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

REPO = pathlib.Path(r"D:\pythonpro\audit_network")
ARCHIVE = REPO / "docs" / "全流程运行日志-20260912"
DRIVER = REPO / ".data" / "_ai_compose_demo.py"
STAGING = ARCHIVE / "12-本次运行产物"


def main() -> int:
    spec = importlib.util.spec_from_file_location("ai_compose_demo", DRIVER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_compose_demo"] = mod
    spec.loader.exec_module(mod)  # module-level defs/constants only; main() is __main__-guarded

    mod.STAGING = STAGING
    mod.DB = "postgresql://audit_app:admin@localhost:5432/audit_network"
    STAGING.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("AI 组网全流程运行（完整捕获）")
    print("=" * 72)
    print(f"驱动脚本  : {DRIVER}")
    print(f"产物目录  : {STAGING}")
    print(f"数据库    : {mod.DB}")
    print(f"目标      : {mod.GOAL[:80]}…")
    print(f"选定插件  : {len(mod.VERIFIED)} 个")
    print(f"固定主链  : {len(mod.CHAIN)} 条边")
    print("=" * 72)
    started = time.monotonic()
    mod.main()
    print("=" * 72)
    print(f"总耗时: {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
