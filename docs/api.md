# 当前 API

常用公开类型从 `asterstore` 导入，诊断返回类型从 `asterstore.governance.inspection` 导入。基础诊断与检查接口自 0.2.0 提供。本文同时描述当前源码的待发布接口；读写 `check` 参数、`include_decommissioned`、候选盘点、compact 和 decommission 不在 PyPI 0.2.0 中，见[格式边界](compatibility.md)。

## 声明与读取

- `Declaration(dataset_id, publication_id, files, capabilities)` 是唯一输入模型。
- `FileSet(objects, members)` 明确对象与逻辑成员映射；`Object(object_id, Locator(resource_id, relative_path))` 定位对象，`Member(key, object_id)` 选择成员。
- `Capabilities.registered()` 声明外部生产者的数据；`Capabilities.managed()` 声明库管理的数据。能力标签本身不授予文件删除权。
- `Repository(root)` 只捕获词法绝对根。`bind(declaration, resources=...)` 捕获资源根和成员路径，不访问文件系统。
- `open(dataset_id, publication_id=None, resources=None)` 返回 `Binding`，其 `publication` 总是 `Declaration`。受管根由仓库固定；registered 必须由调用方绑定外部资源根。
- `list_datasets(include_decommissioned=False)` 显式读取在线 current 清单，返回排序后的 dataset ID 元组；忽略尚无 current 的候选，不扫描数据文件。它不是跨数据集原子快照或完整审计。
- `describe(dataset_id, publication_id=None)` 返回 `DeclarationRecord`；发现元数据不意味着历史字节仍可读取。
- `Binding.files(keys=None)` 返回所有去重路径；显式 keys 按逻辑成员选择并保留请求顺序和重复项。选择复用缓存，不做 stat、扫描、hash、刷新或隐式保留。
- `Binding.capabilities` 总是声明的 `Capabilities`。

默认 `check="none"` 时，稳定态 current open 只读取 format/current 两份控制记录；数据是否存在及如何解码由文件读取或引擎负责。历史读取还要核对可用性与历史能力。

## 初始化与发布

`initialize(resource_ids=..., store_id=None, managed_resource_id=None, lifecycle=False, durable=True)` 显式创建仓库。同一配置可重试，不允许改变身份、资源或隐式启用功能。

外部登记使用 `register(declaration, operation_id=..., expected_generation=..., durable=True)`；`resume_registration(operation_id)` 恢复固定请求，`registration_status(operation_id)` 查看状态。默认登记不访问外部数据字节；显式 `check` 可检查由 resources 定位的外部文件。

受管发布使用 `prepare(dataset_id, publication_id=..., operation_id=..., expected_generation=..., history=HistoryAccess.CURRENT_ONLY, durable=True)`，返回上下文管理的 `Candidate`：

- `path(key, relative_path=...)` 声明一个逻辑成员，取得供原生引擎写入的私有路径。
- `write_bytes(key, data, relative_path=...)` 声明并写入新文件。
- `alias(key, member=...)` 为已有成员增加别名。
- `reuse(publication_id=None, keys=None)` 复用同数据集已提交对象；不复制、读取或重新同步共享字节。
- `seal()` 固定完整声明；`commit()` 原子发布，返回 `DeclarationRecord`。

退出上下文不会自动提交。`resume(operation_id)` 只恢复已封存工作；`candidate_status(operation_id)` 显式查询状态。operation_id 和 generation 固定请求；冲突不自动变基，重试不回退 current。状态查询不能代替固定保留。返回的 CandidateStatus 包含 operation_id、state，以及持久化请求中的 store_id、dataset_id、publication_id、expected_generation；放弃后仍可查询归属。

## 保留、回收和失败候选

唯一治理入口是 `repository.governance`，构造无 I/O；操作要求初始化时启用 lifecycle。

- `retain(name, dataset_id, publication_id, scope=...)` 创建具名固定保留；`RetentionScope.METADATA` 保留声明，`OBJECTS` 同时保护受管字节。
- `open(name, expected_revision=..., resources=None)` 打开固定引用；`release(name, expected_revision=...)` 条件释放。
- `list_retentions(active_only=False)` 显式列举固定引用，按名称排序；返回目标、scope、revision 与 active 状态。默认包括已释放记录，active_only=True 只返回活动引用；不访问数据字节。
- `preview()` 显式全仓治理扫描；`collect(operation_id)` 保存固定计划、退役并回收；`resume_collection(operation_id)` 恢复原计划。
- `abandon(operation_id)` 永久放弃未提交候选；`preview_cleanup(operation_id)` 预览残留，`cleanup(operation_id)` 清理其拥有的文件，`resume_cleanup(operation_id)` 恢复固定清单。

registered 不能取得 OBJECTS 保留或数据删除权。普通 Binding 和 LazyFrame 都不会保护字节；并发回收期间的消费者须显式保留。未知文件不会被当作孤儿自动清除。

## 错误与引擎

