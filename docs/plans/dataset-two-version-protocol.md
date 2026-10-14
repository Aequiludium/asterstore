# AsterStore：数据集双版本最小协议

> 实施更新：用户随后授权立即实施；当前工作树已开始替换为格式 2。下文“未实施”等表述保留为讨论时状态，运行时契约以 docs/api.md 和 docs/protocol.md 为准。用户已授权本地分批提交；未授权或执行推送、生产迁移或正式发布。

2026-10-14。状态：基于用户已确认需求的设计稿；不代表已实现。不修改运行时代码、不提交、不操作生产。本文取代 Issue #21 的元数据共享树、有效治理索引及增量校验方案，作为当前设计入口。最小 API 与磁盘结构建议见第 10–13 节，新增细节属于设计建议，不冒充用户逐项确认。用户明确 NFS 不是核心议题：它只作为部署前提，不阻塞核心设计，不另起存储后端选型。

## 1. 已确认的需求

当前工作范围仅为 AsterStore 设计。Aster/data-team 的调用方式是接口参考，不是本项目设计或实现验收的前置条件；不继续调查、设计或修改其业务流程。生产迁移另行安排，不把本设计完成等同于生产已经兼容。

- 每日更新、发布两次，日常只读取最新版本。
- 每个数据集独立发布，不需要跨数据集原子切换。
- 业务质量检查在发布前完成；成功发布后，旧 current 成为 rollback。
- 旧版本用于失败回退，不永久保存每次发布。
- 托管文件只属于一个数据集，只在该数据集的版本之间复用。
- 清理窗口由调用方协调，确保目标数据集没有读写任务，包括已取得清单但尚未结束的读任务。

由此采用的最小设计：同一数据集的修改操作互斥，失败允许安全重跑；正式保留 current 和至多一个 rollback；不提供永久操作重试、任意历史读取或动态 retention。

## 2. 权威状态与文件布局

概念布局（不冻结文件名和编码）：

```text
store/
  format.json
  datasets/<dataset-key>/
    head.json
    versions/<version-id>.json
    objects/<unique-path>
    staging/<attempt-id>/
    writer.lock
```

| 状态 | 保存内容 | 权威与寿命 |
| --- | --- | --- |
| store marker | 格式版本、仓库身份 | 拒绝不兼容客户端；不记录全仓发布事务 |
| head | 数据集身份、revision、current、可空 rollback | 唯一发布可见点；同时更新两个版本指针 |
| version manifest | 数据集/版本身份、有序成员到文件的完整映射及读取必需属性 | 不可变；不依赖历史链，不内嵌所有操作记录 |
| objects | 本数据集拥有的不可变数据文件 | 由有效清单引用；路径不可重绑或原地覆盖 |
| staging | 当次更新的临时工作 | 不是正式发布；失败后可丢弃、重算 |
| 日志 | 时间、版本、结果、错误摘要 | 非权威；不决定读取或删除，不保存完整清单 |

head 的 revision 每次发布或回退都前进，即使重新指向一个旧版本，也不复用旧 revision。version-id 和对象路径使用不复用的标识，不通过重建永久身份数据库来防冲突；遇到已有目标拒绝覆盖。清单可以表达多个逻辑成员引用同一个文件，清理按文件路径去重。

托管路径只能落在本数据集 objects 内。路径校验和删除不得穿越软链接、父目录或其他数据集。未知文件类型、非法路径和损坏权威状态导致停止清理，不靠猜测恢复。外部文件引用若未来需要，单独定义只读能力，绝不进入托管删除范围；这不是当前最小模型的前置功能。

## 3. 读取

1. 检查格式支持（会话内是否缓存另定）。
2. 一次读取 head，固定 current 的版本标识。
3. 读取该不可变清单，随后所有文件访问使用这一份清单。

读取不写元数据、不注册 retention、不加写锁、不扫描目录。发布可与读取并行，旧清单及文件留到维护窗口才删除。读者固定的是一次绑定的版本，不是每读一个文件重新读取 current。显式刷新才取得新版本。

不再把旧设计的 marker + 完整 current 两次读取作为硬性约束；允许 head 到 manifest 的额外访问，后续测量实际延迟。不存在 current 时返回未发布；损坏的 head/清单报错，不静默选取某个目录中的版本。

## 4. 发布

对目标数据集持有同一个修改锁，覆盖准备、检查与切换。不同数据集可以独立并行。

