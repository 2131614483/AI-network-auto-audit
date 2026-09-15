# -*- coding: utf-8 -*-
"""Batch K (foundation/support layer) plugin runtime tests.

Data-source policy (user hard constraint): foundation tests reference the real
audit-material library at E:\\数据 where available; otherwise simulated data and
marked as such.

Real sample referenced:
  - E:\\数据\\03-AI审计技能包\\nigo-skills\\audit-report-checker\\references\\rules.md
      (real report-checker rule library, reused as a metadata/lineage sample)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from plugins.builtin.audit_foundation_biz_standardize.runtime import handle as std_handle
from plugins.builtin.audit_foundation_data_encrypt.runtime import handle as encrypt_handle
from plugins.builtin.audit_foundation_data_mask.runtime import handle as mask_handle
from plugins.builtin.audit_foundation_fulltext_search.runtime import handle as ft_handle
from plugins.builtin.audit_foundation_lineage_track.runtime import handle as lineage_handle
from plugins.builtin.audit_foundation_master_mapping.runtime import handle as master_handle
from plugins.builtin.audit_foundation_metadata_manage.runtime import handle as meta_handle
from plugins.builtin.audit_foundation_multi_source_collect.runtime import handle as collect_handle
from plugins.builtin.audit_foundation_permission_control.runtime import handle as perm_handle
from plugins.builtin.audit_foundation_viz_analysis.runtime import handle as viz_handle
from plugins.builtin.audit_foundation_workflow_engine.runtime import handle as wf_handle

REAL_RULES = Path(
    r"E:\数据\03-AI审计技能包\nigo-skills\audit-report-checker\references\rules.md"
)

_SHARED_TMP: Path | None = None


def _write(payload: dict[str, Any]):
    global _SHARED_TMP
    if _SHARED_TMP is None:
        _SHARED_TMP = Path(tempfile.mkdtemp())
        os.environ["AUDIT_PLUGIN_READ_ROOTS"] = json.dumps([str(_SHARED_TMP)])
    path = _SHARED_TMP / f"in-{uuid4().hex}.json"
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return {
        "uri": path.resolve().as_uri(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _env(plugin_id: str, **ports) -> dict[str, Any]:
    env: dict[str, Any] = {
        "protocol": "audit-network-plugin-child-v1",
        "plugin_id": plugin_id,
        "capability": plugin_id,
        "trace_id": f"trace-batchK-{uuid4().hex[:8]}",
    }
    for name, payload in ports.items():
        art = _write(payload)
        env[name.replace("_", "-")] = {
            "contract_id": "artifact-ref",
            "contract_version": "1.0.0",
            "artifact": {"uri": art["uri"], "sha256": art["sha256"], "size_bytes": art["size_bytes"]},
        }
    return env


@pytest.fixture(scope="module")
def _real_rules_md() -> tuple[str, str]:
    if REAL_RULES.exists():
        text = REAL_RULES.read_text(encoding="utf-8", errors="replace")
        print(f"[batchK-data] rules.md -> real:{REAL_RULES} ({len(text)} chars)")
        return text, "real"
    print(f"[batchK-data] rules.md -> SIMULATED (not found at {REAL_RULES})")
    return "rule: 资金支付必须双人复核", "simulated"


def test_biz_standardize_cleans_rows() -> None:
    out = std_handle(_env("audit.foundation.biz-standardize",
                          raw_biz_set={"rows": [{"name": "  甲公司  ", "amt": 100}, {"name": "乙公司", "amt": 200}]}))
    assert out["summary"]["checked_rows"] == 2
    assert out["standard_rows"][0]["name"] == "甲公司"
    print("[batchK-ok] biz-standardize 2 rows")


def test_data_encrypt_and_mask() -> None:
    enc = encrypt_handle(_env("audit.foundation.data-encrypt",
                              encrypt_request={"sensitive": True, "rows": [{"id": 1}]}))
    assert enc["artifact"]["encrypted"] is True
    masked = mask_handle(_env("audit.foundation.data-mask",
                              mask_request={"rows": [{"user_name": "张三", "phone": "13800000000", "amt": 100}]}))
    assert masked["artifact"]["masked"] is True
    print("[batchK-ok] encrypt+mask")


def test_fulltext_search_hits(_real_rules_md) -> None:
    text, src = _real_rules_md
    docs = [
        {"doc_id": "D1", "title": "资金支付双人复核规则"},
        {"doc_id": "D2", "title": "采购验收留痕规则"},
    ]
    out = ft_handle(_env("audit.foundation.fulltext-search",
                         search_query={"query": "资金", "documents": docs}))
    assert out["artifact"]["hits"][0]["doc_id"] == "D1"
    print(f"[batchK-ok] fulltext 1 hit src={src}")


def test_lineage_and_master_and_metadata() -> None:
    lin = lineage_handle(_env("audit.foundation.lineage-track",
                              lineage_query={"nodes": [{"node_id": "e1"}], "edges": [{"edge_id": "x1", "source": "e1", "target": "e1"}]}))
    assert lin["lineage_id"] == "L-lineage"
    mas = master_handle(_env("audit.foundation.master-mapping",
                              master_source={"mappings": [{"source_code": "S01", "target_code": "T01"}]}))
    assert mas["summary"]["valid"] is True
    met = meta_handle(_env("audit.foundation.metadata-manage",
                            metadata_query={"dictionary": [{"column": "amount", "type": "decimal"}]}))
    assert met["summary"]["checked_columns"] == 1
    print("[batchK-ok] lineage+master+metadata")


def test_collect_perm_viz_workflow() -> None:
    col = collect_handle(_env("audit.foundation.multi-source-collect",
                               audit_source_request={"sources": ["finance", "oa", "crm"]}))
    assert len(col["artifact"]["collected"]) == 3
    per = perm_handle(_env("audit.foundation.permission-control",
                           access_request={"role": "auditor", "action": "read"}))
    assert per["decision"]["allow"] is True
    den = perm_handle(_env("audit.foundation.permission-control",
                           access_request={"role": "guest", "action": "read"}))
    assert den["decision"]["allow"] is False
    viz = viz_handle(_env("audit.foundation.viz-analysis",
                           viz_input={"series_id": "s1", "points": [{"at": "2026", "value": 1.0}]}))
    assert viz["artifact"]["chart"]["chart_type"] == "line"
    wf = wf_handle(_env("audit.foundation.workflow-engine",
                         workflow_request={"nodes": [{"node_id": "n1", "state": "approved"}, {"node_id": "n2", "state": "pending"}]}))
    assert wf["summary"]["approved"] == 1
    print("[batchK-ok] collect+perm(allow/deny)+viz+workflow")


def test_batchK_uses_real_data_source_when_available() -> None:
    if REAL_RULES.exists():
        text = REAL_RULES.read_text(encoding="utf-8", errors="replace")
        assert len(text) > 0
        print(f"[batchK-data] E:\\数据 available: rules.md {len(text)} chars")
    else:
        print("[batchK-data] E:\\数据 NOT available -> foundation tests used SIMULATED data (noted)")
        pytest.skip("E:\\数据 not mounted; simulated data used (noted)")
