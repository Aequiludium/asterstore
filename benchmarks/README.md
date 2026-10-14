# 双版本容量检查

`uv run python benchmarks/retention_scale.py --publications 30 --members 128`

输出清理前峰值元数据字节、清理后字节和保留清单数。使用临时目录，只修改一个成员并复用其他成员；不访问生产目录。清理后必须只有两份版本清单。

这不是 NFS 性能或断电测试。无缓存服务；结果不能代表任意规模的延迟。
