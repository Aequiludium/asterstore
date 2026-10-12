# 治理：直接保留、退役与可恢复回收

状态：v0.1.0 已发布，使用单一格式。当前支持固定发布的 metadata/objects 两种直接保留、候选复用保护、预览、发布退役、固定计划回收和候选显式放弃。已放弃候选的物理清理见 [清理](cleanup.md)。发布依赖图、Release/Pointer、元数据压缩尚未实现。

## 入口和必要功能

```python
from asterstore import Repository, RetentionScope

repo = Repository("./simulation")
repo.initialize(
    store_id="simulation",
    resource_ids=["results"],
    managed_resource_id="results",
    lifecycle=True,
)
# 使用 prepare 生产 run:0，再由任务显式取得保留。
held = repo.governance.retain(
    "task:analysis",
    "heat",
    "run:0",
    scope=RetentionScope.OBJECTS,
)
binding = repo.governance.open(held.name, expected_revision=held.revision)
# 任务使用结束后释放；普通 Binding 本身没有保护作用。
repo.governance.release(held.name, expected_revision=held.revision)
preview = repo.governance.preview()
result = repo.governance.collect("collect:old-runs")
assert repo.governance.resume_collection(result.operation_id) == result
```

lifecycle 在 marker 中表现为 required_features 的必要功能项。允许四个固定组合：registered；registered+managed；registered+lifecycle；registered+managed+lifecycle。仅登记的仓库也可以保留元数据，但没有对象删除能力。

lifecycle 必须在创建仓库时显式开启，之后不能手改 marker 或隐式开启；它要求候选创建与复用过程保存相应的保护记录。

## 固定保留

| scope | 保证 | 不保证 |
| --- | --- | --- |
| METADATA | 目标声明与解释它所需的控制信息被保留 | 文件继续存在、外部字节不变 |
| OBJECTS | managed 发布的声明和成员对象受统一 GC 协调保护 | 已丢失文件的修复、业务质量或外部系统遵守协议 |

retain 必须显式指定 dataset_id、publication_id 和 scope；默认 expected_revision=0 表示新名字。名字终身固定目标及范围，不能用相同名字换绑另一发布。修改通过 revision 条件完成；释放后可以对相同目标重新申请，但已退役的 managed 对象发布不能复活。响应丢失后，相同创建或释放请求可以幂等重试。

两个任务使用两个不同名字，释放自己的名字不会影响其他名字的保护。`get(name)` 返回最新引用记录，包括释放状态；`open(name, expected_revision=...)` 只接受仍激活的 OBJECTS 引用，可以绑定 current_only 的旧发布。它在 GC 协调内校验引用和目标，但返回 Binding 后不会续租；另一个调用方释放该引用也会终止保护。

METADATA 引用通过 get 确认，再通过 Repository.describe 读取声明。它不能用 governance.open 冒充对象保留。registered 声明只能取得 METADATA，OBJECTS 请求明确失败，不自动降级。

引用建立、释放与 GC 共用仓库协调锁。引用检查控制状态，不扫描文件，也不将 durable=True 的新操作解释成旧对象字节已经被重新同步。

## 候选保护

启用 lifecycle 后，每个 managed 候选都有 `protection.json`，初始为空声明。reuse 在共享治理锁内解析来源并核验权属，然后**在返回前**持久保存当前成员保护记录。seal 同样在协调内固定完整声明。

GC 将未提交、未放弃候选的 protection 与 sealed 成员对象并入保护集；候选退出上下文或进程死亡均不解除保护。即使候选的 generation 已冲突，也保守保留，直到调用者显式放弃。保护记录缺失或损坏不能视为空保护，必须阻止回收。

`governance.abandon(operation_id)` 先等待候选写入锁，再取得治理锁，先同步候选现存的请求、封存、保护、固定提交请求及对象权属记录，再持久保存永久放弃事实。原候选使用 durable=False 也不会跳过这些控制证据的同步；尚未写出的可选记录不伪造。已提交或已退役的候选不能放弃。放弃后 resume/commit 明确失败；重复放弃幂等。它只解除未完成候选对复用对象的保护，不删除私有输出或已安装但未提交的新对象。这些残留由 governance.preview_cleanup/cleanup/resume_cleanup 使用独立固定计划处理，见 [清理](cleanup.md)。

## 保留计算与预览

预览遍历全仓权威控制记录，核对标识、对象创建者、操作、候选、引用和退役/回收记录之间的关系。当前没有持久索引或后台增量缓存；工作量随控制历史和成员总量增长。对象使用者映射在本次遍历中建立，回收不会为每个对象重新扫描所有发布。

