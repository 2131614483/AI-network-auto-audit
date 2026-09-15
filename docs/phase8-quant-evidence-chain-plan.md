# Phase 8：量化证据链治理（QT 模块）

## 目的

在已验收的 CSV 点时回测（数据快照 + 代码 SHA + 模拟盘标记）之上，补齐**量化结果的可读证据链**：量化工作台不仅能回测，还能按「数据快照 → 数据集 → 回测参数 → 结果指标」回溯完整血缘，只读查看每次回测的数据源（快照 SHA 与新鲜度）、策略与代码哈希、点时门状态（point-in-time gate）与结果指标（收益/波动/回撤/仅模拟标记）。本阶段只读治理，不自动交易、不追加新回测跑批、不联网、不生产自动化，写操作（未来新增回测）不纳入本阶段。

## 边界（本阶段只做 / 不做）

**只做：**
- 回测列表只读查询：`backtests` 摘要（策略 key、数据集 key、状态、指标、创建时间、数据集新鲜度）
- 单回测血缘：`backtest → dataset → snapshot` 关联，含 `source_sha256`、`code_sha256`、`point_in_time_gate` 状态与结果指标，读取经 RLS 租户隔离
- 契约 + 测试先行；读取仅只读能力 `quant.chain.read`（read_only / read_only），页面与 API 均经 Policy Gateway
- 桌面量化工作台：回测列表 + 所选工具链血缘视图（只读），替换现有通用“运行投影”量化视图
- 与 Phase 7 审计一致：Golden Query 与回归仅在 `audit_network_test` 复核，主库不被多余写入

**不做：**
- 不新增/扩展现有回测跑批；不在本阶段写 `quant.*` 新记录（读取与治理只读）
- 不自动下单、不接聚宽等外部行情；不启动插件、不执行外部网络或生产建议
- 不修改既有 `quant.*` 表结构；如需新增列走显式迁移
- 不做 Point-in-time 之外的生产一致性/漂移校验（留待后续独立阶段）

## 可复用组件

- `run_csv_backtest`（`packages/quant/backtest.py`）：已持久化 `quant.datasets`（含 `source_sha256` 与 freshness）与 `quant.backtests`（参数 `data_snapshot_sha256`/`code_sha256`/`point_in_time_gate`、指标 `total_return`/`volatility`/`max_drawdown`/`simulated_only`）
- 现有表：`quant.datasets`、`quant.backtests`（迁移 `0005_domain_tables`，RLS 于 `0006_rls_and_indexes` 启用）
- `require_policy`（`apps/api/main.py`）
- 策略发布模式（`packages/plugin_runtime/registration.py`）

## 数据契约

### 新增 capability（策略白名单）
- `quant.chain.read`：risk_class=`read_only`，side_effects=`read_only`

### 血缘监听（只读返回）
```jsonc
{
  "backtest_id": "uuid",
  "backtest": {
    "strategy_key": "daily_momentum", "status": "completed",
    "parameters": { "data_snapshot_sha256": "sha", "code_sha256": "sha", "point_in_time_gate": "passed" },
    "metrics": { "total_return": 0.12, "volatility": 0.18, "max_drawdown": -0.2, "simulated_only": true },
    "created_at": "..."
  },
  "dataset": {
    "id": "uuid", "key": "csv:prices.csv", "as_of": "...", "freshness_status": "fresh",
    "metadata": { "source_sha256": "sha", "row_count": 4, "classification": "public" }
  }
}
```
- 摘要 `GET /api/v1/quant/backtests`：`[{ id, strategy_key, dataset_key, status, freshness_status, created_at, total_return, volatility, max_drawdown, observations }]`
- 血缘 `GET /api/v1/quant/backtests/{id}/lineage`：上例；空/未知回测正确 404。

## 验收清单

1. 血缘查询返回 backtest → dataset → snapshot 完整关联（含代码/数据 SHA 与点时门）；未知回测拒绝而非静默空。
2. 列表仅在租户隔离（RLS）可见范围内返回摘要，limit 越界被拒。
3. API 读取端点全部经 Policy Gateway（`quant.chain.read` read_only），未登记能力一律 DENY；带租户/Trace。
4. 桌面量化工作台展示回测列表与所选工具链血缘（只读）；Electron IPC 只增加精确只读路径。
5. Golden Query 与全量回归仅在 `audit_network_test` 复核；主库不因本阶段测试被写入。

本阶段不进行自动交易、不扩展现有回测、不启动插件、不执行外部网络或生产自动化。