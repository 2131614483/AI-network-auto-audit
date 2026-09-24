# 待定插件区（pending plugins）

这里的插件**不在** `plugins/builtin/` 的正式注册清单（`tests/contract/test_plugin_layout.py::IDS`）中，
因此不参与任何域的发现（`discover_plugins`）与端口契约构建。放这里是为了**保留内容**、
同时不让未完成的契约卡住主干。

## audit_network_skill_output（`audit.network.skill-output`）

- **来源**：2026-09-14 21:14 由另一个会话创建，2026-09-22 从 `plugins/builtin/` 移到此处。
- **为什么隔离**：它被 `git add` 入库了，但从没走完注册流程，并因此造成确定的回归：
  - `test_plugin_layout.py::test_on_disk_declaration_dirs_match_the_allow_list_exactly`
    报 `on disk but not allow-listed: ['audit_network_skill_output']`；
  - 它声明但**不存在**的 2 个 schema，经 `composer.schema_sha256()` 的 fail-closed，
    让 `catalog.build_port_contracts(_specs_for("audit"))` **整体抛 `ValueError`** ——
    即 audit 域的端口契约目录完全无法构建，连带 `tests/unit/test_workbench_domain_gates.py` 5 个用例红灯。
- **它自己声明的、同样不存在的测试**（见 `plugin.manifest.json` 的 `tests` 字段）：
  - `tests/contract/test_audit_network_skill_output_contract.py`
  - `tests/unit/test_audit_network_skill_output_runtime.py`

### 要正式注册，至少需要

1. 补上 2 个 schema 到 `contracts/jsonschema/`，与其 `plugin.protocol.json` 声明的端口一致：
   - 输入 `contract_id=network-result-set`，`schema_ref=network-result-set.schema.json`
   - 输出 `contract_id=skill-output` v1.0.0，`schema_ref=skill-output@1.0.0`
2. 写出上面 2 个测试文件。
3. 把目录名 `audit_network_skill_output` 加入 `tests/contract/test_plugin_layout.py` 的注册清单。
4. 重新跑 `python -m pytest tests/unit/test_workbench_domain_gates.py
   tests/contract/test_plugin_layout.py -q`，并确认 `build_port_contracts(_specs_for("audit"))` 不再抛异常。

> 注意：`plugin.runtime-binding.json` 里记录的 `plugin.sha256` / `protocol.sha256`
> 是对当时文件内容算的；若改动 manifest/protocol，需同步更新这两个摘要。
