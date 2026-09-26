# 当前开发 API

本文描述当前已实现版本及其阶段约定。[治理规范草案 0.1](spec/README.md) 定义后续目标；逻辑身份与路径分离已在 v3 实现，v4 已接入 registered 登记，新模型的受管发布已接入，直接保留已接入，依赖图仍是后续目标。

状态：`0.1.0rc1`。已实现声明、内存/磁盘绑定、受管发布、候选恢复、对象复用、具名保留引用、回收预览、发布退役、可恢复物理回收、候选放弃、残留诊断及清理。公开 API 和磁盘协议以 [0.1 冻结决议](compatibility.md) 为准；完整审计尚未实现；v4 已提供外部声明持久登记。

原有 prepare(Dataset) 流程创建 v3 仓库；显式 initialize 创建 v4 仓库并指定 managed/lifecycle 必要功能，见 [v4 协议](protocol-v4.md)。v1/v2 保留读取兼容，治理写入、回收预览和残留诊断报 `RepositoryUpgradeRequiredError`。v2 已有固定引用仍可 get/open。没有自动迁移或升级命令。精确兼容矩阵见 [v3 协议](protocol-v3.md)。

## 新声明模型

新增 `Declaration`、`FileSet`、`Member`、`Object`、`Locator` 和 `Capabilities`，以及 `Management`、`ByteStability`、`HistoryAccess`、`RetentionScope`。详细规则和示例见[声明与资源绑定](declarations.md)。

`Repository.bind(declaration, resources={...})` 返回 `Binding[Declaration]`；files 的 keys 是逻辑成员键。bind 本身不写控制目录、不登记或取得保留；持久登记通过 v4 register 显式执行，暂不接入 v3 发布和 GC。`UnknownMemberError` 表示成员不存在，`ResourceNotBoundError` 表示缺少资源绑定，`UnsupportedCapabilityError` 表示请求的治理保证不被声明能力支持。

