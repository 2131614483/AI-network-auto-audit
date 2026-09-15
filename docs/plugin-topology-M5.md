# Plugin Topology M5 实施计划：隔离执行层——受限只读子进程调用

> 状态：**已实施并验收（2026-09-07）**。实施与验收记录见文末第 9 节。
> 关联：`docs/plugin-topology-orchestration.md`、`docs/plugin-topology-M1.md`、`docs/plugin-topology-M2.md`、`docs/plugin-topology-M3.md`、`docs/plugin-topology-M4.md`、`AGENTS.md`。
> M5 目标：把 M4 仅作契约/门控预留的隔离执行分支（ISO）实现为**受限只读子进程调用**——仅允许已验证内置插件（`_BUILTIN_LAYOUT` 白名单）在 `python -I` 隔离进程中执行，输入来自 `db_artifacts` 物化或上游节点 artifact（SHA256 锁定），输出物化为 artifact 后逐节点写入 `execution_ledger`（`mode=isolated`）。用现有种子链 `ledger-quality-slot → research-note-slot` 桥接路径做受限只读演练。**不做**真实交易、外部网络、生产修复、任意命令、Temporal/生产调度或泛化 AUTO。

## 0. 合规边界（前置声明）

Plugin Topology 是用户显式验收的独立模块线（M1–M4 已验收）。M5 的"子进程调用"严格限定为：

- 仅调用项目内**已验证内置插件**（runner `_BUILTIN_LAYOUT` 硬编码白名单，manifest/protocol/binding 三重 SHA256 校验），`python -I` 隔离、无网络、只读。
- 不触达生产数据库、真实基础设施、外部网络；不自动执行生产修复。
- 演练全部跑在独立测试库 `audit_network_test` 与受控 staging 目录。
- `topology.chain.execute.isolated` 策略**默认不授予**（fail-closed）：未显式放行即 403，不启动任何子进程。

## 1. 范围

| 做                                                                                                                              | 不做                                          |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| ISO 门控：每一节点 capability 必须命中已验证内置插件白名单、`side_effects=read_only`、Policy（`topology.chain.execute` + `.isolated`）双重放行 | 任意/未验证插件、网络访问、写侧效应                       |
| 输入物化：`db_artifacts` 物化（alerts/incidents/topology/semantic-document）或上游节点 artifact，输入 SHA256 锁定；桥接路径 `ledger-quality → research-note` 输入适配 | 接收任意文件路径/任意 URI 作为输入                      |
| 逐节点真实子进程调用（`IsolatedPluginRuntime.invoke`），失败 → `status='failed'` + trace，不自动降级、不跳过                      | 静默跳过失败节点、把隔离执行冒充 simulated                |
| 输出物化：子进程输出 JSON 写为 artifact（受控 staging，注入 `allowed_roots`），账本只写 Refs + SHA256，不写正文 payload                | 把真实输出正文塞进 `execution_ledger`                |
| `execution_ledger` 扩展 `mode IN ('simulated','isolated')` + 真实执行元数据列（plugin/version/runtime 校验和/输入 SHA256/artifact refs）     | 覆盖/硬删历史账本；同链同 slot 同 mode 重跑              |
| 只读 GUI：执行账本区分 simulated/isolated 行；「受限演练」按钮（二次确认，经 Policy 网关）                                          | 桌面暴露任意子进程入口、绕过网关                        |

## 2. 契约先行（先于服务逻辑）

扩展现有 2 份 Schema（`contracts/jsonschema/`，沿用内联样例测试风格），不动 M4 已验收语义：

| Schema                                | 变化                                                                                                                        | 关键约束                                                                        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `chain-execution-request.schema.json` | `mode` 由 `const: simulated` 扩为 `enum: [simulated, isolated]`；`isolated` 时 `reason` 必填（说明演练用途）                                   | 必须携带幂等键；`mode` 仍为有限枚举（任意其他值拒绝）；`isolated` 依赖服务端 ISO 门（白名单）                |
| `execution-ledger.schema.json`        | `mode` 扩为 `enum: [simulated, isolated]`；新增可选字段 `plugin_id`、`plugin_version`、`runtime_code_sha256`、`input_sha256`、`output_artifact_refs`（`[{uri, sha256, media_type}]`，maxItems 64） | `mode`/`status` 枚举；输出只允许 Refs/artifact-refs + checksum，仍禁止 payload 正文        |

