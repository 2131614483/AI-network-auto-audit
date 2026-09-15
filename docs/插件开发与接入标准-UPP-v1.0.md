# 插件开发与接入标准（UPP v1.0）

状态：**现行开发标准**  
适用范围：知识库、图谱、审计、量化、AIOps、Agent、工作流、数据连接器和声明式 GUI 扩展。

本标准以 [统一插件协议](plugin-protocol.md) 和 JSON Schema 为唯一机器可校验依据。它的目标是让任何业务插件都能被目录、调度、策略、审计与桌面 GUI 统一理解，同时不把“写了一段代码”误当作“已获系统执行授权”。

## 1. 先说结论：插件不是一个脚本

一个可接入插件至少有四层对象，必须分开提交、分开审查：

| 层 | 文件/对象 | 用途 | 是否可执行 |
| --- | --- | --- | --- |
| 规划 | `PluginBlueprint`、`PluginRoutingPlan` | 描述缺少什么能力、可如何组合 | 否；Routing Plan 只能是 `plan_only` |
| 协议 | `plugin.protocol.json`（UPP） | 描述身份、能力、数据契约、权限、资源、可观测性、GUI | 否 |
| 实现 | `plugin.manifest.json` + 插件代码 | 把一个已审查实现绑定到固定运行时 | 单独验证前不能运行 |
| 运行时绑定 | `plugin.runtime-binding.json` | 固定 UPP/Manifest 的 ID、版本与 SHA256，并限定隔离模式 | 仅 `verified` 后可被运行器接受 |

**UPP 不得出现**入口命令、Shell、容器命令、任意 URL、明文密钥、数据库连接串或交易凭据。Schema 使用 `additionalProperties: false`，此类字段会被拒绝。

当前实例中，四个示例协议包均可供学习；只有 `knowledge.document-ingestion@0.1.0` 另有经过验证的本地只读运行时绑定。其他协议包仍是 `contract_only`，不能被调度或执行。

## 2. 协议包的目录标准

每个插件使用一个独立、稳定的目录；目录名与插件 ID 的最后一段保持可辨识，但**身份以 JSON 中的 `id` 为准**。

```text
plugins/
  builtin/ 或 third_party/
    <plugin-directory>/
      plugin.protocol.json              # 必须：UPP 业务与治理契约
      plugin.manifest.json              # 只有进入实现验证时才增加
      plugin.runtime-binding.json       # 只有验证通过后才增加
      ui.contribution.json              # 可选：声明式 GUI，不含任意前端脚本
      README.md                          # 建议：用途、样本、版本和变更说明
      <implementation>/                 # 实现代码，不能绕过运行器
```

协议包必须通过 [unified-plugin-protocol.schema.json](../contracts/jsonschema/unified-plugin-protocol.schema.json)；实现 Manifest、运行时绑定和 GUI 分别受对应 Schema 校验。

## 3. `plugin.protocol.json` 必填内容

| 区块 | 必填字段 | 标准要求 |
| --- | --- | --- |
| 身份 | `protocol_version`、`id`、`version`、`name`、`kind`、`domains`、`lifecycle` | ID 使用小写稳定标识，例如 `audit.evidence-lineage`；所有版本为 SemVer。 |
| 兼容性 | `compatibility.host_protocol`、`adapters` | 可声明 `python`、`node`、`http`、`mcp`、`container`、`wasm`、`desktop`，但声明适配器不等于允许启动该适配器。 |
| 能力 | `capabilities[]` | 每个能力声明稳定 capability ID、输入/输出、幂等依据与副作用。禁止 `run`、`execute` 一类含糊名称。 |
| 治理 | `governance` | 明确数据分级、读写域、网络白名单引用、密钥引用和策略/证据要求。 |
| 资源 | `resources` | 预先声明 CPU、内存、GPU 与超时上限，方便调度器隔离和限额。 |
| 可观测性 | `observability` | 强制 `trace_required: true`；声明结构化日志、指标、审计事件和健康检查。 |
| 溯源 | `provenance.source_refs` | 记录设计来源、标准、数据说明或模型版本。 |
| GUI | `ui` | 只能是 `none` 或 `declarative`；不能把 React、HTML、远程脚本直接塞入协议。 |

### 3.1 能力、数据和幂等键