v4 推荐入口及 v3 兼容边界见[首版范围](release-scope.md#2-api-与协议边界)。下文标明旧流程的接口继续可用，不把 `PhysicalHistory` 自动转换成新能力。

## v4 持久登记入口

- `initialize(resource_ids=..., store_id=None, managed_resource_id=None, lifecycle=False, durable=True)`：显式初始化 v4 Store，不保存资源根路径。
- `register(declaration, operation_id=..., expected_generation=..., durable=True)`：固定 registered 请求并原子发布声明，返回 DeclarationRecord。
- `describe(dataset_id, publication_id=None)`：读取已提交声明，返回 generation 与原操作身份。
- `open(dataset_id, publication_id=None, resources=...)`：使用显式资源根返回 Binding[Declaration]。
- `registration_status(operation_id)`：查询 planned / committed / conflict。
- `resume_registration(operation_id, durable=True)`：按持久操作恢复或返回已提交结果。

仅声明元数据由库管理，外部文件不被写入、同步、复制或删除。registered 不取得对象保留；启用 lifecycle 后可通过 `governance` 保留元数据，managed 对象可显式保留与回收。详见 [v4 协议](protocol-v4.md)。

## v3 兼容入口

以下旧模型及其关联入口继续由 `asterstore` 提供；新应用优先使用本文的 v4 入口：

| 名称 | 当前职责 |
| --- | --- |
| `Dataset(dataset_id, physical_history=PhysicalHistory.CURRENT_ONLY)` | 数据集身份及历史能力；受管数据集首次发布后不能改变能力 |
| `PhysicalHistory` | `CURRENT_ONLY` 或 `VERSIONED` |
| `ObjectRef(key)` | 仓库根目录下的文件键；构造时检查词法结构 |
| `Publication(dataset, publication_id, objects)` | 显式成员声明；复制输入序列并拒绝重复对象键 |
| `Reference(name, dataset_id, publication_id)` | 具名引用值对象；构造本身不写持久记录 |
| `Repository(root)` | 捕获根目录的绝对路径；不创建或扫描目录 |
| `Repository.bind(publication)` | 零文件 I/O 地绑定调用者提供的声明 |
| `Repository.open(dataset_id, *, publication_id=None)` | 从磁盘加载当前或指定发布，返回独立 `Binding` |
| `Repository.prepare(dataset, *, publication_id=None, expected_generation=None, durable=True)` | 创建候选上下文，进入时初始化仓库并记录基准 generation |
| `Repository.resume(candidate_id, *, durable=True)` | 创建恢复上下文，进入时读取已封存候选，保留原来的 generation |
| `Repository.abandon(candidate_id)` | 永久放弃未提交候选并强制同步，不删除文件 |
| `Repository.inspection` | 无 I/O 地取得显式候选残留诊断服务 |
| `Repository.candidate_status(candidate_id)` | 协调下查询元数据状态，返回 `CandidateStatus` |
| `Repository.retention` | 无 I/O 地取得具名引用和回收预览服务 |
| `Binding.files(keys=None)` | 返回全部路径，或按对象键顺序选择路径 |
| `Candidate` | 由 `prepare()` / `resume()` 返回的单次上下文对象 |
| `CandidateStatus` | 候选身份、发布身份、状态及观察到的当前 generation |

逻辑身份（dataset_id、publication_id、引用 name）是非空 UTF-8 文本，拒绝控制字符；允许冒号、斜杠等，不做大小写折叠或 Unicode 归一化。内存声明与格式无关，v3 持久记录额外限制每个身份不超过 4096 个 UTF-8 字节；prepare 在任何 I/O 前检查。对象键继续使用非空、可打印的规范相对路径：拒绝空段、`.`、`..`、反斜杠和冒号。根目录由调用者指定。候选 ID 是生成的 32 位小写十六进制 UUID。

`Publication` 允许空成员集合。声明本身不证明文件存在、内容完整或已经持久化。`Reference` 不表示已经获得保留保护。

## 受管发布

```python
from asterstore import Dataset, Repository

repository = Repository("./data")
with repository.prepare(Dataset("market/prices"), publication_id="batch-001") as candidate:
    candidate.write_bytes("day=2026-09-01/data.csv", b"price\n12.5\n")
    publication = candidate.commit()

binding = Repository("./data").open("market/prices")
paths = binding.files()  # 交给原生读取引擎
```

`prepare()` 本身不执行 I/O；进入上下文才创建仓库和暂存目录。`publication_id=None` 自动生成 ID。默认捕获进入上下文时的 current generation，也可以传入明确的非负整数；初始 generation 为 0。提交时发现基准已改变会抛出 `PublicationConflictError`，不会自动合并或重置基准。

| 候选接口 | 行为 |
| --- | --- |
| `candidate_id` | 进入前即可获得，供持久记录和恢复使用 |
| `publication_id` / `expected_generation` | 活跃上下文中的发布身份和基准 |
| `path(key)` | 注册逻辑输出键并返回暂存路径，供 Parquet 等原生写入器使用 |
| `write_bytes(key, data)` | 写入少量字节的便利接口 |
| `copy_file(key, source)` | 复制外部文件进入暂存；不与可变来源建立硬链接 |
| `reuse(publication_id=None, *, keys=None)` | 从当前或可访问历史发布选取受管对象，返回本次加入的 ObjectRef 元组 |
| `reuse_reference(name, *, expected_revision, keys=None)` | 从对应 revision 的 active 引用选择成员，返回 ObjectRef 元组 |
| `seal()` | 固定新文件及复用成员；只检查/同步新增文件，持久化恢复状态 |
| `commit()` | 自动封存、安装对象并原子发布，返回 `Publication`；同一候选可重试 |

原生写入器必须在 `seal()` / `commit()` 前关闭文件并停止修改。封存不会解码数据、检查业务 schema 或计算内容 hash；协议依赖生产者遵守写入约定。退出上下文只释放锁，不隐式提交或删除。

每个候选拥有独立对象目录。逻辑键 `part.csv` 在提交后成为 `.asterstore/objects/<candidate_id>/part.csv`；`Binding.files(keys=...)` 接收的是 `publication.objects` 中的完整对象键。暂存路径在提交安装后失效，应从返回的发布建立绑定，或重新 `open()` 获取正式路径。

当前发布声明完整成员集合，由新文件和显式复用的对象组成。一个候选内不允许同时将 `a` 和 `a/b` 声明为文件。已封存候选禁止新增成员，候选上下文不能重复进入。

## 增量发布与对象复用

```python
first = repository.open("market/prices").publication
unchanged_keys = [obj.key for obj in first.objects]  # 应用自行选择确实未变化的分区
with repository.prepare(first.dataset) as candidate:
    candidate.reuse(first.publication_id, keys=unchanged_keys)
    candidate.write_bytes("new-day/data.csv", b"price\n13.0\n")
    second = candidate.commit()
```

来源必须由本仓库同一数据集的已提交控制记录解析；不能直接把外部路径、调用者构造的 Publication 或 Binding 作为受管来源。省略 publication_id 选择调用时的 current；显式 ID 必须仍可访问，current_only 的旧发布只能通过有效的具名引用选择。引用必须仍为 active 且 revision 完全一致。数据集能力不匹配、跨数据集引用均拒绝。

keys 接收完整受管对象键。None 选择该来源全部成员，空序列不加入成员；未知键或重复对象键报错，失败不会部分加入本次成员。重复键检查覆盖此前已经加入的复用对象。最终成员顺序为新文件注册顺序，然后是复用对象加入顺序。

复用直接保留原物理路径，不复制、不创建数据硬链接、不检查共享文件存在性，也不读取内容、计算 hash 或同步共享数据。库不按文件名猜测分区替换：写入同名逻辑键并不会移除旧对象；调用者应只选择未变化的对象。完整例子见 [reuse.py](../examples/reuse.py)。

来源发布 ID 记录成员出处，不产生对来源整个发布的永久保留。活跃候选的仓库共享锁阻止 GC；封存之后由持久成员关系保护共享对象。已提交候选残留不额外保护对象，保护来自 current、具名引用或其他保留发布。封存前退出或进程死亡的候选不能恢复。

共享对象继承源发布原有的持久性保证。本候选 durable=True 只同步新增文件及控制记录，不升级 durable=False 来源的字节保证。要获得全部成员的强持久性，来源也应使用 durable=True 发布。显式 resume 在提交前检查封存共享文件的存在性和普通文件类型，不重新选择来源，也不要求原引用仍活跃；generation 冲突仍照常处理。

## 读取与历史

`open()` 读取控制元数据并检查格式、结构和身份；打开当前发布只读取仓库格式标记和该数据集的 current 记录。它不锁住读者，不探测数据文件，不检查内容，也不写保留记录。

`bind()` 连控制文件也不读取，适合绑定已有声明或应用自行维护的外部文件声明。外部声明绑定不等于已持久登记。

`files()` 复用内存中的路径和成员集合，没有文件 I/O。缺失文件在实际读取时自然报错。选择未声明的键会抛出 `UnknownObjectError`；空选择返回空元组，重复键按请求保留重复位置。

显式刷新就是再次调用 `open()` 获取新绑定；失败和成功都不会修改原来的绑定。普通读者不自动轮询 current。

`CURRENT_ONLY` 是默认能力，允许打开当前发布；显式打开已经成为历史的发布会抛出 `HistoryUnavailableError`。`VERSIONED` 支持按发布 ID 打开历史声明。两者的旧绑定都不会自行变化，但绑定不产生保留保证。显式 collect 可删除不再受保护的历史文件；旧绑定的真实读取可能自然报 FileNotFoundError。退役记录和恢复证据仍保留。

## 中断恢复

```python
from asterstore import Dataset, Repository

repository = Repository("./data")
with repository.prepare(Dataset("events")) as candidate:
    recovery_id = candidate.candidate_id
    candidate.write_bytes("part.json", b"[]")
    candidate.seal()

status = repository.candidate_status(recovery_id)
with repository.resume(recovery_id) as candidate:
    publication = candidate.commit()
```

需恢复跨进程任务时，应用应保存 `candidate_id`，完成文件后显式封存。未封存的候选不能恢复为可提交候选，需要重新生成数据。恢复前原上下文必须已经退出或原进程已经结束，否则候选独占锁会等待。

`CandidateStatus` 包含 `candidate_id`、`dataset_id`、`publication_id`、`state`、`current_generation`。状态为 `writing`、`prepared`、`conflict`、`current`、`historical`、`retired` 或 `abandoned`。它是持锁时的元数据观察，不审计数据内容，也不承诺查询返回后状态不变。

提交异常可能发生在 current 已切换之后；不要仅凭异常重新生成另一个发布。退出上下文后查询状态，再恢复同一候选。已经提交且尚未退役的候选重试返回原发布；即使后续已有新发布，也不会将 current 回退。

默认 `durable=True` 在写入和提交边界同步文件及目录。`durable=False` 省略显式 fsync，保留原子可见性和并发冲突检测，接受更弱的崩溃持久性。恢复时可以选择 `durable=True`，在确认成功前重新同步本候选新增文件及必要控制记录；复用对象仍沿用来源持久性。发生同步错误会向调用者报错，不把持久性失败伪装成成功。

## 具名保留引用

所有操作通过 `repository.retention` 调用。获取服务对象没有 I/O；建立引用、显式按引用打开和查询预览才执行控制元数据操作。

| 方法 | 当前行为 |
| --- | --- |
| `retain(name, dataset_id, *, publication_id=None, expected_revision=0, durable=True)` | 原子选定目标并登记引用，返回 `RetainedPublication` |
| `get(name)` | 返回 `ReferenceRecord`，从未存在则为 None；已释放记录仍可查询 |
| `open(name, *, expected_revision)` | 验证该 revision 仍为 active，返回保留目标的绑定 |
| `release(name, *, expected_revision, durable=True)` | 条件释放；保留 released 记录和单调 revision |
| `preview(policy)` | 返回控制元数据回收预览，不删除或退役 |
| `collect(policy)` | 重新计算保留关系、持久化计划、退役并逐文件回收，返回 CollectionResult |
| `resume_collection(operation_id)` | 在原操作范围内恢复，重新排除当前受到保护的对象 |
| `cleanup_candidate(candidate_id)` | 清理已显式放弃候选的私有文件；重复调用恢复原计划 |

`RetainedPublication` 包含 `reference`、`revision` 和 `binding`。`ReferenceRecord` 包含 `reference`、`revision`、`state`（active/released）和 `selection`（current/publication）。名字和目标位于其中的 `Reference`。这些对象没有析构释放或自动续租行为。

```python
held = repository.retention.retain("training/run-17", "market/prices")
try:
    paths = held.binding.files()
    # 完成全部原生文件读取，包括延迟计算与后台任务。
finally:
    repository.retention.release("training/run-17", expected_revision=held.revision)
```

首次创建 expected_revision=0，返回 active/1；释放返回 released/2。重用名字需明确传入 expected_revision=2，返回 active/3。旧 release/1 不能影响新 active/3。相同创建请求的重试返回原目标，即使 current 已更新；选择方式或目标不同则冲突。名字不是身份认证，独立消费者应使用独立名字。

CURRENT_ONLY 首次建立引用只能选择当时的 current；已存在的 active 引用可以在 current 更新后重开原目标。普通按 ID 打开旧发布仍然拒绝。VERSIONED 可以为尚未退役的历史发布建立引用。这个保证只覆盖遵守新路径发布约定的受管对象，不适用于外部原地覆盖。

引用建立不逐文件检查存在性或内容。它登记防止后续协议内 GC 删除的保留理由，不证明数据健康。若其他调用者显式释放同一引用，旧绑定不再受其保护。

## 回收预览

`CollectionPolicy`、`CollectionPreview`、`PublicationDecision`、`ObjectDecision`、`PreviewIssue` 均由顶层导出。

```python
from asterstore import CollectionPolicy

report = repository.retention.preview(CollectionPolicy(("market/prices",), keep_last=1))
if report.status == "complete":
    publications = report.retiring_publications
    objects = report.reclaimable_objects
else:
    issues = report.issues
```

datasets 必须明确且非空，不允许重复；keep_last 是至少为 1 的整数。最近 N 个发布按 generation 选择，再叠加 current 和 active 引用的保护，其他数据集的发布也参与对象保留计算。未提交的封存候选保护新增及复用对象；已提交候选不额外保留历史。

`CollectionPreview` 包含 policy、status、publications、objects、issues 和 pending_operations。publications 中每项包含 publication、generation、retire、reasons；objects 中每项包含 object、reclaimable、reasons。便利属性 retiring_publications 返回发布元组，reclaimable_objects 返回 ObjectRef 元组。理由字符串用于解释，当前包含 current、keep_last、reference:<name>、outside_scope 等。

预览只扫描控制元数据，不进入数据对象目录，不探测数据文件大小或内容，因此不提供精确回收字节数。发现损坏、身份矛盾、必要记录缺失、范围中的未知数据集或不支持的回收状态，返回 blocked、具体 issues，以及空的退役/回收清单。complete 表示本次控制关系计算完整，不等于文件完整性检查通过。

预览持有仓库独占锁，会等待活跃 prepare/resume 上下文结束；同一任务应退出这些上下文后再调用，避免等待自己仍持有的共享锁。普通读取继续不获取治理锁。预览是一次观察，不是可执行删除清单，实际执行必须显式调用 collect，并在锁内重新计算。

## 发布退役与物理回收

```python
result = repository.retention.collect(policy)
if result.status == "blocked":
    issues = result.issues
    # 排除失败原因后，如果已经有持久计划，恢复同一个操作。
    if result.operation_id is not None:
        result = repository.retention.resume_collection(result.operation_id)
```

collect 只接受 CollectionPolicy，不能把旧 preview 或任意文件列表交给执行器。所有执行与恢复强制同步控制记录和目录，不提供 durable=False。先同步当前发布、引用、候选与历史等决策依据，再持久化不可变 plan；全部本轮应退役的发布完成持久退役后，才逐文件 unlink 并同步父目录。数据内容不重新读取、hash 或 fsync，原来的弱持久性数据保证不因 GC 自动升级。

CollectionResult 包含 operation_id、policy、status、retired_publications、deleted_objects、missing_objects、protected_objects、remaining_objects 和 issues。后三类对象分别表示无使用者且已不存在、恢复时发现受保护而跳过、尚未处理完的原计划成员。deleted_objects 记录已确认删除；进程死在 unlink 与进度写入之间，恢复可能将该文件报告为 missing_objects。protected_objects 不表示文件已通过存在性检查。retired_publications 是该操作已确认的退役目标；发生响应丢失时，以后续恢复读取的事实为准。

没有可退役或可回收内容时，返回 complete、operation_id=None，不创建空计划。控制损坏、未知策略范围或存在未决操作时，返回 blocked；没有创建计划则 operation_id=None。计划建立前的 I/O 失败直接抛出；计划建立后的可记录失败返回 blocked。若连进度都无法持久写入，底层异常仍会抛出，不能把异常解释为没有生效。恢复入口的非法操作 ID 报 InvalidDeclarationError，不存在报 FileNotFoundError；计划自身损坏无法解析则报 StoreCorruptionError。

响应不确定时，先退出其他候选上下文，通过 preview().pending_operations 找到未决操作。未决操作会阻止新的 collect 和可执行预览；正常发布和引用操作仍可以继续。resume_collection 重新检查原目标，只退役仍符合策略的部分，且只删除原计划文件集合减去当前使用关系；不会扩大到中断后出现的发布。已经退役的身份不会恢复为 available。完成操作可以重试读取原结果，不重复删除。

历史记录退役后，不再允许 open、retain 或 reuse 通过该身份取得发布；这些调用抛出 PublicationRetiredError，包含 dataset_id、publication_id、candidate_id。旧候选的 status 为 retired，同候选 commit/resume 提交抛出相同错误；新候选重复使用该 publication_id 仍是 PublicationConflictError。已经封存的共享候选可以继续使用自己的成员证据，不需要来源重新可用。

回收只处理计划中的受管普通文件。执行时逐层检查父目录，拒绝数据符号链接、符号链接父目录和其他意外类型；不递归删除，不扫描未知数据文件，空对象目录也暂时保留。当前、具名引用和封存候选继续保护对象。来源退役后仍被共享的对象，会在最后的保护消失后由后续 collect 找到；已完成删除的对象不会在后续预览反复列出。

退役记录、完整候选清单、计划和完成进度首版都保留，元数据仍会增长。候选放弃和私有文件清理已经提供；未知孤儿自动清理和元数据压缩尚未提供。完整运行例子见 [collection.py](../examples/collection.py)。

## 候选放弃、诊断与清理

```python
report = repository.inspection.candidates()
# 确认生产者已停止且候选不再需要后：
repository.abandon(candidate_id)
result = repository.retention.cleanup_candidate(candidate_id)
```

abandon 永久释放未提交候选的保留作用，不探测或删除文件；未封存、已封存和冲突候选均支持，已提交或退役者拒绝。CandidateAbandonedError 防止之后 resume 再提交。调用者应先退出相关 prepare/resume 上下文，停止原生写入器与后台任务。

cleanup_candidate 首次在锁内扫描私有文件并持久化固定计划；重试只处理原范围。它不删除来源共享文件，不跟随符号链接，不递归删除目录，保留候选身份和日志。`CandidateCleanupResult` 提供 candidate_id/status/planned/deleted_files/missing_files/remaining_files/issues。

inspection.candidates 显式扫描未提交及放弃候选的私有目录，统计路径和普通文件字节，不读取数据内容。返回 `CandidateInspection`（status/candidates/unknown_locations/issues）与 `CandidateResidual` 列表；损坏控制或未知位置会报告 blocked，不自动删除。普通读取和回收预览仍不进入数据目录。三个操作都持仓库独占锁，等待活跃候选退出，不按时间判断任务已失效。

具体状态、错误、清理重试与兼容约定见 [候选残留治理](candidates.md)。未知孤儿的自动认领、完整内容审计和元数据压缩尚未实现。

## 错误与平台

- `AsterStoreError`：库异常基类。
- `InvalidDeclarationError`：非法声明，同时属于 `ValueError`。
- `UnknownObjectError`：选择未声明对象，同时属于 `KeyError`。
- `RepositoryNotInitializedError` / `PublicationNotFoundError`：仓库或发布不存在，同时属于 `FileNotFoundError`。
- `StoreCorruptionError`：控制元数据不合法或相互矛盾。
- `PublicationRetiredError`：发布身份已经退役，不再可打开、引用或作为复用来源。
- `PublicationConflictError`：基准变化、发布 ID 被其他候选使用或能力改变。
- `CandidateAbandonedError`：候选已经永久放弃，属于 CandidateStateError，包含 candidate_id。
- `CandidateStateError`：非法候选操作或未封存恢复。
- `HistoryUnavailableError`：能力不支持历史打开。
- `UnsupportedPlatformError`：平台缺少当前所需的本地写入协调能力。
- `RepositoryUpgradeRequiredError`：操作需要 v3，当前为只读兼容的 v1/v2 仓库。
- `ReferenceConflictError`：引用 revision、创建请求或 active 状态不匹配。
- `ReferenceNotFoundError`：引用名字从未记录，同时属于 KeyError。

底层文件 I/O 异常保留原样。写入协调当前使用 POSIX `flock`，验证环境是本地 Linux 文件系统。根路径只做词法绝对化，不解析符号链接；仓库不是针对恶意目录修改的安全隔离。更多边界见 [磁盘协议](protocol-v3.md)。

## 可选 Polars 扫描

`asterstore.integrations.polars.scan_parquet(binding, *, keys=None, hive_partitioning=False)` 返回原生 LazyFrame，仅传递明确文件成员；固定关闭 glob 展开，空选择抛出 ValueError。导入不加载引擎，调用时需要安装 `asterstore[polars]`。详细参数、延迟执行与保留时间见 [引擎接入](integrations.md)。

## v4 managed 发布

`initialize(..., managed_resource_id=...)` 显式启用库内受管资源。
`prepare_managed(dataset_id, publication_id=..., operation_id=..., expected_generation=..., history=...)`
返回 ManagedCandidate，提供 path/write_bytes、alias、reuse、seal、commit。
`resume_managed(operation_id)` 恢复已封存候选；`managed_status(operation_id)` 查询控制状态。
managed open 自动捕获受管根，无须传 resources；默认 open 的返回类型因此是旧 Publication 或新 Declaration 的 Binding。
完整约束和示例见 [managed v4](managed-v4.md)。

## v4 直接保留与 GC

initialize(..., lifecycle=True) 显式启用治理；Repository.governance 提供 retain/get/release、按精确 revision 打开对象保留、preview、collect/resume_collection、abandon，以及 preview_cleanup/cleanup/resume_cleanup。
retain 必须指定固定 publication_id 和 scope；METADATA 不保护字节，registered 拒绝 OBJECTS。
collect 必须提供新的 operation_id，重试和恢复均限制在固定原计划内。abandon 不删除私有输出；显式 cleanup 使用候选 operation_id 固定清理计划，resume_cleanup 只恢复已有计划。返回 ManagedCleanupPreview/ManagedCleanupResult，字段及错误边界见 [清理 v4](cleanup-v4.md)。
完整契约、成本与剩余边界见 [治理 v4](governance-v4.md)。
