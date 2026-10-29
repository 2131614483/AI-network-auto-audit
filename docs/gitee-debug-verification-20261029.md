# Gitee 下载源码与运行时复测

日期：2026-10-29；当前为部署调试，GitHub 正式发行后置，Phase 10 保持进行中。

## 远端源码

已将产品源码、契约、迁移、测试与部署说明提交到 Gitee 的 `codex/gitee-deployment-debug` 分支，首次提交为 `fa49b83ab6d5b111c7f606a4868b57d1ddb91ed0`。此后只补充部署文档和维护者验证脚本的依赖定位，业务代码不变。没有覆盖远程主分支，没有发布正式安装附件。

第一次直接克隆到较深的 Windows 路径，历史案例归档因文件名过长而检出失败。保留失败目录；第二次使用 `git clone --config core.longpaths=true ...` 成功完整检出。没有删除或改写历史资料，没有修改系统或全局 Git 配置。

从第二次 Gitee 克隆目录创建独立 Python 3.12.12 虚拟环境，执行 `uv sync --python 3.12 --frozen --offline --no-dev`，33 个运行包安装成功。该安装使用现有依赖缓存，不意味着没有缓存的新电脑可离线安装源码。

检查结果：

| 项目 | 结果 |
| --- | --- |
| 远端分支 HEAD | 与上述提交一致 |
| API 导入 | 134 条路由 |
| 审计域已验证插件目录 | 100 项，包含样本量计算插件 |
| 模块来源检查 | 已加载的 apps / packages 模块均来自本次克隆目录，没有从原项目目录兜底导入 |
| 项目许可 | 沿用 Gitee 已有的 MIT LICENSE |

## 用下载源码重新组装运行时

运行克隆源码中的 `scripts/package-windows.py --stage-only --stage <新目录>`，私有 Python 依赖来自上述独立虚拟环境；PostgreSQL 16 / pgvector 来自已验证的本机运行时。组装未下载新工具，排除了 editable 路径钩子和私有 .env，旧资源目录和安装包保留。

| 资源 | 文件数 | 大小 |
| --- | ---: | ---: |
| Python | 4291 | 83.3 MiB |
| PostgreSQL / pgvector | 1903 | 144.0 MiB |
| 应用源码、插件、契约和迁移 | 980 | 4.1 MiB |
| 合计 | 7174 | 231.5 MiB |

这是未压缩运行资源的大小，不是安装器大小。未生成或发布新正式 EXE。

维护者使用根目录 npm 构建工具运行 `scripts/verify-release-runtime.cjs`。真实创建一个新的合成用户工作区，使用随包 Python 和 PostgreSQL 完成以下检查：

1. 明确确认初始化后，经维护策略裁决创建角色、数据库、扩展、迁移、示例和目录；API 健康与租户上下文正常。
2. 本地维护历史为 started / succeeded 两条，并关联为正式数据库策略记录；应用账号的 rolbypassrls=false、rolsuper=false，固定维护策略存在。
3. 通过设置 API 保存仅用于本轮的合成提供方配置；模型端点为 loopback 的不可用测试端口，不调用实际模型。
4. 取消启用规划时不写授权；确认后写入绑定配置的有限 plan_only 规则，重启本机规划 Worker，目录接口成功返回插件。
5. 正常退出再启动，未要求初始化或迁移，未新增维护动作。
6. 测试结束，自己创建的 PostgreSQL 正常停止。保留工作区、日志与测试报告，没有修改开发库。

本机安全报告为原工作区 `build/release-runtime-test-iCbb0e/result.json`，标识 test_only=true、ok=true、native_gui_tested=false、postgres_stopped=true。生成的数据库凭据和合成配置留在被 Git 忽略的测试目录，不进入发布提交。

## 完整测试与范围

同一产品源码在原工作区执行完整 Python 测试：**2579 passed / 13 skipped**，670.77 秒；桌面 **120 passed**、Web **4 passed**；根/桌面 TypeScript、149 文件 Mypy、全仓 Ruff 通过。产品代码摘要与下载分支一致，后续文档修改不重复扩大测试结论。

13 项跳过涵盖本机 Ollama 不可用/未启用、图谱召回两条样例没有匹配、已记录的隔离演练、MinerU 未启用及 E 盘真实资料未挂载。它们不是通过项。

独立源码的额外开发依赖离线安装缺少 ast-serialize 缓存，桌面 npm 离线安装缺少 undici 缓存；二者都明确失败，没有自动联网下载，也没有记成独立冷安装成功。根目录 npm 依赖、Web 测试和类型检查在独立目录正常。

本轮验证发生在当前电脑的新目录、新虚拟环境和新数据库实例。其他电脑的完整原生安装、完整角色/ACL/工件恢复、完整浏览器工作台、Docker 和实际云端模型仍待验收，当前不能称为正式发行验收完成。
