# 统一插件协议（UPP）v1.0

状态：**Phase 0 契约已冻结；所有内置包均为 `contract_only`，不可执行。**

UPP 是审计、知识库、知识图谱、量化、AIOps、Agent、数据连接器和 GUI 扩展共用的控制面协议。它解决的是“一个插件如何被发现、理解、组合和治理”，而不是“如何在某种语言里启动代码”。因此 Python、Node、HTTP 服务、MCP、容器、WASM 和桌面 UI 都可用同一份业务契约描述；实际运行时绑定永远另行验证。

规范文件：[unified-plugin-protocol.schema.json](../contracts/jsonschema/unified-plugin-protocol.schema.json)。

## 1. 四层对象必须分开

| 对象 | 回答的问题 | 当前能否执行 |
| --- | --- | --- |
| `PluginBlueprint` | 系统还缺什么能力槽位？ | 否 |
| `UnifiedPluginProtocol`（UPP） | 某个候选实现承诺什么能力、数据与治理边界？ | 否 |
| `PluginManifest` | 已验证实现如何被特定运行时绑定？ | 当前阶段否 |
| `PluginRoutingPlan` | 对一个意图可选择哪些候选链？ | 仅 `plan_only` |

不能以 UPP 代替运行时 Manifest，也不能把蓝图当作已安装插件。UPP 不包含 `entrypoint`、命令、容器镜像、网络 URL、凭据或密钥值；Schema 会拒绝这些未知字段。

## 2. 每个协议包的统一结构

| 区块 | 强制内容 | 兼容意义 |
| --- | --- | --- |
| 身份 | `protocol_version`、`id`、`version`、`kind`、`domains`、`lifecycle` | 稳定识别跨行业实现，不以文件路径或类名耦合 |
| 兼容性 | 宿主协议范围、可适配的语言/运行模式、可选特性与降级策略 | 一个能力可同时准备 Python、Node、MCP 等适配器 |
| 能力 | 名称、幂等键、输入输出契约、副作用 | 路由器按契约匹配，不按插件名称猜测 |
| 治理 | 数据分级、读写域、网络白名单引用、Policy 与证据要求 | 在计划阶段就暴露风险与信息墙 |
| 资源与可观测性 | CPU/内存/GPU/超时、trace、日志、指标、健康声明 | 可由 AIOps 与调度器统一理解 |
| 溯源与 UI | 设计来源、声明式 GUI 能力 | 可审计、可追踪，GUI 不加载任意脚本 |

### 2.1 能力与数据契约规则

1. Capability 使用稳定小写标识，例如 `knowledge.extract.document`、`audit.evidence.lineage`；不能复用模糊的 `run`、`execute`。
2. 每项能力必须明确幂等依据，例如不可变工件 SHA256、策略哈希与行情快照哈希。
3. 输入输出都必须携带契约 ID、SemVer、格式、交付方式与数据分级。格式只使用 JSON/JSONL/Parquet/工件引用/图谱引用/事件/表引用/文本等明确类型。
4. 大文件、PDF、模型文件、行情和证据正文只能以 `artifact_ref`、`graph_ref` 或其他已发布 Ref 交接；禁止把原始正文塞进控制面消息。
5. 消费者只能接受相同契约 ID、兼容主版本、可接受格式和不低于其安全要求的数据分级。条件不满足时必须报告缺口，不能按名称近似替代。

### 2.2 副作用与策略规则

`none`、`read_only`、`write_data`、`external_action`、`financial_execution` 是唯一允许的副作用等级。

- 所有协议包均强制 `gateway_required: true` 和 `trace_required: true`。
- 写数据能力必须要求证据记录；外部动作和金融动作必须同时要求证据与人工审批。
- `financial_execution` 仅是未来的风险分类，不是授权，也不会因为通过 Schema 而出现交易入口。
- `network` 只能是 `none` 或主机/端口白名单；密钥只允许写 `secret_refs`，永不写入协议内容。
- GUI 只接受 `declarative` 贡献及现有 `ui-contribution.schema.json`，按钮仍须通过 Policy Gateway。

## 3. 兼容性与版本规则

1. `protocol_version`、协议包 `version` 和接口契约版本全部使用 SemVer。
2. 宿主至少兼容当前 UPP 主版本和上一个主版本；新增可选字段只能向后兼容，消费者必须忽略未知可选字段。
3. 删除字段、改变副作用、降低数据分级、改变交付方式或不兼容的输出格式必须升主版本，并附迁移/替代说明。
4. `compatibility.adapters` 仅说明未来可被哪个适配器实现（`python`、`node`、`http`、`mcp`、`container`、`wasm`、`desktop`），不提供执行绑定。
5. 每个实际运行时实现日后必须经独立的 Manifest、签名、隔离、依赖扫描、契约/黄金样本测试和 Policy Gateway 审批后，才能绑定到同一 `id + version` 的协议包。

## 4. 生命周期

| 状态 | 含义 | 本阶段可用性 |
| --- | --- | --- |
| `contract_only` | 已有完整业务/治理/接口声明，无代码绑定 | 可以被目录、GUI 和测试读取 |
| `pending_verification` | 待独立环境验证 | 本阶段不使用 |
| `verified` | 未来已通过兼容和安全验证 | 本阶段不使用 |
| `retired` | 停止新增使用，但历史版本仍可解析 | 保留追溯 |

Phase 0 不得把任何包升级为可执行状态，也不得创建执行队列、运行时入口、自动修复或交易操作。

## 5. 第一批协议插件包

| 包 | 能力 | 数据/风险边界 |
| --- | --- | --- |
| `knowledge.document-ingestion` | `knowledge.extract.document` | 工件引用 → 文档内容；内部资料，只读 |
| `audit.evidence-lineage` | `audit.evidence.lineage` | 发布图引用 → 证据链；审计保密，只读 |
| `quant.simulated-backtest` | `quant.backtest.simulate` | 行情快照 → 回测报告；受限数据，只读、非交易 |
| `aiops.alert-triage` | `aiops.alert.triage` | 告警事件 → 人工复核提案；内部资料，只读、无自动修复 |

协议包位于 `plugins/builtin/*/plugin.protocol.json`。它们可作为未来业务插件的模板，但不能注册到运行时，也不会进入自动调度。

## 6. 后续实现顺序

1. 为业务实现编写 UPP 协议包与正反契约样例；不写运行逻辑。
2. 在隔离模块中实现适配器和黄金样本测试。
3. 独立验证接口、数据分级、资源上限、签名、漏洞与失败恢复。
4. 仅在后续 Phase 经批准后，创建受签名 Manifest 到 UPP 的绑定；Policy、审批、授权租约和审计链仍逐次生效。

