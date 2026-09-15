from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "超级审计与量化智能中枢总体设计-模块自治版.md"
DATABASE = ROOT / "数据库架构与AI开发实施方案-模块自治版.md"


def test_modular_design_preserves_originals_and_defines_runtime_profiles() -> None:
    assert (ROOT / "超级审计与量化智能中枢总体设计.md").is_file()
    assert (ROOT / "数据库架构与AI开发实施方案.md").is_file()
    text = DESIGN.read_text(encoding="utf-8")
    for required in ("MODULE_DEV", "MODULE_TEST", "INTEGRATION", "PRODUCTION", "Integration Hub"):
        assert required in text
    assert "模块之间只通过版本化契约" in text


def test_modular_database_plan_has_independent_ownership_and_no_cross_module_fk() -> None:
    text = DATABASE.read_text(encoding="utf-8")
    for module in (
        "Knowledge Library",
        "Knowledge Graph",
        "Audit",
        "Quant",
        "Risk",
        "Agent",
        "Plugin",
        "AIOps",
        "Integration Hub",
    ):
        assert module in text
    assert "每模块独立数据库" in text
    assert "禁止跨模块外键" in text
    assert "LocalAllowlistPolicy" in text
    assert "RemotePolicyGateway" in text


def test_first_module_instruction_is_standalone_knowledge_library() -> None:
    text = DATABASE.read_text(encoding="utf-8")
    assert "首批执行任务：Knowledge Library" in text
    assert "中央 Policy 数据库" in text
    assert "waiting_runtime" in text