契约测试：2 份正例（simulated、isolated 请求与账本均通过）；`mode` 越界、isolated 空 reason、账本携带 payload 正文、`output_artifact_refs` 缺 sha256/uri 越界一律拒绝。

## 3. 表结构（迁移 0041）

`migrations/versions/0041_isolated_execution.py`，`down_revision = "0040_execution_verification"`（revision 24 字符 < 32 上限）。全部 `IF NOT EXISTS` / 幂等，账本仍 RLS + FORCE、不可 UPDATE/DELETE、不可硬删。

```sql
ALTER TABLE topology.execution_ledger DROP CONSTRAINT IF EXISTS execution_ledger_mode_check;
ALTER TABLE topology.execution_ledger ADD CONSTRAINT execution_ledger_mode_check
  CHECK (mode IN ('simulated', 'isolated'));

ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS plugin_id           text   NOT NULL DEFAULT '';
ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS plugin_version      text   NOT NULL DEFAULT '';
ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS runtime_code_sha256 text   NOT NULL DEFAULT '';
ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS input_sha256        text   NOT NULL DEFAULT '';
ALTER TABLE topology.execution_ledger ADD COLUMN IF NOT EXISTS output_artifact_refs jsonb NOT NULL DEFAULT '[]';
```

- 新增列全部含 `DEFAULT`，既有 simulated 行保持兼容；`UNIQUE(tenant_id, chain_id, plan_node_slot_key, ordinal, mode)` 不变（同链同 slot 的 simulated 与 isolated 各可留一条，各自幂等）。
- 策略 upsert：追加 `topology.chain.execute.isolated`（medium / read_only）——按 fail-closed，**种子默认 `status='inactive'`**，集成测试中显式激活；`topology.chain.execute`（M4 已有）继续作为共同门槛。
- 不新增种子账本行（M5 真实子进程演练放集成测试，不在迁移里落库，避免在用户库启动子进程）。

## 4. 服务层模块

```
packages/plugin_topology/
├── approvals.py      # 不变（M4）
├── executor.py       # 扩展：GATE → SIM | ISO → 账本（ISO 委托 isolated.py）
└── isolated.py       # 新增：IsolatedChainExecutor——ISO 门 → 输入物化 → 逐节点子进程 → 输出物化 → 账本
```

- **ISO 门（fail-closed）**：载入链全部 intents，先跑 M4 `gate_blockers`（denied/未审批 → 整链拒绝）；再对每条链节点断言：`capability` 在 `verified_builtin_ids()` 白名单、intent `side_effects=read_only`、`isolation=isolated_subprocess`；任一不满足 → `PermissionError`（不启动任何子进程）。

- **输入物化**：`materialize_node_input()` 按节点约定解析输入：
  1. 首节点：`db_artifacts.materialize_*`（按 capability 映射：alerts/incidents/topology/semantic-document）物化到受控 staging（`chains/{chain_key}/{slot}/`，注入 `allowed_roots`），返回 `ArtifactInput`（file:// URI + SHA256 锁定）；
  2. 桥接节点：注入 `bridge_materializer`——读取上游 producer 输出的 artifact，按 consumer 的 `input_sha256` 公式（如 `research_note.evaluation.sha256`）构造引用 artifact（含 sha256/uri），保证锁值与子进程校验一致；
  3. 输入大小/行数预算封顶（复用 db_artifacts 既有 budget），超限即失败。

- **逐节点调用**：对每个节点构造 `PluginInvocation`（tenant/trace/idempotency-key/plugin_id/capability/payload），`IsolatedPluginRuntime(allowed_roots=(staging, *plugin_read_roots)).invoke(...)`，Policy 逐节点裁决（`topology.chain.execute` + `topology.chain.execute.isolated`）。每个节点一次子进程，严禁同一进程连续多节点。

- **输出物化与账本**：子进程输出 dict 写为 artifact（`{slot}/output-{checksum[:16]}.json`，SHA256 = 账本 `output_checksum`，`output_artifact_refs=[{uri, sha256, media_type}]`）；账本行写 `mode='isolated'`、`plugin_id/plugin_version/runtime_code_sha256/input_sha256`、`status`。失败节点写 `status='failed'` + trace 并终止后续节点（fail-closed 延续，绝不静默降级）。

- **依赖注入**：`IsolatedChainExecutor(runtime_factory, materializer)` 注入 runtime/materializer 工厂——单元测试注入 fake runtime 断言门控与账本，集成测试注入真 `IsolatedPluginRuntime`。

