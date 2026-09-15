# Phase 5：受 ChangeSet 治理的自动图谱抽取

## 目的

在 Phase 4 的多级图谱底座之上，把「文档 → 图的自动抽取」从直接写活图变为**受治理的提案管线**：抽取器只能产出候选节点/边，先进入影子 ChangeSet，经校验、批准、Release 后才发布到 L0–L4 对应空间。冲突、重复、越界主张一律先入冲突收件箱，绝不自动抹除或外溢。本阶段沿用 AGENTS.md 的约束：不处理音视频、不联网、不执行真实插件、不生产自动化。

## 边界（本阶段只做 / 不做）

**只做：**
- 从已入库文本类文档（`semantic.chunks` 或 `semantic.documents`，含 Phase 3 的 page_idx 回链）产出**候选**图节点/边
- 一个可替换的轻量抽取器（非 LLM、非 MinerU 新扩容），基于显式规则提取实体与关系，输出 `GraphExtractionCandidate`
- 候选批量装入一个影子 `ChangeSet`（复用 `KnowledgeLifecycleService`，仅 `graph.node` create）
- 冲突/重复主张经 `record_conflict` 入 `knowledge.conflict_cases`，不进入 ChangeSet
- 桌面「抽取提案」视图：预览候选节点/边、来源引用、按 ChangeSet 放行/驳回、冲突箱与版本历史
- Golden Query：只在 `audit_network_test` 上生成合成文档并跑抽取提案管线（不发 Release 到主库）

**不做：**
- 不自动抽取到活图（不经批准绝不调用 `activate_release`）
- 不用 LLM、不调用 MinerU/OCR 新扩容、不处理音视频
- 不批量改写或覆盖主库已存在文档/节点/边；历史证据不硬删除
- 不跨域任意连边；抽取产出的边只能落在已登记空间与四类桥接范围内

## 可复用组件（已存在，不重复造）

- `KnowledgeLifecycleService`（`packages/knowledge/lifecycle.py`）：`create_changeset/validate/approve/create_release/activate_release/rollback_release`，已支持 `graph.node` create
- `GraphService.ensure_space/upsert_node/register_bridge/record_conflict/list_conflicts`（`packages/graph/service.py`）
- `require_policy`（`apps/api/main.py:L1045`）：capability + risk_class + side_effects + idempotency_key + arguments
- 富媒体策略发布模式（`packages/plugin_runtime/registration.py`）：`policy.policy_sets` + allow rule
- `semantic.chunks` / `semantic.documents` / `knowledge.recycle_bin` / `graph.node_revisions` / `knowledge.conflict_cases`

## 数据契约

### 新增 capability（策略白名单）
- `knowledge.extract.graph`：risk_class=`low`，side_effects=`write_data`（仅允许写入影子 ChangeSet，绝不直接写活图）；approval 前置。

### 抽取候选（内存结构，写入 ChangeSet 前存在）
```jsonc
{
  "document_id": "uuid",          // 来源文档
  "source_uri": "file://...",      // 回链
  "space_key": "audit-l1",         // 目标 L1–L4 空间
  "node_key": "company:ACME",
  "node_type": "organization",
  "label": "ACME",
  "properties": {},
  "relation": { "target_key": "...", "relation_type": "regulated_by", "weight": 0.9 }
  // relation_type 只能来自显式登记集合，否则走冲突箱
}
```

### 冲突判定（自动抽取场景）
- 同 `node_key` 已在目标空间存在且内容不一致 → `record_conflict(entity_key, severity, summary, claim)`
- `relation_type` 不在登记集合 → 冲突（拒任意连边）
- 提名到未登记/非 L1–L4 空间 → 冲突

## 验收清单

1. 契约拒绝「无注册关系类型的抽取边」与「提名未登记空间」。
2. 抽取器等价的投票 `validate_changeset` 拒绝缺 `canonical_key/node_type/label/space_key` 的 create。
3. 候选先进入影子 ChangeSet（status 至少 `draft`/`pending_review`），**未批准前图中不出现新节点**。
4. 经 approve + create_release + activate_release 后才在 `graph.nodes` 出现，且写入 `node_revisions` 留痕。
5. 冲突/重复主张进入 `knowledge.conflict_cases`（open），不直接进活图。
6. Golden Query 工具只在 `audit_network_test` 上生成合成文档跑抽取提案管线，主库不被写入。
7. 桌面「抽取提案」视图展示候选、来源引用、按 ChangeSet 放行/驳回、冲突箱与版本历史；所有读取经策略网关，写操作带租户/Trace/幂等键。

本阶段不进行自动 LLM 抽图、不启动插件、不执行外部网络或生产自动化。