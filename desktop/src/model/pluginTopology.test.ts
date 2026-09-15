import { describe, expect, it } from "vitest";
import { anyRunRunning, createTopologyPlan, evidenceHashShort, evidenceMismatchLabel, evidenceScopeLabel, evidenceSourceTableLabel, evidenceVerifiedLabel, executionModeLabel, PLANNING_INTENT_MAX, planningIntentCanGenerate, planningIntentEvidenceNote, planningIntentLengthHint, planningIntentMatchKindLabel, planningIntentMatchSourceLabel, planningIntentNodeTypeLabel, pluginTopologyCatalog, proposalActionLabel, proposalCanDecide, proposalCanRerun, proposalStatusLabel, rollbackActionLabel, runCanVerify, runStatusLabel, verificationStatusLabel } from "./pluginTopology";

describe("plugin topology planning catalog", () => {
  it("models each planned blueprint across more than one cluster axis", () => {
    expect(pluginTopologyCatalog.clusters.some((cluster) => cluster.axis === "business_domain")).toBe(true);
    expect(pluginTopologyCatalog.clusters.some((cluster) => cluster.axis === "capability_family")).toBe(true);
    expect(pluginTopologyCatalog.blueprints.every((blueprint) => blueprint.memberships.length >= 2)).toBe(true);
    expect(pluginTopologyCatalog.blueprints.every((blueprint) => blueprint.lifecycle === "planned")).toBe(true);
  });

  it("returns an auditable plan_only chain for local PDF knowledge extraction", () => {
    const plan = createTopologyPlan("将 PDF 加入知识库并进行本地提取");
    expect(plan.mode).toBe("plan_only");
    expect(plan.nodes.map((node) => node.blueprintId)).toContain("mineru-local-slot");
    expect(plan.nodes.every((node) => node.lifecycle === "planned")).toBe(true);
    expect(plan.catalogRelease.checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(plan.unresolvedCapabilities).toEqual([]);
  });

  it("reports a capability gap instead of silently selecting an unrelated blueprint", () => {
    const plan = createTopologyPlan("执行真实券商下单");
    expect(plan.mode).toBe("plan_only");
    expect(plan.nodes).toEqual([]);
    expect(plan.unresolvedCapabilities).toContain("trading.execute.live");
    expect(plan.requiresApproval).toBe(true);
  });

  it("labels simulated and isolated execution rows distinctly", () => {
    expect(executionModeLabel("simulated")).toBe("影子模拟");
    expect(executionModeLabel("isolated")).toBe("受限演练");
    expect(executionModeLabel("unknown-mode")).toBe("unknown-mode");
    expect(executionModeLabel("")).toBe("未知");
  });

  it("labels chain-run states with the run state machine vocabulary", () => {
    expect(runStatusLabel("running")).toBe("运行中");
    expect(runStatusLabel("success")).toBe("成功");
    expect(runStatusLabel("failed")).toBe("失败");
    expect(runStatusLabel("queued")).toBe("queued");
    expect(runStatusLabel("")).toBe("未知");
  });

  it("disables a new run launch while any run is still running", () => {
    expect(anyRunRunning([])).toBe(false);
    expect(anyRunRunning([{ status: "success" }, { status: "failed" }])).toBe(false);
    expect(anyRunRunning([{ status: "success" }, { status: "running" }])).toBe(true);
    expect(anyRunRunning([{ status: "running" }])).toBe(true);
  });

  it("labels run verification status with verified/drifted vocabulary", () => {
    expect(verificationStatusLabel("verified")).toBe("已验证");
    expect(verificationStatusLabel("drifted")).toBe("漂移");
    expect(verificationStatusLabel("")).toBe("未验证");
  });

  it("says only success runs may be verified (fail-closed otherwise)", () => {
    expect(runCanVerify({ status: "success" })).toBe(true);
    expect(runCanVerify({ status: "running" })).toBe(false);
    expect(runCanVerify({ status: "failed" })).toBe(false);
    expect(runCanVerify({ status: "queued" })).toBe(false);
  });

  it("maps rollback verdict actions to read-only advisory labels", () => {
    expect(rollbackActionLabel("re-verify")).toContain("重新核实");
    expect(rollbackActionLabel("re-run-locked-release")).toContain("重跑锁定发布");
    expect(rollbackActionLabel("escalate-human")).toContain("升级人工裁决");
    expect(rollbackActionLabel("unknown-action")).toBe("unknown-action");
    expect(rollbackActionLabel("")).toBe("未知建议");
  });

  it("labels remediation proposal terminal states and actions", () => {
    expect(proposalStatusLabel("pending_approval")).toBe("待审批");
    expect(proposalStatusLabel("approved")).toBe("已批准");
    expect(proposalStatusLabel("rejected")).toBe("已驳回");
    expect(proposalStatusLabel("closed")).toBe("已关闭");
    expect(proposalStatusLabel("")).toBe("未知");
    expect(proposalActionLabel("re-verify")).toBe("重新核实");
    expect(proposalActionLabel("re-run-locked-release")).toBe("重跑锁定发布");
    expect(proposalActionLabel("escalate-human")).toBe("升级人工裁决");
    expect(proposalActionLabel("unknown")).toBe("unknown");
    expect(proposalActionLabel("")).toBe("未知动作");
  });

  it("allows decisions only on pending proposals (irreversible terminal states)", () => {
    expect(proposalCanDecide({ status: "pending_approval" })).toBe(true);
    expect(proposalCanDecide({ status: "approved" })).toBe(false);
    expect(proposalCanDecide({ status: "rejected" })).toBe(false);
    expect(proposalCanDecide({ status: "closed" })).toBe(false);
  });

  it("allows a governed re-run only for approved re-run-locked-release proposals", () => {
    expect(proposalCanRerun({ status: "approved", action: "re-run-locked-release" })).toBe(true);
    expect(proposalCanRerun({ status: "pending_approval", action: "re-run-locked-release" })).toBe(false);
    expect(proposalCanRerun({ status: "approved", action: "escalate-human" })).toBe(false);
    expect(proposalCanRerun({ status: "rejected", action: "re-run-locked-release" })).toBe(false);
  });
});

describe("M9 evidence chain model", () => {
  it("labels evidence scopes with the locked enum vocabulary", () => {
    expect(evidenceScopeLabel("full")).toBe("全量");
    expect(evidenceScopeLabel("topology")).toBe("目录");
    expect(evidenceScopeLabel("chain")).toBe("调用链");
    expect(evidenceScopeLabel("execution")).toBe("执行");
    expect(evidenceScopeLabel("verification")).toBe("验证");
    expect(evidenceScopeLabel("remediation")).toBe("修复");
    expect(evidenceScopeLabel("")).toBe("未知");
  });

  it("strips the topology schema prefix for dense source tables", () => {
    expect(evidenceSourceTableLabel("topology.topology_releases")).toBe("topology_releases");
    expect(evidenceSourceTableLabel("topology.evidence_chain_anchors")).toBe("evidence_chain_anchors");
    expect(evidenceSourceTableLabel("public.table")).toBe("public.table");
    expect(evidenceSourceTableLabel("")).toBe("—");
  });

  it("maps the verification proof verdict to a human label", () => {
    expect(evidenceVerifiedLabel(true)).toBe("完整一致");
    expect(evidenceVerifiedLabel(false)).toBe("发现失配");
  });

  it("localizes the first mismatching link precisely", () => {
    expect(evidenceMismatchLabel(null)).toBe("无失配");
    expect(evidenceMismatchLabel(undefined)).toBe("无失配");
    const mismatch = { seq: 7, source_table: "topology.topology_releases", source_pk: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" };
    const label = evidenceMismatchLabel(mismatch);
    expect(label).toContain("7");
    expect(label).toContain("topology_releases");
    expect(label).toContain("aaaaaaaa-bbbb");
    expect(evidenceMismatchLabel({ seq: 3, source_table: "topology.chains", source_pk: "chain-abc" })).toContain("chain-abc");
  });

  it("shortens sha256 hashes for dense tables", () => {
    expect(evidenceHashShort("1234567890abcdef".repeat(4))).toMatch(/^[a-f0-9]{12}…$/);
    expect(evidenceHashShort("abc")).toBe("abc");
    expect(evidenceHashShort("")).toBe("—");
    expect(evidenceHashShort(undefined)).toBe("—");
  });
});

describe("M10 graph planning intent model", () => {
  it("accepts only non-empty intent text within the 1-512 character contract", () => {
    expect(planningIntentCanGenerate("总账质量校验")).toBe(true);
    expect(planningIntentCanGenerate("  总账质量校验  ")).toBe(true);
    expect(planningIntentCanGenerate("")).toBe(false);
    expect(planningIntentCanGenerate("   ")).toBe(false);
    expect(planningIntentCanGenerate("a".repeat(PLANNING_INTENT_MAX))).toBe(true);
    expect(planningIntentCanGenerate("a".repeat(PLANNING_INTENT_MAX + 1))).toBe(false);
  });

  it("gives a character budget hint for the required input field", () => {
    expect(planningIntentLengthHint("")).toContain("请输入意图");
    expect(planningIntentLengthHint("a".repeat(PLANNING_INTENT_MAX + 1))).toContain("意图超长");
    expect(planningIntentLengthHint("总账")).toBe("2/512 字符");
  });

  it("labels matched-node evidence kinds with the locked vocabulary", () => {
    expect(planningIntentMatchKindLabel("direct")).toBe("直接匹配");
    expect(planningIntentMatchKindLabel("alias")).toBe("别名匹配");
    expect(planningIntentMatchKindLabel("expanded")).toBe("图遍历扩展");
    expect(planningIntentMatchKindLabel("")).toBe("未知");
  });

  it("labels match sources as deterministic scoring or bounded traversal", () => {
    expect(planningIntentMatchSourceLabel("trigram")).toBe("Trigram 打分");
    expect(planningIntentMatchSourceLabel("bridge")).toBe("域桥接扩展");
    expect(planningIntentMatchSourceLabel("depends_on")).toBe("依赖扩展");
    expect(planningIntentMatchSourceLabel("")).toBe("—");
  });

  it("labels graph node types in capability/domain vocabulary", () => {
    expect(planningIntentNodeTypeLabel("capability")).toBe("能力");
    expect(planningIntentNodeTypeLabel("domain")).toBe("业务域");
    expect(planningIntentNodeTypeLabel("")).toBe("—");
  });

  it("explains whether the intent replay reused the evidence row or plan", () => {
    const base = { intent_id: "i", intent_text: "t", matched_nodes: [], capability_requirements: ["audit.ledger.validate"], plan_key: "plan-key", mode: "plan_only" as const, plan_checksum: "c".repeat(64), reused_plan: false, node_count: 1, edge_count: 0, created_at: "2026-09-07T00:00:00", trace_id: "t" };
    expect(planningIntentEvidenceNote({ ...base, idempotent: true })).toContain("幂等重放");
    expect(planningIntentEvidenceNote({ ...base, idempotent: false, reused_plan: true })).toContain("复用既有计划");
    expect(planningIntentEvidenceNote({ ...base, idempotent: false, reused_plan: false })).toContain("新生成");
  });
});
