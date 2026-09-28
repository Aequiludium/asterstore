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

## 发版后治理诊断基准

`governance_scale.py` 还测量 `governance.inspect()` 和 metadata 级 `governance.check()`。
使用 `--mixed-datasets 32 --mixed-history 100` 可组合数据集数量与每个数据集的发布历史。
脚本校验测量前后的源码指纹；测量期间源码改变会失败，避免将不同实现的结果混在一起。

```bash
uv run --locked --extra polars python benchmarks/governance_scale.py \
  --history 100 1000 --datasets 32 --references 128 --logs 100 \
  --mixed-datasets 32 --mixed-history 100 --repeats 3 \
  --parent /tmp --storage-label '填写实际文件系统和存储介质' \
  --output /tmp/governance-scale.json
```

本次[测量说明](../docs/validation/governance-scale-2026-09-28.md)与[原始报告](../docs/validation/governance-scale-2026-09-28.json)记录真实 API 操作产生的合成控制历史。它不代表完整生产数据、数据体积上限、并发争用或真实断电验收。格式/内容检查的吞吐量也不包含在 metadata 检查计时中。