每个 `capabilities[]` 必须写清：

1. `id`：稳定、可审计，例如 `knowledge.extract.document`。
2. `side_effects`：UPP 只允许 `none`、`read_only`、`write_data`、`external_action`、`financial_execution`。
3. `idempotency`：相同请求如何识别，例如 `artifact_sha256`、`data_snapshot_sha256 + code_sha256`。
4. `inputs` / `outputs`：每项都有 `contract_id`、版本、格式、交付方式、数据分级；大对象只交付 `artifact_ref`、`graph_ref`、`table_ref` 等引用，禁止在控制面消息中传证据正文。
5. `policy`：`write_data` 必须有证据要求；`external_action` 和 `financial_execution` 必须同时要求人工审批和证据记录。

数据分级仅允许：`public`、`internal`、`audit_confidential`、`restricted`。消费者不能因方便而把高分级数据降级交给低权限插件。

### 3.2 权限与网络

`governance.permissions` 必须最小化声明：

- `data_read` / `data_write`：声明逻辑数据域，不以数据库表名作为跨模块接口。
- `network`：默认 `none`；如确有需要，只写主机:端口白名单。
- `secret_refs`：只保存密钥引用名，绝不保存密钥值。
- `gateway_required`：固定为 `true`；每一次能力调用都必须经 Policy Gateway，带租户、Trace 和适用时的幂等键。

UPP 的 `external_action` 在实现 Manifest 中对应 `external_mutation`。开发时必须保持副作用强度一致；不得以较低级别的 Manifest 声明绕过 UPP 的审批和证据约束。

## 4. 声明式 GUI 标准

GUI 是插件能力的**受限展示和请求界面**，不是绕过桌面主程序的执行通道。

当 `ui.mode` 为 `declarative` 时，必须声明 `contribution_schema: "ui-contribution.schema.json"`，并使用 [ui-contribution.schema.json](../contracts/jsonschema/ui-contribution.schema.json)。

| GUI 对象 | 允许内容 | 禁止内容 |
| --- | --- | --- |
| 导航 `navigation` | 已声明插槽、中文标题键、路由、权限引用 | 覆盖主应用页面或未声明插槽 |
| 视图 `views` | `markdown`、表格、表单、图表、图谱、卡片、JSON 或经审查的 renderer descriptor | 任意内联 JS、动态下载组件 |
| 查询 `queries` | capability、类型化输入/输出、缓存与分页 | 直接暴露数据库表或直连数据库 |
| 动作 `actions` | capability、参数 Schema、位置、确认框、风险提示、启用条件 | 直接调用 OS、网络、数据库或绕过审批 |

可用插槽仅有：`global.navigation`、`workspace.navigation`、`workspace.tools`、`dashboard.cards`、`entity.tabs`、`detail.actions`、`settings.sections`。按钮必须映射到一个已声明 capability；桌面端发起请求后仍由 Policy Gateway 判定 ALLOW、DENY 或 REQUIRE_APPROVAL。

## 5. 从协议到真实运行的准入流程

```text
协议包（contract_only）
  → Schema + 契约正反例测试
  → 隔离实现 + 黄金样本测试
  → Manifest 审查（权限 / 资源 / 依赖 / 签名）
  → UPP、Manifest、代码 SHA256 固定绑定
  → 显式发布精确 Policy
  → 逐请求：租户 + Trace + 幂等键 + Policy Gateway
  → 隔离子进程执行 + 审计证据 + 可回放结果
```

准入时必须同时满足：

- UPP、Manifest 和运行时绑定的 `id`、`version`、能力集合与 SHA256 一致；
- 固定入口、显式允许目录、资源上限和超时；不得接受任意命令行或任意路径；
- 至少有契约测试、负向测试和黄金样本测试；
- 策略只发布所需 capability 的最小权限规则；没有规则即拒绝；
- 写入先走证据/ChangeSet/审批链；`financial_execution` 与真实外部动作不能由 GUI 自动放行；
- 运行失败、超时、策略拒绝和重放都可经 Trace 审计，且不静默改写历史。

## 6. 版本和变更规则

