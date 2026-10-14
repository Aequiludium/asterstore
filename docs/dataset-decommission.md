> 历史文档：以下描述格式 1，不适用于当前格式 2。当前入口为仓库 README、docs/api.md 和 docs/protocol.md。

# 整个数据集下线（0.3.0 起）

以下接口自 0.3.0 提供，0.2.0 不包含这些功能。

下线属于显式运维治理阶段，用于过期批次、废弃实验或停止服务的数据集。应用负责选择过期的 dataset_id；库不在读取时检查 TTL，不启动后台删除任务。要求仓库已启用 `lifecycle=True`。

```python
result = repo.governance.decommission(
    "expire:2025", datasets={"batch:2025-01": 3, "batch:2025-02": 1}
)
assert result.complete and not result.conflicts
```

映射值是调用方观察到的 generation。首次执行在协调锁内校验整个清单；任一目标不存在、已经下线或代际不匹配，就不开始下线。清单固定保存后，每个数据集分别原子下线，整个批次不保证同时可见。运行中的新客户端写入通过同一 GC 锁协调；无须等待已有 writer 关闭。

`decommission` 永久结束该 dataset_id 的发布生命周期，不能重新启用或复用名称。未来批次使用新的 dataset_id。它不删除数据文件；`preview()`、`collect()` 和 `resume_collection()` 继续承担对象回收。

| 接口或对象 | 下线后的行为 |
| --- | --- |
| `open(dataset)`，含显式 publication_id | 抛 `DatasetDecommissionedError` |
| 新 prepare、register、未提交候选的 commit | 拒绝发布 |
| `describe(dataset)` | 拒绝；使用 dataset_status 查询状态 |
| `describe(dataset, publication_id=...)` | 保留审计声明，包括已回收发布 |
| `list_datasets()` | 默认隐藏；`include_decommissioned=True` 包括下线身份 |
| 已有 Binding、纯内存 bind | 不新增检查、不撤销路径，也不保护文件 |
| 已有对象 retention | 继续保护，允许通过 `governance.open` 读取 |
| 新对象 retention | 拒绝；已有活跃引用的相同请求重试仍允许 |
| metadata retention、release | 继续可用 |
| 未完成候选 | 原保护保留；用 abandon/cleanup 显式结束 |
| 已提交操作重试 | 返回原结果，不恢复在线；已退役发布沿用拒绝规则 |
| registered 外部文件 | 永不因下线或 GC 获得删除权 |

下线阻止其后的新绑定，不能撤销已经开始的读取、交出的写入文件句柄或外部引擎的 LazyFrame。没有对象 retention 的既有消费者可能遭遇后续 GC；需要跨治理操作读取的作业应在下线前建立 retention，并在完成后释放。已有 writer 可以继续暂存字节，但提交失败。

## 过期批次的操作流程

先由应用按业务时间确定 dataset_id。一个 dataset_id 内只过期部分成员时，不应下线整个数据集；应发布新的成员集合，复用保留对象，然后回收旧发布中无保护的对象。

以下代码假设 repo 已初始化，且目标数据集当前在线：

```python
# 名称由业务过期规则决定；generation 来自当前状态观察。
expired = ["batch:2025-01", "batch:2025-02"]
targets = {dataset: repo.governance.dataset_status(dataset).generation for dataset in expired}
result = repo.governance.decommission("expire:2025", datasets=targets)
if result.conflicts or not result.complete:
    raise RuntimeError(f"下线未全部成功，请检查任务状态：{result}")

# 下线任务只修改控制状态；独立决定何时回收。
preview = repo.governance.preview()
print("可退役发布数：", len(preview.retiring))
print("可回收对象数：", len(preview.reclaimable))
collected = repo.governance.collect("gc:after-expire:2025")
print("本次删除对象数：", len(collected.deleted_objects))
```

状态读取与执行之间允许其他发布发生，decommission 会在锁内重新校验 generation，不能把上述观察当作预约。相同任务恢复时使用原 operation_id，不重新生成清单。

