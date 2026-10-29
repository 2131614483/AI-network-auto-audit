# 2026-10-29 分发验收

本轮以用户提供的更新包为输入，合并修复后继续在 Gitee 独立调试分支验证。
当前仍是调试候选，Phase 10 未标记完成。公网网页版、未覆盖平台和真实云模型不在通过范围内。

## 已完成

| 验证 | 结果 |
|---|---|
| 完整 Python 回归 | 2586 passed / 13 skipped / 3 warnings，670.89 秒 |
| 桌面测试 | 121 passed；添加独立配置，避免误用根配置而漏扫 |
| Web 测试 | 4 passed |
| 实际浏览器脚本的上传交互模拟 | 3 passed：审批/网络重试保留键、换文件换键、防重复提交、可读错误 |
| TypeScript | 根目录与桌面通过 |
| Ruff / Mypy | 全仓 Ruff 和 149 文件 Mypy 通过；最终提交前复核 |
| 依赖锁 | uv.lock 与 SQLAlchemy 限制同步，离线检查通过 |
| npm 依赖审计 | 根目录与桌面已知漏洞均为 0；不代表未知漏洞不存在 |
| 新合成工作区、真实随包 Python/PostgreSQL/API/Worker | 初始化、应用角色 NOBYPASSRLS/NOSUPERUSER、维护历史关联通过 |
| 真实文件上传与审批 | 阻断、取消、批准、导入成功、同键重试同一批次通过 |
| 授权边界 | 换键/换内容不可复用；过期/失效审核人不可执行；当前 deny/freeze 优先 |
| 规划配置入口 | 合成端点配置、取消/确认有限授权、目录读取通过；不发生真实云模型调用 |
| 再次启动 | 不迁移、不重复初始化；测试退出后专用 PostgreSQL 正常停止 |

随后实际组装发现 Windows `NODE_OPTIONS` 的反斜杠会被 Node 解析为转义。已改为带引号的正斜杠绝对路径，并通过实际 Node 子进程、含空格目录和禁止网络访问的回归验证；打包单元测试 **4 passed**。这项测试是上述全量回归之后新增的。

发行清单实际生成还发现中文 Git 提交在 GBK 系统上解码失败。已显式统一 Git 日志及 Python 子进程为 UTF-8，以独立临时 Git 仓库复现并验证；分发工具测试合计 **5 passed**。

Python 跳过项保持显式记录：Ollama 未配置/不可用、图谱召回样例未匹配、已有隔离演练、MinerU 未启用和 E 盘真实资料未挂载。这些项目没有算作通过。

上传审批仅对 `knowledge.upload` 消费已绑定的租约。保留原始 REQUIRE_APPROVAL，追加 ALLOW，锁定同租户、原调用、文件指纹和幂等键。没有引入通配 AUTO 或生产执行授权。本机工作区审核人属于操作系统用户的本地身份，不是公网登录机制。

## 尚未通过的发行门槛

- 本轮 Windows 新安装器的交互安装、原生确认框、退出与卸载保留数据，仍需实际界面验收。
- 另一台无 Python/PostgreSQL/Node 的 Windows 电脑实际安装和业务流程，尚未验证。
- 本机 Docker 引擎未运行；本轮只校验 Compose 配置与首次部署构建顺序，不能冒充容器复测。
- 完整旧工作区权限、角色、工件与资料路径恢复，仍未验收为跨电脑一键迁移功能。
- 公网账号认证与可信租户映射尚未提供；简化浏览器工作台不作为公网正式部署入口。

安装包必须以同一提交生成，并提供版本、SHA256、构建来源与范围说明。未完成上述必需界面和异机验收前，不发布为正式稳定版。

## 可复跑检查

```text
python -m pytest -q
python -m ruff check .
python -m mypy packages
uv lock --check
npm ci
npm test
npm run typecheck
npm ci --prefix desktop --ignore-scripts
npm --prefix desktop run typecheck
npm --prefix desktop test
npm --prefix desktop run build
node --test scripts/tests/browser-upload.test.cjs
node scripts/verify-release-runtime.cjs <stage目录> <报告目录>
```

构建使用 Electron 44.10.0、ECharts 6.1.0、Vitest 4.1.12、Vite 7.3.7，保留既有技术栈。修复依据为 [Electron 发布记录](https://github.com/electron/electron/releases/tag/v44.10.0)、[ECharts 安全公告](https://github.com/advisories/GHSA-fgmj-fm8m-jvvx) 和 [Vitest 安全公告](https://github.com/advisories/GHSA-5xrq-8626-4rwp)。
