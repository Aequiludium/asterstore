# Format 2 API

`Repository(root)` 只捕获路径，不创建目录。`initialize(durable=True)` 只初始化空目录，重复调用检查格式，durable=True 还会同步已有 marker 及其父目录，可补齐弱初始化或上次同步失败的持久性；同步失败继续抛出异常。旧格式和不明既有目录拒绝初始化。

| 方法 | 行为 |
| --- | --- |
| `list_datasets()` | 枚举有 head 的数据集；不是跨数据集快照 |
| `describe(dataset_id)` | 返回不可变 `Head(dataset_id, revision, current, rollback)` |
| `open(dataset_id, version="current")` | 固定 current 或 rollback，返回 Binding；不接受任意历史 ID |
| `prepare(dataset_id, expected_revision=None, durable=True)` | 返回单次 Draft，进入上下文后取得修改锁 |
| `rollback(dataset_id, expected_revision=..., durable=True)` | 条件回退，revision 前进，rollback 置空 |
| `clean(dataset_id, quiescent=True, dry_run=False)` | 维护窗口内删除未引用文件；dry_run 不授权后续删除 |

revision 从 1 开始，首次发布可要求 expected_revision=0。prepare 未指定 revision 时，在获得锁后选择当时的 current；基于先前读取结果计算时应显式传入 Binding.revision。

## Draft

- `version_id`：提交前已知的版本身份；业务文件可以记录它。
- `base_revision`：进入上下文后确认的 head revision。
- `reuse_current()`：在空草稿上调用一次，继承锁内 current 的成员集。
- `allocate(key, relative_path="data.bin")`：返回新文件的私有写入路径。已有 key 被替换时保留成员位置，新 key 追加；未被最终成员引用的中间文件不会安装。
- `remove(key)`：从草稿移除逻辑成员，不删除旧文件。
- `alias(new_key, existing_key)`：增加引用同一文件的逻辑成员。
- `seal()`：要求写入器已关闭；检查声明文件，安装到不可变路径，写一份清单并返回候选 Binding。此时尚未发布。重复 seal 返回同一结果。
- `commit()`：只允许 sealed 状态；原子更新 head，返回 Head。同一存活草稿成功后重复 commit 返回已知结果。

任何 seal/commit 异常都会终止该草稿；提交异常可能发生在 head 替换之后，不能直接解释为未提交。重新 describe，比较已知 version_id；版本已退出两个保留槽位后不承诺恢复历史结果。离开上下文后 Draft 不可再使用；无自动提交或磁盘恢复入口。

本次新文件在 seal 时必须只有一个硬链接（st_nlink=1）；外部输出请复制到 allocate 路径，不能硬链接导入。复用已发布数据使用 reuse_current/alias，它们是清单引用，不增加物理硬链接。

调用方不得在 seal 后继续修改之前取得的文件路径或仍打开的文件描述符。

## Binding 与结果

Binding 提供 `dataset_id`、`version_id`、`manifest`、`revision`、`base_revision`、`files(keys=None)`。files 返回有序 Path 元组，默认全部成员，显式 keys 按请求顺序；未知 key 报 UnknownMemberError。它不 stat、读数据、刷新或注册保护。

候选 Binding.revision=None；正式 Binding.revision 是 open 时观察到的 head revision，包含 rollback 读取。Binding.base_revision 是候选基线或正式绑定观察到的 revision，不是永久版本 generation。

CleanResult 包含 dataset_id、dry_run、scanned_files、candidate_files、candidate_bytes、deleted_files、deleted_bytes、skipped。计数涵盖数据文件、过期清单和 staging 文件，不只数据。没有 head 时跳过；异常不会伪装成完整成功，I/O 异常后允许重新 clean。

## 错误

`AsterStoreError` 为库错误基类；格式不兼容为 UnsupportedFormatError，控制记录损坏为 StoreCorruptionError，revision 冲突为 PublicationConflictError，缺少版本为 PublicationNotFoundError，草稿状态错误为 CandidateStateError。非法调用参数可抛出 ValueError/TypeError；底层读写失败保留 OSError/FileNotFoundError，不吞掉故障。