- 新增可选字段：小版本升级，消费者必须安全忽略未知可选字段。
- 删除字段、改变输入输出格式、提升副作用、降低数据分级、改变交付方式：主版本升级，并提供迁移或替代路径。
- 已 `retired` 的插件不得用于新任务；历史记录和证据仍必须可解析。
- 变更 UPP 后必须重新计算 SHA256、重新跑契约测试；已验证绑定不能静默指向新文件。

## 7. 最小 UPP 模板

```json
{
  "protocol_version": "1.0.0",
  "id": "domain.capability-name",
  "version": "0.1.0",
  "name": "中文插件名称",
  "kind": "analysis",
  "domains": ["audit"],
  "lifecycle": "contract_only",
  "compatibility": {
    "host_protocol": ">=1.0.0 <2.0.0",
    "adapters": ["python"],
    "fallback_behavior": "human_review"
  },
  "capabilities": [{
    "id": "domain.capability-name.run",
    "side_effects": "read_only",
    "idempotency": "immutable_input_sha256",
    "inputs": [{"contract_id": "artifact-ref", "version": "1.0.0", "format": "artifact_ref", "delivery": "reference", "classification": "internal"}],
    "outputs": [{"contract_id": "result-ref", "version": "1.0.0", "format": "json", "delivery": "request_response", "classification": "internal"}]
  }],
  "governance": {
    "default_data_classification": "internal",
    "permissions": {"data_read": ["domain.records"], "data_write": [], "network": "none", "secret_refs": []},
    "policy": {"gateway_required": true, "approval_required": false, "evidence_required": true}
  },
  "resources": {"cpu": 1, "memory_mb": 256, "gpu": "none", "timeout_seconds": 300},
  "observability": {"trace_required": true, "emits": ["structured_log", "audit_event"], "health": "declared_only"},
  "provenance": {"source_refs": ["设计文档或数据说明"]},
  "ui": {"mode": "none"}
}
```

模板中的 capability ID 应替换成业务含义明确的稳定名称；不要真的使用 `.run` 作为泛化执行入口。

## 8. 开发者交付清单

- [ ] `plugin.protocol.json` 通过 UPP Schema 与正反例契约测试。
- [ ] 能力、数据契约、幂等依据、副作用和数据分级均明确且最小化。
- [ ] 无入口命令、密钥、连接串、任意 URL 或未声明字段。
- [ ] GUI 只使用声明式贡献、稳定插槽与 capability 动作。
- [ ] 实现阶段另附 Manifest、依赖锁定、资源限额、黄金样本和负向测试。
- [ ] 运行时绑定固定 UPP/Manifest/代码 SHA256，且使用隔离子进程。
- [ ] 每一条策略按 capability、风险和副作用精确放行；不使用广泛 AUTO。
- [ ] 写入、外部动作或金融动作已具备审批、证据、回滚/补偿与审计回放设计。

## 9. 本项目中可直接打开的参考文件

| 用途 | 文件 |
| --- | --- |
| 本标准的简明/原始协议说明 | [plugin-protocol.md](plugin-protocol.md) |
| UPP 的机器校验规则 | [unified-plugin-protocol.schema.json](../contracts/jsonschema/unified-plugin-protocol.schema.json) |
| GUI 声明规则 | [ui-contribution.schema.json](../contracts/jsonschema/ui-contribution.schema.json) |
| 当前唯一已验证的只读插件协议 | [knowledge-document-ingestion/plugin.protocol.json](../plugins/builtin/knowledge-document-ingestion/plugin.protocol.json) |
| 对应实现 Manifest | [knowledge-document-ingestion/plugin.manifest.json](../plugins/builtin/knowledge-document-ingestion/plugin.manifest.json) |
| 对应隔离运行时绑定 | [knowledge-document-ingestion/plugin.runtime-binding.json](../plugins/builtin/knowledge-document-ingestion/plugin.runtime-binding.json) |
| 内置协议包目录说明 | [plugins/README.md](../plugins/README.md) |
| 插件拓扑与调度边界 | [plugin-topology-orchestration.md](plugin-topology-orchestration.md) |

在 Codex 中可直接点击以上链接；在 Windows 文件资源管理器中打开项目根目录 `D:\pythonpro\audit_network` 后，进入 `docs` 或 `plugins\builtin` 即可查看。建议用 VS Code、Cursor 或任意文本编辑器打开 JSON/Markdown；不要双击运行插件目录中的 Python 文件来绕过注册、策略和隔离流程。