1. 读取基线 head/revision；首次发布允许尚无 head。
2. 在 staging 准备新数据。复用文件限于本数据集已保留版本中引用的文件；不修改旧文件。
3. seal 安装新文件到不会复用的最终路径，保存完整不可变候选清单；head 尚不引用它。
4. 在已封存的候选结果上完成业务检查。检查由业务任务负责，AsterStore 不解释行数、日期或因子质量；检查后内容不得改变。
5. commit 满足选定的持久化顺序，不重写或复制候选清单。
6. 原子替换 head：新 current 指向候选版本，rollback 指向旧 current，revision 前进。
7. 返回新版本与 revision；不在发布尾部执行清理。

第一次发布的 rollback 为空。第二次及后续发布只保留紧邻的成功旧 current；被替换的旧 rollback 退出正式保留，但物理文件暂留。无 head 时的首次创建也必须使用相同的原子可见机制。

唯一生效点是 head 的原子替换，不分别提交 current 和 rollback。文件和清单就绪但 head 未替换，只产生未引用遗留，不构成半次发布。

## 5. 发布结果不确定与回退

调用方在提交前已知 version-id。进程中断或响应丢失后，先读取 head，而不是盲目重放旧请求：

- 目标仍是 current：可以确认该版本当前已发布。
- 目标是 rollback：可以确认它已被后续操作保留，但不当作本次调用仍是当前状态。
- 目标不在 head：可能未提交，也可能已经被后续发布淘汰；不承诺永久判定。基于最新 head 发起新更新，不重新激活旧请求。
- head 损坏或无法读取：停止自动发布/删除，进入明确的故障处理。

回退需持有修改锁，并使用调用方观察到的 expected revision，避免迟到的回退请求撤销更新的成功发布。rollback 为空则拒绝回退。

回退一次原子写入新 head：current = 原 rollback，rollback = 空，revision 前进。故障版本不成为新的安全回退版。故障文件留待维护窗口清理；后续正常发布会重新建立双版本状态。回退不复制数据，不承诺已经开始的读任务切换版本。

## 6. 清理

调用方先停止目标数据集新读写任务，并等候所有旧读任务结束；维护窗口覆盖整个清理过程。修改锁只能排除 writer，不能证明无读者。API 必须明确声明这个前提，不把锁成功解释为自动建立静默窗口。

在修改锁内执行：

1. 读取并验证 head，以及 current/rollback 的完整清单和引用边界。
2. 在允许删除前，确保用于判断保留范围的 head、清单及保留数据满足崩溃持久性要求；无法建立保证则不删除。
3. 计算保留文件集合 current ∪ rollback。
4. 枚举本数据集 objects，删除集合之外的托管文件。
5. 删除不再保留的版本清单和废弃 staging，返回数量/字节及错误摘要。

没有 head 的目录不能直接解释为空保留集合并清空：首次失败遗留与 head 丢失无法仅靠目录区分。首版普通清理跳过这种目录并报告，显式处理流程另定。

任一权威清单无法读取或引用不合法时，在删除开始前失败。清理中途 I/O 错误可以留下部分未引用文件，重试重新计算；已不存在的待删文件视为已清理。不生成永久 collection plan、retired 正文或逐文件事务档案。

清理完成后正式清单最多两份；两次维护之间允许积累旧清单、旧文件和失败遗留。磁盘使用取决于清理频率，不能宣传为任意时刻都严格两份。

## 7. 故障与并发矩阵

| 情形 | 可见状态/处理 |
| --- | --- |
| 准备或业务检查失败 | head 不变；已有版本可读；遗留稍后清理 |
| 文件已安装、head 未切换即退出 | head 不变；新文件属于未引用遗留 |
| head 切换后响应丢失 | 重新读取 head 查询；不自动回放旧版本 |
| 两个 writer 更新同一数据集 | 修改锁串行化；在锁内确认基线 |
| 不同数据集同时发布 | 互不依赖，不建立全仓事务 |
| 发布时读者使用旧清单 | 继续读取，发布不删除旧文件 |
| 回退请求到达前已有新发布 | expected revision 不符，拒绝回退 |
| 回退后进程退出 | head 为回退前或回退后的一整份状态，不能出现混合指针 |
| 清理中途退出 | 保留版本不受删除；余下遗留下一次清理 |
| 当前清单损坏 | 读取报错，禁止据此自动清理；恢复需明确操作 |
| 维护期间出现新读者 | 调用方违反协调约定；首版不提供租约兜底 |

