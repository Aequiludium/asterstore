# 诊断与显式检查（未发布）

这些接口属于主分支的未发布增量，PyPI 0.1.0 尚不包含。使用现有 store 格式，不新增控制记录，也不自动运行。与其他治理操作一样，要求仓库初始化时显式启用 `lifecycle=True`。

## 治理状态

```python
snapshot = repo.governance.inspect()
for candidate in snapshot.candidates:
    print(candidate.operation_id, candidate.state)
for task in snapshot.maintenance:
    print(task.kind, task.operation_id, task.processed_objects, task.complete)
explanation = snapshot.explain_object(object_id)
for reason in explanation.reasons:
    print(reason.kind, reason.reference_name, reason.operation_id)
```

快照包含 current/historical/retired 发布、候选、包括已释放记录的引用、已有固定计划的回收/清理任务及已登记对象。候选状态沿用 `candidate_status()` 的判定；`writing` 表示未封存，不能据此判断进程是否存活。未封存任务不能自动恢复写入。

对象原因分为 current、具名 OBJECTS 引用、未提交候选使用和需要单独候选清理的创建者。METADATA 引用不会保护字节。registered 对象没有普通 GC 删除权。快照中的 `reclaimable` 仅表示当次观察符合现有普通 GC 判定，不能作为绕过固定计划的删除凭据。

`PublicationStatus.objects_protected` 表示该发布本身是对象保护根；共享对象也可能被其他发布保护。对象级解释提供实际原因。私有候选文件尚无 ObjectRecord 时不进入对象清单。`explain_object()` 对未知对象抛 KeyError，查询本身无 I/O。

## 检查范围

```python
report = repo.governance.check()  # metadata
report = repo.governance.check(level="existence")
report = repo.governance.check(level="checksum", checksums={object_id: trusted_sha256})
```

| level | 实际检查 |
| --- | --- |
| metadata | 独立控制记录解码，再检查发布、权属、保留、退役与固定计划的关系；不访问数据文件 |
| existence | 追加检查全部未退役已提交发布的对象，按 object_id 去重；检查文件及受管父目录的类型 |
| format | 在 existence 基础上调用显式提供的 `validator(path)`，正常返回表示通过，异常记为 format_error |
| checksum | 在 existence 基础上流式计算 SHA-256，与调用方提供的可信基准比较；缺少基准不会标记通过 |

format 与 checksum 是独立检查方式。核心不自带格式解码器；例如可传入一个调用 `polars.read_parquet_schema(path)` 的函数，但这只检查 Parquet 元数据，不证明所有行都可解码。业务质量和完整解码由调用方明确实现。

检查只读，不写入基准，不修复或删除。registered 的物理路径必须通过 resources 显式提供；不能把某次外部可变数据检查的成功当成永久保证。符号链接数据文件不作为普通文件通过检查。已退役数据、未提交候选文件、未知数据文件不属于数据检查范围；检查不会通过扫目录猜测垃圾。

`CheckReport` 记录请求级别、开始/结束 UTC 时间、是否协调、关系检查是否完成、目标对象、成功完成该级别的对象及问题。多个独立坏控制记录或缺失文件会分别报告；独立记录无误后再检查关系，关系层遇到首个错误停止，数据层不在元数据不可信时继续。它不是自动修复或穷尽所有关联错误的工具。

## 并发与成本

两个接口只打开已有 gc.lock，以非阻塞排他锁稳定控制状态，并阻止受管 GC 在数据检查期间删除对象。忙时 inspect 抛 BlockingIOError，check 返回 busy 问题；调用方决定重试时机。已初始化但从未写入的空仓库可返回 `coordinated=False`，不为查询创建锁；检测到首次写入竞态则报告忙。

排他锁贯穿显式检查，长时间格式或内容检查会延迟发布与 GC，应在维护窗口运行。validator 应是纯读取函数，禁止重入本仓库的变更 API，否则可能等待自己持有的锁。检查不防御绕过库直接改文件，也不冻结 registered 数据。

快照返回后不继续保护对象，不是跨后续操作的事务或保留引用。现有普通 open / Binding.files 不调用这些接口。诊断主动读取全仓控制记录，成本随历史增长；不要放进每次业务读取。
