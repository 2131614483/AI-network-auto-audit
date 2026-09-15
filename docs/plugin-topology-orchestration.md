# 插件知识图谱与上层调度设计（M0 契约版）

状态：**已设计、不可执行**。本文件定义未来插件接入前的上层统筹图谱；不注册真实插件、不加载代码、不创建运行时、不触发 Agent 或工作流。

参考基线：已扫描 `D:\python_rejiance\gh_2131614483\Audit-Networking` 的 484 份 Markdown（90 份顶层方案、78 组模块的 README/API/ARCHITECTURE/CUSTOMIZATION/TROUBLESHOOTING 共 390 份，以及其余说明），重点精读组网、知识图谱增强、接口矩阵、开发规划、进度与经验文档。吸收/修正记录见 `docs/reference-audit-networking-review.md`。

## 1. 目标

系统需要的不只是一个“插件列表”，而是一张可解释的插件知识图谱：用户或 Agent 提出目标后，系统能先判断应进入哪个集群、需要哪些能力槽位、依赖哪些领域图谱，以及计划是否需要审批。只有计划被后续 Policy 与 Workflow 模块再次裁决，才可能形成实际任务。

```text
意图 / Mission
  → L0 插件集群选择
  → L1 插件蓝图候选（尚未安装）
  → L2 能力与输入输出契约链
  → L3 领域知识子图的受控桥接引用
  → plan_only 路由计划
  → [未来] Policy 决策 + Workflow 版本化 + Agent 执行
```

当前阶段在最后一个 `plan_only` 停止。它不是“auto 模式”的旁路，也没有 shell、网络、文件、数据库或交易执行权。

## 2. 四图分治 × 五层调度 × 多轴集群

参考项目提出“模块能力图、流程编排图、统一本体图、证据链图”四图协同。当前系统采用这项分治思想，但不让一张图成为全部系统状态的唯一真相：

| 图族 | 权威内容 | 当前/未来所有者 | 在线职责 |
| --- | --- | --- | --- |
| T：插件能力拓扑图 | 集群、蓝图、能力、契约、场景、兼容/替代关系 | Plugin Topology | 发现能力、缺口分析、生成候选计划 |
| W：工作流定义与运行图 | 版本化 DAG、决策点、补偿、任务/运行引用 | Agent Orchestrator | 冻结已批准计划、调度与恢复 |
| O：领域本体与知识图 | 实体、关系、别名、时间快照、领域推理 | 各 Domain Graph | 语义对齐和受预算领域查询 |
| E：证据与数据血缘图 | Artifact、Evidence、Claim、Finding、派生链 | Audit/Evidence | 不可变追溯、矛盾检查、复核 |

T 图是本文件要先完成的“插件知识图谱”。W/O/E 通过版本化公共引用协同，不与 T 图共享可变节点，也不把心跳、任务队列或原始证据正文写入 T 图。

### 2.1 五层调度语义

| 层级 | 图谱/对象 | 职责 | 能否存领域事实 | 能否执行 |
| --- | --- | --- | --- | --- |
| L0 | 插件集群图（Plugin Cluster Graph） | 按知识、图谱、审计、量化、AIOps、治理等调度域分组；定义候选数、链长、延迟预算 | 否 | 否 |
| L1 | 插件蓝图图（Plugin Blueprint Graph） | 描述未来可填充的插件槽位、所属集群、生命周期和选择条件 | 否 | 否 |
| L2 | 能力/契约依赖图（Capability Graph） | 描述 capability、输入/输出契约、依赖、替代与回退关系 | 仅契约元数据 | 否 |
| L3 | 领域知识子图（Domain Graphs） | Knowledge、Audit、Quant、AIOps 等各自权威图谱；各自分层、分库、分预算 | 是，由领域模块拥有 | 领域模块决定 |
| L4 | 运行与证据图（Runtime/Evidence Graph） | 将来记录计划、Policy 决策、工作流、运行、产物和证据引用 | 仅不可变引用/投影 | 仅在已批准工作流中 |