原子可见性不等于断电持久性。强路径要求数据、清单和目录先于 head 持久化，再确认 head 持久化；弱路径不能承诺机器故障后发布仍存在。任何路径都不能在清理时删除一个故障恢复后可能重新成为有效版本的文件。部署需提供协议所需的互斥、同文件系统原子替换和相应同步能力；NFS 适配检查留在实现验收，不作为架构讨论前置阶段，也不为此引入数据库、服务或分布式事务。

## 8. 取消或推迟的设计

取消核心承诺：无限发布历史、永久操作恢复、动态 retention、跨数据集共享托管对象、在线并发 GC、永久退役证明、全仓引用图。

暂不建设：成员共享树、增量清单链、refcount 数据库、治理 checkpoint、全仓事件日志、incremental/full 治理状态机。有限份完整清单优先，必要时再以真实规模测量决定优化。

普通发布写入成本允许 O(本次完整成员数)，读取清单也是 O(成员数)。维护成本为 O(该数据集物理文件数 + 两份有效清单大小)，不是 O(全仓永久发布历史)。更新频率不能替代对实际成员数量和清理周期的测量。

这套回退用于错误发布，不是备份：共享文件损坏、外部篡改、磁盘故障需独立备份策略。

## 9. 实现前的剩余设计与验证

产品边界已按本次讨论收敛，后续只解决必要协议细节，不借机重新引入已取消能力：

1. 根据第 10–13 节确认最小 API、head/manifest 字段和路径规则；新格式编号在实现时统一分配，保留成员顺序/别名所需语义。
2. 按普通文件系统协议实现；部署互斥、原子替换和同步能力作为验收前提，不继续展开 NFS 专题设计。
3. 明确清理窗口的调用方集成方式与频率；仓库级停任务可用，但不是数据集协议要求。
4. 设计迁移：旧 writer 明确拒绝新格式；只选 current 和经确认的 rollback。不能默认把旧目录的最后一份 history 认作业务安全版本。旧控制记录保留为隔离备份，不继续参与新治理。
5. 迁移前核查跨数据集共享文件、旧 retention、外部 registered 数据等实际情况；不能静默丢弃保护或扩大删除权。需要复制或重新分配所有权时单列成本。
6. 设计验证：发布故障注入、回退 revision 冲突、旧读者跨发布、清理中断重试、缺失/损坏清单禁止删除、路径逃逸拒绝、NFS 部署实测。
7. 容量验收：固定数据规模重复发布并按周期清理，正式元数据不随累计发布次数增长；分别报告清理前峰值和清理后稳态，不只报告压缩比。

进入实现、迁移或生产维护需后续明确指令。本轮仅记录协议，不运行生产清理，不变更任何现有数据或依赖。

## 10. 最小 API 建议

保留 Repository 作为入口，取消单独的通用 Governance 子系统。日常接口只围绕读、准备、提交、回退、清理，另提供初始化和数据集枚举：

```python
repo = Repository(root)  # 纯构造，无隐式初始化
repo.initialize()  # 显式创建新格式仓库
repo.list_datasets()  # 枚举已发布的数据集
state = repo.describe(dataset_id)  # 只读 head，返回 revision/current/rollback
binding = repo.open(dataset_id)  # 默认 current
old = repo.open(dataset_id, version="rollback")

with repo.prepare(dataset_id, expected_revision=state.revision) as draft:
    draft.reuse_current()  # 显式继承当前完整成员集
    path = draft.allocate("2026-10-14", relative_path="data/date=2026-10-14/data.parquet")
    write_data(path)  # 业务使用自己的 Parquet 等写入器并关闭文件
    candidate = draft.seal()  # 冻结成员和文件，生成候选 Binding
    validate_business(candidate)  # 业务自己的函数，失败则不 commit
    result = draft.commit()  # 原子发布，返回 head 状态

repo.rollback(dataset_id, expected_revision=result.revision)
repo.clean(dataset_id, quiescent=True, dry_run=True)
repo.clean(dataset_id, quiescent=True)  # 重新计算，不执行之前保存的计划
```

以上是目标接口示意，不是可运行的现有 API。首次发布用 expected_revision=0；实际首个 head revision 为 1。prepare 可省略 expected_revision，在获得锁后使用当时基线；业务基于先前读取结果计算时应传入该 revision，避免使用过期基线。回退始终要求 expected_revision。

### Draft 的最小语义

