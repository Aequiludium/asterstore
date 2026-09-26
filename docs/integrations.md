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