### 2.2 图谱隔离原则

1. L0–L2 是**插件控制面图**，不能复制 PDF、证据正文、行情或领域节点属性。
2. L3 保持多个物理或逻辑图空间；例如“审计证据图”“量化因子图”“知识实体图”不能因为要可视化就合并成一张大图。
3. L0–L2 到 L3 只允许四种有类型桥接：`artifact_ref`、`capability_contract`、`released_graph_ref`、`health_signal`；桥只保存版本化 Ref。
4. 任何跨图查询都先在控制面收敛到集群、蓝图与契约，再向一个或少量 L3 图空间提交带预算查询；禁止全图扫边。
5. 一张图的节点或边被回收、版本回滚或发布变更时，只发布新的 Ref；旧引用保持可解析，控制面不能修改领域权威数据。
6. Outbox/Inbox 负责“刚发生了什么”，图谱负责“对象是什么关系、历史如何追溯”。图谱不承担消息总线、锁、队列或高频心跳。
7. Registry/运行时健康表是在线状态真相；T 图只保存某个时间点的 `health_snapshot_ref`，避免图查询结果因心跳写入不断抖动。

### 2.3 多轴集群，而不是单棵目录树

一个蓝图同时拥有多种集群成员关系，不能再用单一 `cluster_id`：

| 集群轴 | 初始集群 | 用途 |
| --- | --- | --- |
| `business_domain` | `financial-audit`、`internal-audit`、`compliance`、`it-audit`、`fraud`、`tax`、`supply-chain`、`esg`、`capital-markets`、`financial-institutions`、`cross-border`、`continuous-audit`，另加 `knowledge`、`quant`、`aiops` | 先按业务边界收敛候选；对应参考项目的 12 个审计域并覆盖本项目新增域 |
| `capability_family` | `thin`、`ml-nlp`、`llm-rag`、`kg-gnn`、`cv`、`blockchain`、`federation`、`rpa`、`streaming` | 对应参考项目 78 个模块的 9 类依赖/能力族；用于能力检索和环境规划 |
| `runtime_pool` | `local-cpu`、`local-gpu`、`long-running`、`isolated-process`、`remote-approved` | 将业务语义与实际可用资源分开；这里只是资源需求规划，不代表已部署 |
| `governance_zone` | `public`、`internal`、`audit-confidential`、`restricted`、`financial-action`、`production-change` | 在规划阶段提前形成信息墙与审批边界 |

集群可通过 `parent_cluster_id` 形成子集群，例如 `financial-audit/related-party`；不同轴之间只通过蓝图的 `cluster_memberships` 相交，不建立全连接边。未来一个真实插件可填充一个蓝图；一个蓝图也可以具有多个候选实现，但必须经过独立 Manifest、签名、隔离和审批流程。

### 2.4 从参考项目导入“规划蓝图”，不导入插件

参考项目已经形成 78 个模块、12 个业务域、9 个能力族、45 条内部数据依赖边、39 个外部数据入口。它们可作为第一批**设计蓝图来源**：

1. 每个模块文档只生成 `PluginBlueprint(lifecycle=planned)`，保留源文档 URI/哈希；
2. `输入/输出`转换为带版本、格式、传递方式、数据分级的契约绑定；
3. 45 条数据边转换为 `PRODUCES/CONSUMES` 推导出的候选依赖，不直接变成可执行流程；
4. 39 个外部入口转换为 `ExternalSourceRequirement` 缺口，默认无连接凭证、无网络授权；
5. CO-04↔CO-05 一类反馈回路拆成两个运行 epoch 的事件反馈，单次 `PluginRoutingPlan` 始终保持 DAG；
6. README 中的成熟度只记录为蓝图来源事实，不继承“已完成/可生产”的结论，必须由本项目重新验证。

