# Phase 7：审计证据链治理（A1 模块）

## 目的

在已验收的 CSV 总账摄入（Artifact → Evidence → Anomaly）之上，补齐**证据链的可读与受治理确认**：审计工作台不仅能摄入，还能按「审计项目 → 证据 → 主张 → 发现」回溯完整血缘、只读查看异常候选，并**显式确认候选为可报告发现**（不可自动确认/批量改写），所有写操作经 Policy Gateway 且带租户/Trace/幂等键。本阶段是总体设计 A1 轨道的后续增强，沿用 AGENTS.md 约束：不联网、不执行真实插件、不生产自动化。

## 边界（本阶段只做 / 不做）

**只做：**
- 证据链只读查询：engagements 列表、单个 engagement 的 evidence、claims、findings、workpapers 血缘；异常候选（open/confirmed）只读列表
- 受治理确认：`confirm_candidate` 通过 **幂等键** 精确确认一次，写入 claim + evidence_link + finding + finding_claim；同一候选二次确认被拒（fail-closed），绝不静默覆盖
- 契约 + 测试先行；读取仅只读能力、写经 capability + risk_class + side_effects + idempotency_key + arguments
- 桌面审计工作台：证据链血缘视图（只读）+ 异常候选确认（经策略网关与幂等键）
- Golden Query 与回归仅在 `audit_network_test` 复核，主库不被多余写入

**不做：**
- 不自动生成/自动确认任何发现；不批量改写用户审计数据
- 不新增外部分析器/LLM/插件/网络；不生产自动化
- 不硬删除历史；不修改既有 `audit.*`/`belief.*` 表结构（若有新增字段走显式迁移）

## 可复用组件

- `AuditPipeline`（`packages/audit/pipeline.py`）：`run_ledger_csv` / `confirm_candidate`
- 现有表：`artifact.artifacts`、`artifact.blobs`、`audit.engagements`、`audit.evidence`、`audit.anomaly_candidates`、`audit.findings`、`audit.finding_claims`、`audit.workpapers`、`belief.claims`、`belief.evidence_links`
- `require_policy`（`apps/api/main.py`）
- 策略发布模式（`packages/plugin_runtime/registration.py`）

## 数据契约

### 新增 capability（策略白名单）
- `audit.chain.read`：risk_class=`read_only`，side_effects=`read_only`
- `audit.finding.confirm`：risk_class=`medium`，side_effects=`write_data`；乐观参数 `candidate_id`

### 确认请求（`AuditConfirmRequest`）
```jsonc
{
  "candidate_id": "uuid",       // audit.anomaly_candidates 中 status='open'
  "reviewer_label": "independent-qa"
}
```
- 幂等键头用于去重：同 `candidate_id` 已确认则返回既有 finding_id（重放），但**不同候选一律各自独立**

### 证据链血缘（只读返回）
```jsonc
{
  "engagement_id": "uuid",
  "engagement": { "name": "Ledger Review", "status": "completed", "created_at": "..." },
  "evidence": [ { "id": "uuid", "evidence_type": "ledger_source", "artifact_id": "uuid", "metadata": {} } ],
  "anomalies": [ { "id": "uuid", "source_ref": "1", "rule_key": "large_amount", "score": 0.6, "status": "open" } ],
  "findings": [ { "id": "uuid", "title": "...", "severity": "medium", "status": "confirmed",
                  "claims": [ { "claim_id": "uuid", "reviewer_label": "independent-qa" } ] } ]
}
```

## 验收清单

1. 只读血缘查询返回 engagement 下 evidence / anomaly_candidates / findings / claims 完整关联；空 engagement 正确空态。
2. `confirm_candidate` 幂等：同候选重复确认返回同一 finding，不产生多余 claim/finding；**不同候选**不会互相污染。
3. 确认已 closed/confirmed 候选、非法 reviewer_label、未知候选均被拒。
4. API 写端点全部经 Policy Gateway（`audit.finding.confirm` medium/write_data），带租户/Trace/幂等键；读取走 `audit.chain.read`。
5. 桌面审计工作台展示证据链血缘（只读）并提供经策略网关的候选确认；Electron IPC 只增加精确只读与幂等写路径。
6. Golden Query 与全量回归仅在 `audit_network_test` 复核；主库不因本阶段测试被写入。

本阶段不进行自动确认、不启动插件、不执行外部网络或生产自动化。