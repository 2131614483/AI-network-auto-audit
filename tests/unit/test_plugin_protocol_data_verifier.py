from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_plugin_protocol_data_verifier_is_explicitly_read_only() -> None:
    script = (ROOT / "scripts" / "verify-plugin-protocol-data.ps1").read_text(encoding="utf-8")

    assert "G:\\数据" in script
    assert "unified-plugin-protocol.schema.json" in script
    assert "semantic.documents" in script
    assert "audit.evidence" in script
    assert "quant.backtests" in script
    assert "aiops.alerts" in script
    assert "INSERT " not in script.upper()
    assert "UPDATE " not in script.upper()
    assert "DELETE " not in script.upper()
