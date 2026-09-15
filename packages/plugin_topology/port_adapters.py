"""CW3: real port-level plugin adapters (explicit, registered, deterministic).

A port adapter converts one port contract into another so a plan edge may
cross schema boundaries with an auditable conversion instead of a silent
passthrough.  Registration is a module-import side effect; the same registry
is used by the compiler's adapter gate and the executor's binding step.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.plugin_topology.ports_executor import register_adapter

_ADAPTER_ID = "candidates-to-backtest"

_CANDIDATES_CONTRACT = "audit-quality-candidates"

#: The adapter returns a simulated backtest (``simulated_only: True`` in the
#: metrics), so there is no real strategy file or market snapshot to hash.
#: These names are therefore explicit "no real artifact" markers, not actual
#: hashes — callers must not treat them as evidence of anything.
_SIMULATED_SHA = "simulated-no-real-artifact"


def _candidates_to_backtest(producer_path: Path, node_dir: Path, tenant_id: UUID) -> dict[str, Any]:
    """audit-quality-candidates -> backtest-report (deterministic conversion).

    Keeps source provenance in ``summary.source_refs`` so the consumer's
    output remains traceable to the exact ledger-quality run that produced it.

    Provenance is taken from the contract's own ``ledger_sha256`` field.  An
    earlier revision read ``summary.quality_hash``, which the
    ``audit-quality-candidates`` contract does not define — so every converted
    report silently carried ``ledger-quality:unknown`` and the audit chain was
    untraceable while still looking successful.  A candidates artifact without
    the required hash is now rejected outright rather than fabricated.
    """
    candidates = json.loads(producer_path.read_text(encoding="utf-8-sig"))
    if str(candidates.get("contract_id") or "") != _CANDIDATES_CONTRACT:
        raise ValueError(
            f"adapter {_ADAPTER_ID} expects a {_CANDIDATES_CONTRACT} artifact, "
            f"got {candidates.get('contract_id')!r}"
        )
    ledger_sha = str(candidates.get("ledger_sha256") or "").strip()
    if not ledger_sha:
        raise ValueError(
            f"adapter {_ADAPTER_ID}: candidates artifact carries no ledger_sha256; "
            "refusing to emit a backtest-report with unattributable provenance"
        )
    rule_pack_sha = str(candidates.get("rule_pack_sha256") or "").strip()
    source_ref = f"ledger-quality:{ledger_sha[:16]}"
    if rule_pack_sha:
        source_ref = f"{source_ref}:rules:{rule_pack_sha[:16]}"
    report = {
        "contract_id": "backtest-report",
        "contract_version": "1.0.0",
        "backtest_id": "bt-" + ledger_sha[:8],
        "strategy_key": "cw3-strategy",
        "strategy_version": "1.0.0",
        "code_sha256": _SIMULATED_SHA,
        "snapshot_sha256": _SIMULATED_SHA,
        "reference_time": "2026-01-05T00:00:00+00:00",
        "point_in_time_gate": "2026-01-05T00:00:00+00:00",
        "metrics": {
            "simulated_only": True,
            "total_return": 0.05,
            "annualized_return": 0.20,
            "volatility": 0.10,
            "sharpe": 1.50,
            "max_drawdown": -0.05,
            "win_rate": 0.60,
            "n_periods": 4,
        },
        "period_returns": [0.01, 0.02, -0.005, 0.03],
        "summary": {"simulated": True, "source_refs": [source_ref]},
    }
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    path = node_dir / f"backtest-{sha[:16]}.json"
    path.write_bytes(raw)
    return {
        "report": {"uri": path.resolve().as_uri(), "sha256": sha, "size_bytes": len(raw)},
        "baseline": None,
    }


register_adapter(_ADAPTER_ID, _candidates_to_backtest)