- prepare 进入上下文时取得数据集修改锁；初始成员集为空，避免把追加和全量替换混为隐式行为。
- reuse_current 只能在空草稿上调用一次，复制当前成员映射到内存，不复制文件。首版不提供任意历史版本或跨数据集复用接口。
- allocate(key, relative_path=...) 分配由库生成唯一前缀的临时路径，允许经过校验的相对后缀，以支持 Aster 的 Hive 分区布局；安装后保留后缀。替换已有成员保持位置，新成员追加。配套 remove(key) 删除成员；alias(new_key, existing_key) 允许多个成员引用同一文件。非法重复定义与未知 key 报错，具体异常名实现时确定。
- 所有写入器在 seal 前关闭。seal 将本次新文件安装到最终唯一对象路径并写入唯一候选清单，返回可读取的 Binding；这不是发布。业务检查因此读取最终待发布的同一批字节，不需要 staging/final 两份清单。
- seal 后不允许通过 Draft 修改成员或文件；调用方也不得继续使用此前取得的路径写入。业务检查失败就退出并重跑，不重新打开 sealed 工作继续修改。
- commit 只接受 sealed 状态，不再次复制清单。上下文退出只释放锁，不隐式提交；未发布文件由维护窗口统一回收。
- draft.version_id 在 commit 前可取得，用于响应丢失后的 head 查询。重复 commit 仅在同一存活 Draft 内返回已知结果，不提供持久化 resume 协议。

Binding 固定 manifest，并携带本次读取 head 的 revision，供生产计划使用同一份状态；files 只做内存映射，解析已固定的文件路径不再读 head。业务可以加载各种文件格式，核心不提供专用行情或因子校验器。Aster 的 aster:publication 业务 JSON 作为普通成员保留，包装层使用 draft.version_id 构造其发布身份，不新增核心业务附件协议。

### 结果和校验

describe 返回 head，而不是展开全量声明。open 的 version 只允许 current/rollback，不接受任意历史 ID；rollback 不存在时明确报错。list_datasets 不把只有 staging 而没有 head 的目录列为已发布数据集。

发布总是检查清单结构、路径归属和本次新文件已完成；业务质量检查由调用方负责。读取不默认做文件全量 stat/hash。既有 existence/format/checksum 能力如有调用需求，可保留为独立的显式检查辅助函数，不再和发布生命周期状态绑定。

建议保留 durable=True 默认以及显式弱路径的选择，不因简化架构强制所有用户采用单一同步成本。清理没有弱删除选项：删除前建立保留状态的持久保证，必要时同步有效清单所引用的文件；不为规避这个成本引入永久同步索引。该选择与业务质量无关。

clean 返回扫描数、候选删除数/字节、实际删除数、错误与跳过原因。dry_run 只提供当次估算，不生成可延迟执行的权威 plan；后续实际清理重新检查。quiescent=True 是调用方声明已协调停任务，不是系统自动阻止读者的开关。

## 11. 最小磁盘字段建议

采用 UTF-8 JSON，小型 head 与完整 manifest 分离，不新增第三套权威索引。

| 文件 | 必需字段 |
| --- | --- |
| format.json | 新格式版本、store_id |
| head.json | dataset_id、revision、current、rollback（可空） |
| versions/<version-id>.json | dataset_id、version_id、members |
| members 的单个条目 | key、path（相对本数据集 objects 根的路径） |

members 是有序数组，key 唯一；不同 key 可以指向同一 path。定位直接使用 path，不再重复保存 object_id + locator + member.object_id 三层关联。对象唯一性由库分配的路径表达，不要求内容 hash。格式必需的字段校验保留，业务自由字段不进入首版核心；有实际需求后再定义附件。

dataset-key 使用稳定的 ID 编码或摘要避免路径转义，并核对正文 dataset_id；具体沿用现有 token 编码即可，不新增映射数据库。version-id 由库生成，第一次创建时禁止覆盖。对象路径也由库生成，逻辑 key 与物理路径分开；已发布路径不能重用，即使旧文件已经清理。

版本清单只保存一份：seal 后位于 versions，head 引用它即正式生效。判断有效性只看 head，不以清单存在、文件时间或目录排序推断发布成功。日志时间戳不是选择 current 的依据。

新布局必须使用明确的新格式，旧客户端拒绝；不通过伪装旧格式让现有 writer 混写。仓库 marker 可沿用现有固定发现位置，最终目录名在实现时统一，避免旧客户端把新库误认为空目录而初始化。

## 12. 现有接口和模块去留

本表是替换目标，不表示现在删除代码或立即中断旧应用。

