# Issue #21 workplan 独立评审

日期：2026-10-13。评审对象：[issue-21-workplan.md](issue-21-workplan.md)。范围：方案与本地实现静态核对；未修改运行时代码、未执行远端写入、未启动其他代理。

本地基线：HEAD `8cf733cb8208d475df38c3ad4e915f466d68e83f`，`pyproject.toml` 版本为 `0.2.0`。开始评审时工作区仅显示未跟踪的 `docs/plans/`。下列发现是进入实现前需要补齐的设计边界，不表示尚未实现的新接口已经存在运行时缺陷。本次未运行测试；测试文件仅用作现有行为证据。

## 结论

方向成立，可以继续阶段 A 的契约讨论及阶段 B 的基线设计；阶段 C 开始前应关闭三个 P1 缺口。没有发现需要否定职责分离方向的 P0 问题。

计划已经正确保留：默认读取仅解析必要控制记录、绑定后选择无治理 I/O、检查与 durable 正交、复用保护不可选、回调不进入全局协调锁、检查结果不授权删除。阶段 D 也已经独立标明为后续设计，当前不应扩大到历史压缩或锁粒度重构。

## P1-1：需要明确检查与候选状态的转移，不能只为 seal 增加参数

**计划位置：** 第 3 节第 39–42 行；第 6 节第 85–86 行。

**源码依据：**

