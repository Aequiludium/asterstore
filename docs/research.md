# 开源实现学习记录

研究日期：2026-09-24。

本文记录已阅读的关键源码路径和设计文档，说明它们如何影响 `asterstore`。这不是对参考项目的完整审计或性能评测，也不是直接采用这些库作为依赖的决定。

## 阅读范围

| 项目 | 本轮重点 | 版本依据 |
| --- | --- | --- |
| Kartothek | 元数据缓存、分区写入、元数据提交、垃圾回收 | `1821ea5df60d4079d3911b3c2f17be11d8780e22` |
| Apache object_store | 本地文件读写、暂存发布、条件写入与同步 | `279572ea60f3a7a6e5237e066a6f4a35eee611e0` |
| delta-rs | 提交冲突、Python 表句柄与读取、vacuum | `6ac4ce0a7a7627bcf257dae1546204e543ed573f` |
| Lance | manifest 提交、数据集加载、标签与回收 | `9dc7c69c32cb090535de681e8a57fc84ccac22df` |
| PyStore | collection、item、snapshot 与 transaction | 研究日读取的默认分支；链接未固定 commit |
| ArcticDB | 引用、版本、索引和数据的组织方式 | 官方存储设计文档；未进行对应源码审计 |
| Aster | 读取成本边界、提交、显式检查与 pin/GC | 本地 HEAD `405019f27c2356d01180d5a5ee742bacde11310f` 的相关路径 |

前四个项目的代表源码与上述 commit 内容作过一致性核对。以下结论只适用于实际阅读的实现范围；默认分支和在线文档后续可能变化。

## Kartothek：绑定缓存与最终声明提交

`DatasetFactory` 的 `_instantiate_metadata_cache()` 只在缓存为空时加载声明，`invalidate()` 显式清空缓存。复用 factory 可以避免重复绑定开销。

`write_single_partition()` 写分区而不更新 dataset header；`store_dataset_from_partitions()` 汇总分区、索引和元数据，最后写 JSON 或 msgpack 声明。大文件生产与对外声明更新分别进行。

回收路径根据元数据确定引用的文件，从列举的对象中减去这些文件，再删除候选。阅读到的这条路径本身没有提供活跃写入保护，因此不能把这个简单差集直接复制为并发 GC 协议。

对 `asterstore` 的启发：

- 可复用绑定应成为正常入口，刷新必须显式。
- 暂存与发布分开，让失败留下可识别残留，而非半份公开声明。
- 采用清单思想时，同时设计提交和回收协调；原子写一个 header 不自动解决并发丢更新。

