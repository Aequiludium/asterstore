# AsterStore

AsterStore 管理普通文件的**当前版本和上一个安全版本**。每个数据集独立发布、回退和清理，核心无第三方运行依赖，不解释业务数据格式。

当前开发版本为 **0.4.0，磁盘格式 2**。这是不兼容的架构替换：不读取或迁移格式 1，不提供永久历史、retention、跨数据集文件共享或持久事务恢复。已有生产目录不会自动转换。

## 快速开始

```python
from asterstore import Repository

repo = Repository("/path/to/new-empty-store")
repo.initialize()

with repo.prepare("prices", expected_revision=0) as draft:
    path = draft.allocate("day:2026-10-14", relative_path="date=2026-10-14/data.bin")
    path.write_bytes(b"validated data")
    candidate = draft.seal()
    # 在这里检查 candidate.files()；失败就抛出异常，不提交。
    head = draft.commit()

binding = repo.open("prices")
assert binding.files()[0].read_bytes() == b"validated data"

with repo.prepare("prices", expected_revision=head.revision) as draft:
    draft.reuse_current()
    draft.allocate("day:2026-10-15").write_bytes(b"next day")
    draft.seal()
    head = draft.commit()

repo.rollback("prices", expected_revision=head.revision)
# 调用方已停止新读写任务，并等待全部旧读任务（含延迟计算）结束。
repo.clean("prices", quiescent=True)
```

## 保证与边界

- current 与 rollback 在同一份 head 中原子切换。发布成功后，旧 current 成为 rollback。
- 每个版本使用一份完整、不可变清单。文件只在同一数据集内部复用，已发布文件禁止原地修改。
- 读取固定一次版本；Binding.files 不产生 I/O。发布期间已有读者可以继续使用旧文件。
- 同一数据集的准备、发布、回退、清理使用修改锁；不同数据集独立。
- seal 后才可 commit；上下文退出不自动提交。失败允许重跑，无跨进程 resume。
- 回退后 rollback 为空，故障版本不成为安全回退版；下一次正常发布重建双版本。
- 清理只能在调用方协调的无读写窗口执行。清理后不要再使用旧 Binding、Dataset 或 LazyFrame。
- 清理后最多两份正式清单。两次清理之间旧版本和失败遗留仍占空间；需安排清理周期。
- 默认 durable=True；弱持久化不保证机器故障后提交仍存在。清理先同步保留状态再删除。
- 依赖协作式 POSIX 文件锁和同文件系统原子替换，不防御其他进程恶意改写仓库。回退不是备份。

可选 Polars 适配器：`pip install 'asterstore[polars]'`，使用 `asterstore.integrations.polars.scan_parquet(binding, keys=...)`。普通成员可以是 JSON、Parquet 或其他文件，不需要插件声明其删除语义。

## 文档与验证

- [API](docs/api.md)、[磁盘协议](docs/protocol.md)、[设计边界](docs/design.md)
- [不兼容变化](docs/migration-v2.md)、[发布与回退](docs/publishing.md)、[清理](docs/cleanup.md)
- [双版本示例](examples/two_versions.py)、[Parquet 示例](examples/parquet.py)
- [规模验证](benchmarks/README.md)、[发布构建](docs/releasing.md)

```bash
uv sync --locked --extra polars
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python tools/check_protocol.py
uv run python tools/check_distribution.py --polars
```

旧格式专属测试和示例已随协议替换退出当前套件，旧实现保存在 Git 历史。新的测试覆盖并发写入、进程退出、提交响应不确定、回退、清理中断、路径边界和重复发布后的容量上界。
