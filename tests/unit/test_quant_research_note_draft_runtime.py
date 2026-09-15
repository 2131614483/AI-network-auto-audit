from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from plugins.builtin.quant_research_note_draft.runtime import InputRejected, handle

EXPERIMENT_ID = "a1b2c3d4e5f67890"
STRATEGY_KEY = "momentum"
STRATEGY_VERSION = "2.1.0"


def _evaluation(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": "experiment-evaluation",
        "contract_version": "1.0.0",
        "experiment_id": EXPERIMENT_ID,
        "report_sha256": "a" * 64,
        "baseline_sha256": "b" * 64,
        "strategy_key": STRATEGY_KEY,
        "strategy_version": STRATEGY_VERSION,
        "snapshot_sha256": "a" * 64,
        "reference_time": "2026-03-31T00:00:00Z",
        "status": "proposed",
        "robustness": {
            "overall": "stable",
            "score": 0.72,
            "basis": "period_returns",
            "period_return_stability": 0.88,
            "positive_period_ratio": 0.83,
            "max_drawdown": -0.08,
            "volatility": 0.11,
            "notes": ["基于 12 个逐期收益计算稳健性。"],
        },
        "drift": {
            "available": True,
            "drift_count": 0,
            "worst_metric": None,
            "compared_metrics": [
                {
                    "metric": "sharpe",
                    "challenger": 1.7,
                    "baseline": 1.5,
                    "absolute_delta": 0.2,
                    "relative_delta": 0.1333,
                    "drifted": False,
                    "threshold": 0.2,
                }
            ],
        },
        "promotion": {
            "recommendation": "promote",
            "rationale": "挑战者在稳健性与风险调整收益上满足晋级门槛，可进入独立 Reviewer 复核。",
            "blockers": [],
        },
        "evidence_refs": ["factor-pack:abc123", "snapshot:snap-2026-03"],
        "summary": {
            "report_sha256": "a" * 64,
            "baseline_included": True,
            "recommendation": "promote",
            "truncated": False,
        },
    }
    payload.update(overrides)
    return payload


def _artifact(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "artifact_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "uri": path.resolve().as_uri(),
        "media_type": "application/json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "classification": "restricted",
    }


def _envelope(path: Path, **overrides: object) -> dict[str, object]:
    envelope: dict[str, object] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": "quant.research-note-draft",
        "capability": "quant.research-note.draft",
        "research_note": {"evaluation": _artifact(path)},
    }
    envelope.update(overrides)
    return envelope


@pytest.fixture()
def read_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "research-note-root"
    root.mkdir()
    monkeypatch.setenv("AUDIT_PLUGIN_READ_ROOTS", json.dumps([str(root)]))
    return root


def _write(root: Path, name: str, evaluation: dict[str, object]) -> Path:
    path = root / name
    path.write_text(json.dumps(evaluation, ensure_ascii=False), encoding="utf-8")
    return path


def test_confirmed_evaluation_produces_an_unpublished_research_note(read_roots: Path) -> None:
    path = _write(read_roots, "experiment-evaluation-01.json", _evaluation())
    output = handle(_envelope(path))

    assert output["contract_id"] == "research-note-draft"
    assert output["contract_version"] == "1.0.0"
    assert output["status"] == "draft"
    assert len(output["note_id"]) == 16
    assert output["evaluation_refs"] == [f"evaluation:{EXPERIMENT_ID}"]
    assert output["strategy"] == {"key": STRATEGY_KEY, "version": STRATEGY_VERSION}
    assert output["summary"] == {
        "evaluations": 1,
        "recommendation": "promote",
        "truncated": False,
    }
    assert output["signature"] == {"drafted_by": "quant.research-note-draft@0.1.0", "published": False, "publisher": None}
    assert output["constraints"] == {
        "no_auto_publish": True,
        "no_order_generation": True,
        "requires_human_approval": True,
        "immutable_source": True,
    }
    assert output["provenance"]["evaluation_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert output["provenance"]["plugin"] == "quant.research-note-draft@0.1.0"
    assert any("promote" in item for item in output["recommendations"])


def test_research_note_draft_is_deterministic_over_the_same_evaluation(read_roots: Path) -> None:
    path = _write(read_roots, "experiment-evaluation-02.json", _evaluation())
    first = handle(_envelope(path))
    second = handle(_envelope(path))

    assert first["note_id"] == second["note_id"]
    assert first["title"] == second["title"]
    assert first["overview"] == second["overview"]
    assert first["findings"] == second["findings"]


def test_recommendation_maps_to_findings_and_recommendations(read_roots: Path) -> None:
    evaluation = _evaluation()
    evaluation["promotion"]["recommendation"] = "hold"  # type: ignore[index]
    path = _write(read_roots, "experiment-evaluation-03.json", evaluation)
    output = handle(_envelope(path))

    assert output["summary"]["recommendation"] == "hold"
    assert any("hold" in item for item in output["recommendations"])


def test_fragile_robustness_reflects_in_findings(read_roots: Path) -> None:
    evaluation = _evaluation()
    evaluation["robustness"]["overall"] = "fragile"  # type: ignore[index]
    path = _write(read_roots, "experiment-evaluation-04.json", evaluation)
    output = handle(_envelope(path))

    assert any("fragile" in item for item in output["findings"])


def test_unconfirmed_evaluation_is_rejected(read_roots: Path) -> None:
    evaluation = _evaluation(status="confirmed")
    path = _write(read_roots, "experiment-evaluation-05.json", evaluation)

    with pytest.raises(InputRejected, match="not confirmed"):
        handle(_envelope(path))


def test_tampered_sha256_is_rejected(read_roots: Path) -> None:
    path = _write(read_roots, "experiment-evaluation-06.json", _evaluation())
    artifact = _artifact(path)
    artifact["sha256"] = "0" * 64

    with pytest.raises(InputRejected, match="sha256 does not match"):
        handle(_envelope(path, research_note={"evaluation": artifact}))


def test_invalid_promotion_recommendation_is_rejected(read_roots: Path) -> None:
    evaluation = _evaluation()
    evaluation["promotion"]["recommendation"] = "publish"  # type: ignore[index]
    path = _write(read_roots, "experiment-evaluation-07.json", evaluation)

    with pytest.raises(InputRejected, match="fixed enum"):
        handle(_envelope(path))