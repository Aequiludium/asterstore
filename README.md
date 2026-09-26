# asterstore

面向本地文件数据的独立管理库。

[源码](https://github.com/Aequiludium/asterstore) · [贡献指南](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [变更记录](CHANGELOG.md)

`asterstore` 管理数据的声明、发布、引用、保留、检查和回收，让不同框架共享可解释的数据生命周期，同时保持普通读取轻量。

当前处于 `0.1.0.dev1` 开发阶段。只维护一种声明模型、一种磁盘格式和一套生命周期实现；旧开发格式与旧 API 已移除，尚未作首版兼容冻结。下一步是实际应用接入验证，见[范围与门槛](docs/release-scope.md)。

已实现外部数据登记、受管发布与复用、固定保留、回收和候选恢复/清理。核心无第三方运行依赖；普通读取不逐文件校验，不扫描全仓，不自动建立保留。

## 开发入口

```bash
uv sync --locked --extra polars
uv run --locked python examples/managed_declarations.py
uv run --locked python examples/registration.py
uv run --locked python examples/governance.py
uv run --locked python examples/cleanup.py
uv run --locked --extra polars python examples/parquet.py
uv run --locked --extra polars pytest
```

发布示例在临时目录中完成写入、发布、重新打开、历史绑定与候选恢复。核心没有第三方运行时依赖。

```python
from asterstore import Repository

repository = Repository("./data")
repository.initialize(
    store_id="example",
    resource_ids=["results"],
    managed_resource_id="results",
    lifecycle=True,
)
with repository.prepare(
    "prices",
    publication_id="batch:1",
    operation_id="produce:1",
    expected_generation=0,
) as candidate:
    candidate.write_bytes("prices", b"price\n12.5\n", relative_path="part.csv")
    candidate.commit()

binding = repository.open("prices")
paths = binding.files()  # 直接交给读取引擎；不逐文件预检
```

默认在写入边界同步文件和目录；需要较弱持久性时显式使用 `durable=False`。一次发布声明完整成员集合，可显式复用未变化的受管对象，只写变化的文件。共享对象沿用来源的持久性，不因本次 durable=True 被重新同步。物理回收由调用者显式执行，先持久化退役事实再逐文件删除。具体边界见 [当前 API](docs/api.md)。

## 使用场景

- 一个生产程序持续更新本地分区数据，多个研究或训练框架使用这些数据。
- 消费者绑定一次发布声明后反复读取，在明确的时机刷新。
- 部分任务只需要当前数据，部分任务需要保留特定发布及其物理历史。
- 运维人员希望知道哪些数据仍被使用、哪些写入中断了、哪些文件可以回收。
- 已有目录需要接入统一目录与检查能力，同时保留原来的生产流程。

这里的“本地”首先指直接通过文件系统访问。NFS 等共享挂载的并发和故障保证需要单独验证，不能由本地磁盘上的测试推导。

## 核心原则

1. **独立于业务框架。** Aster 是使用方之一；核心库不依赖 Aster 的协议、因子运行时、交易日历或 PIT 规则。
2. **在边界承担治理成本。** 绑定、发布和运维执行各自必要的检查；普通读取复用绑定结果，不轮询发布、不逐文件预检、不重复完整审计。
3. **直接使用数据引擎。** 本地路径是一等读取结果，Polars、Arrow 等引擎可直接读取；适配层按需提供易用接口。
4. **允许有限保证。** `current_only` 是完整支持的方式，物理历史按需启用。发布身份、物理历史和保留引用分别说明。
5. **使失败可解释。** 明确发布生效点、冲突行为、中断残留和恢复方式，不把缺失文件伪装成空数据。
6. **显式管理保留和回收。** 普通读取不自动写保留记录；需要保护的任务显式建立具名引用，回收服从已登记的引用与写入协调。

## 职责边界

核心库负责数据集身份、存储声明、发布记录、文件或分区定位、提交协调、具名保留引用、显式审计与回收。

调用框架负责业务数据如何产生、哪些分区应存在、字段业务含义、数据可用时间以及业务质量规则。核心库管理声明与对象之间的关系，读取引擎负责文件解码、过滤、投影和计算。

第一版以单个数据集的发布为提交单位。跨数据集事务、分布式协调服务、自动读者续租和查询引擎不属于第一版范围。

## 文档

- [当前 API](docs/api.md)与[项目结构](docs/architecture.md)
- [声明模型](docs/declarations.md)与[唯一磁盘协议](docs/protocol.md)
- [发布](docs/publishing.md)、[治理](docs/governance.md)与[候选清理](docs/cleanup.md)
- [引擎接入](docs/integrations.md)与[性能验证](benchmarks/README.md)
- [开发格式边界](docs/compatibility.md)、[实施路线](docs/roadmap.md)与[目标规范](docs/spec/README.md)
- [开发与构建](docs/development.md)、[仓库自动化](docs/automation.md)与[发行流程](docs/releasing.md)
- [开源实现学习](docs/research.md)与[审计修复](docs/audit-fixes.md)

## License

Copyright 2026 asterstore contributors. 本项目采用 [Apache License 2.0](LICENSE)，许可证全文来自 [Apache 官方](https://www.apache.org/licenses/LICENSE-2.0.txt)。
