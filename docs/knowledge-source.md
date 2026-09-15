# 外部只读知识源登记

`register_read_only_source` 只登记目录 URI 和源目录顶层显式 README 文件的元数据（大小、SHA256）。它不会递归扫描 `G:\数据`，不会复制或移动文件，也不会执行任何数据集内容。

示例：

```powershell
python -m packages.knowledge.source G:\数据 `
  --database-url postgresql://audit_app:admin@localhost:5432/audit_network `
  --name "公开资料与数据集"
```

登记后，后续知识工厂仍需由单独的导入任务读取用户明确指定的 README 或子目录。Source 的 `file_rules.recursive_scan=false`、`explicit_readmes_only=true`、`read_only=true` 是数据库和服务共同保存的安全边界。

当前默认只检查源根目录下的 `README.md`、`README.txt`、`README`。若需要登记特定清单，应通过服务的 `readme_names` 显式传入文件名；不会搜索深层目录。
