# Phase 6：图谱合并/拆分与冲突仲裁

## 目的

在 Phase 4/5 的多级图空间与 ChangeSet 治理之上，提供**受治理的图结构修正能力**：把重复/漂移节点合并（merge）或拆分（split），并对冲突收件箱的每条主张做显式仲裁（arbitration）。节点合并/拆分不改写用户原始文档，只重指受控空间内的边并把源节点软删至回收站（历史证据不硬删除）；冲突绝不被自动抹除，每条裁决都会落账。本阶段沿用 AGENTS.md 约束：不联网、不执行真实插件、不生产自动化、写操作一律经 Policy Gateway 且带租户/Trace/幂等键。

## 边界（本阶段只做 / 不做）

**只做：**
- 同空间内的节点合并（`merge_nodes`）：重指源节点的边到目标节点（按边唯一键去重），源节点软删进回收站并留 `node_revisions`，别名从源迁移到目标，写入 `merge_records`+`members`+`edge_redirects` 账本以便回滚
- 同空间内的节点拆分（`split_node`）：源节点保持，把指定关系子集的边重指到若干新建子节点，写入 `split_records`+`split_parts`+`edge_redirects` 账本
- **乐观并发锁**：merge/split/update 必须携带节点 `expected_revision`（row_version），不符即拒绝并转入冲突收件箱，绝不静默覆盖
- **冲突仲裁**（`arbitrate_conflict`）：对 open 冲突案件给出 `merge` / `keep_source` / `reject_claim` / `resolve` 裁决，裁决写入 `arbitration_decisions`，案件置 resolved；`merge` 裁决可选联动执行一次受治理合并
- 契约 + 测试先行；服务只读/写经策略网关（capability + risk_class + side_effects + idempotency_key + arguments）
- Golden Query 与桌面只读治理清单：仅在 `audit_network_test` 上合成测试并复核；桌面端只展示合并/拆分账本与仲裁清单（只读）

**不做：**
- 不跨空间合并/拆分（跨域仍只能通过已登记桥接关系）
- 不批量改写主库用户文档/节点/边；不自动抽取；不调用 LLM/音视频/插件/外网
- 不自动仲裁——任何冲突裁决都必须显式落账，绝不静默解决
- 不硬删除历史；合并源节点软删进回收站、账本保持可回滚

## 可复用组件

- `GraphService`（`packages/graph/service.py`）：`upsert_node/soft_delete_node/restore_node/add_alias/record_conflict/list_conflicts/list_node_revisions/rollback_node_revision`
- `graph.node_revisions` 触发器：任何节点变更（含软删）都会留下不可变快照
- `knowledge.conflict_cases` + `conflict_items`：冲突收件箱（open→resolved 状态机）
- `require_policy`（`apps/api/main.py`）：capability + risk_class + side_effects + idempotency_key + arguments
- 策略发布模式（`packages/plugin_runtime/registration.py`）：`policy.policy_sets` + allow rule

## 数据契约

### 新增 capability（策略白名单）
- `graph.node.merge`：risk_class=`medium`，side_effects=`write_data`；乐观锁参数 `space_key/target_key/source_keys/expected_revision`
- `graph.node.split`：risk_class=`medium`，side_effects=`write_data`
- `graph.arbitration.resolve`：risk_class=`medium`，side_effects=`write_data`

### 合并请求（`MergeRequest`）
```jsonc
{
  "space_key": "audit-l1",
  "target_key": "company:acme",        // 保留作为主节点的 canonical_key
  "source_keys": ["company:acme-dup", "company:acme-twin"],
  "expected_revision": 3,              // 各源+目标共享的乐观锁（不符即拒）
  "reason": "重复实体合并"
}
```
- 目标与所有源必须同租户、同空间、均存活；跨空间/缺节点/自合并/任一 `row_version != expected_revision` 均拒绝
- 源节点的全部出边/入边重指到目标，`UNIQUE(space,source,target,relation)` 冲突去重
- 源软删（回收站快照 + `node_revisions`），源别名并入目标，合并明细写入 `merge_records/members/edge_redirects`

### 拆分请求（`SplitRequest`）
```jsonc
{
  "space_key": "audit-l1",
  "source_key": "domain:parent",
  "parts": [ { "node_key": "domain:part-a", "node_type": "entity", "label": "Part A",
               "redirect_relations": ["owns"] } ],
  "expected_revision": 4,
  "reason": "拆分职责"
}
```
- 子键必须在同一空间、合法 node_key、不与现有节点冲突、与源键不同、至少一个 part
- 仅把 `source` 的、关系类型 ∈ part.redirect_relations 的边重指到该 part；源节点保持存活

### 仲裁请求（`ArbitrationRequest`）
```jsonc
{
  "case_id": "uuid",                // open 的 conflict case
  "decision": "merge"               // merge | keep_source | reject_claim | resolve
}
```
- 仅在 open 案件上显式裁决一次；裁决落 `arbitration_decisions`，案件置 resolved
- decision=merge 时可选联动一次合并；其余为纯裁决标记，不改图

## 验收清单

1. 契约拒绝跨空间合并/拆分、目标或源缺失、自合并、空拆分、非法子键、子键冲突。
2. 乐观锁：`row_version != expected_revision` 的 merge/split 被拒绝，并作为冲突入 `conflict_cases`（open），绝不静默覆盖。
3. merge 后源节点软删、边重指到位（同级重复关系去重）、`merge_edge_redirects` 与 `node_revisions` 留痕；源别名并入目标。
4. split 后只重指声明关系的边、源保持存活、`split_parts`/`split_edge_redirects` 留痕。
5. 仲裁：同一案件二次裁决被拒；裁决落 `arbitration_decisions` 且案件 open→resolved；merge 裁决可联动执行受治理合并。
6. API 端点全部经 Policy Gateway（merge/split/arbitration 写）、带租户/Trace/幂等键；读取治理账本仅只读能力。
7. Golden Query 与桌面只读治理清单仅在 `audit_network_test` 复核，主库不被写入除显式放行外的任何行。

本阶段不进行自动合并/拆分/仲裁、不启动插件、不执行外部网络或生产自动化。