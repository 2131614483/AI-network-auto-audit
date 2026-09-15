# Phase 3：本地富媒体解析接入（MinerU/OCR）

状态：**已完成**。用户于 2026-09-04 选定方向 A；本地 MinerU、持久队列、单并发 Worker、失败显式重试和 API → Worker → 检索真实链路均已验收。

## 已完成（2026-09-04）

- 契约 `contracts/jsonschema/rich-media-parse-result.schema.json`：定义 `ParsedDocument`（markdown + 带 `page_idx`/`bbox`/`text_level` 的 chunks）。
- `packages/knowledge/rich_media.py`：`MineruAdapter`（输入 SHA256/大小/根边界校验 → MinerU 子进程隔离调用 → 输出解析为 schema 校验的 `ParsedDocument`）。
- `packages/knowledge/rich_media_service.py`：保留确定性的同步服务测试缝，同时新增持久 `enqueue_rich_media_extraction` / 租约领取 / 成功或失败收尾；MinerU 始终运行在两个短数据库事务之间，页码 metadata 写入 `semantic.chunks`。
- 迁移 `0032_rich_media_job_queue`：新增带 RLS 的 `knowledge.rich_media_jobs`。同一文件只能有一个活跃任务；请求带租户、Trace、幂等键和请求哈希；过期租约可重新领取，失败绝不自动重跑。
- API 端点 `POST /api/v1/knowledge/rich-media/extract`：经 Policy Gateway（capability `knowledge.extract.rich_media`，租户/Trace/幂等键）后**只入队**，不在 API 线程运行 MinerU；`POST /retry` 只允许对已记录失败显式重新入队。
- 本机 `apps.worker.local`：沿用既有 Worker 进程，按持久租约一次处理一个 MinerU 任务，避免并发占用 GPU；Worker 只接受账本中的 SHA256 绑定工件，不接受 GUI 传来的路径或命令。
- 待解析列表 `GET /api/v1/knowledge/rich-media/pending`：返回 `waiting_extractor`、`queued`、`processing`、`failed` 的 PDF/图片队列状态和安全错误摘要。
- 桌面知识工作台新增「本地解析队列」标签：显示状态/说明，提供中文确认的「提交队列」及仅针对失败项的「重新提交」；Electron IPC 白名单明确列出两个写接口。
- 适配器目录 `local_adapter_catalog()` 如实反映 MinerU 可用性（`available`/`isolated_subprocess` 或 `waiting_for_local_runtime`/`not_configured`）。
- 策略登记：`registration.py` 新增 `publish_rich_media_allow_policy`（`--enable-rich-media-policy`）。
- 测试：解析/输入边界/非零进程退出单元测试，队列重复提交/租约/失败显式重试集成测试，API 入队不执行测试，RLS 测试，以及真实 MinerU opt-in API → Worker → 检索端到端测试。
- 真实验收：`AUDIT_NETWORK_RUN_MINERU_TESTS=1` 下，生成的单页 PDF 完整链路 27.52 秒通过；不读取或修改用户 PDF、`G:\数据` 或 `D:\MinerU-master`。

本阶段把 `local.mineru` 从「waiting_for_local_runtime」升级为真实的、策略网关托管、隔离子进程运行的本地解析 Worker，让 PDF/图片经 MinerU 解析为带页码引用的 Markdown 与结构化内容块。写入由 Policy Gateway 的持久裁决和不可变任务账本关联，文档仍使用既有版本/回收站生命周期；面向图谱或 Agent 自我进化的 ChangeSet 发布不在本阶段引入。音视频转写（`local.media`）仍延后，缺本地转写运行时。

## 已验证的调用契约（2026-09-04 冒烟实测）

- 源码：`D:\MinerU-master`（**只读参考，绝不修改其任何文件**）
- 入口：`D:\MinerU-master\.venv\Scripts\mineru.exe`（venv 内已装 mineru + torch + pypdfium2）
- 命令：`mineru -p <文件> -o <输出目录> -b pipeline -m auto|txt|ocr -l ch [-f] [-t]`
- 实测：GPU 16GB；`demo1.pdf`（13 页）`pipeline` + `txt` 约 19 秒；模型缓存于 `~/.cache/huggingface`、`~/.cache/modelscope`
- 输出目录：`<out>/<stem>/<method>/`，含：
  - `<stem>.md` —— 解析后的 Markdown（标题、段落、图片引用、图表说明）
  - `<stem>_content_list.json` —— 内容块列表，每项 `{type, text, bbox, page_idx, text_level?}`（页码/坐标回链依据）
  - `<stem>_middle.json`、`<stem>_model.json` —— 布局与模型元数据
  - `images/*.jpg` —— 提取图片

## 目标与范围

1. `packages/knowledge/rich_media.py`：`MineruAdapter` 封装子进程调用（隔离工作目录、超时、输入 SHA256 绑定、输出解析），产出 `ParsedDocument`（markdown + 带 `page_idx`/`bbox` 的内容块）。
2. 把 `local.mineru` 登记为真实运行时绑定（沿用 Phase 1 的显式登记 + 隔离运行时模式），适配器声明不得携带任意命令/网络/密钥。
3. 新增策略 capability（`knowledge.extract.rich_media`，或扩展 `knowledge.extract.document` 白名单），经 Policy Gateway 持久裁决，具备租户 / Trace / 幂等键。
4. 解析结果写入带文档版本的 `semantic.chunks`；chunk `metadata` 携带 `page_idx`、`bbox`、`adapter_key`、`input_sha256`，支持页码引用回链与来源追溯；既有回收站/恢复决定检索可见性。
5. 失败重试、超时租约与可审计任务事件（adapter key、输入 SHA、耗时、错误、Trace、幂等键）。

## 安全边界

1. `D:\MinerU-master` 只读，绝不写入、修改或在其目录内生成输出。
2. 解析输出只落 `audit_network/.data/`，绝不写 MinerU-master 或用户目录。
3. 子进程隔离：专用工作目录、明确超时、输入 SHA256 绑定；不传网络地址、密钥或任意 shell。
4. 所有外部请求经 Policy Gateway；Worker 只消费已授权的持久任务，历史块不硬删除；已回收文档不进检索（沿用 Phase 2）。
5. 音视频转写（`local.media`）本阶段仍为 `waiting_for_local_runtime`。

## 验收

| 交付 | 通过条件 |
| --- | --- |
| 真实解析 | 一个真实 PDF 经 MinerU 解析为 Markdown + 带 `page_idx` 的内容块，内容块落库且可检索 |
| 隔离 | 子进程调用、超时、输入 SHA256 绑定；输出不落 `D:\MinerU-master` |
| 策略与审计 | 解析经 Policy Gateway，具备租户 / Trace / 幂等键，记录 adapter key、输入 sha、耗时 |
| 生命周期 | 解析结果具备文档版本和 chunk 页码引用，可 retire/restore，回收后检索不可见 |
| 回归 | 契约、单元、原生 PostgreSQL 集成、桌面 typecheck/test/build 通过 |

## 已知风险

- MinerU 单文件约 19–28 秒（GPU）；持久队列的本机 Worker 一次只处理一个任务，子进程 900 秒超时并配合 960 秒租约；失败留在队列视图，必须明确重试。
- 扫描件需 `-m ocr`（加载 OCR 模型，更慢）；带文本层 PDF 用 `-m txt` 最快。默认 `auto` 由 MinerU 判定。
- 本机 GPU 被 MinerU 占用时会影响其他任务；当前以单并发保证可预测性，未来若增加设备级并行，必须另行验收 GPU 配额和租约边界。
