# Phase 10：保留 Debug 与外接 AI API 两种模式

日期：2026-10-28。对应用户要求“现在保留 ai 自己外部模拟 api 接口的 debug 模式和外接 ai api 模式”。本项目是本地 Electron 软件；浏览器只是可选入口。

## 使用入口

桌面 **AI 设置 → AI 组网接入模式**，选择后保存：

| 模式 | 数据流与用途 |
|---|---|
| Debug（默认） | 项目经过策略检查，把真实上下文请求发到绑定本会话的 loopback OpenAI 兼容 API；中继导出请求，当前 Codex 会话逐次编写并提交响应；项目继续校验和评估。仅合成资料联调。 |
| 外接 AI API | 使用下方已有统一模型配置 `AI_PROVIDER/BASE_URL/MODEL/API_KEY`。支持 OpenAI 兼容 API 或本机 Ollama；逐次调用经过出站策略裁决。 |

模式对应 `AI_PLANNING_MODE=debug|external`。保存不会清除 API Key；两种模式共用审计主脑、知识/插件关系/历史检索、编排、独立评审与预算。没有自动回退或预置成功答案。连接测试按钮明确标为“测试已保存的 API 连接”，测试的是保存的模型提供方，不会代替当前会话回填。

每个新会话保存不含密钥的接入快照（模式、提供方、模型、端点、配置指纹），并加入创建事件。checkpoint 保留此快照；处理中配置不一致，或历史非终态没有快照，会停止并要求新建。终态回读不接触模型。API Key 轮换不改变模型配置指纹。独立后台 Worker 需要在设置修改后重启，以加载与 API 一致的配置；不一致会明确失败，不会偷偷改用旧出口。

## Debug 约束与回填

服务端显式设置 `AUDIT_COLLABORATIVE_DEBUG_SYNTHETIC=1`，连接独立本机测试库（loopback，库名以 `_test` 结尾或 `test_` 开头）。没有声明或连到普通/远端数据库时拒绝开启中继。该约束用于避免误连，合成资料仍须由操作者准备。

默认记录目录为 `.data/ai-session-debug/<tenant>/<session>/`，受信服务端可通过 `AUDIT_COLLABORATIVE_DEBUG_ROOT` 改变根目录。工作台会话详情显示该目录。每个请求写入 `requests/<request-id>.json`；当前会话读取上下文并通过 `submit_session_response(exchange_dir, request_id, answer)` 回填，生成不可覆盖的 `responses/<request-id>.json`。响应必须通过角色契约并匹配请求、租户、Trace、会话、轮次和消息摘要。

本机 API 使用进程内随机 Bearer Token，禁代理和重定向；令牌不写文件。单次等待不超过 120 秒，并受整体预算限制。超时明确失败。**需要当前 Codex 会话参与，不是独立的无人值守模型服务。**

## 本次实际验证

使用正式 `debug` 开关和自动创建的中继（未注入模型），独立 `audit_network_test` 合成资料：

- 会话：`1bfdc5c6-a15e-4a7d-8cc5-eae119c07492`。
- 记录目录：`.data/collaborative-validation/mode-debug-88d08972798a40428129ea2368c1dca3/`。
- 当前会话实际回填 `audit_brain → audit_brain → planner → reviewer` 四次响应；首轮缺方法正文，补查后才进入插件关系与历史读取。
- 最终 `accepted`、2 轮、4 次模型调用、7 次来源查询计数、24 条事件。两项审计程序映射两节点和一条连线，硬检查和程序评审均通过。
- 编译方案摘要：`0d9b2200ec9af4e9c9f9f8aa65badb71fa9e926dbd430372f7db55a84584d7cb`；仅规划，没有执行插件。
- `verification.json` 核对了四条 ALLOW 裁决的模式与传输、实际请求和回填响应摘要，以及无需模型的终态重放，全部一致。

外接模式另外用受控 loopback HTTP 提供方走完整服务闭环，两轮六次调用通过，事件记录 `external/configured-ai-api`，未发送任何内容到互联网。针对 `medium/external_network` 的拒绝策略在网络调用前生效。单元检查覆盖无自动回退、密钥不入快照、错误正文不入规划记录、禁重定向与响应大小上限。

桌面纯逻辑测试、TypeScript 检查和 Electron 构建通过。实际启动 Electron 的操作被自动审批以“策略阻止”拒绝，未完成本轮原生窗口走查，不能以此前浏览器组件走查替代。本次没有重测线上余额，也未修改用户的模型地址或 Key；已有线上配置先前返回余额不足，不能声称真实云端组网已通过。

没有新增数据库迁移；开发库和默认未启用策略保持现状。真实业务、替换模型效果与长期后台运行仍待验收，Phase 10 保持进行中。
