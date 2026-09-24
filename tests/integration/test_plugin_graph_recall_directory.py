"""S4：把「全量目录」换成「按意图召回的目录」—— 这里锁定它安全且真的变小。

10 万级插件规模下，把整份目录塞进提示词是不可行的：audit 域 107 个插件实测
**8.1 万字符（约 2–3 万 token）**，而图谱本来就是为「按需取用」建的。S4 让规划
端点先做图谱召回，只把**被召回的能力**与**它们的端口契约**交给模型。

这里锁定的不是"能召回"，而是两件会**静默出错**的事：

1. **catalog 与 contracts 必须同源。** 提示词由 contracts 生成，而 ``validate_draft``
   要求模型逐字段复制目录里每个端口的契约。若召回后的 catalog 里有一个端口的契约
   不在 contracts 里，模型看不到它、却仍被要求复制它 —— 草稿必然 ``contract_mismatch``。
   这正是 S4 最容易犯的错，因为"筛能力"和"筛端口"是两次独立的筛选。
2. **召回为空时必须能被调用方识别并回退。** 图谱里没有对应能力时（比如意图太泛或
   指向尚未实现的插件），静默返回一个空目录会让模型无能力可选、规划必然失败。
"""

from __future__ import annotations

import os

import psycopg2
import pytest

from packages.ai_planner.workbench import (
    domain_catalog,
    planning_directory,
    recall_planning_directory,
)

TEST_DB = os.environ.get("AUDIT_NETWORK_TEST_DATABASE_URL") or (
    "postgresql://audit_app:admin@localhost:5432/audit_network_test"
)
DOMAIN = "audit"

# 一个能精确命中真实插件的意图，以及一个图谱里确实没有的概念。
_HIT_INTENT = "底稿三级复核"
_HIT_CAPABILITY = "audit.evidence.workpaper-review3"
_MISS_INTENT = "完全不存在的业务概念xyzzy"


def _recall(intent: str, **kwargs: object):
    return recall_planning_directory(TEST_DB, DOMAIN, intent, **kwargs)


# -- 1. the pair stays consistent --------------------------------------------------


def test_every_recalled_port_has_its_contract() -> None:
    """The single most important invariant: no port without a contract.

    A port present in the catalog but absent from the registry produces a prompt
    that omits its contract while validation still demands it be copied
    verbatim — an unwinnable draft.  Filtering capabilities and filtering ports
    are two separate steps, so this is asserted rather than assumed.
    """
    catalog, contracts, recalled = _recall(_HIT_INTENT)
    assert recalled, "the hit intent must recall something"
    missing: list[tuple[str, str]] = []
    for capability, entry in catalog.items():
        for port_id in (*entry.inputs, *entry.outputs):
            if port_id not in contracts:
                missing.append((capability, port_id))
    assert not missing, f"recalled catalog names ports with no contract: {missing}"


def test_recalled_directory_is_a_subset_of_the_full_one() -> None:
    """Recall selects; it must never invent a capability or a contract."""
    full_catalog, full_contracts = planning_directory(DOMAIN)
    catalog, contracts, recalled = _recall(_HIT_INTENT)

    assert set(catalog) <= set(full_catalog)
    assert set(contracts) <= set(full_contracts)
    assert set(catalog) == set(recalled)
    for capability in catalog:
        assert catalog[capability] == full_catalog[capability]


# -- 2. recall actually finds the capability the intent names ----------------------


def test_recall_includes_the_plugin_the_intent_names() -> None:
    catalog, _contracts, recalled = _recall(_HIT_INTENT)
    assert _HIT_CAPABILITY in recalled
    assert _HIT_CAPABILITY in catalog


def test_recall_is_smaller_than_the_full_directory() -> None:
    """The whole point: fewer capabilities, therefore a smaller prompt.

    Asserted structurally (counts) rather than as a character count, so the test
    does not break when plugin descriptions change.
    """
    full_catalog, _ = planning_directory(DOMAIN)
    catalog, _contracts, _recalled = _recall(_HIT_INTENT)
    assert len(full_catalog) > 50, "the fixture must actually hold a large directory"
    assert len(catalog) < len(full_catalog) / 2, (
        f"recall returned {len(catalog)} of {len(full_catalog)} capabilities; "
        "that is not a narrowing"
    )


def test_recall_contracts_are_a_strict_subset_of_the_full_registry() -> None:
    _full_catalog, full_contracts = planning_directory(DOMAIN)
    _catalog, contracts, _recalled = _recall(_HIT_INTENT)
    assert set(contracts) < set(full_contracts), "contracts were not narrowed at all"


# -- 3. an unmatched intent is reported, not faked ----------------------------------


def test_an_unmatched_intent_recalls_nothing() -> None:
    """Returning an empty selection is the signal to fall back to the full
    directory.  Silently returning the full directory instead would defeat S4;
    silently returning *nothing at all* would break planning.  The caller must be
    able to tell the two apart, so the empty case is explicit.
    """
    catalog, contracts, recalled = _recall(_MISS_INTENT)
    assert recalled == ()
    assert catalog == {}
    assert contracts == {}


def test_recall_does_not_read_other_domains() -> None:
    """Every recalled capability must belong to the requested domain."""
    _catalog, _contracts, recalled = _recall(_HIT_INTENT)
    domain_caps = set(domain_catalog(DOMAIN))
    assert set(recalled) <= domain_caps, set(recalled) - domain_caps


def test_an_unknown_tenant_degrades_instead_of_raising() -> None:
    """A tenant with no graph must not turn into a 500 on a planning endpoint.

    ``CapabilityGraphAdapter`` resolves the tenant by slug and raises
    ``ValueError`` when it is absent.  That is "there is no graph for you", not a
    caller mistake — so recall reports nothing and the caller falls back to the
    full directory, exactly as it worked before S4 existed.  Swallowing only
    ``psycopg2.Error`` here would have let a multi-tenant deployment 500 on every
    tenant that is not the one the graph was built for.
    """
    catalog, contracts, recalled = recall_planning_directory(
        TEST_DB, DOMAIN, _HIT_INTENT, tenant_slug="no-such-tenant-xyzzy",
    )
    assert recalled == ()
    assert catalog == {}
    assert contracts == {}


# -- 4. the graph is what does the narrowing, so it must be populated --------------


def test_recall_needs_a_populated_graph() -> None:
    """A guard against the failure mode where recall silently returns nothing
    because the graph was never built (S1's job).  If this fails, run
    ``scripts/index-plugin-graph.py`` rather than touching the recall code.
    """
    with psycopg2.connect(TEST_DB) as connection, connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.tenant_id', "
            "(SELECT id::text FROM iam.tenants WHERE slug='local-dev'), false)"
        )
        cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM graph.nodes n JOIN graph.spaces s ON s.id = n.space_id "
            "WHERE n.node_type = 'capability' AND n.deleted_at IS NULL AND s.level = 'L2'"
        )
        populated = int(cur.fetchone()[0])
    if populated < 50:
        pytest.fail(
            f"only {populated} capability nodes in the graph; recall cannot narrow "
            "anything. Run scripts/index-plugin-graph.py first."
        )