**collect 扫描整个仓库，不仅限于刚下线的批次。** preview 也是全仓观察；执行时会重新计算，没有“执行这个 preview”接口。如果暂不希望回收仓库中其他无保护发布，可以先只下线，待统一运维窗口执行 GC。回收结果为空不表示下线失败：对象可能仍被其他发布、retention 或候选保护。

## 状态与恢复

```python
status = repo.governance.dataset_status("batch:2025-01")
# DatasetStatus: dataset_id, state, publication_id, generation, operation_id
progress = repo.governance.decommission_status("expire:2025")
result = repo.governance.resume_decommission("expire:2025")
```

`dataset_status` 只观察 marker/current；`decommission_status` 会做完整控制 inventory，采用非阻塞治理锁，忙时抛 RepositoryBusyError。它不适合作为高频低成本轮询。

`DecommissionResult` 包含 `decommissioned`、`pending`、`conflicts` 三个排序后的 dataset_id 元组。`complete` 只表示没有 pending，**不表示所有目标都成功下线**。恢复时，未处理目标已经推进 generation 或被另一批次下线，会成为 conflicts，保持其现状。复用同 operation_id 但传入不同清单会失败；需要处理冲突时先重新观察，再使用新的 operation_id 明确指定目标。

发生 I/O 错误不代表完全没有生效。检查 decommission_status，并恢复同一固定批次。如果进程在启用 feature 后、保存计划前退出，状态查询会报告计划不存在；以原 operation_id 和原清单重试 decommission。不要删除计划、tombstone 或历史证据来“修复”。

`governance.inspect().decommissioned` 提供下线身份与最后发布的审计快照。未提交候选状态为 `decommissioned`，随后可以变为 `abandoned`；已提交候选仍报告 historical 或 retired。

## 常见结果与处理

| 结果或异常 | 处理 |
| --- | --- |
| 首次请求 PublicationConflictError | 重新观察 generation，确认业务目标后使用明确清单执行；不自动追随新版本 |
| 同 ID 清单不同 | 使用原计划恢复，不能用旧 operation_id 表示另一个批次 |
| 状态含 pending | resume_decommission 恢复原清单 |
| complete=True 且 conflicts 非空 | 任务已分类完成，但不是全部成功；核对冲突数据集后再决定新任务 |
| PublicationNotFoundError（计划） | 核对 ID；首次执行可能停在 marker 与计划之间，按原清单重试 decommission |
| RepositoryBusyError（状态查询） | 稍后重试，库不自动等待 |
| StoreCorruptionError | 停止治理，核查控制证据；不要删除 marker、计划或 tombstone |

## 格式和成本

首次执行启用 `dataset_decommission` 必要功能，保留 `format_version=1`。所有访问仓库的客户端须先升级；旧 0.2.0 客户端会拒绝新 marker，不支持与旧进程混用或删除 feature 降级。

计划保存于 `.asterstore/decommissions/<operation-token>.json`，每个目标保存完整原发布。最后发布先持久化到 history，再将 current 原子替换为 `dataset_tombstone`。治理扫描核对计划、tombstone 和原历史；缺失或不一致时拒绝回收。

稳定状态下默认 current open 仍只读取 marker 和 current，不查询计划、扫描目录或检查数据文件。首次下线恰好发生在两次读取之间时，若读到旧 marker 和新 tombstone，仅此竞态分支额外刷新一次 marker 并核验仓库身份，避免将合法并发误报为损坏。治理操作承担完整 inventory 校验及控制证据同步成本，不扫描或同步 payload。下线后的 current 不再是对象保护根；retention 与候选保护仍生效。计划和 tombstone 保持内联，历史正文可归档；有 pending 下线任务时拒绝 compact，需先恢复或解决其状态。

可执行示例：[decommission_datasets.py](../examples/decommission_datasets.py)。