当前根集合为：

- current：保留声明，managed current 还保护对象。
- 激活的 METADATA 引用：只增加声明根。
- 激活的 OBJECTS 引用：增加声明根和对象根。
- 未完成、未放弃候选：保护其已声明的新对象和复用对象。

同一对象只按 ID 计一次。来源发布可以退役，其对象仍由复用者保护；无需永远保留来源发布处于可打开状态。未知数据文件不是对象记录，不从数据目录扫描推断管理权。

`GovernancePreview` 返回 retiring、reclaimable、protected_objects、retained_metadata。它是观察结果，不是删除授权。collect 总是在锁内重新计算，不能直接执行旧 preview。此阶段仍保存全部历史控制记录，因此未处于声明根中的元数据也可能被额外保留；这不意味着未保留对象的文件也被保护。

## 回收与恢复

```text
验证完整治理控制状态
  → 持久保存原始计划
  → 同步依赖的控制证据
  → 为可退役目标持久保存不可逆退役记录
  → 逐个删除有权属、无保护且所有使用者已退役的普通文件
  → 同步目录并记录进度
  → 记录完成
```

collect 的 operation_id 属于回收操作命名空间；相同 ID 永远对应原计划，不能拿已完成 ID 表示“重新扫描一遍”。计划固定完整发布记录和对象权属记录，恢复不扩大范围。

计划落盘后、退役前中断，新的读者仍可能取得对象保留。恢复重新观察根集合，遇到新保护就跳过相关退役和删除；跳过的对象被记为 protected，此次操作以后不会再次删除它，后续可使用新 operation_id 重新计算。

进度在累计处理 1、2、4、8……个对象及结束时写入，避免逐文件重写完整累计列表造成平方级序列化成本。每个删除仍同步相关目录。文件 unlink 后、进度落盘前中断时，恢复可能记录为 missing，而非 deleted；两者都不会再次作为新 GC 的待回收对象。已经完成的操作在检查本任务计划、进度、退役和对象证据，以及顶层控制布局后，同步仓库标识、计划、完成记录及其父目录，再返回保存的结果，不重新选择删除目标；最后一次替换成功但 fsync 失败也按此恢复。回收不是全批次事务，失败时可能已有部分对象被删除，必须通过原操作恢复。

删除前检查数据根、每层子目录及文件类型；拒绝符号链接和非普通文件。只删除原计划中的确切文件，不递归删除目录，不处理未登记文件，不删除 registered 资源。控制记录损坏、身份冲突、未知治理记录（包括 .asterstore 顶层未知条目）或不完整证据会阻止回收；实际删除前采用全仓阻断，未实现删除故障的局部隔离。完成态重试只重放已有结果，不进行全仓关系审计；完整审计需显式 check。

首次 current 写入可能在创建父目录后中断。若空数据集目录与 generation=0 的固定请求匹配，且没有历史记录或未知条目，治理将其视为可解释的未提交残留；不会因此阻断放弃/清理及其他数据集 GC。有历史却无 current、没有初始请求依据或未知条目的目录仍拒绝。

退役记录存在后，普通历史 open、对象级保留和新 reuse 都拒绝该发布；describe 仍返回原始声明。已在候选中固定且受保护的对象可以继续提交，而不是重新从已退役发布选择对象。旧提交重试也不会复活退役发布。

## 文件与读取成本

新增控制路径：

```text
.asterstore/fixed-retentions/<name-token>.json
.asterstore/retired/<dataset-token>/<publication-token>.json
.asterstore/collections/<operation-token>/plan.json
.asterstore/collections/<operation-token>/progress.json
.asterstore/managed-candidates/<operation-token>/protection.json
.asterstore/managed-candidates/<operation-token>/abandoned.json
.asterstore/managed-candidates/<operation-token>/cleanup-plan.json
.asterstore/managed-candidates/<operation-token>/cleanup-progress.json
```

记录采用严格 UTF-8 JSON，随包提供 Schema 与 golden fixtures。普通当前 open 仍只读取 marker 和 current；不会运行治理遍历。历史打开额外检查目标退役状态，Binding 的成员选择仍没有文件系统 I/O。普通打开不取得保留，不能保证之后不与合法 GC 竞争；有持续读取需求的任务应先建立对象保留。

当前保护来自协作式协议，不是抵抗控制目录被任意删改的隔离机制。测试覆盖本地 POSIX 多进程竞争、进程终止与提交边界异常；不将其当作断电、存储介质损坏或 NFS 多客户端资格。
