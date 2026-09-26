# asterstore

面向本地文件数据的独立管理库。

[源码](https://github.com/Aequiludium/asterstore) · [贡献指南](CONTRIBUTING.md) · [安全报告](SECURITY.md) · [变更记录](CHANGELOG.md)

`asterstore` 管理数据的声明、发布、引用、保留、检查和回收，让不同框架共享可解释的数据生命周期，同时保持普通读取轻量。

项目处于 `0.1.0.dev0` 开发阶段。v4 已支持 registered 登记、managed 发布与复用、候选恢复，以及两种范围的直接保留、退役、可恢复文件回收和失败候选清理。新仓库须显式初始化并声明必要功能；API 与协议尚未冻结。发布依赖图、元数据压缩和完整审计仍未完成。原有 v3 生命周期保留为独立实现，v1/v2 只读兼容，没有自动升级。

新的目标设计见 [治理规范草案 0.1](docs/spec/README.md)：既有目录登记与受管发布并列，明确能力、元数据权威性、保留范围和验收要求。该规范尚未全部实现；身份与路径分离已在 v3 落地，旧仓库不自动升级。

设计优先级：通用治理语义和性能契约优先。继承 Aster 的轻量读取、边界检查、显式保留与可恢复运维原则，不承诺兼容旧 Aster 的 API、目录布局或元数据实现。Aster 接入由应用侧适配，必要时重构应用或显式迁移。

已提供新的[声明与资源绑定模型](docs/declarations.md)：逻辑成员、对象身份和物理定位分离，显式区分 managed/registered 能力。当前支持内存绑定、引擎读取及 v4 registered 持久登记；新模型的 managed 发布、复用与恢复也已接入 v4；直接保留与 GC 也已接通。详见 [managed v4](docs/managed-v4.md) 和 [治理 v4](docs/governance-v4.md)。

## 开发入口

```bash
uv sync --locked
uv run --locked python examples/publication.py
uv run --locked python examples/retention.py
uv run --locked python examples/reuse.py
uv run --locked python examples/collection.py
uv run --locked python examples/candidate_cleanup.py
uv run --locked python examples/cleanup_v4.py
uv run --locked python examples/binding.py
uv run --locked python examples/declarations.py
uv run --locked python examples/registration.py
uv run --locked python examples/managed_declarations.py
uv run --locked python examples/governance_v4.py
uv run --locked pytest
uv build --no-sources
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
with repository.prepare_managed(
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

- [治理规范入口](docs/spec/README.md)：目标语义、实现差距与规范文档。
- [设计与保证边界](docs/design.md)：原则和核心规范导航。
- [保留、退役与回收设计](docs/retention.md)：引用、共享对象、退役及物理回收的实现语义与保证边界。
- [可选引擎接入](docs/integrations.md)：Polars、延迟执行与显式保留。
- [候选残留治理](docs/candidates.md)：显式放弃、私有文件清理和中断重试。
- [场景推演](docs/retention-scenarios.md)：共享对象、引用竞争、回收中断与验收规格。
- [实施路线与验收](docs/roadmap.md)：阶段交付物、需要验证的行为和暂缓事项。
- [首版范围与冻结检查](docs/release-scope.md)：v4 推荐入口、旧协议兼容、已有证据与发行缺项。
- [开源实现学习记录](docs/research.md)：源码证据、适合吸收的机制及其限制。
- [项目结构](docs/architecture.md)：子包职责、导出与依赖方向。
- [新声明 v4 协议](docs/protocol-v4.md)：Store 身份、registered 持久登记、对象身份约束与恢复。
- [受管 v3 协议](docs/protocol-v3.md)：逻辑身份、版本门槛、JSON Schema 与兼容边界。
- [历史 v2 协议](docs/protocol-v2.md)：v3 沿用的记录结构、引用与回收流程。
- [历史 v1 协议](docs/protocol.md)：旧仓库的只读兼容格式。
- [声明与资源绑定](docs/declarations.md)：Member/Object/Locator、能力组合和外部数据的零 I/O 绑定。
- [当前 API](docs/api.md)：已实现能力和未实现的边界。
- [开发与构建](docs/development.md)：uv 环境、检查与独立安装验证。

v4 治理示例见 [governance_v4.py](examples/governance_v4.py)，失败候选清理见 [cleanup_v4.py](examples/cleanup_v4.py) 和 [清理规范](docs/cleanup-v4.md)。以下为原有 v3 生命周期：具名引用和预览示例见 [examples/retention.py](examples/retention.py)。增量发布示例见 [examples/reuse.py](examples/reuse.py)。实际回收示例见 [examples/collection.py](examples/collection.py)。预览不删除文件；显式 collect 才执行退役与删除。候选残留治理示例见 [candidate_cleanup.py](examples/candidate_cleanup.py)。已提供可选 [Polars 接入](docs/integrations.md)、真实 Parquet 生命周期示例与读取清单规模测量；v4 已有历史、数据集、引用和回收日志的独立[规模测量](benchmarks/README.md#v4-历史数据集引用和回收日志)，并披露全仓扫描与完成重试成本；不代表生产容量或共享挂载资格。

## License

Copyright 2026 asterstore contributors. 本项目采用 [Apache License 2.0](LICENSE)，许可证全文来自 [Apache 官方](https://www.apache.org/licenses/LICENSE-2.0.txt)。
