# 2026-10-29：Phase 10 与 Windows 预览包测试

用户要求进行测试。本轮测试只使用本机独立测试数据库、合成资料和本机受控模型；没有访问在线模型，没有修改开发库或执行新组网方案。Phase 10 不因此自动完成。

## 测试结果

| 范围 | 结果 | 说明 |
|---|---|---|
| Python 全部单元测试 | **1197 passed / 4 skipped** | 四项因 E 盘业务资料未挂载跳过；额外 2 条第三方弃用警告 |
| 全部契约及相关集成 | **906 passed** | 覆盖三源、会话、协作服务、模型设置及 CW1/CW3 等指定测试；不是全部集成套件 |
| Electron / 桌面单元测试 | **114 passed** | 含 14 项数据库维护与启动器故障注入测试 |
| 根/桌面 TypeScript | 通过 | `npm run typecheck`、`npm --prefix desktop run typecheck` |
| 业务 Ruff / Mypy | 通过 | `ruff check apps packages tests`；Mypy 共 148 文件 |
| 全仓 Ruff | **未通过** | 既有 `docs/showcase-video/build_showcase_v3.py` 两项：导入排序 I001、未使用变量 F841 |
| 预览包完整性 | 通过 | 二进制大小与 SHA256 与预览 manifest 一致；manifest 标记 dirty、未签名 |
| 随包后端取消初始化 | 通过 | 随包 Python 返回目标 0069；模拟取消后未创建 `PG_VERSION`。没有打开安装器或 Electron GUI |

测试入口保留 `AUDIT_NETWORK_SKIP_DOTENV=1`；单元测试使用 `--confcutdir=tests/unit`，集成只使用 `audit_network_test`。`external` 模式的集成使用受控 loopback HTTP，不能视为真实外部 API 已通过。

## 实际备份及隔离恢复

使用预览包自带的 PostgreSQL 工具，只从本机 `audit_network_test` 读取并生成完整自定义格式备份。备份 **7,626,615 字节**，SHA256 `024b6b2416cb25e79538faf4c177c7f66b4f30d7434c3b18ebcdb08800ec3f2d`。

- `pg_restore --file NUL` 读取完整备份成功；故意截断的副本被拒绝。
- 使用随包 PostgreSQL 在新的独立目录创建并启动隔离测试实例，显式恢复到 `audit_restore_test`。`pg_restore` 成功，`vector` 扩展存在。
- 恢复后规划会话 **257/257**、知识文档 **489/489** 与源测试库对应计数一致。
- 恢复使用 `--no-owner --no-acl`，因此只验证此次数据库恢复和两类数据计数，**没有验证原始对象所有权、授权或跨租户隔离**。
- 隔离实例已正常停止并经 `pg_ctl status` 验证；测试目录及结果保留，不删除或覆盖原资料。
- 原始备份和结果：`.data/collaborative-validation/backup-read-check-31454eb71b4c4c24b2a8c59660733672/`。恢复结果在其中的 `restore-check-3614c6d9782d4a99ba9d2678aa26d6f2/result.json`；`.data` 不纳入发行。

## 未完成项

1. 初始化/升级的 GUI 维护入口仍未接入 Policy Gateway 的租户、Trace、幂等与裁决审计；预览包不满足全部正式发布边界。
2. 没有在真实 Windows 干净机器完成安装器交互、Electron 原生窗口目视及升级过程的完整端到端验证。此前原生窗口启动被自动审批拦截；本轮没有绕过该限制。
3. 上述数据库恢复没有检查原所有权和 ACL；未对现有业务工作区进行实际升级。当前也没有进行真实业务数据或长期后台运行验收。
4. 未重新验证互联网模型/API Key；10 月 28 日的额度不足结果属于历史记录。本轮没有对外联网。
5. 当前构建基线包含未提交的文件。没有上传 GitHub、发布 Release 或部署网页；没有运行 `python -m pytest -q` 全量集成套件。

用户授权通过 `AIchatroom` 交流，本轮测试结论另写入 AG-12 交接；留言时尚未收到其他 AI 对 AG-10/AG-11 的新回复。
