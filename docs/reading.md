# 固定版本读取

```python
binding = repo.open("prices")
paths = binding.files(keys=["data"])
```

open 读取当前 head 和所选版本清单，返回固定 Binding。之后的发布不会刷新这个对象。`version="rollback"` 可读取回退槽位；槽位不存在时抛出 PublicationNotFoundError。

files 返回 Path 元组，省略 keys 时按清单顺序返回所有成员，指定 keys 时按请求顺序返回。未知成员抛出 UnknownMemberError。选择路径不会 stat 文件、读取内容或登记读者。

如果成员集包含数据和 JSON 元数据，读取引擎应显式选择数据成员，避免把元数据文件交给 Parquet 扫描器。

## Polars

安装 polars 可选依赖后：

```python
from asterstore.integrations.polars import scan_parquet

binding = repo.open("prices")
frame = scan_parquet(binding, keys=["data"]).collect()
```

适配器使用明确文件列表并关闭 glob；`hive_partitioning=True` 可开启 Hive 分区解析。空文件选择无法推断 schema，会抛出 ValueError。列投影、过滤和聚合直接使用 Polars。

完整可运行示例见 [parquet.py](../examples/parquet.py)。

## 读取寿命

Binding 和 LazyFrame 都不会自动保护文件。普通发布可以与它们并行；清理前必须等读取结束，包括 collect 或其他延迟操作中的最后一次 I/O。

维护窗口结束后重新 open，不再使用窗口前的 Binding 或延迟查询。跨数据集分别 open 可以得到各自的固定版本，但不构成跨数据集的一致快照。
