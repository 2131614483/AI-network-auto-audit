"""Generate 100 per-plugin folders (ComfyUI-style) under plugins/builtin/audit-*.

Each plugin folder gets plugin.protocol.json + plugin.manifest.json
(lifecycle=contract_only; no runtime binding until implemented & verified).
Also writes docs/audit-plugin-network-100.md (the business relation map).
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _audit_plugin_specs import ALL, BUSINESS, FOUNDATION, GOVERN  # noqa: E402

ROOT = Path(r"D:\pythonpro\audit_network")
PLUGINS = ROOT / "plugins" / "builtin"
DOC = ROOT / "docs" / "audit-plugin-network-100.md"

LAYER_PREFIX = {"foundation": "audit-foundation", "govern": "audit-govern"}
STAGE_PREFIX = {
    "mandate": "audit-mandate", "risk": "audit-risk", "plan": "audit-plan",
    "field": "audit-field", "evidence": "audit-evidence", "finding": "audit-finding",
    "report": "audit-report", "remedy": "audit-remedy",
}
STAGE_NAMES = {
    "mandate": "阶段1 审计立项与准备（10）", "risk": "阶段2 风险识别与评估（11）",
    "plan": "阶段3 审计计划与资源调度（9）", "field": "阶段4 现场审计实施（14）",
    "evidence": "阶段5 审计证据与底稿管理（10）", "finding": "阶段6 问题核查与定性（8）",
    "report": "阶段7 审计报告与成果输出（7）", "remedy": "阶段8 整改跟踪与闭环管理（7）",
}
ID_PREFIX = {"foundation": "audit.foundation", "govern": "audit.govern"}
STAGE_ID = {
    "mandate": "audit.mandate", "risk": "audit.risk", "plan": "audit.plan",
    "field": "audit.field", "evidence": "audit.evidence", "finding": "audit.finding",
    "report": "audit.report", "remedy": "audit.remedy",
}


BUSINESS_STAGES = ("mandate", "risk", "plan", "field", "evidence", "finding", "report", "remedy")


def _unpack(entry):
    n, part, name, cap, kind = entry[:5]
    if isinstance(entry[5], str) and entry[5] in BUSINESS_STAGES:
        layer, stage = "business", entry[5]
        ins, outs, desc = entry[6], entry[7], entry[8]
    else:
        ins, outs, desc = entry[5], entry[6], entry[7]
        layer = "foundation" if n <= 18 else "govern"
        stage = None
    return n, part, name, cap, kind, layer, stage, ins, outs, desc


def folder_slug(entry) -> str:
    _n, part, _name, _cap, _kind, layer, stage, _i, _o, _d = _unpack(entry)
    if layer == "foundation":
        return f"{LAYER_PREFIX['foundation']}-{part}"
    if layer == "govern":
        return f"{LAYER_PREFIX['govern']}-{part}"
    return f"{STAGE_PREFIX[stage]}-{part}"


def plugin_id(entry) -> str:
    _n, part, _name, _cap, _kind, layer, stage, _i, _o, _d = _unpack(entry)
    if layer == "foundation":
        return f"{ID_PREFIX['foundation']}.{part}"
    if layer == "govern":
        return f"{ID_PREFIX['govern']}.{part}"
    return f"{STAGE_ID[stage]}.{part}"


def make_protocol(entry) -> dict:
    n, part, name, cap, kind, layer, stage = _unpack(entry)[:7]
    ins, outs, desc = entry[-3:]
    p = plugin_id(entry)
    capability = {
        "id": p,
        "summary": f"{name}：{desc[:180]}",
        "side_effects": "read_only",
        "idempotency": "input_sha256 + capability_version",
        "inputs": ins,
        "outputs": outs,
    }
    return {
        "protocol_version": "1.0.0",
        "id": p,
        "version": "0.1.0",
        "name": name,
        "description": desc,
        "kind": kind,
        "domains": ["audit", "compliance"],
        "lifecycle": "contract_only",
        "compatibility": {
            "host_protocol": ">=1.0.0 <2.0.0",
            "adapters": ["python"],
            "fallback_behavior": "human_review",
        },
        "capabilities": [capability],
        "governance": {
            "default_data_classification": "audit_confidential",
            "permissions": {
                "data_read": [f"{p}.read"],
                "data_write": [],
                "network": "none",
                "secret_refs": [],
            },
            "policy": {
                "gateway_required": True,
                "approval_required": False,
                "evidence_required": True,
            },
        },
        "resources": {"cpu": 1, "memory_mb": 256, "gpu": "none", "timeout_seconds": 300},
        "observability": {
            "trace_required": True,
            "emits": ["structured_log", "artifact_ref", "audit_event"],
            "health": "declared_only",
        },
        "provenance": {
            "source_refs": [
                f"docs/audit-plugin-network-100.md#{n}",
                "docs/插件开发与接入标准-UPP-v1.0.md",
            ]
        },
        "ui": {"mode": "none"},
    }


def make_manifest(entry) -> dict:
    n, part, name, cap, kind, layer, stage = _unpack(entry)[:7]
    ins, outs, desc = entry[-3:]
    p = plugin_id(entry)
    in_schema = ins[0]["schema_ref"]
    out_schema = outs[0]["schema_ref"]
    module = f"plugins.builtin.{folder_slug(entry).replace('-', '_')}.runtime"
    return {
        "id": p,
        "version": "0.1.0",
        "name": f"{name}（契约声明）",
        "runtime": "python",
        "entrypoint": f"{module}:handle",
        "capabilities": [p],
        "inputs": {"capability": p, "schema": in_schema, "content_type": "application/json"},
        "outputs": {"capability": p, "schema": out_schema, "content_type": "application/json"},
        "side_effects": "read_only",
        "idempotency": "input_sha256 + capability_version",
        "permissions": {"data_read": [f"{p}.read"], "data_write": [], "network": "none", "secrets": []},
        "resources": {"cpu": 1, "memory_mb": 256, "gpu": False, "timeout_seconds": 300},
        "data_classification": "audit_confidential",
        "evidence": {"preserve_inputs": True, "code_hash_required": True},
        "tests": {
            "contract": f"tests/contract/test_{folder_slug(entry).replace('-', '_')}_contract.py",
            "golden": f"tests/unit/test_{folder_slug(entry).replace('-', '_')}_runtime.py",
        },
    }


def arrow_summary(entry) -> str:
    ins, outs, desc = entry[-3:]
    in_ids = ",".join(c["contract_id"] for c in ins)
    out_ids = ",".join(c["contract_id"] for c in outs)
    return f"{in_ids or '-'} ⟹ {out_ids}"


def main() -> None:
    generated: list[tuple[int, str, str]] = []
    for entry in ALL:
        slug = folder_slug(entry)
        folder = PLUGINS / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "plugin.protocol.json").write_text(
            json.dumps(make_protocol(entry), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (folder / "plugin.manifest.json").write_text(
            json.dumps(make_manifest(entry), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        generated.append((entry[0], slug, plugin_id(entry)))

    # ---- docs: business relation map ----
    lines: list[str] = []
    a = lines.append
    a("# 审计插件网络关系梳理（100 插件循环组网）")
    a("")
    a("> 本文件是「AI 画布组网」的能力蓝图：按 ComfyUI 风格，每个插件一个独立文件夹，")
    a("> 位于 `plugins/builtin/audit-*`，含 `plugin.protocol.json`（端口契约）与 `plugin.manifest.json`（安装声明）。")
    a("> 当前全部为 `contract_only`（协议先行，只读分析，不写领域表）；实现与验证按既有 UPP 流程补齐。")
    a("> 生成器：`.data/_gen_audit_plugins.py`，规格：`.data/_audit_plugin_specs.py`。")
    a("")
    a("## 0. 数量与分层")
    a("")
    a("| 层 | 数量 | 说明 |")
    a("|---|---|---|")
    a("| 数据支撑层 foundation | 18 | 全网络共享能力底座，被上层按需调用（星型网状） |")
    a("| 核心业务循环层 business | 76 | 沿立项→风险→计划→实施→底稿→定性→报告→整改 8 阶段串联（主循环） |")
    a("| 治理优化层 govern | 6 | 承接全流程输出，反向赋能前序阶段（闭环迭代） |")
    a(f"| **合计** | **{len(ALL)}** | 目录：`plugins/builtin/audit-*` |")
    a("")
    a("三类链接模式：`⟹` 主干正向流转（阶段间数据传递）；`→` 阶段内联动（同阶段上下游）；")
    a("`⇢` 跨层能力调用（上层调支撑层）；`⇠` 反向闭环迭代（治理层赋能前序）；`⊣` 全流程穿透（管控类贯穿所有节点）。")
    a("")
    a("## 1. 全流程穿透层（5 个管控插件，⊣ 全程并行生效）")
    a("")
    a("| 插件 | 能力 | 穿透范围 |")
    a("|---|---|---|")
    a("| 权限管控 | audit.foundation.permission-control | 所有插件访问控制 |")
    a("| 数据脱敏 | audit.foundation.data-mask | 所有数据展示/对外输出 |")
    a("| 数据加密存储 | audit.foundation.data-encrypt | 所有归档/底稿存储 |")
    a("| 工作流引擎 | audit.foundation.workflow-engine | 所有审批/流转/派单 |")
    a("| 审计日志自动记录 | audit.field.audit-log | 所有操作/数据访问 |")
    a("")
    a("## 2. 数据支撑层（18 个公共节点，⇢ 被上层调用）")
    a("")
    a("| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |")
    a("|---|---|---|---|---|")
    for entry in FOUNDATION:
        a(f"| {entry[0]} | {folder_slug(entry)} | {plugin_id(entry)} | {arrow_summary(entry)} | {_unpack(entry)[-1]} |")
    a("")
    a("## 3. 核心业务循环层（76 个，8 阶段主循环）")
    a("")
    a("### 总览主循环")
    a("")
    a("```text")
    a("立项 ⟹ 风险 ⟹ 计划 ⟹ 实施 ⟹ 底稿 ⟹ 定性 ⟹ 报告 ⟹ 整改 ──┐")
    a("  └──────────────────────────────────────────────────────┘（下一审计周期回流）")
    a("治理层 ⇠ 接收全流程输出，沉淀规则/方法/案例后反向赋能前序阶段")
    a("```")
    a("")
    from collections import OrderedDict
    stages: "OrderedDict[str, list]" = OrderedDict()
    for entry in BUSINESS:
        stages.setdefault(_unpack(entry)[6], []).append(entry)
    for stage, entries in stages.items():
        a(f"### {STAGE_NAMES[stage]}")
        a("")
        a("| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |")
        a("|---|---|---|---|---|")
        for entry in entries:
            a(f"| {entry[0]} | {folder_slug(entry)} | {plugin_id(entry)} | {arrow_summary(entry)} | {_unpack(entry)[-1]} |")
        a("")
    a("### 阶段内箭头流转（主链）")
    a("")
    a("```text")
    a("阶段1 立项: 需求征集→战略对齐→立项申报→立项评审→项目库→优先级排序→通知书→资料报送→资料预检；项目库→项目组建分工")
    a("阶段2 风险: 政策扫描/行业对标/内控测绘/财务预警/断点识别/舞弊匹配→风险矩阵→等级赋值→高风险定位→应对建议（→热图）")
    a("阶段3 计划: 年度计划→项目方案→程序模板；抽样→样本量→排班→资源冲突；工时预算→版本管控")
    a("阶段4 实施: 底稿编制←凭证穿透/访谈/纪要/函证/监盘/盘点/协查；取证→疑点标记→进度填报→延期审批")
    a("阶段5 底稿: 证据归档→真实性校验→索引关联←底稿；勾稽校验→三级复核→电子签章→加密存储；版本对比/借阅审批")
    a("阶段6 定性: 疑点汇总→性质判定→违规匹配→金额计算→责任认定→分级分类→意见反馈→定性复核")
    a("阶段7 报告: 框架生成→问题描述→建议匹配→数据校验→多级审核→成果提炼→脱敏发布")
    a("阶段8 整改: 派单→方案审核→进度跟踪→逾期预警；成效验证→销号→结果公示")
    a("```")
    a("")
    a("## 4. 治理优化层（6 个，⇠ 反向赋能）")
    a("")
    a("| # | 文件夹 | 能力 | 输入 ⟹ 输出 | 链接语义 |")
    a("|---|---|---|---|---|")
    for entry in GOVERN:
        a(f"| {entry[0]} | {folder_slug(entry)} | {plugin_id(entry)} | {arrow_summary(entry)} | {_unpack(entry)[-1]} |")
    a("")
    a("### 反向闭环迭代")
    a("")
    a("```text")
    a("规则库迭代 ⇠ 更新规则/预警模型 → 风险扫描、问题定性、底稿校验")
    a("问题趋势分析 ⇠ 调整年度审计重点 → 立项准备、计划编制")
    a("典型案例库 ⇠ 对标参考 → 风险评估、报告建议")
    a("审计方法沉淀 ⇠ 优化审计程序 → 计划、现场实施")
    a("质量评分/成效评估 ⇠ 优化考核与配置 → 立项、资源调度")
    a("```")
    a("")
    a("## 5. 跨阶段直连（网状链接）")
    a("")
    a("| 源插件 | 目标阶段 | 语义 |")
    a("|---|---|---|")
    a("整改成效验证 | 证据/底稿管理 | 直接调取历史底稿证据核验 |")
    a("问题定性复核 | 现场实施 | 追溯审计日志与访谈记录 |")
    a("风险应对建议 | 计划编制 | 直接输入审计重点 |")
    a("项目库管理 | 全流程各阶段 | 同步项目状态与进度 |")
    a("")
    a("## 6. 与既有已验证审计插件的关系")
    a("")
    a("| 既有插件（已验证，含 runtime） | 本网络对应能力 | 关系 |")
    a("|---|---|---|")
    a("| audit-ledger-quality | audit.foundation.finance-clean / quality-check | 复用其隔离运行时模式（ledger CSV → candidates） |")
    a("| audit-journal-anomaly | audit.risk.finance-anomaly-alert | 同规则引擎语义，可迁移 |")
    a("| audit-finding-draft | audit.finding.issue-type-judge 等 | 输入契约 anomaly-candidates → finding-draft 可复用 |")
    a("| audit-investigation-plan | audit.plan.project-scheme-build | 计划草稿契约可复用 |")
    a("| audit-report-draft | audit.report.* 系列 | report-draft 契约可复用 |")
    a("| audit-workpaper-export | audit.report.result-distill | workpaper 契约可复用 |")
    a("| audit-evidence-lineage | audit.foundation.lineage-track / audit.evidence.* | evidence-lineage 契约可复用 |")
    a("")
    a("## 7. 数据契约（schema_ref）现状与待建")
    a("")
    a("| schema_ref | 状态 | 用途 |")
    a("|---|---|---|")
    a("| ledger-artifact-ref / audit-quality-candidates / anomaly-candidates / finding-draft / investigation-plan-draft / report-draft / workpaper-export / evidence-lineage / artifact-ref / dataset-validation / metric-series / workflow / document-content / graph-candidate-set / graph-proposal-draft / retention-recommendation | 已有（contracts/jsonschema） | 可立即作为端口契约 |")
    a("| audit-source-set / clean-finance-set / standard-biz-set / master-map / demand-set / proposal-set / risk-matrix / high-risk-area / remedy-task / case-entry 等业务语义契约 | 规划中（随对应插件实现） | 端口契约已按命名约定先行声明 |")
    a("")
    a("## 8. 运行约束")
    a("")
    a("- 全部 `contract_only` 且 `read_only`：只做影子分析，不写领域表、不触达主机/网络/基础设施。")
    a("- 所有调用必经 Policy Gateway（`gateway_required: true`），带租户、trace_id、幂等键。")
    a("- 同一数据边两端 schema_ref 一致才可编译链接（与画布组网编译器语义一致）。")
    a("- 实现与验证按「先契约→再测试→后 runtime→注册绑定→精确策略」既有流程推进。")
    a("")
    a("## 9. 生成清单（100 目录）")
    a("")
    a("| # | 文件夹 | 插件 id |")
    a("|---|---|---|")
    for n, slug, pid in generated:
        a(f"| {n} | {slug} | {pid} |")
    a("")

    DOC.write_text("\n".join(lines), encoding="utf-8")
    print(f"generated {len(generated)} plugin folders under {PLUGINS}")
    print(f"wrote {DOC}")
    print("first:", generated[0], "| last:", generated[-1])


if __name__ == "__main__":
    main()
