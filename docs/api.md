# API

核心导出 `Repository`、`Dataset`、`Batch`、`PruneBatch`、`State` 和错误类型。不提供旧 API 或旧协议适配。

## Repository

- `Repository(root)`：指定仓库登记目录，解析入口及父目录软链接为真实路径，不产生写入。
- `initialize()`：在空目录建立 format 4 登记文件；非空目录拒绝初始化。
- `create_dataset(name, path) -> Dataset`：在指定空目录初始化数据集，登记独占路径。入口及父目录软链接先解析为真实路径；真实路径不能与其他数据集或仓库控制目录重叠。
- `dataset(name) -> Dataset`：从登记信息打开数据集，不扫描数据文件。
- `list_datasets() -> tuple[str, ...]`：按字典序返回已登记的数据集名称；空仓库返回空元组，不扫描数据集目录。
- `drop_dataset(name, *, expected_revision=None, quiescent) -> None`：永久删除整个独占目录及注册，包括正式文件、元数据、暂存和撤销材料。不存在时成功；中断后重复调用继续删除，不支持回退。第一次删除前校验 revision，已记录删除意图的续删不再校验。

`list_datasets()` 原样返回注册名称，包括冒号、斜杠和中文。例如：

```python
repo.list_datasets()
# (
#     "factor:barra/size/Size",
#     "factor:basic/capital/Capital",
#     "其他数据集",
# )
```

登记锁用于创建和整体删除数据集，不参与日常发布、回退和清理。整体删除期间持有登记锁；其他登记操作等待，其他数据集的日常读写不受影响。不同仓库不能并发在同一目录树初始化；部署应统一登记目录。

整体删除的 `quiescent=True` 表示调用方已停止该数据集的全部使用，包括读取、尚未 collect 的延迟查询、写入、排队写者、恢复及清理。完成或失败后，重用名称或路径前必须丢弃旧 `Dataset` 及其派生句柄。库不追踪这些使用者，也不给日常读写增加登记查询或额外锁。不要在删除中绕过登记直接构造 `Dataset`。

删除意图持久化后，`list_datasets()` 隐藏该名称，`dataset(name)` 抛出 `RecoveryRequiredError`，提示重试 `drop_dataset()`；创建操作保留其名称和路径占用。未完成的发布、回退或路径维护须先通过 `Dataset.recover()` 恢复，才能开始删除。删除开始后只允许继续删除，`Dataset.recover()` 不负责处理整体删除。

## Dataset

- `root: Path`：正式数据集目录；隐藏控制目录为 `root/.asterstore`。
- `current() -> State`：读取当前元数据，发现活动事务时抛出 `RecoveryRequiredError`。不是快照或读锁。
- `path(relative_path) -> Path`：校验并返回一个数据路径，允许尚未存在的路径。不能把它用于绕过事务直接写入。
- `write(expected_revision=None)`：上下文管理器，持有数据集写锁，返回 `Batch`。未提交退出丢弃暂存；安装开始后异常保留恢复材料。
- `rollback(*, quiescent, expected_revision=None) -> State`：恢复最近一次成功发布之前的数据和业务元数据，操作序号递增，回退槽清空。
- `recover(*, quiescent) -> State`：在维护窗口完成待处理事务；没有活动事务时返回当前状态。可重复调用。
- `clean(*, dry_run=False) -> tuple[str, ...]`：返回失效工作目录的操作 ID；非预览模式删除这些目录。保留当前有效撤销材料；活动事务存在时拒绝。
- `prune(*, expected_revision=None, quiescent) -> Iterator[PruneBatch]`：上下文管理器，在现有 dataset 写锁内校验 revision、读取原 State，并规划永久删除指定文件或目录。调用方停止此 dataset 的全部使用，包括延迟查询、写者和排队任务。退出不自动提交；未 commit 不改变磁盘。

`quiescent` 必须显式为 `True`，表示调用方已停止新读取、等待全部在途读取结束。此声明不提供自动读者管理。

`expected_revision` 校验请求基于的状态，过期时抛出 `PublicationConflictError`。不传则使用取得写锁时的当前状态。

## Batch