错误继承 `AsterStoreError`；声明错误为 `InvalidDeclarationError`，冲突为 `PublicationConflictError` / `ReferenceConflictError`，损坏或不支持的格式为 `StoreCorruptionError`。`CandidateAbandonedError` 和 `PublicationRetiredError` 暴露 `operation_id`。文件系统和引擎错误可以原样传出。

`asterstore.integrations.polars.scan_parquet(binding, keys=None, hive_partitioning=False)` 只把精确文件路径交给 Polars，禁用 glob，无额外治理 I/O。详见[引擎接入](integrations.md)。

## 诊断与显式检查（0.2.0 起）

- `governance.collection_status(operation_id)` / `cleanup_status(operation_id)` 读取单个任务计划和进度，不扫描全仓；返回 `MaintenanceStatus`，缺少计划抛 `PublicationNotFoundError`。
- `governance.inspect()` 返回 `GovernanceSnapshot`：历史发布、候选状态、保留引用、回收/清理进度和对象保护来源。
- `snapshot.explain_object(object_id)` 只查询内存中的结果。
- `governance.check(level="metadata", resources=None, validator=None, checksums=None)` 返回带范围、时间和问题列表的 `CheckReport`。

详细的只读边界、并发行为和各级检查能力见[诊断与检查](inspection.md)。

诊断接口 `inspect`、`check`、`collection_status`、`cleanup_status` 无法取得稳定观察时，统一抛 `RepositoryBusyError(AsterStoreError)`，不自动等待；调用方稍后重试。诊断返回类型从 `asterstore.governance.inspection` 导入，详情见[诊断与检查](inspection.md)。

`check()` 将检查过程中捕获的非忙 `AsterStoreError` / `OSError` 汇总到报告；`inspect()` 和任务状态查询直接抛出遇到的错误。参数校验错误仍抛出，详见[错误契约](inspection.md#错误契约)。


## 可选读写检查（未发布）

`Repository.open`、`register`、`resume_registration` 和 `Candidate.commit` 增加 keyword-only `check="none"`、`validator`、`checksums`。registered 写检查另传 `resources`。返回类型不变；describe/bind/Binding.files 不增加检查。参数和错误状态见[可选检查](optional-checks.md)。`DataCheckError` 可从包根或 `asterstore.errors` 导入。

`governance.inspect_candidate(operation_id)` 返回 `CandidateInspection`，类型从 `asterstore.governance.inspection` 导入。提供候选状态、位置、声明/观察文件、缺失项、未知条目和类型/I/O 问题；未封存时 declaration_known=False，不把未持久化成员误认成已知声明。接口非阻塞，冲突抛 RepositoryBusyError，不写锁文件、不删除数据。

## 控制历史压缩（待发布）

`repo.governance.compact(operation_id, *, quiescent: bool) -> CompactionResult`：显式维护窗口内归档终态控制正文。同 ID 恢复已固定的 checkpoint；无收益、未完成任务或损坏证据会拒绝。结果类型从 asterstore.governance 导入。执行启用旧版本不支持的 control_archive；详细契约见 [控制历史压缩](control-compaction.md)。

## 数据集下线（待发布）

`governance.decommission(operation_id, datasets={dataset_id: expected_generation})` 固定批次并逐个永久下线；不删除数据。`resume_decommission` 恢复原清单，`decommission_status` 返回已下线、待处理和冲突目标，`dataset_status` 查询单数据集最后发布与状态。`DatasetStatus` 和 `DecommissionResult` 从 `asterstore.governance` 导入。`complete` 仍须结合 `conflicts` 判断成功；读写和保留边界见 [整个数据集下线](dataset-decommission.md)。


| 治理入口 | 返回与成本 |
| --- | --- |
| `decommission(operation_id, *, datasets: Mapping[str, int])` | `DecommissionResult`；全仓控制盘点与同步，固定批次后逐数据集替换 current |
| `resume_decommission(operation_id)` | 恢复原计划，不扩展目标；仍做完整控制关系验证 |
| `decommission_status(operation_id)` | `DecommissionResult`；非阻塞协调并做完整 inventory，**不是**单计划的低成本轮询 |
| `dataset_status(dataset_id)` | `DatasetStatus`；读取 marker/current，不做完整审计，也不保护对象 |

`DatasetStatus` 字段为 dataset_id、state（active/decommissioned）、publication_id、generation、operation_id；在线数据集的 operation_id 为 None。`DecommissionResult` 提供 operation_id、decommissioned、pending、conflicts；后三项为排序后的数据集 ID 元组，complete 等于没有 pending。没有计划时状态查询/恢复抛 PublicationNotFoundError。

下线后普通 open（含显式历史 open）与新发布抛 `DatasetDecommissionedError`，带 dataset_id 和下线 operation_id；显式历史 describe 仍可审计。`list_datasets(include_decommissioned=True)` 可发现已下线身份，`inspect().decommissioned` 返回 tombstone 元组。已有对象 retention 的消费者仍可使用 governance.open；返回路径本身不会续租或撤销。
