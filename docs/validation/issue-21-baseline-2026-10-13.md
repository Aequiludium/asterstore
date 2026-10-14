# Issue #21 A/B 基线报告 — 2026-10-13

状态：A 契约已完成；B 首轮可复现合成基线已完成。C 可选检查尚未实现。本报告不是性能优化前后对比，也不代表生产容量资格。

[A 阶段契约](../plans/issue-21-contract.md) · [workplan](../plans/issue-21-workplan.md)

## 环境与方法

基线提交 `8cf733cb8208d475df38c3ad4e915f466d68e83f`，asterstore 0.2.0，Python 3.12.12。文件系统 `/dev/sda2 ext4   /`，工作目录 /tmp。底层设备缓存、磁盘介质能力未独立鉴定。

三次正式运行串行执行；期间未并行运行本任务的测试或其他基准。缓存未驱逐，控制记录和数据刚由准备阶段生成，不能称冷缓存。外部系统负载不受控。每场景三次无插桩计时，另用独立仓库测调用计数和 Python 内存峰值，共 50 组。早期重叠运行的结果已被正式串行结果替换，不用于本报告。

对象为 128 字节合成文件，单数据集。准备和数据生产不计时；增量场景计时包括 reuse 保护记录和提交。成员数 16/128/1024，增量场景增加 1 个对象，分批 reuse 每批 16 个。history 表示发布总数；GC durable 标签仅标记准备发布的持久性，GC 本身始终保留现有同步策略。

恢复在安装完成后注入异常，不代替真实进程终止/断电证据。内存是 tracemalloc 峰值而非 RSS；控制写字节为写入尝试量，不是设备实际写量。序列化统计覆盖候选/提交声明和对象编码，不覆盖所有治理 JSON。scandir 为调用计数，其返回迭代器的完整耗时不计入该函数计时。

插桩计数来自额外一轮，含补丁开销；锁持有、fsync、编码等计时有包含关系，不能相加，也不能除以另一轮无插桩耗时算占比。候选写锁在准备阶段取得时不计入操作范围；恢复时取得的锁计入。未主动制造锁争用，锁等待结果不能证明并发扩展性。

## 强持久发布结果

单位 ms 为三次无插桩中位数；读取字节与 fsync 来自独立插桩执行。

| 对象基数 | 场景 | 中位 ms | 控制读取字节 | fsync 次数 | Python 峰值 MiB |
| --- | --- | --- | --- | --- | --- |
| 16 | new | 61.1 | 508 | 122 | 0.04 |
| 16 | incremental | 29.4 | 32733 | 69 | 0.05 |
| 16 | reuse_all | 11.2 | 32733 | 60 | 0.05 |
| 16 | reuse_batches | 11.1 | 32733 | 60 | 0.05 |
| 128 | new | 153.8 | 508 | 682 | 0.24 |
| 128 | incremental | 49.5 | 249649 | 181 | 0.38 |
| 128 | reuse_all | 47.7 | 249649 | 172 | 0.38 |
| 128 | reuse_batches | 95.9 | 683894 | 193 | 0.41 |
| 1024 | new | 1409.8 | 508 | 5162 | 2.57 |
| 1024 | incremental | 364.6 | 1992633 | 1077 | 2.94 |
| 1024 | reuse_all | 586.4 | 1992633 | 1068 | 2.94 |
| 1024 | reuse_batches | 2986.4 | 33114822 | 1257 | 2.95 |

## 历史增长与完成重试

同为 16 对象/发布、强持久准备。完成重试处理的是原固定任务，任务本身也随回收对象数增长，并非常数工作量。

| 发布数 | 场景 | 中位 ms | 控制读取字节 | 删除调用数 |
| --- | --- | --- | --- | --- |
| 8 | gc_first | 77.8 | 144478 | 112 |
| 8 | gc_retry | 27.7 | 212899 | 0 |
| 32 | gc_first | 485.6 | 577684 | 496 |
| 32 | gc_retry | 195.2 | 941332 | 0 |

## 结论与实施优先级

1. 首次提交中候选检查执行两次，sealed/installed 恢复执行一次；证明检查与同步拆分有明确测量点，尚不能据此删除第二次检查或同步。
2. 1024 对象分批复用的控制读取约 33.1 MB，一次复用约 1.99 MB。先关注复用保护记录反复物化及身份记录读取，不应把全部耗时归咎于 current JSON。该结论是本合成工作负载的计数证据，不是普遍复杂度证明。
3. 首次大写的 fsync 调用数量显著，关闭可选健康检查不会消除持久化成本。durable=False 的复用仍可能同步保护记录，不能将其解释为全流程无 fsync。
4. 完成 GC 重试的删除调用为零，但仍读取固定任务证据；不能宣传为与任务大小无关或首次 GC 已优化。
5. 个别更轻场景耗时不严格单调（例如 1024 对象 incremental 比 reuse_all 快），表明计时受缓存/设备/负载波动影响；本轮不设硬性毫秒预算或推断这些差异有因果意义。
6. C 优先保证默认读取 I/O 不增加、检查与同步职责可分测、恢复/残留治理闭环；完成 C 后按同脚本参数复测，才评价收益与回退。

## 覆盖与剩余工作

已覆盖：新写、增量、一次/分批复用、空发布、sealed/installed 恢复、weak→strong、首次 GC、完成 GC 重试，以及两种发布持久性。原始结果保留全部单次耗时、控制读取/写入、编码、扫描/stat、fsync、锁等待/持有和内存测量。

未覆盖：尚未实现的 none/existence/format/checksum 档位比较、真实业务文件体积、几十个真实数据集、冷缓存、竞争负载、真实断电。当前检查字段明确标为 legacy mandatory verification；这些不伪造为 B 已有证据。现有 governance_scale.py 可继续扩展数据集/引用/历史轴，生产容量测量需独立真实负载。

## 原始报告与复现

- [issue-21-baseline-2026-10-13.json](issue-21-baseline-2026-10-13.json)，UTC 2026-10-13T02:43:23.445161+00:00 至 2026-10-13T02:44:36.350243+00:00。
- [issue-21-scale-2026-10-13.json](issue-21-scale-2026-10-13.json)，UTC 2026-10-13T02:44:36.592258+00:00 至 2026-10-13T02:46:34.243586+00:00。
- [issue-21-history-2026-10-13.json](issue-21-history-2026-10-13.json)，UTC 2026-10-13T02:46:34.460639+00:00 至 2026-10-13T02:47:04.531515+00:00。

```bash
uv run --locked python benchmarks/publication_cost.py --members 16 128 --history 8 --repeats 3 --parent /tmp --output docs/validation/issue-21-baseline-2026-10-13.json
uv run --locked python benchmarks/publication_cost.py --members 1024 --history 8 --repeats 3 --cases new incremental reuse_all reuse_batches --parent /tmp --output docs/validation/issue-21-scale-2026-10-13.json
uv run --locked python benchmarks/publication_cost.py --members 16 --history 32 --repeats 3 --cases gc_first gc_retry --parent /tmp --output docs/validation/issue-21-history-2026-10-13.json
```

源码和脚本 SHA-256 在每份 JSON 中，已核对与 B 阶段测量时文件一致；C 改动后的工作区不再是该基线。原脚本已保存为 issue-21-publication-cost-v02.py.txt，运行时代码由报告中的基线 SHA 恢复。正式运行区间无重叠。报告不包含任何尚未实现的速度提升承诺。
