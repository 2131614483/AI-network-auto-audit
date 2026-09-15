# 审计智能中枢 · 展示视频分镜方案

## 定位

- **目标观众**：技术评审者、同行、潜在落地客户
- **核心信息**：AI 落地审计/AIOps 时，把"会胡说、会越权、结果不可信"收敛成一条**可授权、可追溯、可复算、可回滚**的执行链路
- **风格**：深色科技感，真实界面实拍截图 + 缓慢推拉镜头（Ken Burns）+ 底部字幕，不使用 AI 生成虚构画面
- **规格**：1920×1080，30fps，H.264 mp4，时长约 100 秒

## 叙事主线

问题与定位 → AI 组网全过程 → 知识与图谱 → 插件与策略 → 业务证据链 → 数据收尾

## 分镜表

| # | 时长 | 画面（截图） | 底部字幕 |
|---|------|-------------|---------|
| 1 | 6s | 标题卡（深色底） | 审计智能中枢 · audit_network／让 AI 执行过程可被复核 |
| 2 | 7s | desktop/overview.png | 租户隔离 · 策略优先的桌面驾驶舱，控制平面已连接 |
| 3 | 7s | desktop/operations.png | Mission → Workflow → Task → Agent 四层持久运行投影 |
| 4 | 7s | desktop/ops-detail-4level.png | 逐层下钻到节点、能力、尝试与 Token 消耗 |
| 5 | 4s | flow-canvas-frame-1.png | 一次 AI 组网：全员待命 |
| 6 | 4s | flow-canvas-frame-2.png | 凭证穿透：数据包沿端口飞行 |
| 7 | 4s | flow-canvas-frame-3.png | 凭证穿透完成，财务清洗并行推进 |
| 8 | 4s | flow-canvas-frame-4.png | 底稿编制汇聚两路输入 |
| 9 | 4s | flow-canvas-frame-5.png | 证据索引关联，链路逐段导通 |
| 10 | 4s | flow-canvas-frame-6.png | 问题金额核算，接近全通 |
| 11 | 4s | flow-canvas-frame-7.png | 全链路导通，每个节点产物带 sha256 |
| 12 | 7s | desktop/knowledge.png | 文档入库、分块、向量化全链路；历史只回收不硬删 |
| 13 | 7s | desktop/graph.png | 六图分治，claim–evidence 承载主张与证据 |
| 14 | 7s | desktop/plugins.png | 100 个已验收插件，按三层组织 |
| 15 | 7s | desktop/plugins-shadow-run.png | 影子模拟：不启动子进程、不触达外部系统 |
| 16 | 7s | desktop/policy-result.png | 策略网关 deny-first，未裁决即拒绝（fail-closed） |
| 17 | 7s | desktop/approvals.png | 高风险动作先落审批，请求方收到 409 |
| 18 | 8s | desktop/audit-lineage.png | 证据链血缘：确认带账户 Trace，产物锁 sha256 可离线复算 |
| 19 | 7s | desktop/quant-lineage.png | 量化回测 simulated_only，数据快照与代码哈希共同背书 |
| 20 | 7s | desktop/aiops-detail.png | AIOps：告警→提案→变更授权→模拟执行→人工核验 |
| 21 | 6s | 收尾卡（深色底） | 1200+ 测试 · 100 插件 · 16 schema · 113 表 |

合计约 115 秒。Run 画布 7 帧（镜头 5–11）快切，模拟一次组网从待处理到全导通的动画。

## 字幕规范

- 字体：微软雅黑 Bold（msyhbd.ttc），白色，黑色描边
- 位置：底部居中，距下沿约 110px
- 标题字 56px，正文字 40px
