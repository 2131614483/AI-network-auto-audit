"""CW5 capability recall: the deterministic whitelist of what the AI may use.

The recall list is the *only* source of acceptable capabilities, ports and
contracts.  A draft referencing anything outside it is rejected with
``capability_unavailable`` / ``data_boundary_denied`` before compilation — the
model can never widen its own permission by inventing a plugin name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:  # `composer` imports nothing from here at runtime
    from .composer import PluginSpec, schema_sha256
else:
    from .composer import schema_sha256
from uuid import UUID

import psycopg2


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    capability: str
    plugin_id: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    #: 下面三个是**给模型看的理解面**，不参与校验。此前 recall 只给模型
    #: `capability (plugin= inputs= outputs=)` —— 模型知道"能接什么、能吐什么"，
    #: 却不知道"这个能力是干什么的"。实测后果：同一目标自由组网 5 次，每次都
    #: 只能组出同一条两节点链。留空时提示词与从前**逐字节一致**（向后兼容）。
    description: str = ""
    domains: tuple[str, ...] = ()
    lifecycle: str = ""


# Verified runtime plugins (the CW3 execution engine really runs these through
# its isolated runtime).  They are declared in the plugin manifests under
# tests/contract/test_quant_experiment_evaluator_contract.py and are not part
# of the DB blueprint directory — recall must include them so a draft can be
# compiled AND executed, not just look plausible.
RUNTIME_CAPABILITIES: dict[str, CapabilityEntry] = {
    "audit.ledger.validate": CapabilityEntry(
        capability="audit.ledger.validate",
        plugin_id="audit.ledger-quality",
        inputs=("ledger",),
        outputs=("candidates",),
    ),
    "quant.experiment.evaluate": CapabilityEntry(
        capability="quant.experiment.evaluate",
        plugin_id="quant.experiment-evaluator",
        inputs=("experiment",),
        outputs=("evaluation",),
    ),
}


# Authoritative port contracts (方案 5.1).  The recall list is the *only*
# source of acceptable capabilities, ports and contracts: the model must copy
# these values verbatim into its draft, so a source port and a target port
# carrying the same ``schema_ref`` compile into a data edge without the model
# inventing a schema.  ``port_id -> contract fields`` (port_id/direction are
# positional and excluded here).
_PORT_DEFAULTS: dict[str, object] = {
    "schema_version": "1.0.0",
    "media_type": "application/json",
    "required": True,
    "cardinality": "one",
    "classification": "internal",
    "transport": "artifact_ref",
}

#: ``port_id -> schema_ref``, and nothing else written by hand.  The hash is
#: derived from the schema file at import, because a hand-written digest is a
#: digest that can be wrong: the previous value was a 64-character placeholder
#: that made two divergent contracts compare equal through every gate, and the
#: value shipped in its place ("pending") would have been just as false to any
#: reader of this registry.
_PORT_SCHEMA_REFS: dict[str, str] = {
    "ledger": "ledger-artifact-ref",
    "candidates": "audit-quality-candidates",
    "audit-quality-candidates": "audit-quality-candidates",
    "finding-draft": "finding-draft",
    "experiment": "backtest-report",
    "evaluation": "experiment-evaluation",
    "research-note-draft": "research-note-draft",
}
PORT_CONTRACTS: dict[str, dict[str, object]] = {
    port_id: {**_PORT_DEFAULTS, "schema_ref": ref, "schema_sha256": schema_sha256(ref)}
    for port_id, ref in _PORT_SCHEMA_REFS.items()
}
# Fields of a port contract the model must reproduce exactly.
_CONTRACT_FIELDS = (
    "schema_ref", "schema_version", "schema_sha256", "media_type",
    "required", "cardinality", "classification", "transport",
)


def _contract_json(port_id: str, registry: Mapping[str, dict[str, object]]) -> str:
    """Serialize the contract fields *this registry* actually pins.

    The registry is an argument rather than the module-level ``PORT_CONTRACTS``
    because a directory-derived one (``build_port_contracts``) deliberately
    omits ``required``/``cardinality`` — those follow the direction, not the
    contract — and echoing a field the registry does not pin would ask the
    model to reproduce a value no gate will accept.  Iterating the registry's
    own keys mirrors ``draft._validate_port_contract``, which also compares
    only the registered fields.  The ``_CONTRACT_FIELDS`` order is preserved so
    the hand-written registry keeps emitting its historical byte-for-byte text.
    """
    contract = registry[port_id]
    return json.dumps(
        {field: contract[field] for field in _CONTRACT_FIELDS if field in contract},
        ensure_ascii=False,
        separators=(",", ":"),
    )


# Blueprint key -> runtime plugin id: the topology blueprint key is the
# governance identity (slot in the catalog graph); the runtime id is what the
# isolated executor actually runs.  A draft carrying a blueprint key as
# plugin_id compiles but can never execute, so recall must expose the runtime id.
PLUGIN_ID_BY_BLUEPRINT_KEY: dict[str, str] = {
    "ledger-quality-slot": "audit.ledger-quality",
    "finding-draft-slot": "audit.finding-draft",
    "research-note-slot": "quant.research-note-draft",
}


def capability_catalog_from_db(
    database_url: str, tenant_id: UUID, *, include_runtime: bool = True,
) -> dict[str, CapabilityEntry]:
    """Build the recall whitelist from ``topology.plugin_blueprints`` plus the
    verified runtime plugins (DB entries win on key conflicts)."""
    entries: dict[str, CapabilityEntry] = {}
    with psycopg2.connect(database_url) as connection, connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.fetchone()
        cur.execute(
            """SELECT key, capability_contract FROM topology.plugin_blueprints
               WHERE status IN ('active','planned') ORDER BY key""",
        )
        for key, contract_raw in cur.fetchall():
            contract: dict[str, Any] = {}
            if isinstance(contract_raw, dict):
                contract = contract_raw
            elif isinstance(contract_raw, str):
                try:
                    parsed = json.loads(contract_raw)
                    if isinstance(parsed, dict):
                        contract = parsed
                except json.JSONDecodeError:
                    continue
            capability = str(contract.get("capability") or "")
            if not capability:
                continue
            entries[capability] = CapabilityEntry(
                capability=capability,
                # Execute against the verified runtime plugin id, not the
                # blueprint key; blueprint keys are topology identities and the
                # isolated executor rejects them.
                plugin_id=PLUGIN_ID_BY_BLUEPRINT_KEY.get(str(key), str(key)),
                inputs=tuple(str(item) for item in contract.get("inputs") or ()),
                outputs=tuple(str(item) for item in contract.get("outputs") or ()),
            )
    if include_runtime:
        for capability, entry in RUNTIME_CAPABILITIES.items():
            entries.setdefault(capability, entry)
    return entries


def _port_hints(catalog: dict[str, CapabilityEntry]) -> dict[str, str]:
    """每条能力的"上游/下游"一句话，**从 catalog 自身推导**，不新增事实源。

    模型的处境是：它知道有哪些能力、各自吃什么吐什么，但要自己把 107 条两两比对才能
    发现"谁能喂饱谁"。实测后果正是它 5 次只组出同一条链。这里把匹配结果直接写出来，
    等于替它做掉那一步；`←无` / `→无` 同时点出了**补链方案**里的两类缺口
    （不可填输入 / 死端输出），模型也就知道哪些端口得靠外部注入。
    """
    producers: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    for capability, entry in catalog.items():
        for port in entry.inputs:
            consumers.setdefault(port, []).append(capability)
        for port in entry.outputs:
            producers.setdefault(port, []).append(capability)

    def _names(capabilities: list[str], exclude: str) -> str:
        # 排除自己（自产自销不构成上下游），最多列 2 个，其余计数收尾。
        others = [name for name in capabilities if name != exclude]
        if not others:
            return "无"
        shown = ",".join(sorted(others)[:2])
        return shown if len(others) <= 2 else f"{shown}…({len(others)})"

    hints: dict[str, str] = {}
    for capability, entry in catalog.items():
        parts = [f"{port}←{_names(producers.get(port, []), capability)}" for port in entry.inputs]
        parts += [f"{port}→{_names(consumers.get(port, []), capability)}" for port in entry.outputs]
        hints[capability] = " · ".join(parts)
    return hints


def adapter_kinds() -> tuple[str, ...]:
    """插件协议允许的适配器种类（`python` / `node` / `http` / `mcp` / …）。

    从 `contracts/jsonschema/unified-plugin-protocol.schema.json` 的枚举读，**不另抄一份**：
    MCP 支持早就写在协议里（`adapters` 枚举含 `"mcp"`），抄一份出来就会与协议脱节。
    提示词里给出这份清单，模型才知道目录里的能力不止一种运行形态。
    读不到时返回空元组（提示词少一行，不影响其余内容）。
    """
    global _ADAPTER_CACHE
    if _ADAPTER_CACHE is not None:
        return _ADAPTER_CACHE
    _ADAPTER_CACHE = ()
    path = Path(__file__).resolve().parents[2] / "contracts" / "jsonschema" / "unified-plugin-protocol.schema.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _ADAPTER_CACHE
    # 递归找"包含 mcp 的那个枚举"，而不是硬编码路径 —— 它住在 $defs 下，
    # 而 schema 的结构是会变的；写死路径会让这里静默返回空（我第一次就写错了）。
    stack = [raw]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            enum = node.get("enum")
            if isinstance(enum, list) and any(str(item) == "mcp" for item in enum):
                _ADAPTER_CACHE = tuple(str(item) for item in enum)
                return _ADAPTER_CACHE
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return _ADAPTER_CACHE


_ADAPTER_CACHE: tuple[str, ...] | None = None


def _entry_context(entry: CapabilityEntry) -> str:
    """能力的一句话说明 + 域 + 生命周期；都为空时返回空串（提示词不变）。"""
    parts: list[str] = []
    if entry.description.strip():
        # 截断：描述进的是提示词，长度必须可控。240 字足以说清"做什么"。
        text = " ".join(entry.description.split())
        parts.append(text if len(text) <= 240 else f"{text[:239]}…")
    if entry.domains:
        parts.append(f"域={','.join(entry.domains)}")
    if entry.lifecycle:
        parts.append(f"生命周期={entry.lifecycle}")
    return "；".join(parts)


def enrich_from_directory(
    catalog: dict[str, CapabilityEntry], root: Path | None = None
) -> dict[str, CapabilityEntry]:
    """用插件目录里的描述补全理解面（就地返回同一个 catalog）。

    描述的事实源是插件目录（`plugin.protocol.json` / manifest），不是 DB 蓝图 —— 蓝图只有
    能力与契约。目录里没有对应插件的条目（例如运行时内置能力）保持留空，**不编造描述**。
    """
    from dataclasses import replace

    from packages.ai_planner.composer import discover_plugins

    specs = discover_plugins(root)
    by_capability = {spec.capability: spec for spec in specs.values()}
    for capability, entry in list(catalog.items()):
        spec = by_capability.get(capability)
        if spec is None:
            continue
        # CapabilityEntry 是 frozen 的 —— 必须造新对象，不能就地改。
        catalog[capability] = replace(
            entry,
            description=spec.description or entry.description,
            domains=spec.domains or entry.domains,
            lifecycle=spec.lifecycle or entry.lifecycle,
        )
    return catalog


def _capability_order(
    catalog: dict[str, CapabilityEntry], prior: Mapping[str, float] | None
) -> list[str]:
    """能力清单的呈现顺序：**先验降序 → capability 字典序**。

    环 7（回流生效）的落点。它只改顺序：本函数不做任何截断，返回的成员集合与
    ``sorted(catalog)`` 逐项相同 —— 这正是设计 §6.3「仅改概率分布、不得新增能力或
    端口」所要求的最小影响面。没有先验时输出与从前逐字节一致。
    """
    if not prior:
        return sorted(catalog)
    return sorted(catalog, key=lambda cap: (-float(prior.get(catalog[cap].plugin_id, 0.0)), cap))


def recall_snapshot(
    catalog: dict[str, CapabilityEntry],
    port_contracts: Mapping[str, dict[str, object]] | None = None,
    *,
    prior: Mapping[str, float] | None = None,
) -> str:
    """Compact deterministic prompt block: capability -> ports/contracts.

    Ports carry their full registered contract so the model reproduces the
    exact schema values (never invents a schema_ref).  The header states the
    verbatim-copy rule; a draft that deviates is rejected by ``validate_draft``.

    ``port_contracts`` must be the *same* registry handed to
    ``validate_draft``/``compile_plan``.  It defaults to the hand-written
    :data:`PORT_CONTRACTS` (seven legacy ports) for backward compatibility, but
    a caller planning against the real plugin directory must pass the
    directory-derived registry from :func:`build_port_contracts` — otherwise
    every port outside the seven is omitted from the prompt while validation
    still demands it be copied verbatim, and the draft can only fail with
    ``contract_mismatch``.
    """
    registry: Mapping[str, dict[str, object]] = (
        port_contracts if port_contracts is not None else PORT_CONTRACTS
    )
    lines = ["能力清单（只能使用这些 capability；端口的契约字段必须逐字段原样复制，不得省略、修改或新增）："]
    hints = _port_hints(catalog)
    for capability in _capability_order(catalog, prior):
        entry = catalog[capability]
        lines.append(
            f"- {capability} (plugin={entry.plugin_id}, inputs={','.join(entry.inputs) or 'none'}, "
            f"outputs={','.join(entry.outputs) or 'none'})"
        )
        # 理解面：告诉模型"这个能力是干什么的、属于哪个域、处于什么生命周期"。
        # 留空则整行不输出 —— 既有调用方的提示词因此逐字节不变。
        context = _entry_context(entry)
        if context:
            lines.append(f"  {context}")
        hint = hints.get(capability)
        if hint:
            lines.append(f"  · {hint}")
        for direction, names in (("input", entry.inputs), ("output", entry.outputs)):
            for port_id in names:
                if port_id not in registry:
                    continue
                lines.append(
                    f"  - {direction} port \"{port_id}\" contract: {_contract_json(port_id, registry)}"
                )
    return "\n".join(lines) if lines else "(no capabilities recalled)"


# --------------------------------------------------------------------------
# deriving the port-contract directory from the plugin directory
# --------------------------------------------------------------------------

#: The contract fields a port must reproduce verbatim.  Mirrors
#: ``draft._CONTRACT_FIELDS``; ``direction`` is positional and excluded.
PORT_CONTRACT_FIELDS: tuple[str, ...] = (
    "schema_ref", "schema_version", "schema_sha256", "media_type",
    "required", "cardinality", "classification", "transport",
)


def build_port_contracts(
    specs: Mapping[str, PluginSpec],
) -> tuple[dict[str, dict[str, object]], dict[str, list[str]]]:
    """``port_id -> contract`` derived from the plugin directory.

    The hand-written ``PORT_CONTRACTS`` above covers seven ports — the legacy
    ledger/finding/research-note flow.  Anything planning the real plugin network
    needs the *whole* directory: ``validate_draft`` rejects a port that is not in
    the registry it was given, so with the seven-entry one every AI draft of an
    audit or aiops flow fails with ``contract_mismatch`` before the compiler is
    even reached.

    Returns the registry plus ``port_id -> [schema_refs]`` for any port two
    plugins disagree about.  A conflict is reported, never silently resolved:
    picking one would make the model's draft valid for a contract it did not see.
    """
    contracts: dict[str, dict[str, object]] = {}
    seen: dict[str, set[str]] = {}
    for spec in specs.values():
        for port in (*spec.inputs, *spec.outputs):
            seen.setdefault(port.port_id, set()).add(port.schema_ref)
            # `required` / `cardinality` are deliberately not pinned here: one
            # port id may be produced as `one` and consumed as `many` (a merge
            # input), so they follow the direction.  The compiler's fan-in and
            # required-input gates are their authority.
            contracts.setdefault(port.port_id, {
                "schema_ref": port.schema_ref,
                "schema_version": "1.0.0",
                "schema_sha256": schema_sha256(port.schema_ref),
                "media_type": "application/json",
                "classification": "internal",
                "transport": "artifact_ref",
            })
    conflicts = {pid: sorted(refs) for pid, refs in seen.items() if len(refs) > 1}
    return contracts, conflicts
