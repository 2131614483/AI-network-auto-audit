"""随包入口形状声明 × 真实案例资料：每条都要能产出工件，且该工件要能把插件真跑通。

单元测试用的是构造出来的小数据，证明不了"声明和案例资料对得上"。这一层才对得上：
glob 在真实目录里命中、列名与真实表头一致、数值清洗能过 `float()`、插件 `handle()` 不拒绝。

**缺源也要有声音**：某条声明在某个变体上没有源文件时必须**报错**，不能产出空工件 ——
这条比"能跑通"更重要，因为静默产空会让下游以为"数据本来就没有"。
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

from packages.catalog.input_shapes import ShapeError, build_artifact, load_shapes

ROOT = Path(__file__).resolve().parents[2]
BUILTIN = ROOT / "plugins" / "builtin"
CASE_ROOT = ROOT / "审计项目案例报告效果展示"

VARIANTS = {
    "对照-基础": "黔岭酒业2025年度财务报表审计",
    "实验组": "黔岭酒业2025年度财务报表审计_实验组",
    "高难度": "黔岭酒业2025年度财务报表审计_实验组_高难度",
}

pytestmark = pytest.mark.skipif(
    not CASE_ROOT.is_dir(), reason="案例数据集不在仓库里（外部资料，不入库）"
)


def _consumers(contract: str) -> list[Path]:
    """哪些插件把这个契约当输入 —— 从 protocol.json 取，不手抄一张表。"""
    hits = []
    for protocol_path in sorted(BUILTIN.glob("*/plugin.protocol.json")):
        data = json.loads(protocol_path.read_text(encoding="utf-8"))
        for capability in data.get("capabilities", []):
            for port in capability.get("inputs", []) or []:
                if port["contract_id"] == contract:
                    hits.append(protocol_path.parent)
    return hits


def _port_and_identity(plugin_dir: Path, contract: str) -> tuple[str, str, str]:
    """端口名与 (plugin_id, capability) —— 端口名从 runtime 里读出来，避免两处各写一份。"""
    runtime = (plugin_dir / "runtime.py").read_text(encoding="utf-8")
    match = re.search(rf'envelope\.get\(\s*"{re.escape(contract)}"\s*\)', runtime)
    assert match, f"{plugin_dir.name} 的 runtime 里没有以 {contract!r} 为名的端口"
    protocol = json.loads((plugin_dir / "plugin.protocol.json").read_text(encoding="utf-8"))
    return contract, protocol["id"], protocol["capabilities"][0]["id"]


def _build(name: str, spec: dict, variant: str, out_root: Path):
    return build_artifact(spec, CASE_ROOT / variant, out_root / variant, name=name)


def test_every_declared_shape_matches_at_least_one_variant(tmp_path: Path) -> None:
    """声明要么对上真实资料，要么明确报"找不到源文件"；不许出现"匹配到但产出空壳"。"""
    for name, spec in load_shapes().items():
        built, refused = [], []
        for variant in VARIANTS.values():
            try:
                built.append((variant, _build(name, spec, variant, tmp_path)))
            except ShapeError as exc:
                refused.append((variant, str(exc)))
        assert built, f"{name} 在三个变体上都没有源文件：{refused}"
        assert all(result["items"] > 0 for _, result in built), name


def test_declared_shapes_drive_their_plugin_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """工件喂进真插件 —— 结构对不代表运行时认：列口径、数值清洗都要过得了 handle()。"""
    for name, spec in load_shapes().items():
        plugin_dirs = _consumers(name)
        assert len(plugin_dirs) == 1, f"{name} 的消费方不是唯一：{plugin_dirs}"
        port, plugin_id, capability = _port_and_identity(plugin_dirs[0], name)

        ran = False
        for variant in VARIANTS.values():
            try:
                built = _build(name, spec, variant, tmp_path)
            except ShapeError:
                continue
            monkeypatch.setenv(
                "AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(built["path"].parent.resolve())])
            )
            module = importlib.import_module(f"plugins.builtin.{plugin_dirs[0].name}.runtime")
            result = module.handle({
                "protocol": "audit-network-plugin-child-v1",
                "plugin_id": plugin_id,
                "capability": capability,
                port: {"artifact": {
                    "uri": built["path"].resolve().as_uri(),
                    "sha256": built["sha256"],
                    "size_bytes": built["size_bytes"],
                }},
            })
            assert result.get("contract_id"), f"{name} 在 {variant} 上没产出契约"
            ran = True
        assert ran, f"{name} 没有跑通任何变体"