## 3. 四个版本化契约

| 契约 | 用途 | 强制限制 |
| --- | --- | --- |
| `plugin-cluster.schema.json` | L0 多轴集群、父子层级、桥接种类、配置继承和路由预算 | 不含 endpoint、密钥或执行参数 |
| `plugin-blueprint.schema.json` | L1/L2 未来插件槽位、多集群成员关系、能力、来源和接口契约 | 生命周期只能是 `planned`；刻意没有 runtime、entrypoint、包路径和权限字段 |
| `plugin-topology-release.schema.json` | 冻结一次集群/蓝图目录版本及 SHA256 | 发布后不可原地覆盖；路由计划必须锁定具体发布 |
| `plugin-routing-plan.schema.json` | 调度器输出的候选 DAG、替代项和能力缺口 | 模式只能是 `plan_only`；不能出现 `execute`/`auto`；必须携带目录发布锁、planner 版本、trace 和时间 |

`PluginBlueprint` 与已有的 `PluginManifest` 不可混用：前者用于设计与统筹，后者是未来安装、验证和运行时登记的声明。只有受审批的绑定操作才能把蓝图关联到某一个已验证 Manifest 版本。

## 4. 调度器算法（未来实现约束）

1. 解析用户意图或 Mission，识别业务场景、数据类型、数据分级、目标能力和必须保留的证据。
2. 先按 `business_domain` 与 `governance_zone` 收敛，再按 `capability_family` 与 `runtime_pool` 交叉筛选；禁止遍历全目录后再过滤。
3. 将需求契约与蓝图的输入/输出契约做版本、格式、传递方式和数据分级兼容检查；大对象只传 `ArtifactRef`/`GraphReleaseRef`。
4. 执行能力缺口分析：找不到候选时输出 `unresolved_capabilities`，不得用名称相似的蓝图静默替代。
5. 在 L2 组成无环候选链：`depends_on`、`data_flow`、`fallback`、`bridge` 均为显式边；条件只引用已发布的 `condition_ref`，禁止在边上存任意可执行表达式。
6. 同时生成主路径和最多 8 条替代路径；替代要求输出契约兼容，且风险等级不能高于原节点，除非明确升级审批。
7. 计算候选数、链长和规划延迟；任一超过 L0 预算即返回“需要缩小范围”，不降级为全局扫描。
8. 锁定 `PluginTopologyRelease`、planner 版本和健康快照引用，输出不可执行的 `PluginRoutingPlan(mode=plan_only)`。
9. 后续阶段才允许：Policy 模拟 → 人工/规则决策 → Workflow 版本冻结及依赖校验和 → Agent 创建任务 → 插件隔离执行。

调度优先级：硬黑名单 > 信息墙/数据分级 > 契约兼容 > 集群预算 > 可用健康快照 > 成本/延迟 > 场景偏好。健康、成本只能影响候选排序，不能越过黑名单或批准要求。

配置采用三级继承，但全部配置必须是结构化值：蓝图版本默认值（不可改）→ 用户自定义版本（可回滚）→ 带 TTL 的运行时覆盖。运行时覆盖只能影响并发、超时等允许字段，不能扩大权限、网络、数据域或副作用等级。

## 5. 未来数据库边界（不在本阶段创建）

未来由独立 `Plugin Topology` 模块拥有自己的数据库/迁移，建议表如下：