## 5. API 与桌面只读 GUI

- API（`apps/api/main.py`，全部经 Policy 网关 + 租户 + Trace + 幂等键）：
  - `POST /api/v1/topology/chains/{chain_key}/executions`（`topology.chain.execute`/medium + `topology.chain.execute.isolated`）→ body `mode='isolated'` 走 ISO；缺 `.isolated` 权限 → 403（子进程不启动）。simulated 行为保持 M4 不变。
  - `GET /api/v1/topology/chains/{chain_key}/executions`（`topology.execution.read`/read\_only）→ 返回含 isolated 行（含 plugin/校验和/artifact refs 字段）。
  - Electron IPC 白名单不变（approvals/executions 路径已覆盖）。

- GUI（`desktop/src/App.tsx`，复用 M4 拓扑卡片）：
  - 执行账本 tab：模式标签区分 `simulated`（影子）/ `isolated`（受限真实）；isolated 行展示 plugin/version、input 校验和、输出校验和/artifact refs、状态标签（成功/失败/挂起）。
  - 调用链表操作列：保留「影子模拟」；新增「受限演练」按钮（二次确认弹窗注明"启动已验证内置插件的只读隔离子进程，演练仅限测试库；未经 execute.isolated 权限将被 403 拒绝"）。
  - 横幅提示更新为「规划，不执行 · 影子模拟 · 受限只读演练（经网关）」；桌面仍不暴露任意子进程执行入口。

## 6. 测试计划

| 层   | 用例                                                                                                                                                                                   |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 契约  | 2 份 Schema 正反例：`mode∈[simulated, isolated]`、isolated 空 reason 拒绝、账本带 payload 拒绝、`output_artifact_refs` 结构校验拒绝                                            |
| 单元  | ISO 门（capability 不在白名单/`side_effects≠read_only`/`.isolated` 未授予 → 拒绝且零子进程）；fake runtime 下逐节点账本（成功/失败 `failed`+trace 终止）；桥接物化器构造引用与锁值一致；预算封顶 |
| 集成  | 种子链 `ledger-quality → research-note` 受限演练：db_artifacts 物化输入（合成 A1 审计账本 CSV/staging）→ audit.ledger-quality 真实子进程 → 输出 artifact → bridge 适配 → quant.research-note-draft 真实子进程 → 账本两行 `mode='isolated'`、`output_checksum`=真实文件 SHA256、plugin/runtime 校验和正确 |
| 集成  | 幂等键重放返回既有行；同链同 slot 同 mode 二次（不同幂等键）不可重跑；RLS 跨租户不可见；无 `.isolated` 权限 → 403 且零子进程；策略 deny 节点 → fail-closed                    |
| GUI | Vitest（isolated 行渲染 + 受限演练按钮 mock）+ 桌面 typecheck                                                                                                           |

回归：`python -m pytest -q`、`python -m ruff check .`、`python -m mypy packages`、`npm run typecheck`。

## 7. 验收对照（M5 六条）

1. isolated 模式真实子进程调用：仅白名单已验证内置插件、`python -I` 隔离、无网络、只读；M4 第 8 节承诺兑现（接入 `runner.py.invoke` + `db_artifacts` 物化）。
2. 输入只来自物化（db_artifacts / 上游 artifact / bridge 适配），带 SHA256 锁定；绝不接收任意文件或任意路径。
3. 输出物化为 artifact（SHA256），账本只写 Refs + checksum；`mode=isolated` 与 simulated 如实区分、不得把隔离执行冒充影子模拟。
4. 逐节点 ISO 门：白名单 + `read_only` + Policy（execute + execute.isolated）→ 失败 `failed` + trace、不自动降级、不跳过；任一节点未放行则整链不启动子进程。
5. 幂等与账本约束延续：同链同 slot 同 mode 不可重跑、RLS + FORCE、不可 UPDATE/DELETE、不可硬删；新增列全部 `DEFAULT` 兼容既有行。
6. GUI 经 Policy 网关提供「受限演练」入口（二次确认），桌面仍不暴露任意子进程入口；`.isolated` 权限默认 inactive → 未放行即 403。

完成后：全量回归、更新 `docs/status.md`、`plugins/README.md` 与本文档状态。

## 8. 与后续里程碑的衔接

