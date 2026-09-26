# 受管仓库协议 v2（历史开发格式）

本文件保留 v2 的记录结构与当时写入流程。**当前新仓库使用 [v3](protocol-v3.md)，v1/v2 保留读取兼容，禁止治理写入，不自动升级。** v3 沿用下文的字段和协调流程，但每份控制记录必须声明版本 3，并使用 v3 的逻辑身份语法。下文“新仓库使用 v2”或“操作要求 v2”等描述仅适用于历史实现。

## 版本与布局

仓库标记为 `{"format_version":2,"kind":"repository"}`。在读取边界核对控制记录与仓库的版本，在治理锁内重新检查写入资格。未知版本、混合记录版本、未知 kind、缺失/额外字段和重复 JSON 键都拒绝，不静默忽略。

```text
.asterstore/
├── format.json
├── .init.lock
├── gc.lock
├── locks/
│   ├── candidates/<candidate_id>.lock
│   └── references/<sha256(name)>.lock
├── candidates/<candidate_id>/cleanup-plan.json
├── candidates/<candidate_id>/cleanup-progress.json
├── candidates/<candidate_id>/state.json  # candidate 或 abandoned_candidate
├── candidates/<candidate_id>/files/<logical_key>
├── objects/<candidate_id>/<logical_key>
├── references/<sha256(name)>.json
├── collections/<operation_id>/plan.json
├── collections/<operation_id>/progress.json
└── datasets/<sha256(dataset_id)>/
    ├── commit.lock
    ├── current.json
    └── history/<sha256(publication_id)>.json  # 完整发布或退役记录
```

控制记录使用严格 UTF-8 JSON。标识符 hash 只用于文件名，不计算数据内容 hash。锁文件不能删除或替换。原子写入的临时控制文件可能在中断后残留，预览识别这些文件但不删除。

v1 的 `Repository.open()` 和候选状态查询仍可读取；候选状态查询可能创建协调锁文件。`prepare()`、`resume()` 和 retention 服务要求 v2，否则报 `RepositoryUpgradeRequiredError`。新库不修改旧仓库标记，也没有迁移命令；原地升级需要后续离线迁移设计。

## 发布与候选

沿用 [v1 发布步骤](protocol.md)：封存、安装本候选新增的对象、在数据集锁内检查 generation、归档曾生效的旧 current，再原子替换完整新 current。普通打开当前发布只读格式标记和 current，绑定后不重复访问控制文件。

发布记录与 v1 的字段相同，`format_version` 为 2。候选通过 `keys` 保存新增文件的逻辑键，通过 `reused_objects` 保存复用项 `{"key":".asterstore/objects/<creator_id>/part.bin","publication_id":"source"}`。来源数据集就是候选数据集。发布对象可以属于不同创建候选，但都必须是合法的受管对象键。

复用项来自同仓同数据集的可访问已提交声明；current_only 旧发布需要 active 具名引用和匹配 revision。选择时在数据集锁内检查成员，不探测或同步数据。封存记录固定两类成员；恢复使用该记录，一次检查共享文件是否存在且为普通文件，不重新选择源发布。durable=True 只同步本候选新增文件和控制记录，共享文件沿用来源的持久性。

这是 v2 开发协议中已预留字段的实现，公开协议尚未冻结。此前只支持空 reused_objects 的 v2 开发构建会拒绝新的共享记录；新旧构建不可混合写入或恢复此类仓库。现有空复用记录的编码保持不变；v1 仍只允许本候选命名空间，不开放对象复用。

固定格式样例：

- [candidate.json](../tests/fixtures/protocol/v2/candidate.json)
- [current.json](../tests/fixtures/protocol/v2/current.json)
- [reference.json](../tests/fixtures/protocol/v2/reference.json)
- [reuse-candidate.json](../tests/fixtures/protocol/v2/reuse-candidate.json)
- [reuse-current.json](../tests/fixtures/protocol/v2/reuse-current.json)
- [retired.json](../tests/fixtures/protocol/v2/retired.json)
- [collection-plan.json](../tests/fixtures/protocol/v2/collection-plan.json)
- [collection-progress.json](../tests/fixtures/protocol/v2/collection-progress.json)

编解码测试同时核对 v1 与 v2 样例；读取 v1 后重新编码仍保留 v1，不进行隐式迁移。

## 引用状态

`retention_reference` 记录包含 `name`、`dataset_id`、`publication_id`、`revision`、`state` 和 `selection`，以及 version/kind。

| 字段 | 约定 |
| --- | --- |
| revision | 正整数；active 为奇数，released 为偶数，不接受布尔值 |
| state | active 或 released |
| selection | current 或 publication，保留首次创建请求的选择方式 |
| target | 一个固定的受管已提交发布，名字重用前不能原地改指向 |

不存在的名字按 revision=0 处理。retain 使用 expected_revision 做条件创建；首次选择 current 和写入引用处于同一个数据集锁临界区。重试 active/r+1 时必须匹配数据集、选择方式和显式目标，返回原目标，即使 current 已更新。

release 只接受当前 active/r，写入 released/r+1；重试同一 release 可以识别 released/r+1。重新使用名字必须携带 released revision。旧请求遇到后续变更返回冲突，不覆盖新引用。

