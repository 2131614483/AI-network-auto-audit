"""Read-only evolution view: the seven-ring learning loop, measured not assumed.

Seven rings turn raw runs into reusable knowledge.  The loop is only as strong as
its weakest ring, and a dashboard that shows all green when ring six has never
produced a row is self-deception with better typography.  So every ring reports
a status *plus the numbers that decided it*, and the two rings that are **code
facts rather than data facts** are checked by parsing the real modules:

* ring 2 — is the experience projector actually wired into the DAG run path?
* ring 7 — does the planner's read path reach experience/knowledge at all?

Both are answered with :mod:`ast` imports and call inspection, never with text
search.  Grepping for ``knowledge`` in ``ai_planner`` finds nine hits that are all
docstrings — exactly the false green this module exists to prevent.

Statuses are honest three-valued: ``ok`` (evidence present), ``partial``
(mechanism present, no rows yet in this tenant), ``broken`` (mechanism absent).
Nothing here writes, and nothing here can be greenwashed: each status names the
query or the parse that produced it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import register_uuid

register_uuid()  # type: ignore[no-untyped-call]

_OK = "ok"
_PARTIAL = "partial"
_BROKEN = "broken"


def _count(cur: Any, sql: str, params: tuple[Any, ...]) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _ring2_wiring(project_root: Path) -> dict[str, Any]:
    """Does ``start_plan_run`` project experience?  A parse, not a search.

    The DAG entry point is the one every real run goes through; ``start_run``
    (the M6 chain path) also projects, but it is not the path production traffic
    uses, so only ``start_plan_run`` counts as wiring.
    """

    service = project_root / "packages" / "plugin_topology" / "service.py"
    if not service.is_file():
        return {"wired": False, "detail": "service.py not found", "checked": str(service)}
    tree = ast.parse(service.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "start_plan_run":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                if name == "project_run_best_effort":
                    return {
                        "wired": True,
                        "detail": "start_plan_run calls project_run_best_effort",
                        "checked": "packages/plugin_topology/service.py::start_plan_run",
                    }
        return {
            "wired": False,
            "detail": "start_plan_run does not call project_run_best_effort",
            "checked": "packages/plugin_topology/service.py::start_plan_run",
        }
    return {"wired": False, "detail": "start_plan_run not found", "checked": str(service)}


def _ring7_readpath(project_root: Path) -> dict[str, Any]:
    """Does the planner import experience/knowledge?  Imports only.

    Docstrings mentioning a word are not integration.  An import of
    ``packages.experience`` or ``packages.knowledge`` inside ``packages/ai_planner``
    is; this is the same standard the projector contract was written against.
    """

    planner = project_root / "packages" / "ai_planner"
    hits: list[str] = []
    checked = 0
    for path in sorted(planner.glob("*.py")):
        checked += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(("packages.experience", "packages.knowledge")):
                    hits.append(f"{path.relative_to(project_root).as_posix()}: {name}")
    return {
        "imported": bool(hits),
        "imports": hits,
        "files_parsed": checked,
        "checked": "ast.Import/ImportFrom across packages/ai_planner/*.py",
    }


def evolution_status(database_url: str, tenant_slug: str, project_root: Path) -> dict[str, Any]:
    """The seven rings with their deciding evidence, worst-ring first in spirit.

    The rings are returned in loop order; ``weakest`` names the first non-ok ring
    so a caller can headline the real state of the loop in one field.
    """

    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM iam.tenants WHERE slug=%s", (tenant_slug,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"tenant not found: {tenant_slug}")
        tenant_id = row[0]
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))

        attempts = _count(cur, "SELECT count(*) FROM control.node_attempts WHERE tenant_id=%s", (tenant_id,))
        runs = _count(cur, "SELECT count(DISTINCT run_id) FROM control.node_attempts WHERE tenant_id=%s", (tenant_id,))
        node_obs = _count(cur, "SELECT count(*) FROM experience.node_observations WHERE tenant_id=%s", (tenant_id,))
        edge_obs = _count(cur, "SELECT count(*) FROM experience.edge_observations WHERE tenant_id=%s", (tenant_id,))
        node_stats = _count(cur, "SELECT count(*) FROM experience.node_stats WHERE tenant_id=%s", (tenant_id,))
        edge_stats = _count(cur, "SELECT count(*) FROM experience.edge_stats WHERE tenant_id=%s", (tenant_id,))
        suggestions_total = _count(
            cur, "SELECT count(*) FROM experience.relation_suggestions WHERE tenant_id=%s", (tenant_id,)
        )
        suggestions_decided = _count(
            cur,
            "SELECT count(*) FROM experience.relation_suggestions WHERE tenant_id=%s AND status<>'proposed'",
            (tenant_id,),
        )
        changesets_experience = _count(
            cur,
            "SELECT count(*) FROM knowledge.change_sets WHERE tenant_id=%s AND change_type='experience'",
            (tenant_id,),
        )
        changesets_all = _count(cur, "SELECT count(*) FROM knowledge.change_sets WHERE tenant_id=%s", (tenant_id,))
        releases = _count(cur, "SELECT count(*) FROM knowledge.releases WHERE tenant_id=%s", (tenant_id,))
        archive_links = _count(cur, "SELECT count(*) FROM experience.archive_links WHERE tenant_id=%s", (tenant_id,))

    ring2 = _ring2_wiring(project_root)
    ring7 = _ring7_readpath(project_root)

    rings: list[dict[str, Any]] = [
        {
            "ring": 1,
            "name": "运行发生",
            "status": _OK if runs > 0 else _PARTIAL,
            "evidence": {"attempts": attempts, "runs": runs},
            "detail": "attempt 账本有真实运行记录" if runs > 0 else "本租户还没有任何运行",
            "checked": "control.node_attempts per tenant",
        },
        {
            "ring": 2,
            "name": "事实投影",
            "status": _OK
            if node_obs > 0 and edge_obs > 0
            else (_PARTIAL if ring2["wired"] else _BROKEN),
            "evidence": {
                "node_observations": node_obs,
                "edge_observations": edge_obs,
                "wired_into_dag_path": ring2["wired"],
            },
            "detail": (
                "观测行已落库且投影挂在 DAG 运行路径上"
                if node_obs > 0 and ring2["wired"]
                else "投影已挂接但本租户尚无新运行产生观测行" if ring2["wired"] else ring2["detail"]
            ),
            "checked": ring2["checked"],
        },
        {
            "ring": 3,
            "name": "统计聚合",
            "status": _OK if node_stats > 0 or edge_stats > 0 else _PARTIAL,
            "evidence": {"node_stats": node_stats, "edge_stats": edge_stats},
            "detail": "统计层可由观测重建；行数为当前快照",
            "checked": "experience.node_stats / edge_stats per tenant",
        },
        {
            "ring": 4,
            "name": "建议产生",
            "status": _OK if suggestions_total > 0 else _PARTIAL,
            "evidence": {"relation_suggestions": suggestions_total},
            "detail": "状态机 proposed→accepted/dismissed 已就绪" if suggestions_total else "尚无建议产生",
            "checked": "experience.relation_suggestions per tenant",
        },
        {
            "ring": 5,
            "name": "人工决策",
            "status": _OK if suggestions_decided > 0 else _PARTIAL,
            "evidence": {"decided": suggestions_decided, "total": suggestions_total},
            "detail": (
                f"{suggestions_decided}/{suggestions_total} 条建议已有人工决策"
                if suggestions_total
                else "没有建议可决策"
            ),
            "checked": "relation_suggestions.status <> 'proposed'",
        },
        {
            "ring": 6,
            "name": "知识发布",
            "status": _OK if changesets_experience > 0 else _BROKEN,
            "evidence": {
                "experience_change_sets": changesets_experience,
                "change_sets_all_types": changesets_all,
                "releases": releases,
            },
            "detail": (
                "经验通道已有变更集"
                if changesets_experience
                else f"change_sets 共 {changesets_all} 条但 change_type='experience' 为 0 —— 0057 通道从未产出"
            ),
            "checked": "knowledge.change_sets.change_type='experience'",
        },
        {
            "ring": 7,
            "name": "回流生效",
            "status": _OK if ring7["imported"] else _BROKEN,
            "evidence": {"planner_imports": ring7["imports"], "files_parsed": ring7["files_parsed"]},
            "detail": (
                "规划器已导入经验/知识包"
                if ring7["imported"]
                else "ai_planner 对 packages.experience / packages.knowledge 零导入 —— 经验读路径未接组网"
            ),
            "checked": ring7["checked"],
        },
    ]

    weakest = next((ring for ring in rings if ring["status"] != _OK), None)
    return {
        "rings": rings,
        "weakest": weakest["name"] if weakest else None,
        "summary": {
            "ok": sum(1 for ring in rings if ring["status"] == _OK),
            "partial": sum(1 for ring in rings if ring["status"] == _PARTIAL),
            "broken": sum(1 for ring in rings if ring["status"] == _BROKEN),
            "archive_links": archive_links,
            "loop_closed": weakest is None,
        },
    }
