"""多级展开（S3）：粗粒度意图命中「能力族」，再下钻到具体插件。

M10 的图谱召回原本只有两级 —— 匹配 capability / domain，然后一跳展开。
S1 把插件目录灌进图后多出「能力族」这一层（阶段 s1–s8 + 支持层/治理层），
本文件锁定它真的能当作**粗粒度入口**用：

  - 概略的说法（「证据与底稿」）命中**族**，而不是碰巧某个插件；
  - 精确的说法（「底稿三级复核」）直接命中**插件**，不误命中族；
  - 族沿 ``contains`` 展开出它下辖的插件，仍然受硬预算约束、每条带溯源；
  - ``expand_hops`` 的取值域**不变**（仍是 0/1）—— 多级不是靠放开跳数实现的，
    而是靠"从哪个粒度切入都能下钻一级"。
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from packages.graph.graph_planning import _EXPANSION_CAP, CapabilityGraphAdapter

TEST_DB = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or (
    "postgresql://audit_app:admin@localhost:5432/audit_network_test"
)

# 「证据与底稿管理」是 audit pack 的 s5 阶段名（pack.json），也是图谱里的族节点 label。
_COARSE_INTENT = "证据与底稿"
_FAMILY_KEY = "capability_family:s5"
_PRECISE_INTENT = "底稿三级复核"
_PRECISE_KEY = "capability:audit.evidence.workpaper-review3"


def _adapter() -> CapabilityGraphAdapter:
    return CapabilityGraphAdapter(TEST_DB)


def _contains_targets(family_key: str) -> set[str]:
    """The plugins the graph itself says belong to this family."""
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', "
            "(SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false)"
        )
        cur.fetchone()
        cur.execute(
            """SELECT tgt.canonical_key
               FROM graph.edges e
               JOIN graph.nodes src ON src.id = e.source_node_id
               JOIN graph.nodes tgt ON tgt.id = e.target_node_id
               WHERE e.relation_type = 'contains' AND e.valid_to IS NULL
                 AND src.canonical_key = %s""",
            (family_key,),
        )
        return {str(row[0]) for row in cur.fetchall()}


# -- 1. the two granularities pick different node types ---------------------------


def test_a_coarse_phrase_matches_the_family_node() -> None:
    """A stage-level phrase is exactly what the family layer is for.

    Before S3 the adapter only looked at capability/domain nodes, so this phrase
    could only ever match a *plugin* whose name happened to contain it — the
    twenty-odd stage groupings were unreachable as entry points.
    """
    matched = _adapter().match_nodes(_COARSE_INTENT, max_matches=8)
    families = [m for m in matched if m["node_type"] == "capability_family"]
    assert families, f"no family matched {_COARSE_INTENT!r}: {[m['label'] for m in matched]}"
    assert _FAMILY_KEY in {m["node_key"] for m in families}
    assert families[0]["score"] >= 0.05


def test_a_precise_phrase_matches_the_plugin_not_the_family() -> None:
    """A concrete phrase must reach the plugin directly, without family noise."""
    matched = _adapter().match_nodes(_PRECISE_INTENT, max_matches=8)
    by_key = {m["node_key"]: m for m in matched}
    assert _PRECISE_KEY in by_key
    assert by_key[_PRECISE_KEY]["node_type"] == "capability"


# -- 2. a family expands into the plugins it contains -----------------------------


def test_family_expands_into_its_own_plugins() -> None:
    expanded = _adapter().expand_to_capabilities([_FAMILY_KEY], expand_hops=1)
    keys = {item["node_key"] for item in expanded["expanded"]}
    assert keys, "a family with plugins must expand into them"

    expected = _contains_targets(_FAMILY_KEY)
    assert keys <= expected, f"expanded something the graph does not contain: {sorted(keys - expected)}"
    assert all(item["node_type"] == "capability" for item in expanded["expanded"])


def test_family_expansion_is_capped_and_carries_provenance() -> None:
    expanded = _adapter().expand_to_capabilities([_FAMILY_KEY], expand_hops=1)["expanded"]
    assert len(expanded) <= _EXPANSION_CAP
    for item in expanded:
        assert item["match_source"] == "contains"
        assert item["source_node_key"] == _FAMILY_KEY
        assert item["match_kind"] == "expanded"


def test_family_expansion_is_disabled_by_zero_hops() -> None:
    """``expand_hops=0`` means "match only", for every entry granularity."""
    assert _adapter().expand_to_capabilities([_FAMILY_KEY], expand_hops=0)["expanded"] == []


def test_multi_hop_is_still_rejected() -> None:
    """Multi-level does not mean multi-hop: the 0/1 contract is unchanged."""
    with pytest.raises(ValueError, match="expand_hops"):
        _adapter().expand_to_capabilities([_FAMILY_KEY], expand_hops=2)


# -- 3. the existing granularities keep working ----------------------------------


def test_domain_and_capability_expansion_are_unchanged() -> None:
    """The two original paths must survive S3 untouched."""
    adapter = _adapter()
    from_domain = adapter.expand_to_capabilities(["domain:audit"], expand_hops=1)["expanded"]
    assert from_domain, "domain -> capability bridge stopped working"
    assert {item["match_source"] for item in from_domain} == {"bridge"}

    from_capability = adapter.expand_to_capabilities(
        ["capability:audit.evidence.e-signature"], expand_hops=1
    )["expanded"]
    assert from_capability, "capability -> depends_on stopped working"
    assert {item["match_source"] for item in from_capability} == {"depends_on"}