引用变更通过单记录原子替换生效，默认同步控制文件及其目录。durable=True 创建引用也同步选中的提交证据；不额外读取或同步数据内容。替换后响应或同步失败可能已经生效，调用者使用相同请求重试，库补齐同步后确认。

active 引用重开允许绑定所保留的目标。对 current_only，首次创建只能选择 current，但已有 active 引用可以在 current 更新后重开当时选中的受管文件。普通历史打开仍拒绝。这依赖受管生产者不覆盖已提交对象，不适用于未来原地更新的外部数据。

## 协调

- 候选：仓库共享锁 → 候选独占锁 → 提交期间的数据集独占锁。
- reuse：候选已持仓库共享锁和候选独占锁，再取得数据集共享锁；按引用选择时先取得引用共享锁。
- retain：仓库共享锁 → 引用名字独占锁 → 数据集独占锁。
- release：仓库共享锁 → 引用名字独占锁。
- 引用 open：仓库共享锁 → 引用名字共享锁 → 数据集共享锁。
- preview / collect / resume_collection：仓库独占锁，覆盖完整观察或执行过程。
- abandon / cleanup_candidate / inspection.candidates：仓库独占锁；与活跃写入和回收串行。
- 普通 open/files：不获取治理锁。

取得 Repository.retention 服务对象没有 I/O。get 读取原子引用记录并参加仓库共享协调，不将返回的状态视为永久保证。

preview 会等待活跃候选上下文结束；同一任务应退出 prepare/resume 上下文后再调用 preview，否则会等待自己仍持有的共享锁。引用释放应发生在原生读取器、延迟计算和后台文件访问全部完成之后。

## 回收预览及完整性边界

策略要求明确且不重复的 datasets，keep_last 至少为 1。预览按数据集 generation 保留最近 N 个发布，再叠加 current、active 引用和范围外发布。未提交且已封存候选保护声明对象，已提交候选状态不额外保护历史。

扫描读取全仓的 current、历史、候选和引用，不进入 objects 或候选 files 目录，不逐文件 stat，不报告精确可回收字节数。current 与同一份历史记录去重并核对一致；available 与 retired 一起保留完整 generation 身份链，提交与候选证据应一致。退役记录必须匹配原候选及所属计划中的完整发布。复用成员必须存在于其来源发布，已提交复用的来源 generation 必须更早；缺失或矛盾会阻塞预览。来源只是成员出处，不作为保留整个源发布的根。源发布已退役时从候选清单及计划核对原成员证据，不能因此恢复它的可用性。损坏、身份不匹配、缺失必要记录、未知数据集范围等返回 blocked 和 issues，不给出退役/回收清单。

complete 只表示本次控制关系观察完整，不证明数据文件存在或内容正确，也不是可执行的删除计划。每次 preview 都重新获取锁并计算。

## 退役记录与回收日志

历史路径原子替换为 retired_publication，记录 dataset、publication_id、generation、candidate_id 和 collection_id，不在 tombstone 中重复保存对象列表。current 仍只能是完整 publication；普通 current 打开仍只读 marker 和 current。历史/引用/复用来源遇到 tombstone 抛 PublicationRetiredError；同一候选提交不能复活它，新候选也不能重用身份。

plan.json 是不可变 collection_plan，包含 operation_id、CollectionPolicy、targets（完整 PublishedRecord）和 objects（确切允许删除的完整键）。progress.json 是 collection_progress，含 operation_id、state、retired（本操作确认的候选 ID）、deleted、missing、protected 和 issues。状态 planned/running/blocked/complete。每次写入均严格编码、原子替换及同步，完成记录保留。

只有 plan 已经持久化才允许退役。plan 存在而 progress 尚不存在，按 planned 恢复；没有 plan 的空目录或仅有计划临时文件视为计划写入前残留，不授权任何删除。只有 progress、未知文件、非法记录或身份矛盾则 blocked。未完成计划通过 pending_operations 报告并阻止另起 collect；正常发布和引用仍可进行。

collect 在仓库独占锁内重新计算，先同步全部有效控制记录及目录，再写计划和退役目标，全部退役成功后再次扫描并同步，最后逐文件删除并同步父目录。删除只允许受管普通文件，父路径必须是真实目录；不跟随数据符号链接、不递归清理，空对象目录保留。恢复不信任进度偏移，重新处理原键集合减去当前 available 发布与未提交封存候选使用的对象；缺失的无使用者文件记为 missing。protected 表示保留关系跳过，不额外承诺其文件健康。

扫描同时核对完整候选、计划和退役记录。来源退役后仍受共享保护的对象继续可达；失去最后保护后可由后续操作回收。已完成日志中的 deleted/missing 从后续回收候选中扣除。执行日志不成为数据保留根。

此前不支持退役的 v2 开发构建会拒绝这些历史记录和运维日志；不可混合进行治理或恢复。协议尚未冻结，不提供在线迁移保证。

引用记录、已释放名字、历史和候选均保留，元数据和数据空间会积累。候选显式放弃、私有文件清理和残留诊断已实现，见 [候选残留治理](candidates.md)。未知孤儿自动清理和元数据压缩是后续交付。本地测试不代表断电或 NFS 故障资格。
