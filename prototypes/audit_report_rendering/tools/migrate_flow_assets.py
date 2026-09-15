"""Migrate existing case DAG specifications into portable render bundles.

The adapter reads the existing builder only.  It never writes the builder,
the generated HTML, screenshots, Markdown reports, or source data.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "0.1.0"
FLOW_BUILDER = Path("审计项目案例") / "_flow_render_builder" / "build_dag_pages.py"
CASES = {
    "qianling-2025-base": (
        "黔岭酒业2025年度财务报表审计",
        Path("审计项目案例") / "黔岭酒业2025年度财务报表审计" / "07_审计成果" / "审计报告-黔岭酒业2025年度财务报表审计.md",
        Path("_audit_digest.json"),
        "spec_base",
    ),
    "qianling-2025-experiment": (
        "黔岭酒业2025年度财务报表审计_实验组",
        Path("审计项目案例") / "黔岭酒业2025年度财务报表审计_实验组" / "07_审计成果" / "审计报告-黔岭酒业2025年度财务报表审计(实验组).md",
        Path("_ground_truth") / "检出验证报告.md",
        "spec_exp",
    ),
    "qianling-2025-hard": (
        "黔岭酒业2025年度财务报表审计_实验组_高难度",
        Path("审计项目案例") / "审计报告-黔岭酒业2025年度财务报表审计(实验组_高难度).md",
        Path("_tools") / "audit_evidence.json",
        "spec_hard",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without_content_hash(bundle: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(bundle)
    copied["integrity"].pop("content_sha256", None)
    return copied


def verify_content_sha256(bundle: dict[str, Any]) -> bool:
    return bundle["integrity"]["content_sha256"] == _canonical_sha256(_without_content_hash(bundle))


def _load_legacy_builder(repository_root: Path) -> Any:
    path = repository_root / FLOW_BUILDER
    spec = importlib.util.spec_from_file_location("legacy_case_dag_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载既有 DAG 规格：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sources(source_root: Path) -> list[dict[str, Any]]:
    paths = sorted(path for path in source_root.rglob("*") if path.is_file())
    return [
        {"id": f"S{index:03}", "path": path.relative_to(source_root).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for index, path in enumerate(paths, 1)
    ]


def _node_kind(node: dict[str, Any]) -> str:
    if node.get("virtual"):
        return "evidence_pool"
    if str(node["id"]).startswith("PN"):
        return "noise_exclusion"
    if node.get("kind") == "sink":
        return "conclusion"
    if node["layer"] == 0:
        return "source_domain"
    if "收敛" in node["name"]:
        return "finding_aggregate"
    if "影响" in node["name"] or "重编" in node["name"]:
        return "impact"
    return "procedure"


def _legacy_page_payload(specification: dict[str, Any]) -> dict[str, Any]:
    """Match the payload embedded by the existing HTML generator exactly."""
    payload = copy.deepcopy(specification)
    payload["nodes"] = [
        dict(node, kpis=[list(kpi) for kpi in node.get("kpis", [])], rows=[list(row) for row in node.get("rows", [])])
        for node in payload["nodes"]
    ]
    payload["edges"] = [
        {"from": edge["from_"] if "from_" in edge else edge["from"], "to": edge["to"], "label": edge["label"], "virtual": edge.get("virtual", False)}
        for edge in payload["edges"]
    ]
    payload["stats"] = [list(stat) for stat in payload["stats"]]
    payload["generated"] = "2026-09-15T00:00:00"
    return payload


def _legacy_evidence(specification: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for node in specification["nodes"]:
        finding = str(node.get("finding", "")).strip()
        if not finding:
            continue
        node_id = str(node["id"])
        if node_id.startswith("PN"):
            exclusions.append({"id": f"N{int(node_id[2:]):02d}", "status": "excluded", "description": finding})
        else:
            findings.append(
                {"id": node_id, "tier": f"L{node['layer']}", "group": "legacy_dag_spec", "issue": finding,
                 "amount": "not_quantified_in_dag_spec", "status": "hit", "evidence": ["既有 DAG 节点证据卡"]}
            )
    return findings, exclusions


def _hard_evidence(case_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = json.loads((case_root / "_tools" / "audit_evidence.json").read_text(encoding="utf-8"))
    findings = [
        {"id": item["no"], "tier": item["tier"], "group": item["group"], "issue": item["issue"],
         "amount": item["amount"], "status": "hit", "evidence": item["evidence"]}
        for item in data["findings"]
    ]
    exclusions = [{"id": item["no"], "status": "excluded", "description": item["desc"]} for item in data["noises"]]
    return findings, exclusions


def _bundle(
    repository_root: Path, report_id: str, config: tuple[str, Path, Path, str], builder: Any, spec_hash: str
) -> dict[str, Any]:
    case_name, report_relative, evidence_relative, factory_name = config
    case_root = repository_root / "审计项目案例" / case_name
    source_root = case_root / "01_被审计单位提供资料"
    report_path = repository_root / report_relative
    evidence_path = case_root / evidence_relative
    specification = getattr(builder, factory_name)()
    sources = _sources(source_root)
    findings, exclusions = _hard_evidence(case_root) if report_id.endswith("hard") else _legacy_evidence(specification)
    nodes = [
        {"id": node["id"], "kind": _node_kind(node), "layer": f"L{node['layer']}", "label": node["name"]}
        for node in specification["nodes"]
    ]
    edges = [
        {"id": f"E{index:03}", "from": edge.get("from_", edge.get("from")), "to": edge["to"],
         "kind": "evidence" if edge.get("virtual") else "finding", "label": edge["label"]}
        for index, edge in enumerate(specification["edges"], 1)
    ]
    bundle: dict[str, Any] = {
        "contract_id": "audit-report-render-bundle", "contract_version": "1.0.0",
        "report": {"id": report_id, "title": specification["title"], "markdown_ref": report_relative.as_posix(),
                   "report_date": "2026-09-14", "verdict": "qualified_with_reservation_direction"},
        "sources": sources,
        "graph": {"direction": "LR", "layers": [{"id": f"L{index}", "order": index, "label": label} for index, label in enumerate(specification["layers"])], "nodes": nodes, "edges": edges},
        "evidence_index": {"artifact_ref": (Path("审计项目案例") / case_name / evidence_relative).as_posix(),
                           "sha256": _sha256(evidence_path), "findings": findings, "noise_exclusions": exclusions},
        # Keep the legacy page's complete display payload.  The portable graph above
        # remains the contract-level representation; this snapshot lets the generic
        # viewer faithfully reuse the existing stage layout, evidence cards and
        # deterministic animation without reading the original source again.
        "presentation": {"renderer": "legacy-dag@1.0.0", "legacy_dag": _legacy_page_payload(specification)},
        "integrity": {"markdown_sha256": _sha256(report_path), "source_inventory_sha256": _canonical_sha256(sources),
                      "render_spec_sha256": spec_hash, "content_sha256": "", "generator_version": GENERATOR_VERSION},
    }
    bundle["integrity"]["content_sha256"] = _canonical_sha256(_without_content_hash(bundle))
    return bundle


def build_case_bundles(repository_root: Path) -> dict[str, dict[str, Any]]:
    builder_path = repository_root / FLOW_BUILDER
    builder = _load_legacy_builder(repository_root)
    spec_hash = _sha256(builder_path)
    return {report_id: _bundle(repository_root, report_id, config, builder, spec_hash) for report_id, config in CASES.items()}


def write_bundles(bundles: dict[str, dict[str, Any]], output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for report_id, bundle in bundles.items():
        (output_root / f"{report_id}.render.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    prototype_root = Path(__file__).resolve().parents[1]
    bundles = build_case_bundles(prototype_root.parents[1])
    output_root = prototype_root / "fixtures" / "migrated"
    write_bundles(bundles, output_root)
    print(f"已迁移 {len(bundles)} 份既有 DAG 规格至 {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
