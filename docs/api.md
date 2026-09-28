# 当前 API

所有公开类型从 `asterstore` 导入。v0.1.0 已发布；下述诊断与检查接口是主分支的未发布增量，见[格式边界](compatibility.md)。

## 声明与读取

- `Declaration(dataset_id, publication_id, files, capabilities)` 是唯一输入模型。
- `FileSet(objects, members)` 明确对象与逻辑成员映射；`Object(object_id, Locator(resource_id, relative_path))` 定位对象，`Member(key, object_id)` 选择成员。
- `Capabilities.registered()` 声明外部生产者的数据；`Capabilities.managed()` 声明库管理的数据。能力标签本身不授予文件删除权。
- `Repository(root)` 只捕获词法绝对根。`bind(declaration, resources=...)` 捕获资源根和成员路径，不访问文件系统。
- `open(dataset_id, publication_id=None, resources=None)` 返回 `Binding`，其 `publication` 总是 `Declaration`。受管根由仓库固定；registered 必须由调用方绑定外部资源根。
- `list_datasets()` 显式读取已提交 current 清单，返回排序后的 dataset ID 元组；忽略尚无 current 的候选，不扫描数据文件。它不是跨数据集原子快照或完整审计。
- `describe(dataset_id, publication_id=None)` 返回 `DeclarationRecord`；发现元数据不意味着历史字节仍可读取。
- `Binding.files(keys=None)` 返回所有去重路径；显式 keys 按逻辑成员选择并保留请求顺序和重复项。选择复用缓存，不做 stat、扫描、hash、刷新或隐式保留。
- `Binding.capabilities` 总是声明的 `Capabilities`。

普通 current open 只读取 format/current 两份控制记录；数据是否存在及如何解码由文件读取或引擎负责。历史读取还要核对可用性与历史能力。

## 初始化与发布

`initialize(resource_ids=..., store_id=None, managed_resource_id=None, lifecycle=False, durable=True)` 显式创建仓库。同一配置可重试，不允许改变身份、资源或隐式启用功能。

外部登记使用 `register(declaration, operation_id=..., expected_generation=..., durable=True)`；`resume_registration(operation_id)` 恢复固定请求，`registration_status(operation_id)` 查看状态。登记不访问外部数据字节。

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

## 诊断与显式检查（未发布）

- `governance.collection_status(operation_id)` / `cleanup_status(operation_id)` 读取单个任务计划和进度，不扫描全仓；返回 `MaintenanceStatus`，缺少计划抛 `PublicationNotFoundError`。
- `governance.inspect()` 返回 `GovernanceSnapshot`：历史发布、候选状态、保留引用、回收/清理进度和对象保护来源。
- `snapshot.explain_object(object_id)` 只查询内存中的结果。
- `governance.check(level="metadata", resources=None, validator=None, checksums=None)` 返回带范围、时间和问题列表的 `CheckReport`。

详细的只读边界、并发行为和各级检查能力见[诊断与检查](inspection.md)。
