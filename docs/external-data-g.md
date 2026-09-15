# `G:\数据` 外部开发数据源登记

登记时间：2026-09-03。该目录是**只读外部源**，不属于本仓库，也不允许导入程序移动、改写或全量复制其内容。

## 接入策略

| 数据集/资料 | 用途 | 初始接入方式 | 默认分类 |
| --- | --- | --- | --- |
| `01-审计知识图谱项目` | 图谱 schema、关系抽取与审计查询参考 | 仅导入 README、许可证和小型 Markdown/JSON 样本 | public-reference |
| `02-图谱审计Agent` | 代码/插件审计适配器与 Golden Query 参考 | 仅代码审计扫描，不执行其中脚本 | public-reference |
| `03-AI审计技能包` | 插件 Manifest 和技能契约参考 | 解析 Manifest/文档，人工批准后才可注册 | public-reference |
| `04-审计数据集与基准/Audit-Risk-Classification` | 审计风险分类离线基准 | CSV 快照 + SHA-256，作为只读评测数据 | public-benchmark |
| `04-审计数据集与基准/PCCA-Benchmark` | 因果图谱审计基准 | 按案例增量读取，禁止自动解压 520 MB 主压缩包 | public-benchmark |
| `04-审计数据集与基准/VynFi-Group-Audit` | 合并审计模拟数据 | 仅在人工启动的数据准备任务中解包 | synthetic-benchmark |
| `05-依赖分析与插件审计` | 插件/依赖安全审计 Golden Set | 代码审计扫描，不安装或执行第三方依赖 | public-reference |
| `06-PgVector知识图谱基础设施`、`07-补充参考` | 索引/图谱健康规则参考 | 文档和测试样本优先，按白名单读取 | public-reference |

## 已确认的规模与边界

- 总量约 27,095 个文件、3.62 GB。
- `PCCA-Benchmark` 已解出的样本约 325 MB；完整 zip 不自动解压。
- `VynFi` 的 `.tar.zst` 约 870 MB；不自动解包。
- `Audit-Risk-Classification/audit_risk.csv` 约 81 KB，可作为首个离线风险分类评测集。

## 运行时约束

1. 数据源登记使用绝对路径和内容哈希；任何导入都在数据库中保留来源 URI、大小、SHA-256、许可证与分类。
2. 知识工厂默认只扫描显式选择的子目录和允许的扩展名，绝不递归扫描整个 `G:\数据`。
3. 外部代码仓库只作为审计对象；系统不从其中安装依赖、执行脚本或注册插件。
4. 压缩包、二进制、密钥样式文件和 `.env` 默认隔离；只有经策略批准的提取器可读取。
5. 审计域和量化域仅可消费标有 `public-*` 或 `synthetic-*` 的数据产品；不得把客户审计数据写入此源或反向导出。