来源：[factory.py](https://github.com/JDASoftwareGroup/kartothek/blob/1821ea5df60d4079d3911b3c2f17be11d8780e22/kartothek/core/factory.py)、[write.py](https://github.com/JDASoftwareGroup/kartothek/blob/1821ea5df60d4079d3911b3c2f17be11d8780e22/kartothek/io_components/write.py)、[eager.py](https://github.com/JDASoftwareGroup/kartothek/blob/1821ea5df60d4079d3911b3c2f17be11d8780e22/kartothek/io/eager.py)、[gc.py](https://github.com/JDASoftwareGroup/kartothek/blob/1821ea5df60d4079d3911b3c2f17be11d8780e22/kartothek/io_components/gc.py)。

## object_store / obstore：接口统一不代表保证相同

阅读版本的 `LocalFileSystem::put_opts()` 先写暂存文件，再根据模式以 rename 或 hard link 发布。`PutMode::Update` 在这个本地实现中明确返回不支持。不能仅凭接口上存在条件更新参数，就假设本地后端提供 compare-and-swap。

`with_fsync()` 单独控制文件及相关目录的同步，默认关闭。实现把关闭文件错误也纳入处理；可见性、持久性与错误反馈分别考虑。

`get_opts()` 打开文件并读取文件元数据，处理请求前置条件。由此也能看出，“不逐次做完整内容审计”与“绝对不获取任何文件元数据”不是同一个目标。

obstore 提供本地对象存储的 Python 接口。研究日的 `LocalStore` 构造参数并未展示 Rust 的 `with_fsync` 配置，不能将 Rust 当前源码的全部能力直接归于 Python 包。

对 `asterstore` 的启发：

- 文件操作可以借鉴或复用成熟实现，但发布、引用和 GC 仍需要上层管理语义。
- 明确后端支持的能力以及所依赖的文件系统前提。
- 为本地引擎保留直接路径，是否使用对象存储接口由具体接入需要决定。
- 独立定义持久性策略；不因为参考项目默认关闭 fsync 就直接照搬。

来源：[local.rs](https://github.com/apache/arrow-rs-object-store/blob/279572ea60f3a7a6e5237e066a6f4a35eee611e0/src/local.rs)、[obstore LocalStore](https://developmentseed.org/obstore/latest/api/store/local/)。

## delta-rs：提交端的协调与独立运维

事务提交路径比较当前读取版本与最新版本；存在中间提交时执行冲突检查，并处理提交版本已存在的竞争。这些协调位于写入提交路径。

Python 的 `DeltaTable` 提供显式 `update_incremental()`。其 PyArrow dataset 构建路径使用表状态、schema 和文件信息生成 fragments，展示了“管理元数据决定文件集合，再交给引擎读取”的接入方式。该观察不构成对 Delta 所有读取路径零额外检查的承诺。

Python `vacuum()` 在阅读版本中默认 `dry_run=True`，Rust builder 的默认值不同。清理保留期限用于降低仍被读者或写者使用的文件遭删除的风险，但固定期限不是所有任务的自动保护机制。

对 `asterstore` 的启发：

- 将必要的一致性成本放在发布时，不能把“读得轻”误解为“提交时不需要协调”。
- 初版可以用锁与 generation 检查实现可理解的冲突失败，不立即引入完整的乐观冲突合并。
- 运维默认值由我们的公开接口明确规定，不能依赖多层封装中的隐含默认值。
- 历史读取的有效性依赖实际保留的文件，保存版本号并不足够。

来源：[transaction/mod.rs](https://github.com/delta-io/delta-rs/blob/6ac4ce0a7a7627bcf257dae1546204e543ed573f/crates/core/src/kernel/transaction/mod.rs)、[Python table.py](https://github.com/delta-io/delta-rs/blob/6ac4ce0a7a7627bcf257dae1546204e543ed573f/python/deltalake/table.py)、[vacuum.rs](https://github.com/delta-io/delta-rs/blob/6ac4ce0a7a7627bcf257dae1546204e543ed573f/crates/core/src/operations/vacuum.rs)。

## Lance：引用、孤立对象与可解释清理

`RenameCommitHandler` 先写临时 manifest，再用目标不存在时的原子重命名提交；目标已存在会成为提交冲突。这项能力取决于后端是否支持相应操作。

清理实现保护当前及更新的 manifest 和带标签的版本。代码明确讨论：没有被 manifest 引用的文件，可能是失败残留，也可能属于正在进行的写入。年龄阈值和 `delete_unverified` 都带有使用前提。

`CleanupExplanation` 明确表示预览只是解释；执行重新评估数据集和引用状态。它不是可以延迟执行的无条件删除列表。

对 `asterstore` 的启发：

- 具名保留引用应是实际 GC 根，并能说明对象为什么被保留。
- 候选写入必须纳入协调；未经引用的文件不能直接等同于垃圾。
- 普通绑定不自动意味着保留，长任务需要显式安排保留。
- 预览与执行之间允许状态变化，执行必须重新检查有效基准。

来源：[commit.rs](https://github.com/lance-format/lance/blob/9dc7c69c32cb090535de681e8a57fc84ccac22df/rust/lance-table/src/io/commit.rs)、[cleanup.rs](https://github.com/lance-format/lance/blob/9dc7c69c32cb090535de681e8a57fc84ccac22df/rust/lance/src/dataset/cleanup.rs)、[builder.rs](https://github.com/lance-format/lance/blob/9dc7c69c32cb090535de681e8a57fc84ccac22df/rust/lance/src/dataset/builder.rs)。

## PyStore：检查功能名称背后的实现成本

阅读到的 `create_snapshot()` 用 `shutil.copytree()` 复制 collection 目录。事务路径包含备份复制、顺序执行操作和异常回滚；这些机制本身不能证明崩溃原子性。collection 的元数据缓存还使用时间期限，与我们希望的显式绑定/刷新契约不同。

对 `asterstore` 的启发是保留易理解的目录、集合和数据对象操作方式，同时单独审查 snapshot、transaction、cache 等名称所代表的保证。避免将整目录复制作为默认发布机制，也不把进程内异常回滚描述成断电恢复。

来源：[collection.py](https://github.com/ranaroussi/pystore/blob/HEAD/pystore/collection.py)、[transactions.py](https://github.com/ranaroussi/pystore/blob/HEAD/pystore/transactions.py)、[item.py](https://github.com/ranaroussi/pystore/blob/HEAD/pystore/item.py)。这些链接跟随仓库默认分支。

## ArcticDB：轻量追加会产生后续维护成本

官方存储设计区分引用、版本、索引和数据层。可变引用指向版本历史；不同版本可以复用不变数据。文档同时解释，历史链很长会增加小对象读取，频繁追加会形成数据碎片。

对 `asterstore` 的启发是让发布声明与数据文件独立，并复用不变对象；同时测量清单大小、历史定位与文件碎片的成本。引用分层值得学习，专有数据格式及整个查询引擎不属于当前目标。

来源：[ArcticDB On-Disk Storage](https://docs.arcticdb.io/latest/technical/on_disk_storage/)。这是设计文档阅读所得，没有据此宣称实测性能。

## Aster：保留哲学，解除领域依赖

Aster 的工程约定要求连接时解析一次元数据，普通读取不轮询发布、不逐文件预检。`aster-data` 的读取路径构造精确文件路径，并把完整检查留给显式操作。

因子仓库的提交在锁内检查 generation，安装数据与发布记录后再推进 current。GC 在仓库协调范围中计算 current、pins、公开引用和因子依赖。现有 pin 是共享标记，公开句柄也明确不自动构成保留或物理快照。

这些原则与新库一致，但当前实现的 owner、因子定义版本、交易日历和公开字段映射包含领域语义。抽取时需要换成核心库自己的身份、对象成员与引用模型。

多框架共用时，具名引用比共享布尔 pin 更容易解释：每个使用者管理自己的名字，释放自己的引用不会解除另一个名字的保留作用。名字归属不自动提供权限隔离。

来源：[工程约定](https://github.com/Aequiludium/aster/blob/405019f27c2356d01180d5a5ee742bacde11310f/docs/engineering.md)、[原始数据读取](https://github.com/Aequiludium/aster/blob/405019f27c2356d01180d5a5ee742bacde11310f/aster-data/src/aster_data/reading/storage.py)、[因子提交](https://github.com/Aequiludium/aster/blob/405019f27c2356d01180d5a5ee742bacde11310f/aster-factor/src/aster_factor/storage/repository.py)、[公开句柄与 pin](https://github.com/Aequiludium/aster/blob/405019f27c2356d01180d5a5ee742bacde11310f/aster-factor/src/aster_factor/catalog/store.py)、[GC](https://github.com/Aequiludium/aster/blob/405019f27c2356d01180d5a5ee742bacde11310f/aster-factor/src/aster_factor/storage/gc.py)。

## 已转化的设计决定

| 学习结果 | asterstore 中的落点 |
| --- | --- |
| 元数据缓存需要明确生命周期 | 一次绑定与显式刷新；失败保留旧绑定 |
| 数据写入与宣布可见可以分离 | 候选写入、稳定发布身份、明确的 current 切换点 |
| 读取快不要求放弃写入协调 | 每数据集提交协调和基准版本检查 |
| 引用不等于物理快照 | 分开定义发布绑定、物理历史与保留 |
| 预览结果可能过时 | 执行 GC 时在协调下重新计算 |
| 未引用对象可能仍在写入 | 发布准备过程加入 GC 协调 |
| 多个消费者需要独立表达保留 | 具名持久引用，普通读取不自动写 pin |
| 库无法知道全部外部消费者 | 同仓协调域与显式外部引用登记边界 |
| 文件操作的保证依赖环境 | 可见性、持久性、竞争和 NFS 资格分别说明 |
| 元数据与碎片也会拖慢读取 | 分开测量绑定、定位、引擎读取和维护成本 |

这些决定进入 [设计文档](design.md) 和 [验收计划](roadmap.md)。尚未通过实现与测量验证的部分继续以拟议方案表述。


## 2026-09-25：从实现参考转为治理规范

新增 [治理规范](spec/README.md)，将身份、定位、管理权、字节可变性、历史、保留和持久性分别表达。核心治理记录使用 JSON，不要求使用方统一其业务 JSON/pickle；旧业务控制记录中的治理事实由适配器显式提取。

本轮另核对 [Iceberg 的引用、保留与元数据提交规范](https://iceberg.apache.org/spec/#snapshot-references)、[object_store PutMode](https://docs.rs/object_store/latest/object_store/enum.PutMode.html) 及 [LocalFileSystem](https://docs.rs/object_store/latest/object_store/local/struct.LocalFileSystem.html)。参考的是职责与保证的分离，不采用其整套表格式或推导未测试的后端能力。具体采用范围见 [规范来源](spec/metadata.md#7-参考来源与采用范围)。此前固定 commit 的源码结论没有在本轮全部重验。
