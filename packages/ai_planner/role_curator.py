"""Role curation: let a model propose node roles, behind deterministic gates.

Why a model at all: deciding whether a plugin is a data *shell*, a reusable
*service*, a *scheduler* or a *judgment* node is a reading of its name,
description and ports — semantic classification, which is what models are good
at and what id-marker heuristics are not.  Two heuristics were tried and both
failed (documented in ``roles.derive_role``): output schema alone mislabels
every foundation utility a validator, and requiring a verdict-shaped output name
misses genuine verifiers like ``asset-check``.

What the model is **not** allowed to do: decide by itself.  Each proposal passes
deterministic gates, and anything thin — an unknown role, an empty reason, low
confidence, or the model answering "I don't know" — is rejected and the plugin
stays ``review``.  A rejected proposal is recorded with why, so the gap stays
visible instead of being quietly filled with a guess.

The result is a reviewed artifact (``contracts/semantics/role-map.json``) rather
than a runtime call: the map records a confidence and a reason per plugin, so a
human can audit any single entry, and the composition path stays deterministic
and offline.

Read-only apart from :func:`write_role_map`, which only ever writes the artifact
the caller names.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .composer import PluginSpec
from .domain_pack import DomainPack
from .roles import ROLE_NAMES_ZH, ROLE_REVIEW, ROLES, resolve_all

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROLE_MAP_PATH = PROJECT_ROOT / "contracts" / "semantics" / "role-map.json"

#: Below this, a proposal is not adopted.  Deliberately conservative: an
#: unclassified node costs one line in a report, a wrongly-classified one
#: silently drops half of what a plugin does.
DEFAULT_MIN_CONFIDENCE = 0.6

_ROLE_GUIDE = "\n".join(
    f"- {key}: {ROLE_NAMES_ZH[key]}" for key in ROLES if key != ROLE_REVIEW
)

_SYSTEM = (
    "你是审计/AIOps 插件网络的节点角色判定器。"
    "你只输出一个 JSON 对象，不含任何其他文字、不含 markdown 代码块。"
    "你不得执行任何动作、不得调用工具、不得读取或修改文件。"
)


@dataclass(frozen=True, slots=True)
class RoleProposal:
    plugin_id: str
    role: str
    confidence: float
    reason: str
    facets: tuple[str, ...] = ()
    accepted: bool = False
    rejection: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "facets": list(self.facets),
            "confidence": self.confidence,
            "reason": self.reason,
            "accepted": self.accepted,
        }
        if self.rejection:
            payload["rejection"] = self.rejection
        return payload


def build_messages(plugin_id: str, spec: PluginSpec) -> list[dict[str, str]]:
    """The prompt: facts about one plugin, and the closed role vocabulary."""
    inputs = ", ".join(f"{p.port_id}" for p in spec.inputs) or "无"
    outputs = ", ".join(f"{p.port_id}" for p in spec.outputs) or "无"
    user = (
        f"插件 id: {plugin_id}\n"
        f"名称: {spec.name or '（无）'}\n"
        f"说明: {spec.description or '（无）'}\n"
        f"输入契约: {inputs}\n"
        f"输出契约: {outputs}\n"
        f"已声明的调用(invokes): {', '.join(spec.invokes) or '无'}\n"
        "\n"
        "可选角色（必须从中选一个）:\n"
        f"{_ROLE_GUIDE}\n"
        "\n"
        "判定要点：\n"
        "· 输入是外部文件/人工输入的入口 → ingress；把原始数据归一/标准化的外壳 → adapter\n"
        "· 输出是校验/核验报告，消费一个完整对象并给出判定 → validator\n"
        "· 被其他插件通过 invokes 调用的可复用能力 → service\n"
        "· 负责排期/派单/审批流转而非产出数据对象 → scheduler\n"
        "· 把对象变成结论/分级/排序 → decision；其余消费并产出新对象的 → transform\n"
        "· 业务链条的终点产物，无下游是设计如此 → sink\n"
        "\n"
        '只输出 JSON：{"role": "<角色>", "confidence": <0到1的小数>, '
        '"reason": "<一句话中文依据>", "facets": ["<次要角色面，可空>"]}\n'
        "若确实无法判断，confidence 给低于 0.6 的值并说明原因 —— 宁可交人工，不要猜。"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_proposal(
    plugin_id: str, raw: Any, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> RoleProposal:
    """Apply the deterministic gates to one model answer.

    Every gate is fail-closed toward ``review``: the model's answer is only ever
    a *proposal*, and anything incomplete keeps the plugin visibly unclassified.
    """
    if not isinstance(raw, Mapping):
        return RoleProposal(plugin_id, ROLE_REVIEW, 0.0, "", rejection="模型输出不是 JSON 对象")

    role = str(raw.get("role") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    raw_confidence = raw.get("confidence")
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float, str)):
        return RoleProposal(plugin_id, ROLE_REVIEW, 0.0, reason, rejection="confidence 不是数字")
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        return RoleProposal(plugin_id, ROLE_REVIEW, 0.0, reason, rejection="confidence 不是数字")

    facets_raw = raw.get("facets") or ()
    facets = tuple(
        str(f) for f in facets_raw
        if isinstance(facets_raw, (list, tuple)) and str(f) in ROLES and str(f) != ROLE_REVIEW
    )

    def reject(why: str) -> RoleProposal:
        return RoleProposal(plugin_id, ROLE_REVIEW, confidence, reason, facets, rejection=why)

    if role not in ROLES:
        return reject(f"未知角色 {role!r}")
    if role == ROLE_REVIEW:
        return reject("模型自述无法判断")
    if not reason:
        return reject("缺少判定依据")
    if confidence < min_confidence:
        return reject(f"置信度 {confidence:.2f} 低于阈值 {min_confidence:.2f}")
    return RoleProposal(plugin_id, role, confidence, reason, facets, accepted=True)


def propose_role(
    plugin_id: str,
    spec: PluginSpec,
    llm: Any,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> RoleProposal:
    """Ask the model once and gate its answer.  Never raises on a bad answer."""
    messages = build_messages(plugin_id, spec)
    try:
        raw = llm.complete_json(messages, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 - a model outage must not fake a role
        return RoleProposal(
            plugin_id, ROLE_REVIEW, 0.0, "",
            rejection=f"模型不可用：{type(exc).__name__}",
        )
    return parse_proposal(plugin_id, raw, min_confidence=min_confidence)


def curate_roles(
    specs: Mapping[str, PluginSpec],
    pack: DomainPack,
    llm: Any,
    *,
    catalog: Any | None = None,
    pack_roles: Mapping[str, str] | None = None,
    only_review: bool = True,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int | None = None,
) -> tuple[RoleProposal, ...]:
    """Propose roles for the plugins that need one, in deterministic order.

    ``only_review`` restricts the run to plugins still stuck at ``review`` — the
    ones the heuristics could not classify — so a curation pass costs a handful
    of calls rather than one per plugin.

    ``pack_roles`` overrides which assignments count as already-decided.  Pass
    ``{}`` to curate against a clean slate (the heuristics only), which is what
    a re-curation from scratch needs; the default honours the shipped role map,
    so a second pass asks only about what is still undecided.
    """
    del pack  # the prompt carries the facts; the pack is here for callers that filter by domain
    assignments = resolve_all(specs, catalog=catalog, pack_roles=pack_roles)
    targets = [
        plugin_id for plugin_id in sorted(specs)
        if not only_review or assignments[plugin_id].role == ROLE_REVIEW
    ]
    if limit is not None:
        targets = targets[:limit]
    return tuple(propose_role(pid, specs[pid], llm, min_confidence=min_confidence) for pid in targets)


# --------------------------------------------------------------------------
# the artifact
# --------------------------------------------------------------------------

def write_role_map(
    path: Path | None,
    proposals: Iterable[RoleProposal],
    *,
    domain: str = "audit",
    reviewed_by: str = "ai-assisted (unreviewed by a human)",
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the curated map, keeping rejected proposals for audit.

    ``accepted`` entries change the classification; rejected ones are kept so
    the next human pass sees exactly which plugins the model could not judge and
    why, instead of a silently shortened list.
    """
    target = Path(path) if path is not None else DEFAULT_ROLE_MAP_PATH
    roles: dict[str, Any] = {}
    rejected: dict[str, Any] = {}
    for proposal in proposals:
        record = proposal.as_dict()
        (roles if proposal.accepted else rejected)[proposal.plugin_id] = record
    if previous:
        kept = previous.get("roles") or {}
        for plugin_id, entry in kept.items():
            roles.setdefault(plugin_id, entry)
    payload = {
        "schema_version": "1.0.0",
        "domain": domain,
        "reviewed_by": reviewed_by,
        "note": (
            "角色判定结果。accepted=true 的条目参与分类；rejected 保留供人工复核 —— "
            "模型判不出来的节点必须留在 rejected 里，不得为了让统计好看而当作已判定。"
        ),
        "roles": dict(sorted(roles.items())),
        "rejected": dict(sorted(rejected.items())),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
