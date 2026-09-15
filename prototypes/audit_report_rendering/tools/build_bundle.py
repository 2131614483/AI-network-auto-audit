"""Build a portable render bundle for the high-difficulty audit-report pilot.

The exporter only reads the existing case artifacts.  Its output belongs in
this prototype directory and is deliberately separate from the audit report,
the desktop canvas, and the runtime topology registry.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "0.1.0"
CASE_RELATIVE = Path("审计项目案例") / "黔岭酒业2025年度财务报表审计_实验组_高难度"
REPORT_RELATIVE = Path("审计项目案例") / "审计报告-黔岭酒业2025年度财务报表审计(实验组_高难度).md"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_inventory(source_root: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, path in enumerate(sorted(item for item in source_root.rglob("*") if item.is_file()), 1):
        sources.append(
            {
                "id": f"S{index:03}",
                "path": path.relative_to(source_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return sources


def _source_domain_nodes(sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    by_domain: dict[str, list[str]] = {}
    for source in sources:
        domain = source["path"].split("/", 1)[0]
        by_domain.setdefault(domain, []).append(source["id"])
    nodes: list[dict[str, Any]] = []
    domain_ids: dict[str, str] = {}
    for index, (domain, source_ids) in enumerate(sorted(by_domain.items()), 1):
        node_id = f"D{index:02}"
        domain_ids[domain] = node_id
        nodes.append(
            {"id": node_id, "kind": "source_domain", "layer": "L0", "label": domain, "source_ids": source_ids}
        )
    return nodes, domain_ids


def _domain_for_group(group: str) -> str:
    if group in {"税务与补助"}:
        return "04_税务资料"
    if group in {"银行与资金"}:
        return "05_银行资料"
    if group in {"披露与列报"}:
        return "06_其他资料"
    if group in {"关联方"}:
        return "03_治理资料"
    return "02_业务资料"


def _procedure_layer(tier: str) -> str:
    return {"T1": "L2", "T2": "L2", "T3": "L3", "T4": "L4", "T5": "L4"}[tier]


def _content_hash_input(bundle: dict[str, Any]) -> dict[str, Any]:
    portable = copy.deepcopy(bundle)
    portable["integrity"].pop("content_sha256", None)
    return portable


def verify_content_sha256(bundle: dict[str, Any]) -> bool:
    return bundle["integrity"]["content_sha256"] == _canonical_sha256(_content_hash_input(bundle))


def build_high_difficulty_bundle(repository_root: Path) -> dict[str, Any]:
    case_root = repository_root / CASE_RELATIVE
    source_root = case_root / "01_被审计单位提供资料"
    evidence_path = case_root / "_tools" / "audit_evidence.json"
    report_path = repository_root / REPORT_RELATIVE
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    sources = _source_inventory(source_root)
    domain_nodes, domain_ids = _source_domain_nodes(sources)
    nodes = domain_nodes[:]
    edges: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    edge_number = 1

    for finding in evidence["findings"]:
        node_id = f"P-{finding['no']}"
        source_node = domain_ids[_domain_for_group(finding["group"])]
        nodes.append(
            {
                "id": node_id,
                "kind": "procedure",
                "layer": _procedure_layer(finding["tier"]),
                "label": f"{finding['no']} {finding['issue']}",
            }
        )
        edges.append(
            {"id": f"E{edge_number:03}", "from": source_node, "to": node_id, "kind": "evidence", "label": finding["group"]}
        )
        edge_number += 1
        findings.append(
            {
                "id": finding["no"],
                "tier": finding["tier"],
                "group": finding["group"],
                "issue": finding["issue"],
                "amount": finding["amount"],
                "status": "hit",
                "evidence": finding["evidence"],
            }
        )

    noise_exclusions: list[dict[str, Any]] = []
    for noise in evidence["noises"]:
        node_id = f"P-{noise['no']}"
        nodes.append({"id": node_id, "kind": "noise_exclusion", "layer": "L4", "label": f"{noise['no']} 噪音排除"})
        edges.append(
            {"id": f"E{edge_number:03}", "from": domain_ids["02_业务资料"], "to": node_id, "kind": "evidence", "label": "排除证据"}
        )
        edge_number += 1
        noise_exclusions.append({"id": noise["no"], "status": "excluded", "description": noise["desc"]})

    conclusion_id = "C01"
    nodes.append({"id": conclusion_id, "kind": "conclusion", "layer": "L5", "label": "审计结论：保留意见方向"})
    for node in nodes:
        if node["kind"] == "procedure":
            edges.append({"id": f"E{edge_number:03}", "from": node["id"], "to": conclusion_id, "kind": "finding", "label": "发现收敛"})
            edge_number += 1
        if node["kind"] == "noise_exclusion":
            edges.append({"id": f"E{edge_number:03}", "from": node["id"], "to": conclusion_id, "kind": "exclusion", "label": "排除结论"})
            edge_number += 1

    bundle: dict[str, Any] = {
        "contract_id": "audit-report-render-bundle",
        "contract_version": "1.0.0",
        "report": {
            "id": "qianling-2025-hard",
            "title": "黔岭酒业 2025 年度财务报表审计（实验组_高难度）",
            "markdown_ref": REPORT_RELATIVE.as_posix(),
            "report_date": evidence["report_date"],
            "verdict": "qualified_with_reservation_direction",
        },
        "sources": sources,
        "graph": {
            "direction": "LR",
            "layers": [
                {"id": "L0", "order": 0, "label": "资料接入"},
                {"id": "L2", "order": 2, "label": "规则与跨表勾稽"},
                {"id": "L3", "order": 3, "label": "分析性程序"},
                {"id": "L4", "order": 4, "label": "准则、文本与噪音排除"},
                {"id": "L5", "order": 5, "label": "结论"},
            ],
            "nodes": nodes,
            "edges": edges,
        },
        "evidence_index": {
            "artifact_ref": (CASE_RELATIVE / "_tools" / "audit_evidence.json").as_posix(),
            "sha256": _sha256_file(evidence_path),
            "findings": findings,
            "noise_exclusions": noise_exclusions,
        },
        "integrity": {
            "markdown_sha256": _sha256_file(report_path),
            "source_inventory_sha256": _canonical_sha256(sources),
            "content_sha256": "",
            "generator_version": GENERATOR_VERSION,
        },
    }
    bundle["integrity"]["content_sha256"] = _canonical_sha256(_content_hash_input(bundle))
    return bundle


def write_bundle(bundle: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "fixtures" / "qianling-2025-hard.render.json")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[3]
    bundle = build_high_difficulty_bundle(repository_root)
    write_bundle(bundle, arguments.out)
    print(f"已生成 {arguments.out}（{len(bundle['sources'])} 个源文件，{len(bundle['graph']['nodes'])} 个节点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