| 表 | 权威内容 |
| --- | --- |
| `plugin_clusters` / `cluster_versions` | L0 集群与已发布版本 |
| `plugin_blueprints` / `blueprint_versions` | L1/L2 槽位、契约、依赖与状态 |
| `cluster_memberships` | 蓝图在业务/能力/资源/治理多轴集群中的成员关系 |
| `interface_contracts` / `compatibility_results` | 输入输出契约版本、格式、字段和生产者/消费者兼容结果 |
| `topology_edges` | 蓝图/能力之间的有类型边 |
| `domain_bridges` | 指向 L3 公共 Ref 的受限桥接，不保存领域正文 |
| `routing_plans` / `routing_plan_nodes` / `routing_plan_edges` | 只读、可重建的 plan-only 规划结果 |
| `manifest_bindings` | 已批准蓝图到真实 Manifest 版本的绑定；默认为空 |
| `configuration_versions` / `runtime_overrides` | 默认/自定义版本与带 TTL 的窄范围临时覆盖 |
| `health_snapshot_refs` | Registry/AIOps 健康快照引用；不接收高频心跳写入 |
| `topology_releases` / `topology_recycle_bin` | 发布、回滚、软删除与恢复 |

禁止创建：`plugin_code`、任意命令字段、密钥字段、领域证据正文、副本行情、直接执行队列。真实执行任务仍由 Agent Orchestrator 拥有，真实插件生命周期仍由 Plugin Runtime 拥有。

## 6. GUI 设计

在现有“插件工作台”下新增**插件拓扑**视图，而不是把规划节点混入领域图谱：

- 左栏：集群树（L0）与领域标签；
- 中央：L0–L1 力导向图或 DAG，按集群/蓝图区分形状；能力契约（L2）在右侧检查器按所选蓝图展开，避免总览的节点/边爆炸；
- 视图模式：按业务域、能力族、运行池、治理区四种轴切换；同一蓝图只绘制一次，成员关系以过滤器呈现，避免边爆炸；
- 右栏：蓝图详情、契约、依赖、桥接 Ref、预算和“不可执行”状态；
- 顶部：意图输入 → “生成计划”按钮，结果仅显示候选链、预算和审批需求；
- 底部：计划版本、Policy 模拟链接、未来 Manifest 绑定历史。

画布必须复用当前 ECharts 受预算加载：容器宽高为 0 时不初始化，尺寸变化使用 `ResizeObserver` 重排；节点/边截断时展示 `partial`，API 失败提供可操作的错误状态，不使用过期静态数据伪装在线健康。

任何“执行”“安装”“自动运行”按钮在 M0/M1 都不出现。将来出现写操作时必须先走 ChangeSet/发布，再调用相应模块的 PolicyPort。

## 7. 实施顺序

| 里程碑 | 可做内容 | 明确不做 |
| --- | --- | --- |
| M0（本次） | 参考库 484 份 Markdown 扫描、四图/多轴设计、四份 Schema、契约测试、示例蓝图 | 数据库、真实插件、自动执行 |
| M1 | 独立拓扑模块、版本化草稿/发布/回收站、纯计划算法 | Manifest 安装、Agent 任务 |
| M2 | 本地 PostgreSQL、预算/循环/跨域桥接测试、只读 GUI | 真实插件运行、外部网络调用 |
| M3 | 与 Plugin Runtime、Policy、Agent 的 Adapter 契约 | 绕过审批的 AUTO 执行 |

## 8. 验收条件

1. 每个计划都只能为 `plan_only`，契约必须拒绝 `execute`。
2. 蓝图没有运行时或入口点字段，不能被误当成真实插件。
3. 一个路由计划不超过集群预算，且 DAG 无环（M1 服务层检查）。
4. 跨图桥接只能使用允许的公共 Ref，不能读取领域私有表。
5. 图谱、计划、发布和回收站均有版本/trace/操作者信息（M1 起）。
6. GUI 必须显式写出“规划，不执行”，并在未来执行前展示 Policy/Approval 状态。
7. 路由计划锁定拓扑发布 SHA、planner 版本、健康快照 Ref 与 trace；相同输入和锁定版本可复现相同候选链。
8. 单次计划禁止循环；反馈关系通过下一运行 epoch 的事件表达。
9. 蓝图至少保留一个来源文档/契约 Ref，参考项目的“已完成”标签不能直接成为本项目的可信成熟度。
