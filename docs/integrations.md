# 引擎接入

核心只返回精确文件路径，读取引擎自行解码、投影与过滤。可选 Polars 安装：`uv sync --locked --extra polars`。

```python
from asterstore.integrations.polars import scan_parquet

binding = repository.open("prices")
query = scan_parquet(binding, keys=["day:2026-09-01"])
frame = query.collect()
```

keys 是逻辑成员键。适配器关闭 glob，默认不解析 Hive 目录；需要时显式传 `hive_partitioning=True`。空选择无法确定数据 schema，抛出 ValueError；不存在的文件由引擎报告。

Binding/LazyFrame 都不会自动保留字节。回收可能并发执行时，先用 governance.retain(..., scope=RetentionScope.OBJECTS) 固定受管发布，通过 governance.open 获取绑定，查询完成后按 revision 释放引用。registered 不支持对象级保留，外部生产者负责自己的字节稳定性。

完整增量发布与延迟查询示例见 [parquet.py](../examples/parquet.py)。


支持范围为 `polars>=1.32,<3`。CI 在 Python 3.11/3.12 上分别验证最低版本
1.32.0、1.x 基线 1.44.2 和 2.0.0，覆盖精确成员选择、显式 Hive、延迟查询与
保留/回收、缺失文件以及适配器无额外 I/O 的边界。主测试与发行安装使用锁定或
依赖解析选定的版本；兼容矩阵单独安装指定版本，不重写锁文件。

适配器直接交给 Polars 解码，不保证不同引擎版本的 schema 或计算语义一致。
例如 [Polars 2.0 的变更](https://github.com/pola-rs/polars/releases/tag/py-2.0.0)
包含 Parquet ENUM 解码为 String；依赖具体 dtype 的调用方应验证自己的数据和查询。
