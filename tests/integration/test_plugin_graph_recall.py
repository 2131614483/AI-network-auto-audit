"""召回的可扩展性（S2）：粗筛走 GIN trgm 索引，但结果必须与暴力扫描逐字节一致。

M10 的意图召回原实现是"对每一行算 ``similarity(label)`` 与每行一个别名子查询，
再过滤排序"——语义正确，代价是 O(节点数 × 子查询)，10 万节点时全表扫描且每行三次聚合。

S2 把它改成两段：用 GiST KNN（``ORDER BY label <-> q LIMIT k``）各取 label 与别名
的最近 k 个作候选，只在候选集内精排。**这里锁定的是"优化不改变结果"**，因为这条路线上
有两个真会静默出错的点：

1. 候选预算（``_candidate_budget``）必须够大。每个分支只回最近 k 个，k 太小就会漏掉
   最终 top-N 里的节点 —— 而漏召回不会报错，只会让意图匹配悄悄变差。
2. 别名（``node_aliases``）是第二个候选来源，只剪 label 会丢掉全部别名匹配。

顺带一个反直觉事实：**不能改用 ``%`` 操作符**（GIN，``similarity >= 阈值``）。它语义
等价、也建了索引（0048），但以应用角色（受 RLS 约束）执行时 PG 的选择性估计会退化为
表行数的 1 %，计划器直接放弃索引改全表扫描 —— 实测 50 000 节点下 250 ms vs KNN 的
6 ms。KNN 不需要选择性估计，这就是选它的原因（详见迁移 0067）。

所以第一组测试用**优化前的实现当 oracle**，逐条对比。
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from packages.graph.graph_planning import (
    _MATCH_LEVELS,
    _MATCH_NODE_TYPES,
    _MIN_SIMILARITY,
    CapabilityGraphAdapter,
    _candidate_budget,
)

TEST_DB = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or (
    "postgresql://audit_app:admin@localhost:5432/audit_network_test"
)

# 覆盖三种命中形态：精确命中插件、概略命中族、以及一个刻意不存在的概念。
_INTENTS = ("底稿三级复核", "证据与底稿", "风险识别", "固定资产盘点", "审计报告", "关联方")


def _adapter() -> CapabilityGraphAdapter:
    return CapabilityGraphAdapter(TEST_DB)


def _brute_force_match(intent_text: str, max_matches: int) -> list[dict[str, object]]:
    """The pre-S2 implementation, kept as the equivalence oracle.

    It scans every node and evaluates ``similarity()`` directly, so it is the
    definition of the intended semantics; the indexed path must reproduce it
    exactly.  Deliberately duplicates the shipped scoring rule (including the
    ``match_kind`` tie-break) rather than importing it, so a change to either
    side shows up as a difference.
    """
    sql = """
        SELECT n.canonical_key, s.key, n.label, n.node_type,
               similarity(n.label, %(q)s) AS label_score,
               COALESCE((
                 SELECT max(similarity(a.alias, %(q)s))
                 FROM graph.node_aliases a WHERE a.node_id = n.id
               ), 0) AS alias_score
        FROM graph.nodes n
        JOIN graph.spaces s ON s.id = n.space_id
        WHERE s.level = ANY(%(levels)s)
          AND s.status = 'active'
          AND n.deleted_at IS NULL
          AND n.node_type = ANY(%(types)s)
          AND GREATEST(
                similarity(n.label, %(q)s),
                COALESCE((
                  SELECT max(similarity(a.alias, %(q)s))
                  FROM graph.node_aliases a WHERE a.node_id = n.id
                ), 0)
              ) >= %(min)s
        ORDER BY GREATEST(
            similarity(n.label, %(q)s),
            COALESCE((
              SELECT max(similarity(a.alias, %(q)s))
              FROM graph.node_aliases a WHERE a.node_id = n.id
            ), 0)
          ) DESC, s.key, n.canonical_key
        LIMIT %(limit)s
    """
    params = {
        "q": intent_text,
        "levels": list(_MATCH_LEVELS),
        "types": list(_MATCH_NODE_TYPES),
        "min": _MIN_SIMILARITY,
        "limit": max_matches,
    }
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', "
            "(SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false)"
        )
        cur.fetchone()
        cur.execute(sql, params)
        out: list[dict[str, object]] = []
        for key, space, label, node_type, ls, as_ in cur.fetchall():
            label_score, alias_score = float(ls), float(as_)
            score = max(label_score, alias_score)
            if score < _MIN_SIMILARITY:
                continue
            out.append(
                {
                    "node_key": str(key),
                    "space_key": str(space),
                    "label": str(label),
                    "node_type": str(node_type),
                    "score": round(score, 6),
                    "match_kind": "direct" if label_score >= alias_score else "alias",
                    "match_source": "trigram",
                }
            )
        return out


# -- 1. the indexed path must reproduce the brute-force path exactly ---------------


@pytest.mark.parametrize("intent", _INTENTS)
@pytest.mark.parametrize("max_matches", (1, 4, 16))
def test_indexed_recall_equals_brute_force(intent: str, max_matches: int) -> None:
    """Whether the plan uses the index or not, the answer is the same."""
    expected = _brute_force_match(intent, max_matches)
    actual = _adapter().match_nodes(intent, max_matches=max_matches)
    assert [m["node_key"] for m in actual] == [m["node_key"] for m in expected]
    assert actual == expected


def test_the_oracle_actually_exercises_low_similarity_matches() -> None:
    """Guard against a vacuous equivalence test.

    ``%`` defaults to a 0.3 threshold, so a broken threshold setup would still
    pass an equivalence test built only from exact hits.  Assert the corpus
    really does contain matches in the ``(0.05, 0.3)`` band that only the 0.05
    threshold can reach — that band is exactly what a mis-set threshold drops.
    """
    band: list[float] = []
    for intent in _INTENTS:
        for match in _brute_force_match(intent, 16):
            score = float(match["score"])
            if _MIN_SIMILARITY <= score < 0.3:
                band.append(score)
    assert band, "no low-similarity matches in the corpus; the test would be vacuous"


@pytest.mark.parametrize("intent", _INTENTS)
def test_matches_in_the_low_similarity_band_survive_recall(intent: str) -> None:
    """Every sub-0.3 match the oracle finds must still come back from the index."""
    expected = _brute_force_match(intent, 16)
    low = {m["node_key"] for m in expected if float(m["score"]) < 0.3}
    if not low:
        pytest.skip(f"{intent!r} has no sub-0.3 matches")
    actual = {m["node_key"] for m in _adapter().match_nodes(intent, max_matches=16)}
    assert low <= actual


# -- 2. alias matching must not be lost by the label-only pre-filter ---------------


_PROBE_KEY = "capability:test-alias-probe"
_PROBE_LABEL = "zzz-alias-probe"  # shares no trigram with any intent below
_PROBE_ALIAS = "底稿三级复核"  # ...while the alias is an exact hit


def _set_probe(alias_present: bool) -> None:
    """Create/park a node whose *label* is irrelevant and whose alias is the query.

    The shipped corpus happens to have no label-mismatch/alias-match pair, so the
    alias candidate branch cannot be exercised against it — this installs one.
    The node is parked with ``deleted_at`` rather than deleted (the project never
    hard-deletes graph history), and the key is fixed so repeated runs do not
    accumulate rows.
    """
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', "
            "(SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false)"
        )
        cur.fetchone()
        if not alias_present:
            cur.execute(
                "UPDATE graph.nodes SET deleted_at = now() WHERE canonical_key = %s",
                (_PROBE_KEY,),
            )
            return
        cur.execute(
            """INSERT INTO graph.nodes(tenant_id, space_id, canonical_key, node_type, label)
               SELECT n.tenant_id, n.space_id, %s, 'capability', %s
               FROM graph.nodes n WHERE n.canonical_key = 'capability:audit.ledger.validate'
               ON CONFLICT (space_id, canonical_key) DO UPDATE
               SET label = EXCLUDED.label, deleted_at = NULL
               RETURNING tenant_id, id""",
            (_PROBE_KEY, _PROBE_LABEL),
        )
        tenant_id, node_id = cur.fetchone()
        cur.execute(
            "INSERT INTO graph.node_aliases(tenant_id, node_id, alias) VALUES (%s,%s,%s) "
            "ON CONFLICT (node_id, alias) DO NOTHING",
            (tenant_id, node_id, _PROBE_ALIAS),
        )


def test_alias_only_matches_are_still_found() -> None:
    """An alias-only hit must survive the pre-filter.

    The pre-filter has two candidate sources.  A label-only pre-filter would
    still pass every equivalence test above (no corpus node is alias-driven) and
    silently drop alias recall in production.  So this installs a node whose
    label is irrelevant and whose alias is an exact hit, and checks it both ways:
    absent -> not recalled, present -> recalled *as an alias*.
    """
    _set_probe(alias_present=False)
    parked = {m["node_key"] for m in _adapter().match_nodes(_PROBE_ALIAS, max_matches=16)}
    assert _PROBE_KEY not in parked, "parked probe node should not be recalled"

    _set_probe(alias_present=True)
    try:
        matches = _adapter().match_nodes(_PROBE_ALIAS, max_matches=16)
        probe = next((m for m in matches if m["node_key"] == _PROBE_KEY), None)
        assert probe is not None, f"alias-only match dropped: {[m['node_key'] for m in matches]}"
        assert probe["match_kind"] == "alias"
        assert probe["score"] == pytest.approx(1.0)
    finally:
        _set_probe(alias_present=False)


# -- 3. a concept the corpus genuinely lacks stays unmatched ----------------------


def test_an_absent_concept_returns_nothing() -> None:
    """「关联方」 has no plugin yet (it is a known gap), so recall must be honest."""
    assert _adapter().match_nodes("关联方", max_matches=8) == []


# -- 4. the candidate budget is what keeps KNN recall complete --------------------


def test_candidate_budget_covers_the_requested_matches() -> None:
    """Each KNN branch returns only its k nearest nodes, so ``k >= max_matches``
    is the invariant that keeps their union a superset of the final top-N.

    Shrinking the budget below ``max_matches`` would drop matches without any
    error — the equivalence tests above would only catch it if the corpus
    happened to have a node sitting exactly on the boundary.
    """
    for max_matches in range(1, 17):
        budget = _candidate_budget(max_matches)
        assert budget >= max_matches, f"budget {budget} < max_matches {max_matches}"
        assert budget >= 64, "the fixed floor keeps ties covered when max_matches is small"


# -- 5. determinism ----------------------------------------------------------------


def test_repeated_calls_are_identical() -> None:
    adapter = _adapter()
    first = adapter.match_nodes("证据与底稿", max_matches=8)
    for _ in range(3):
        assert adapter.match_nodes("证据与底稿", max_matches=8) == first