| 当前能力 | 新模型中的处理 |
| --- | --- |
| Repository / open / Binding | 保留用途；只选择 current/rollback，绑定保持不可变 |
| describe / list_datasets | 保留但收缩返回和枚举语义 |
| prepare / Candidate | 改为单次 Draft；复用、seal、commit，无跨进程 resume |
| registration_status / candidate_status | 删除永久查询协议；用 head + 已知 version-id 判断近期结果 |
| resume / resume_registration | 不进入新核心，失败重跑 |
| register / 外部资源映射 / bind | 不进入本次托管双版本核心；外部只读适配可另行设计，不阻塞本项目 |
| HistoryAccess / RetentionScope / FixedRetention | 不进入双版本核心 |
| Declaration / FileSet / ObjectRecord | 收缩为有序 Manifest / Member；不需要独立对象档案 |
| Governance inventory / references / collection / retired | 替换为数据集级 clean，不维护历史关系 |
| compaction / 控制归档 | 不再是日常运行能力；新模型不产生永久重复声明 |
| decommission / 全数据集删除 | 不作为最小发布功能；未来确有需求再单独定义显式删除 |
| 锁、原子文件替换、JSON 边界校验 | 复用经核验的底层实现，不携带旧治理模型 |
| Polars 等文件格式集成 | 作为读写辅助层，不能决定版本和删除权 |

API 名称相近不代表兼容：open(publication_id=...)、describe 的旧返回类型等变化必须在迁移说明中明确。不为无感兼容重新实现已撤销的历史/恢复能力，也不擅自删除已存在的外部数据访问需求。

## 13. 下一阶段的具体产出

核心架构无需再以 NFS 调研、通用后端选型或下游改造为前置。本项目独立交付：

1. 模型与读取：Head、Manifest、Member、Binding 的字段、格式校验、初始化和固定版本读取。
2. 发布与回退：Draft 的内存状态、文件封存、原子 head 切换、revision 冲突和不确定结果查询。
3. 维护清理：以两个有效清单为依据，维护窗口内删除未引用文件，不生成持久化治理关系。
4. 独立验收：合成文件和通用调用示例，覆盖故障矩阵、路径边界、完整清单读取及反复发布/清理后的容量上界。不以 Aster 或 data-team 改造完成作为验收门槛。

实现批次按上述顺序推进，实际实现尚未开始。旧格式迁移保留独立事项：选择两个版本、确认归属、隔离验证，不在本轮设计生产切换脚本。

实际实现仍等待明确指令；本轮仅形成可评审设计。文件规模增长、业务读写流程与双版本正确性是主线，部署检查保持有界。

生产与消费链路已完成首轮只读调查，见[接口迁移调查](dataset-two-version-consumers.md)，现仅作为后续集成参考。核心只吸收通用需求：任意文件成员、安全相对路径、可提前取得的版本身份和固定 Binding；不认识 Hive、证券参考表、每日任务或业务报告，不自动恢复永久历史或 retention。

## 14. 核心内部边界

建议采用以下小模块职责，不预先扩成多个可插拔子系统：

| 模块职责 | 内容 | 不承担 |
| --- | --- | --- |
| models/codec | Head、Manifest、Member、JSON 编解码和结构约束 | 对象注册表、业务 schema |
| storage | 路径、锁、不可变安装、原子替换和同步 | 生命周期策略、全仓扫描 |
| reading | open/describe/list、固定 Binding | retention、数据格式解码 |
| publishing | 单次 Draft、成员变更、seal/commit、rollback | 永久操作档案、跨进程 resume |
| cleanup | 单数据集有效集合与文件清理 | 引用图、持久 plan、历史审计引擎 |

Repository 仅组合这些能力。保留内部函数即可，不为每个概念新增公共服务对象。

Draft 状态为 editing → sealed → committed；退出上下文后不可继续操作。editing 可 allocate/reuse/remove/alias，sealed 可读取候选并 commit，committed 只能取得已知结果。业务校验失败或封存/提交异常后，不允许继续编辑同一 Draft；提交结果不确定时读取 head 判定，不将异常等同于未发布。状态保存在内存，不逐阶段落盘。seal 前生成的新路径只能指向本次 staging；复用引用来自持锁后固定的 current。

Binding 使用不可变 manifest，已发布 Binding 携带观察到的 head revision；候选 Binding 明确未发布，携带 base_revision 而不伪造已提交 revision。只有 head 指向某版本才代表它已正式发布。

库只知道“一次 commit”，不限制每天几次，也不把多个 commit 合并成一次业务任务。rollback 永远是最近一次成功 commit 之前的 current；什么是一次完整业务更新由调用方决定。
