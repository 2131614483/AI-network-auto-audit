from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "jsonschema"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schema_path(schema_ref: str) -> Path:
    """Resolve a ``schema_ref`` to its file.

    The field is overloaded by design: the recall registry in ``catalog`` names
    the bare schema (``finding-draft``) while a plugin protocol carries the file
    name (``finding-draft.schema.json``).  Appending the suffix twice is the
    easy mistake here, so the resolution lives in one place.
    """
    name = schema_ref if schema_ref.endswith(".schema.json") else f"{schema_ref}.schema.json"
    return SCHEMA_DIR / name


def _schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_plugin_topology_schemas_are_valid_and_keep_planning_separate_from_execution() -> None:
    for name in (
        "plugin-cluster.schema.json",
        "plugin-blueprint.schema.json",
        "plugin-topology-release.schema.json",
        "plugin-routing-plan.schema.json",
    ):
        Draft202012Validator.check_schema(_schema(name))

    Draft202012Validator(_schema("plugin-cluster.schema.json")).validate(
        {
            "id": "knowledge-extraction",
            "name": "知识抽取集群",
            "layer": "L0",
            "axis": "capability_family",
            "domains": ["knowledge", "graph"],
            "routing_budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        }
    )
    Draft202012Validator(_schema("plugin-blueprint.schema.json")).validate(
        {
            "id": "mineru-local-slot",
            "name": "本地文档抽取槽位",
            "cluster_memberships": [
                {"cluster_id": "knowledge-extraction", "role": "capability"},
                {"cluster_id": "runtime-cv", "role": "runtime"},
            ],
            "lifecycle": "planned",
            "maturity": "contracted",
            "capabilities": ["knowledge.extract.document"],
            "input_contracts": [
                {
                    "contract_id": "artifact-ref",
                    "version": "1.0.0",
                    "format": "artifact_ref",
                    "delivery": "reference",
                    "data_classification": "audit_confidential",
                }
            ],
            "output_contracts": [
                {
                    "contract_id": "knowledge-draft",
                    "version": "1.0.0",
                    "format": "json",
                    "delivery": "artifact_ref",
                    "data_classification": "audit_confidential",
                }
            ],
            "risk_class": "read_only",
            "source_refs": [{"kind": "design_document", "uri": "reference://TA-01"}],
        }
    )
    Draft202012Validator(_schema("plugin-topology-release.schema.json")).validate(
        {
            "release_id": "topology-20260904-001",
            "version": "1.0.0",
            "status": "published",
            "checksum_sha256": "a" * 64,
            "cluster_ids": ["knowledge-extraction", "runtime-cv"],
            "blueprint_ids": ["mineru-local-slot"],
            "created_at": "2026-09-04T12:00:00Z",
        }
    )
    Draft202012Validator(_schema("plugin-routing-plan.schema.json")).validate(
        {
            "plan_id": "plan-20260904-001",
            "intent": "将 PDF 加入知识库",
            "mode": "plan_only",
            "planner_version": "1.0.0",
            "catalog_release": {"release_id": "topology-20260904-001", "version": "1.0.0", "checksum_sha256": "a" * 64},
            "candidate_clusters": ["knowledge-extraction"],
            "nodes": [{"key": "extract", "kind": "blueprint", "blueprint_id": "mineru-local-slot", "capability": "knowledge.extract.document"}],
            "edges": [],
            "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
            "requires_approval": False,
            "unresolved_capabilities": [],
            "created_at": "2026-09-04T12:00:00Z",
            "trace_id": "eb86326d-077d-4d07-879d-c37359b2440d",
        }
    )


def test_plugin_routing_plan_rejects_an_executable_mode_or_undeclared_blueprint_node() -> None:
    schema = _schema("plugin-routing-plan.schema.json")
    invalid = {
        "plan_id": "plan-20260904-002",
        "intent": "不应执行",
        "mode": "execute",
        "planner_version": "1.0.0",
        "catalog_release": {"release_id": "topology-20260904-001", "version": "1.0.0", "checksum_sha256": "a" * 64},
        "candidate_clusters": ["knowledge-extraction"],
        "nodes": [{"key": "x", "kind": "blueprint", "blueprint_id": "slot", "capability": "knowledge.extract.document"}],
        "edges": [],
        "budget": {"max_candidates": 8, "max_chain_length": 4, "max_latency_ms": 5000},
        "requires_approval": False,
        "unresolved_capabilities": [],
        "created_at": "2026-09-04T12:00:00Z",
        "trace_id": "eb86326d-077d-4d07-879d-c37359b2440d",
    }
    errors = list(Draft202012Validator(schema).iter_errors(invalid))
    assert errors


# -- port contract hashes must be real ----------------------------------------

def test_port_contract_hashes_are_the_real_schema_bytes() -> None:
    """The placeholder ``"a"*64`` let contract divergence pass every gate.

    The registry now derives each digest from the schema file at import, so
    there is no hand-written value left that *could* be wrong -- the value
    briefly shipped in its place was a bare ``"pending"``, which is exactly as
    false as the placeholder it replaced.
    """
    from packages.ai_planner import catalog

    assert set(catalog._PORT_SCHEMA_REFS) == set(catalog.PORT_CONTRACTS)
    for port_id, contract in catalog.PORT_CONTRACTS.items():
        schema_ref = str(contract["schema_ref"])
        assert schema_ref == catalog._PORT_SCHEMA_REFS[port_id]
        assert contract["schema_sha256"] == _file_sha256(_schema_path(schema_ref)), (
            f"{port_id}: stored hash does not match {schema_ref}.schema.json on disk"
        )


def test_no_port_contract_declares_a_non_digest() -> None:
    """A hash that is not 64 hex characters is a placeholder wearing a costume."""
    from packages.ai_planner import catalog

    for port_id, contract in catalog.PORT_CONTRACTS.items():
        digest = str(contract["schema_sha256"])
        assert len(digest) == 64 and all(char in "0123456789abcdef" for char in digest), (
            f"{port_id}: {digest!r} is not a sha256 digest"
        )


def test_a_composed_node_declares_a_real_digest_on_every_port() -> None:
    """Regression: the placeholder was dropped from the port defaults without
    deriving a replacement, so *every* composed draft failed the compiler's
    contract gate with "port ... must declare schema_sha256"."""
    from packages.ai_planner.workbench import new_draft

    draft = new_draft("aiops", "告警处置闭环")
    ports = [
        port
        for node in draft["nodes"]
        for port in (*node["input_ports"], *node["output_ports"])
    ]
    assert ports, "a composed draft should declare its ports"
    for port in ports:
        assert port["schema_sha256"] == _file_sha256(_schema_path(str(port["schema_ref"]))), (
            f"{port['port_id']}: composed port carries a digest that is not the file's"
        )


def test_shipped_protocols_derive_their_digest_instead_of_declaring_one() -> None:
    """A port may state its own ``schema_sha256`` (a synthetic fixture has no
    file to hash), but a *shipped* plugin must not: a declared digest is a
    digest nobody can check, which is the same disease as the placeholder it
    replaced.  Every plugin under ``plugins/builtin`` is required to omit it."""
    declaring: list[str] = []
    for protocol in sorted((ROOT / "plugins" / "builtin").rglob("plugin.protocol.json")):
        raw = json.loads(protocol.read_text(encoding="utf-8"))
        for capability in raw.get("capabilities") or []:
            entries = list(capability.get("inputs") or []) + list(capability.get("outputs") or [])
            for entry in entries:
                if entry.get("schema_sha256"):
                    declaring.append(f"{protocol.relative_to(ROOT)}: {entry.get('contract_id')}")
    assert not declaring, (
        "shipped protocol(s) declare a digest instead of letting it be derived "
        "from the schema file:\n  " + "\n  ".join(declaring)
    )


def test_discovered_plugins_have_resolvable_schemas() -> None:
    """The whole directory is wired by contract -- every declared schema_ref must
    name a real schema file, or fail-closed."""
    from packages.ai_planner.composer import discover_plugins, schema_sha256

    for plugin_id, spec in discover_plugins().items():
        for port in (*spec.inputs, *spec.outputs):
            # Raises ValueError (not a placeholder) if the schema is missing.
            schema_sha256(port.schema_ref)