- [`Candidate.__enter__`](../../src/asterstore/publishing/_candidate.py#L105) 在 resume 时读取 `sealed.json` 并预置 `_sealed`。
- [`seal`](../../src/asterstore/publishing/_candidate.py#L284) 对 `_sealed is not None` 直接返回，第一次 seal 又会先设置 `_pending`；[`_active`](../../src/asterstore/publishing/_candidate.py#L163) 从此拒绝新的 path/write/alias/reuse。
- [`commit`](../../src/asterstore/publishing/_candidate.py#L318) 无条件调用 seal；[`_install`](../../src/asterstore/publishing/_candidate.py#L301) 可能面对 staged 或已 installed 目录。
- [`commit_record`](../../src/asterstore/publishing/transactions/_commit.py#L141) 的已提交分支先确认固定身份和可用性，再执行 `_sync_result`；`durable=True` 时仍调用 install，`False` 时跳过 install。首次提交才在后续 generation 校验通过后安装。

**具体风险：** 若新增检查仅放在 seal 当前的非 sealed 分支，`seal(check="none") → commit(check="checksum")` 与 `resume(...).commit(check="format")` 都可能绕过调用方要求的检查。反过来，若将检查与 install 继续绑定，已提交的强持久重试会发生数据检查失败，而弱持久重试却不检查；检查契约就取决于 durable。已安装未提交恢复的检查路径还不能继续指向已消失的私有 files 目录。

**建议修订：** 阶段 A 增加状态转移表，至少覆盖未封存、已封存未安装、已安装未提交、已提交 current、已提交 historical、retired/abandoned。每个状态列出参数入口、实际检查的确切路径、失败后的状态、是否可再次 commit，以及必须同步哪些证据。将封存声明、执行所请求的检查、安装同步分成职责；不把 `sealed.json` 当作任何数据检查通过的证据。首次提交和未提交恢复显式请求检查时均执行；不跨进程复用检查通过状态。检查升级和 format/checksum 分支切换必须有验收用例。

对于已经 committed 的操作，必须先确定它已经生效，再决定是否拒绝非 none 检查或返回带 committed 状态的检查失败。不能依赖一次无锁 status 预读来决定，因为相同 registered 请求还可能并发提交。无论采用哪一种语义，默认重试仍履行选定持久性，且不重新设置旧 current。

另需明确“检查失败仍可恢复”不意味着允许修补 sealed 字节。当前 seal 失败也可能已 `_pending`；格式失败后需要重新生产数据时，应说明是新操作重做还是允许何种尚未封存的修复。不要以恢复为由悄悄放宽封存后不可变规则。

## P1-2：none 下的最低安装约束与同步失败需要具体矩阵

**计划位置：** 第 2 节第 21 行；第 3 节检查表；第 6 节第 88 行。

**源码依据：**

- [`_verify_files`](../../src/asterstore/publishing/_candidate.py#L267) 同时验证 base 目录、递归 lstat、声明/实际文件集合相等、同步新数据和 request，并同步目录。
- [`sync_file`](../../src/asterstore/storage/_files.py#L35) 使用普通 `open("rb")`，本身不拒绝符号链接，也不使用非阻塞打开避免 FIFO。
- [`sync_control_files`](../../src/asterstore/storage/_files.py#L84) 同步文件和每级父目录；首次和已提交强持久恢复仍依赖它履行数据及目录持久性。
- [`test_seal_checks_declared_files_only_at_write_boundary`](../../tests/test_managed.py#L215) 目前将 missing、extra、symlink、directory 都归入默认 seal 拒绝。

**具体风险：** 去掉全面扫描后，不能同时把防止链接越界、特殊文件导致同步阻塞的约束也去掉。`durable=False, check="none"` 是否允许声明缺失的新文件仍未决定；`durable=True` 则至少会因为无法打开/同步该文件失败。因此“无可选 existence 检查”不能被解释成“保证缺失文件也可强持久提交”，更不能解释成写入时零 stat。

**建议修订：** 对 base、声明的新对象路径及父链、复用对象、未声明条目分别列矩阵，列出存在、缺失、symlink、目录、FIFO 等类型在 `none × durable` 下的行为和必要探测。明确哪些是所有权/安装约束，哪些是可选健康检查，哪些只是持久化操作自然返回的错误。未声明条目的递归遍历可取消，但声明路径和受管安装根的最低边界不能由 format/checksum 选项决定。

同步重构须保留新字节、嵌套目录项、源/目标 rename 目录、request/sealed/operation/object/current 的恢复顺序；复用只同步所依赖的控制证据，不能将全量对象检查器的目标列表直接交给 fsync。建议写明默认 none 下新增数据仍可能被打开以 fsync，但不读取数据内容进行健康验收。现有 [`强持久重试测试`](../../tests/test_managed.py#L287) 与 [`复用不读/不同步旧字节测试`](../../tests/test_managed.py#L269) 应分别保留，不因去重优化合并为同一种保证。

## P1-3：取消候选盘点会新增“额外文件已安装且 cleanup 整体拒绝”的状态

**计划位置：** 第 3 节第 43 行；阶段 C 第 73 行。

**源码依据：**

- [`_install`](../../src/asterstore/publishing/_candidate.py#L301) 对整个 files 目录做 rename；不会仅安装声明文件。
- [`cleanup.choose_plan`](../../src/asterstore/governance/cleanup/_service.py#L104) 对 installed 目录要求实际文件是 sealed 创建路径的子集，第 128–131 行遇到额外文件立即报损坏。
- 同一方法第 135 行的 private cleanup 使用 `scan_files(private)`，与 installed cleanup 的权属依据和范围不同。
- [`cleanup.check_users`](../../src/asterstore/governance/cleanup/_service.py#L66) 明确禁止清理已提交候选；普通 GC 依据对象注册表和声明关系，不能认领未声明文件。
- [`governance.check`](../../src/asterstore/governance/inspection/_checks.py#L163) 数据目标仅为非 retired 的 committed 对象，排除私有候选；控制盘点又在第 74–77 行跳过 managed-data 与候选 files，因此它不能自动补上这项诊断。

**具体场景：** 默认 none 候选含有未声明的普通文件 → seal 成功 → 整体 rename 成功 → current 前故障 → abandon → cleanup 发现 extra，拒绝整个清理计划。成功发布的 extra 则不在 Binding 或普通 GC 范围，也不能走 candidate cleanup。取消扫描后这属于可由新默认正常产生的状态，不应仍被笼统描述为无恢复路径的仓库损坏。

**建议修订：** 阶段 A 必须决定上述状态的可观察结果，并在阶段 C 提交最低可用的候选/受管命名空间诊断入口。它应报告声明范围、未知条目、installed/private 和提交状态；不能复用全仓检查冒充已覆盖。阶段 C 验收增加“extra + rename 后中断 + abandon/cleanup”以及“extra + 成功发布 + GC”两个场景。

可选择：installed cleanup 只删除可证明归属的声明文件并明确返回残留；或维持拒绝清理，但提供明确的诊断与人工处置步骤。无论选择哪一种，都不自动授权删除 extra。新默认导致的文件保留及 cleanup 变化应写进升级说明。未获相应权属设计前，不把“目录属于候选”升级为任意文件的删除证据。

## P2-1：公开入口范围尚未覆盖 registered 写入与内存 bind

**源码依据：** [`Repository`](../../src/asterstore/repository.py#L31) 提供 bind/open/register/resume_registration/prepare/resume；[`registration.register`](../../src/asterstore/registration/_operations.py#L18) 与 resume_registration 只提供 durable，当前不接收外部资源根、不访问外部数据。

**问题：** 计划宣称读写均可显式开启检查，但 API 例子仅有 open 和 managed commit，切入点也没有 registration。若 registered 发布也在写入范围内，强检查需要明确 `resources` 参数、资源映射验证与失败/已提交重试规则。`repo.bind(...)` 是否提供同一检查入口也不明确。仅在读测试里覆盖 registered 资源映射，并不能证明其写入口得到支持。

**建议：** 阶段 A 给出公开入口清单。建议 open、managed commit、register 及两类恢复入口得到一致而显式的检查契约；若 bind 仍保持纯内存操作，说明检查发生在哪个独立入口，不让 Binding.files 或适配器变成延迟检查。registered 的 none 路径继续无需 resources；显式检查才要求绑定，且检查不获得外部字节的管理/同步/删除权利。

## P2-2：checksum 基准、检查范围与旧治理报告语义需明确分开

**源码依据：** [`Candidate.path`](../../src/asterstore/publishing/_candidate.py#L170) 内部生成 object_id，公开返回 Path；alias/reuse 返回行为不同，首次获取完整新对象映射的现有公开入口是 seal 的 DeclarationRecord。[`governance.check`](../../src/asterstore/governance/inspection/_checks.py#L195) 当前将缺失某对象基准报告为 `checksum_unavailable`，不是启动前参数错误。

**问题：** 新写接口要求完整 object_id→SHA-256 基准，但若把校验放在首次 seal 参数上，调用方尚无公开的新对象 ID 映射。读接口的“选定对象”也不清楚是整份发布还是随后 `Binding.files(keys=...)` 的子集。新接口的“完整基准缺失则在启动检查前拒绝”还不能回写成旧 governance.check 的新行为。

**建议：** 冻结可执行的 checksum 示例，例如先以 none 封存声明获取 ID，再在 commit 指定完整基准；这需要 P1-1 确保已 seal 仍执行本次检查。基准完整性在确定目标集合后、任何数据检查前确认。当前没有 open(keys=...)，因此建议第一阶段读检查覆盖该 Binding 的全部唯一对象，并明确随后成员选择不补检查。空发布的完整基准可为 `{}`；无关 object_id 是忽略还是拒绝要写清。

只复用无锁、无 inventory 的对象检查原语。旧治理 API 保持其汇总、缺失基准报告和协调行为；[`inspection_lock`](../../src/asterstore/governance/inspection/_locking.py#L12) 与 [`governance.check`](../../src/asterstore/governance/inspection/_checks.py#L156) 的整段调度不能搬到读写检查路径。

## P2-3：基线与兼容性验收应包含恢复和已有规范的修订

**源码依据：** [`docs/publishing.md`](../publishing.md#L53) 当前承诺默认拒绝未声明文件/链接；第 65–67 行承诺旧操作不回退 current及强持久恢复新字节。已有 [`managed 进程中断测试`](../../tests/test_managed_processes.py#L67) 覆盖 seal/rename/current，已有 [`未封存复用保护测试`](../../tests/test_governance_processes.py#L110) 证明活进程和死亡候选都保留复用保护。[`Binding`](../../src/asterstore/reading/_binding.py#L30) 和 [`Polars 适配器`](../../src/asterstore/integrations/polars/_parquet.py#L14) 均从已捕获路径工作。

**建议：** 阶段 B 在已列的 GC 完成重试之外，明确测 managed 已提交 weak→strong 恢复、sealed/installed 未提交恢复、纯复用/空发布，以及一次复用大集合和多次小批 reuse 的控制记录成本。已有 reuse 每次写完整 protection；检查拆分不会自然消除该成本。无需预先承诺优化这些机制。

阶段 A 的测试映射应逐项标明 `missing/extra/symlink/directory` 哪些保持、哪些改变、哪些改成显式检查，不能直接删除旧严格 seal 测试。文档修订至少纳入 publishing、API、compatibility 和相关 core/spec 约束。新增 format/checksum 不代表 protocol 解析可选；新默认允许的状态要能被旧 writer/旧治理正确解释，不能仅因为 JSON 字段不变就宣称所有混用无差异。适配器中原生引擎必需的 footer/data I/O仍属于引擎成本，不计作治理回归。

## 需要用户决定的事项

1. **已提交附加检查：** 推荐默认重试保持身份/持久性语义，显式非 none 检查遇到已提交时采用清晰拒绝并附带提交状态；如希望该调用同时审计已发布数据，应选择带 committed 状态的结果/错误契约。两者都不能承诺检查失败意味着发布未发生。
2. **显式写检查范围：** 推荐最终声明全部唯一对象，含复用对象；同步仍仅针对当前操作创建的字节及依赖控制证据。
3. **none 下缺失新增数据：** 决定弱持久候选是否允许缺失声明文件，以及如何区别最低安装约束与可选 existence；强持久必须履行同步，无法同步时失败。路径/类型边界不因 none 放宽。
4. **extra 的安装与恢复：** 若接受 extra 随整体目录安装，选择 installed cleanup 的“保留未知条目并报告残留”或“拒绝并给出处置路径”，并明确成功发布后的诊断方式。不能只说“不自动 GC”。
5. **公开 API 范围：** 确定第一阶段是否包含 registered 写检查和纯内存 bind；若暂缓某入口，应明确阶段边界，不继续笼统承诺所有读写入口支持。

参数拼写、检查报告/异常、基准的无关键处理和阶段切片属于上述决策后的常规设计工作，不必逐项增加额外批准流程。

## 最小方案修订清单

- 阶段 A 增加候选/已提交状态表、`none × durable × 对象类型/来源` 责任矩阵、公开入口与资源绑定表、extra 中断恢复约定。
- 阶段 C 先完成检查/安装/同步职责拆分及候选诊断，再以这些契约为依据调整原测试；保留 writer 锁顺序与保护记录持久化时点。
- 将检查升级、已安装恢复、已提交强持久重试、extra cleanup 和 registered 默认无外部 I/O纳入具体验收。
- 阶段 D 保持单独后续设计；本次不实施历史压缩、删除控制证据或全局锁调整。

评审所用历史记忆仅辅助定位既有 governance/GC 重试主题；以上结论均已对当前本地源码复核，未将历史 PR/发布状态作为当前状态证据。