- 本阶段**不创建** `agent` 任务、不引入 Temporal/生产调度器（留给 M6+）。
- 隔离执行本轮仅覆盖**已验证内置插件 + 种子链桥接演练**；任意第三方插件注册、发布到 production 运行时的完整调用面留待后续里程碑，需单独的插件来源信任与签名流程。
- 默认 fail-closed：`topology.chain.execute.isolated` 未显式激活时，所有 isolated 请求 403 且零子进程，simulated 路径不受影响。

## 9. 实施与验收记录（2026-09-07）

> 状态：**已实施并验收**。验收方：用户；实施与验收均在 `AGENTS.md` 阶段边界内完成，M6+ 功能未提前实现。

### 9.1 交付清单

| 交付 | 说明 |
| ---- | ---- |
| 契约 | `chain-execution-request.schema.json`：`mode ∈ [simulated, isolated]`，isolated 时 `reason` 必填（simulated 允许空 reason）；`execution-ledger.schema.json`：`mode` 扩枚举 + `plugin_id`/`plugin_version`/`runtime_code_sha256`/`input_sha256`/`output_artifact_refs`（`[{uri,sha256,media_type}]`，maxItems 64），仍禁 payload 正文 |
| 迁移 0041 | `execution_ledger` mode CHECK 扩 `('simulated','isolated')` + 5 个 DEFAULT 列；策略追加 `topology.chain.execute.isolated`（medium/read_only，种子默认 inactive/fail-closed）；已显式应用至 `audit_network` 与 `audit_network_test` |
| 服务层 | `isolated.py::IsolatedChainExecutor`——ISO 门（白名单 + `read_only` + `isolated_subprocess` + 双策略）→ 输入物化（db_artifacts/上游 artifact/bridge 物化器，SHA256 锁定）→ 逐节点 `python -I` 真实子进程（`IsolatedPluginRuntime.invoke`）→ 输出物化 staging artifact + 账本（`mode='isolated'`）；失败 `failed` + trace 终止后续节点；`executor.py` ISO 分支集成；`service.py::execute_chain_isolated` + `list_executions` 扩展 |
| API | `POST /api/v1/topology/chains/{chain_key}/executions` 支持 `mode='isolated'`：先裁决 `topology.chain.execute` + `.isolated`（缺 `.isolated` → 403 零子进程）；Electron IPC 白名单同步 |
| GUI | 执行账本 isolated 行区分标签 + 插件@版本/输入校验和/artifact refs；「受限演练」按钮（二次确认） |
| 测试 | 契约正反例、ISO 门零子进程拒绝、fake runtime 逐节点账本（成功/失败终止）、桥接物化器锁值一致、幂等重放、RLS 跨租户不可见、403 零子进程、种子链 `ledger-quality → research-note` 受限演练、GUI Vitest/typecheck |

### 9.2 验收对照（M5 六条）

1. ✅ 真实子进程调用仅限 `_BUILTIN_LAYOUT` 白名单已验证内置插件，`python -I` 隔离、无网络、只读；M4 第 8 节承诺兑现（`runner.py.invoke` + `db_artifacts` 物化）。
2. ✅ 输入只来自物化（db_artifacts / 上游 artifact / bridge 适配），SHA256 锁定 + 预算封顶，绝不接收任意文件/路径。
3. ✅ 输出物化为 artifact（SHA256 = `output_checksum`），账本只写 Refs/checksum；`mode=isolated` 与 simulated 如实区分。
4. ✅ 逐节点 ISO 门：白名单 + `read_only` + Policy 双放行；失败 `failed` + trace 且终止后续节点，不降级不跳过；任一节点未放行则整链不启动子进程。
5. ✅ 幂等与账本约束延续：同链同 slot 同 mode 不可重跑、RLS + FORCE、不可 UPDATE/DELETE；新列全 `DEFAULT` 兼容既有 simulated 行。
6. ✅ GUI 经 Policy 网关提供「受限演练」入口（二次确认），桌面无任意子进程入口；`.isolated` 默认 inactive → 未放行即 403。

### 9.3 回归结果

- 全量 Python **584 passed, 2 skipped**（两个默认关闭的真实 MinerU 集成）。
- `ruff check .` 全仓通过；`mypy packages`（43 源）通过；桌面 `npm run typecheck` 通过。
- 修复 1 处回归：`test_plugin_topology_integration.py` 的只读断言受测试库多轮累积数据影响（`list_blueprints`/`list_releases` 默认 limit 截断），显式传大 limit 后恢复。
- 主库/测试库迁移头：`0041_isolated_execution`。