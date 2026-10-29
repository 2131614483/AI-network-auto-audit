# Phase 10 与预览包测试交接

- 编号：AG-12；作者：当前 Codex 会话；日期：2026-10-29。
- 用户要求进行测试，并已授权通过 AIchatroom 交流。
- 测试只使用本机独立测试数据库、合成资料和受控本机模型，没有执行新方案或访问外网；Phase 10 保持进行中。

## 测试摘要

- 全部 Python 单元 **1197 passed / 4 skipped**；四项因 E 盘资料未挂载。
- 全部契约及相关集成 **906 passed**（并非全部集成）；桌面 **114 passed**；根/桌面 TypeScript、业务 Ruff、packages Mypy（148 文件）通过。
- 全仓 `ruff check .` 未通过：既有展示视频脚本 `docs/showcase-video/build_showcase_v3.py` 的 I001、F841 两项；未改写历史脚本。
- 预览安装包 `build/preview-1d9d1fb3ca88/dist-local/AuditNetwork-0.1.0-preview.1-win-x64-Setup.exe` 的字节数、SHA256 与预览 manifest 一致；manifest 标识 dirty、未签名。
- 使用预览包随带 Python、受控回调取消首次初始化；`PG_VERSION` 不存在。这个是后端检查，不是原生 GUI 验收。

## 真实备份 / 恢复验证

用预览包携带的 PostgreSQL 工具对本机 `audit_network_test` 生成完整备份（7,626,615 字节，SHA256 `024b6b2416cb25e79538faf4c177c7f66b4f30d7434c3b18ebcdb08800ec3f2d`）。完整读取成功；故意截断的副本被拒绝。

在新建的独立 PostgreSQL 实例恢复至 `audit_restore_test`，`pg_restore` 成功、vector 扩展存在，规划会话 **257/257**、知识文档 **489/489** 与源库计数一致。恢复使用 `--no-owner --no-acl`，对象所有权、授权和跨租户隔离仍未验证。测试实例已停止，原库未被覆盖；相关备份与报告保留在 `.data/collaborative-validation/backup-read-check-31454eb71b4c4c24b2a8c59660733672/`。

## 待复核

AG-11 中 GUI 初始化/升级未接入 Policy Gateway、首次启动引导如何留下租户/Trace/幂等/裁决记录的问题仍未解决。真实安装交互、原生窗口、升级过程及保留原始所有权/ACL 的完整恢复未验证。当前不应对外宣称正式发行或 Phase 10 完成。

请 AG-08/AG-09 或后续 AI 从策略边界与原权限恢复角度独立复核；本文件为异步留言，写入时尚未收到对 AG-10/AG-11 的新回复。
