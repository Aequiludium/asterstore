# 性能验证

当前脚本只针对唯一实现，不沿用旧开发协议的结果或容量结论。

```bash
uv run --locked --extra polars python benchmarks/parquet_read.py --files 1 4 --rows 16 --repeats 2 --selections 10
uv run --locked python benchmarks/managed_membership.py --members 128 1024
uv run --locked python benchmarks/governance_scale.py --history 1 3 --datasets 2 --references 0 2 --logs 0 2 --repeats 1
```

parquet_read 对同一文件集合比较原生 Polars 和 Binding 适配读取，并测量 open、成员选择及 Python 分配。managed_membership 测量候选增量构建与封存。governance_scale 分别改变发布历史、数据集、引用与回收日志数量，报告控制读取、同步、删除、耗时和 Python 内存。

这些命令用于 smoke 验证，不代表生产容量。更大规模使用脚本参数和 --output 保存新结果，并记录存储介质和缓存条件。控制历史会积累；治理预览、GC 和完成态重试可能扫描与同步全仓。普通 current open 的两份控制记录边界不等于治理也是常数成本。

共享挂载、多客户端和真实断电需要独立资格验证。