- `stage(relative_path) -> Path`：独占创建一个空暂存文件，返回供调用方写入的路径。每个文件必须是完整替换结果。
- `delete(relative_path)`：声明删除一个已存在的普通文件。
- `commit(*, metadata, quiescent) -> State`：提交变更和 JSON 对象元数据。纯元数据发布支持回退；无文件变更且元数据相同则为 no-op。

重复路径、父子冲突路径、越界和控制路径拒绝。一个 Batch 只提交一次，提交失败也不能重用。相同文件内容不会自动比较或去重；只要 stage 了文件就构成变更。

调用方在提交前完成检查。Batch 退出后返回的暂存路径不再可用。

## PruneBatch

```python
with dataset.prune(expected_revision=revision, quiescent=True) as batch:
    # 在锁内校验 batch.before.metadata，选择业务目标并生成一致元数据。
    batch.delete("version=old")
    metadata = {
        **batch.before.metadata,
        "publication_id": batch.id,
        "parent_publication_id": batch.before.publication,
    }
    state = batch.commit(metadata=metadata)
```

- `before: State`：取得写锁并通过 CAS 的原状态，供调用方读取、校验；不要修改其 metadata。
- `id: str`：本次操作的 32 位十六进制 ID，可在提交前用于业务元数据。
- `delete(path: str) -> None`：声明永久删除一个相对业务文件或目录，目录包含全部后代。不存在目标允许；重复、父子重叠、越界和控制路径拒绝。
- `commit(*, metadata: dict) -> State`：删除目标，并清除该 dataset 的**全部旧 work/backup**，包括当前有效 undo 和失效事务材料；不创建新的数据备份。原子提交 `revision+1, operation=id, publication=id, metadata=调用方元数据, undo=None`。其他正式路径及其他 dataset 保留。

空批次且 metadata 与 before 相同为 no-op，保留原 revision 和 undo。空批次修改 metadata 报 `ValueError`；纯元数据变更使用普通 `write()`。非空批次即使所有目标已不存在，也是一笔显式维护提交，会失效 undo、清除旧 work 并推进发布身份。已提交、失败进入执行阶段或退出上下文的批次不可重用。

`PublicationConflictError` 表示取得锁后的 revision 不符；`RecoveryRequiredError` 表示先前活动操作尚未恢复；无效参数／目标冲突为 `ValueError`，关闭批次为 `RuntimeError`；不安全物理路径或控制记录为 `StoreCorruptionError`，底层文件和同步故障仍为 `OSError`。

意图持久化前的校验失败不删除数据；意图一旦可见，操作不可取消，异常后应在维护窗口调用 `dataset.recover(quiescent=True) -> State` **继续完成删除**。恢复无需旧 work/backup 记录，也不重新校验旧 revision。不要把重新创建 prune 批次当作恢复接口；活动意图清除前，普通发布、回退、清理及整 dataset 删除均被拒绝。成功响应后没有撤销机会；后续正常发布会建立新的正常 undo，但不能恢复被 GC 的数据。

此接口从 `0.5.5` 起提供；使用及恢复须升级到 `asterstore>=0.5.5`，正式 `0.5.4` 不包含它。format 4 不变，新 active 变体仅在维护期间存在；旧客户端会拒绝恢复该类型。维护完成后原有客户端可解释存储记录，但调用方业务 metadata 的升级兼容性仍由业务层负责。

## State

- `revision`：每次成功发布、主动回退或非空永久删除维护提交递增，初始为 0。
- `operation`：最近已提交操作的 ID；回退也产生新 ID。
- `publication`：当前数据对应的发布 ID，未发布时为 `None`。
- `metadata`：调用方 JSON 元数据。返回对象的修改不影响磁盘状态。
- `undo`：当前有效撤销材料 ID，无回退机会时为 `None`。

## 错误

`AsterStoreError` 为存储语义错误基类。具体类型有 `StoreCorruptionError`、`UnsupportedFormatError`、`PublicationConflictError`、`RecoveryRequiredError`、`NoRollbackError`。非法调用还可能抛出 `ValueError`、`RuntimeError`；文件缺失、权限、空间和同步故障保留标准 `OSError` 子类。

不能把提交抛出异常简单理解为未提交。维护窗口内调用 recover，依据持久化状态确定最终结果。
