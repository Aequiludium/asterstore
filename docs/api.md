# API

核心导出 `Repository`、`Dataset`、`Batch`、`State` 和错误类型。不提供旧 API 或旧协议适配。

## Repository

- `Repository(root)`：指定仓库登记目录，不产生写入。
- `initialize()`：在空目录建立 format 4 登记文件；非空目录拒绝初始化。
- `create_dataset(name, path) -> Dataset`：在指定空目录初始化数据集，登记独占路径。路径不能与其他数据集或仓库控制目录重叠。
- `dataset(name) -> Dataset`：从登记信息打开数据集，不扫描数据文件。

登记锁只用于创建数据集，不参与日常发布、回退和清理。不同仓库不能并发在同一目录树初始化；部署应统一登记目录。

## Dataset

- `root: Path`：正式数据集目录；隐藏控制目录为 `root/.asterstore`。
- `current() -> State`：读取当前元数据，发现活动事务时抛出 `RecoveryRequiredError`。不是快照或读锁。
- `path(relative_path) -> Path`：校验并返回一个数据路径，允许尚未存在的路径。不能把它用于绕过事务直接写入。
- `write(expected_revision=None)`：上下文管理器，持有数据集写锁，返回 `Batch`。未提交退出丢弃暂存；安装开始后异常保留恢复材料。
- `rollback(*, quiescent, expected_revision=None) -> State`：恢复最近一次成功发布之前的数据和业务元数据，操作序号递增，回退槽清空。
- `recover(*, quiescent) -> State`：在维护窗口完成待处理事务；没有活动事务时返回当前状态。可重复调用。
- `clean(*, dry_run=False) -> tuple[str, ...]`：返回失效工作目录的操作 ID；非预览模式删除这些目录。保留当前有效撤销材料；活动事务存在时拒绝。

`quiescent` 必须显式为 `True`，表示调用方已停止新读取、等待全部在途读取结束。此声明不提供自动读者管理。

`expected_revision` 校验请求基于的状态，过期时抛出 `PublicationConflictError`。不传则使用取得写锁时的当前状态。

## Batch

- `stage(relative_path) -> Path`：独占创建一个空暂存文件，返回供调用方写入的路径。每个文件必须是完整替换结果。
- `delete(relative_path)`：声明删除一个已存在的普通文件。
- `commit(*, metadata, quiescent) -> State`：提交变更和 JSON 对象元数据。纯元数据发布支持回退；无文件变更且元数据相同则为 no-op。

重复路径、父子冲突路径、越界和控制路径拒绝。一个 Batch 只提交一次，提交失败也不能重用。相同文件内容不会自动比较或去重；只要 stage 了文件就构成变更。

调用方在提交前完成检查。Batch 退出后返回的暂存路径不再可用。

## State

- `revision`：每次成功发布或主动回退递增，初始为 0。
- `operation`：最近已提交操作的 ID；回退也产生新 ID。
- `publication`：当前数据对应的发布 ID，未发布时为 `None`。
- `metadata`：调用方 JSON 元数据。返回对象的修改不影响磁盘状态。
- `undo`：当前有效撤销材料 ID，无回退机会时为 `None`。

## 错误

`AsterStoreError` 为存储语义错误基类。具体类型有 `StoreCorruptionError`、`UnsupportedFormatError`、`PublicationConflictError`、`RecoveryRequiredError`、`NoRollbackError`。非法调用还可能抛出 `ValueError`、`RuntimeError`；文件缺失、权限、空间和同步故障保留标准 `OSError` 子类。

不能把提交抛出异常简单理解为未提交。维护窗口内调用 recover，依据持久化状态确定最终结果。
