# 候选放弃、残留诊断与清理

当前已实现，协议属于尚未冻结的 v3 开发格式。操作作用于显式指定的候选 ID，不按年龄、mtime 或“当前没有引用”推断可以删除。普通绑定和读取不调用这些操作。

## 使用流程

```python
report = repository.inspection.candidates()
# 应用或运维确认该候选不再需要，且全部原生写入器、后台任务已停止。
repository.abandon(candidate_id)
result = repository.retention.cleanup_candidate(candidate_id)
if result.status == "blocked":
    issues = result.issues
    # 处理具体问题后，用同一个 ID 重试原计划。
```

可运行例子见 [candidate_cleanup.py](../examples/candidate_cleanup.py)。诊断报告不是删除授权列表，cleanup 每次在锁内核对持久状态。

## 放弃的语义

`Repository.abandon(candidate_id)` 返回 `CandidateStatus`，state 为 `abandoned`。支持未封存、已封存、generation 冲突、安装对象后尚未提交的候选。它先获取仓库独占锁，等待所有活跃写入上下文结束，再检查全仓控制证据。已提交的候选（包括已经退役者）报 `CandidateStateError`，必须通过发布退役流程治理。

放弃原子替换该候选的 state.json 并强制同步；不删除文件、不探测数据、不读取或同步数据内容。它永久禁止该候选恢复或提交，并使其共享成员不再成为保留根。新建的 `resume()` 上下文在进入时抛出 `CandidateAbandonedError`，异常包含 candidate_id。重复 abandon 补齐控制同步并返回 abandoned；观察到的 current_generation 可以随仓库进展变化。

放弃不能撤销。候选 ID 和原始声明保留；一个从未提交的 publication_id 不因此被标记为已经发布，其他新候选仍可使用这个未提交身份。已提交身份的不可重用约定保持不变。发生响应或同步错误后，状态可能已经可见，应通过 candidate_status 或重试 abandon 确认，不依据异常推断没有生效。

放弃与普通 GC 都使用仓库独占锁；GC 在删除刚解除保护的共享对象前仍会同步 abandoned 控制记录，防止删除依据未持久化。不要在本任务仍持有 prepare/resume 上下文时调用 abandon、cleanup 或 inspection，否则会等待自身的共享锁。

## 固定范围的清理

`retention.cleanup_candidate(candidate_id)` 只接受已经 abandoned 且控制证据完整的候选，强制持久化。第一次调用显式扫描该候选自己的两个位置：

- `.asterstore/candidates/<id>/files/`：暂存数据。
- `.asterstore/objects/<id>/`：已安装但尚未发布的数据。

清理计划只包含其中的普通文件，包括原生写入器在私有目录中留下、尚未来得及登记或封存的临时输出。来源发布的共享文件属于其他创建候选，不进入此计划。目录所有权以有效候选控制记录为前提；没有记录的未知位置只报告，不自动认领。

扫描先检查父目录，不跟随符号链接。不支持的文件类型、非法键、两个位置同时存在等情况返回 blocked，创建计划前不删除任何文件。正常情况下先持久化不可变 cleanup-plan.json，再逐文件删除并同步父目录；空目录、state.json、锁文件和清理日志保留。不会执行递归删除。

重试仍调用相同方法和 candidate_id。plan 存在而进度不存在，表示计划写入后尚未确认完成；恢复检查全部原计划路径，不相信进度偏移。unlink 后进程终止，已经不存在的无使用者文件可以记为 missing。完成记录写入后重试返回原结果，不继续扫描或删除后来出现的文件；诊断会报告计划外文件和完成后重新出现的文件。

清理计划一经建立就不自动扩大，也没有“重新规划这个候选”的接口。计划建立后的外部新增文件需要另行调查。尚未完成的候选清理不会阻止正常发布或普通 GC，它们处理不同的对象集合；损坏的控制记录会阻止治理操作。

`CandidateCleanupResult` 字段：candidate_id、status（complete/blocked）、planned、deleted_files、missing_files、remaining_files、issues。文件值为仓库根相对键。planned 表示本次已读取或创建该候选的计划；控制损坏时结果不代表完整文件清单。确认删除和发现已不存在分别报告；缺失不被伪装为本次实际删除。

未 abandon 报 `CandidateStateError`；不存在的候选保留底层 `FileNotFoundError`；非法 ID 报 `InvalidDeclarationError`。建立计划前的 I/O 错误直接抛出；计划建立后可记录的文件处理错误返回 blocked；若进度本身也无法写入则继续抛出底层错误。失败后以同一 ID 查询诊断或重试，不另建一个更大范围的计划。

## 显式诊断

`repository.inspection` 的构造没有 I/O。`inspection.candidates()` 持仓库独占锁取得稳定观察，读取控制元数据，并扫描未提交/已放弃候选的私有目录。已提交和已退役候选不是这份残留列表的成员，不在这里遍历其正式数据内容。

`CandidateInspection` 包含 status、candidates、unknown_locations 和 issues。每个 `CandidateResidual` 包含 candidate_id、dataset_id、publication_id、state、sealed、locations、files、total_bytes、shared_objects、protects_shared 和 cleanup_state。cleanup_state 为 not_started/pending/complete。文件列表和字节数来自显式 lstat，不读取数据内容、schema 或 hash；total_bytes 是观察到的普通文件长度之和，不代表磁盘分配量，blocked 时可能只是部分统计。

writing 表示未封存，不表示某个进程仍在运行；prepared 表示当前基准下仍可提交，不保证文件内容健康；conflict 不等于可自动丢弃。shared_objects 是原封存记录中的来源对象；protects_shared 在放弃后为 False。未知对象目录、损坏控制记录、符号链接等会进入 issues，返回 blocked，同时保留能确认的候选事实。

这是候选残留诊断，不是全仓内容审计。未知对象位置的自动删除、完整孤儿归属修复、控制日志压缩和空目录回收仍未实现。

## 持久格式与兼容

abandon 将 state.json 原子替换为 `abandoned_candidate`，其中嵌套原始 CandidateManifest。不能仅添加一个旧写入器会忽略的 sidecar 标记，否则旧恢复过程可能重新提交已释放的共享对象。v3 marker 使旧 v1/v2 写入器在修改前拒绝；混合版本治理/恢复不受支持。v1/v2 保留读取兼容，不自动迁移。

清理计划和进度存放在同一候选控制目录。plan 的 kind 是 candidate_cleanup_plan，含 candidate_id 和完整私有文件 keys；progress 的 kind 是 candidate_cleanup_progress，含 candidate_id、complete、deleted、missing 和 issues。两者严格核对路径、版本、字段和成员范围。协议样例：

- [abandoned-candidate.json](../tests/fixtures/protocol/v3/abandoned-candidate.json)
- [cleanup-plan.json](../tests/fixtures/protocol/v3/cleanup-plan.json)
- [cleanup-progress.json](../tests/fixtures/protocol/v3/cleanup-progress.json)

放弃记录、原始清单和完成日志都保留。验证覆盖本地 POSIX 进程竞争、响应丢失、同步异常和 SIGKILL，不构成断电或 NFS 多客户端资格验证。
