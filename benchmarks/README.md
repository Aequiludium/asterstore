# 性能验证

当前脚本只针对唯一实现，不沿用旧开发协议的结果或容量结论。

```bash
uv run --locked --extra polars python benchmarks/parquet_read.py --files 1 4 --rows 16 --repeats 2 --selections 10
uv run --locked python benchmarks/managed_membership.py --members 128 1024
uv run --locked python benchmarks/governance_scale.py --history 1 3 --datasets 2 --references 0 2 --logs 0 2 --repeats 1
```

parquet_read 对同一文件集合比较原生 Polars 和 Binding 适配读取，并测量 open、成员选择及 Python 分配。managed_membership 测量候选增量构建与封存。governance_scale 分别改变发布历史、数据集、引用与回收日志数量，报告控制读取、同步、删除、耗时和 Python 内存。

这些命令用于 smoke 验证，不代表生产容量。更大规模使用脚本参数和 --output 保存新结果，并记录存储介质和缓存条件。控制历史会积累；治理预览与实际 GC 仍扫描全仓，删除前同步全仓控制证据；已完成任务重试只检查任务范围并同步完成记录。普通 current open 的两份控制记录边界不等于治理也是常数成本。

共享挂载、多客户端和真实断电需要独立资格验证。

## 发版后治理诊断基准

`governance_scale.py` 还测量 `governance.inspect()`、metadata 级 `governance.check()` 和单任务 `collection_status()`。
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

后续[优化复测](../docs/validation/governance-scale-2026-09-28-optimized.md)及[原始报告](../docs/validation/governance-scale-2026-09-28-optimized.json)保留相同场景下的改善、首次 GC 未改善结果与内存代价。

## Issue #21 发布与恢复分项基线

```bash
uv run --locked python benchmarks/publication_cost.py \
  --members 16 128 1024 --history 8 --repeats 3 --parent /tmp \
  --output /tmp/issue21-publication.json
```

该脚本测量当前实现，可通过 --check none/existence/format/checksum 测量显式提交检查。B 阶段的 0.2.0 原脚本已存档在 docs/validation/issue-21-publication-cost-v02.py.txt，内容 SHA-256 与 B 原始报告一致；复现旧行为需在基线源码环境将快照恢复为原脚本路径。包含首次发布、少量新增并复用、多批 reuse、空发布、sealed/installed 恢复、弱持久到强持久恢复、首次 GC 和完成任务重试。GC 的 durable 标签仅代表准备数据时的发布方式，GC 自身继续采用原有同步策略。

每个耗时样本、调用计数与 tracemalloc 测量都使用独立准备的仓库；数据生产与准备阶段不计入操作时间。恢复使用安装完成后的异常注入，不替代进程终止或断电测试。Python 级计数不是系统调用/设备 I/O 计数；锁内时间和其他分项来自插桩执行，可能大于另一轮无插桩总耗时，禁止相加或相除推导占比。内存为 Python 分配峰值，不是 RSS。

运行基准时不要并发运行其他基准或测试；缓存不驱逐，不声称冷缓存结果。对比报告必须核对源码指纹、文件系统、规模、持久性和历史数。--check 只作用于被测提交/恢复，准备阶段默认不开可选检查，GC 场景不适用此参数。format 使用明确的合成文件校验器，checksum 使用生产前已知字节摘要；不代表真实 Parquet 校验吞吐。

## Issue #21 D1：只读控制历史成本归因

停止目标仓库所有写入进程（包括候选构建）和治理修改后，显式确认静止条件：

```bash
uv run --locked python benchmarks/control_history.py /path/to/repository --quiescent > /tmp/control-history.json
uv run --locked python benchmarks/control_history_sample.py > /tmp/control-history-samples.json
```

第一条命令只读现有 lifecycle 仓库，报告写到标准输出；重定向应放在目标仓库之外。
脚本取得已有 gc.lock，不创建锁文件；该锁不能阻止锁外候选构建，所以 `--quiescent` 是调用方声明，不是自动停写功能。忙锁、损坏控制证据会失败。

报告统计经过既有 inventory 验证的权威记录，不读取数据字节，不盘点数据目录、未知文件或临时文件。它给出各控制目录的文件数/字节、逐记录摘要、完全相同的可研究重复组和暂缓迁移原因。任何未完成发布/候选/GC/cleanup 都保守保留全仓控制证据；current、marker、对象身份和 retention 记录固定留在原处。

重复字节是选定记录之间按原始字节去重的理论上限，不是可删除字节或净空间收益。归档索引、引用与 checkpoint 开销尚未定义，净节省和文件数净减少明确为 null；现有记录今天仍全部必需。第二条命令仅在临时仓库构造六种样本，保存工具与运行时源码摘要，不修改指定业务仓库。详细结果见 [D1 报告](../docs/validation/issue-21-d1-2026-10-13.md)。

## Issue #21 D：实际压缩复测

`uv run --locked python benchmarks/control_compaction.py > /tmp/control-compaction.json`

脚本在临时仓库准备四组历史，测量压缩前后实际控制文件长度、文件数、压缩耗时及 current/历史 describe/inspect 中位延迟，并验证旧声明、压缩重试和 GC 重试。包括 checkpoint 和 pack 开销；不计数据文件、锁和目录分配。缓存不驱逐，顺序测量不保证消除缓存与负载影响。结果见 [D 报告](../docs/validation/issue-21-d-2026-10-13.md)；保留了小样本历史查询和治理变慢的结果。
