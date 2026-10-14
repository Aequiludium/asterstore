# API 参考

公共类型从 `asterstore` 导入。数据集 ID 和成员 key 为非空可打印字符串，UTF-8 长度不超过 4096 字节。文件相对路径还须满足[协议](protocol.md)中的路径约束。

## Repository

`Repository(root)` 接受字符串或 Path，只解析根路径，不创建仓库。

| 调用 | 返回与行为 |
| --- | --- |
| `initialize(durable=True)` | 首次在空目录初始化；已有仓库检查标记，启用 durable 时补齐标记和父目录同步 |
| `list_datasets()` | 返回具有 head 的数据集 ID 元组 |
| `describe(dataset_id)` | 返回 Head |
| `open(dataset_id, version="current")` | 返回 current 或 rollback 的固定 Binding |
| `prepare(dataset_id, expected_revision=None, durable=True)` | 创建单次 Draft，进入上下文时取得修改锁 |
| `rollback(dataset_id, expected_revision=..., durable=True)` | 条件回退，返回 revision 增加且 rollback 为空的 Head |
| `clean(dataset_id, quiescent=..., dry_run=False)` | 在调用方保证的维护窗口内执行或预览清理，返回 CleanResult |

head revision 从 1 开始。首次发布可要求 expected_revision=0；已有状态参与计算时，传入读取时的 revision。prepare 省略 revision 时使用取得锁后的状态。

open 只选择两个槽位，不接受任意版本 ID。读取 rollback 时返回的 revision 也是本次观察到的 head revision。

## Draft

Draft 必须通过上下文管理器使用。进入后可编辑，seal 后可检查候选并 commit。退出上下文后不可再用，也不自动提交。

| 成员 | 含义 |
| --- | --- |
| `version_id` | 库生成的版本 ID，提交前已确定 |
| `base_revision` | 进入上下文后确认的基线 revision，首次发布为 0 |
| `reuse_current()` | 在空草稿上调用一次，继承 current 的全部成员 |
| `allocate(key, relative_path="data.bin")` | 返回新文件的私有写入 Path；同名 key 替换，新 key 追加 |
| `remove(key)` | 从草稿成员集中移除 key，不删除已发布文件 |
| `alias(new_key, existing_key)` | 添加一个引用已有成员文件的不同 key |
| `seal()` | 固定完整成员清单，返回候选 Binding，不发布 |
| `commit()` | 要求已封存；原子切换 head，返回 Head |

allocate 不写入文件内容。seal 前必须写完并关闭文件，新文件必须只有一个硬链接。未被最终成员引用的临时文件不会安装。封存文件之后不得修改。

sealed 状态下重复 seal 返回同一 Binding；同一存活 Draft 成功 commit 后重复调用返回已知 Head。seal 或 commit 抛出异常后 Draft 失效。提交异常可能发生在 head 切换之后，处理方式见[发布与回退](publishing.md)。

## 结果类型

**Head**：dataset_id、revision、current、rollback。rollback 为版本 ID 或 None。

**Manifest**：dataset_id、version_id、members。members 是有序 Member 元组，每个 Member 包含 key 和相对对象路径 path。

**Binding**：root、dataset_id、version_id、manifest、revision、base_revision，以及 `files(keys=None)`。

- files 返回 Path 元组，默认按清单顺序，显式 keys 按请求顺序；字符串本身不能代替 key 序列。
- files 不访问磁盘、不刷新版本、不登记文件保护。
- 候选 Binding 的 revision 为 None，base_revision 为 Draft 基线。
- 正式 Binding 的 revision 和 base_revision 都是 open 时观察到的 head revision，不代表该版本最初发布的 revision。

**CleanResult**：dataset_id、dry_run、scanned_files、candidate_files、candidate_bytes、deleted_files、deleted_bytes、skipped。统计包含对象、清单和 staging 文件；没有 head 时跳过。实际清理失败会抛出异常，而不是返回完整成功。

## 异常

| 类型 | 含义 |
| --- | --- |
| AsterStoreError | 库异常基类 |
| RepositoryNotInitializedError | 缺少仓库标记 |
| UnsupportedFormatError | 格式标记不受当前客户端支持 |
| StoreCorruptionError | 持久记录或目录状态不符合约束 |
| PublicationNotFoundError | 数据集或请求的版本槽位不存在 |
| PublicationConflictError | revision 与预期不一致 |
| CandidateStateError | Draft 已关闭、失败或处于错误阶段 |
| UnknownMemberError | 成员 key 不存在 |

非法参数也可抛出 ValueError 或 TypeError。底层 I/O 异常保留 OSError 等原始类型；调用方不能把所有异常统一解释为未发布。

## 可选 Polars 适配器

`asterstore.integrations.polars.scan_parquet(binding, keys=None, hive_partitioning=False)` 返回 LazyFrame。它使用固定文件列表，空选择抛出 ValueError，详细寿命约定见[读取](reading.md)。
