# Issue #21 A 阶段：检查与提交契约

日期：2026-10-13。基线：0.2.0 / `8cf733cb8208d475df38c3ad4e915f466d68e83f`。
状态：C 阶段已按本契约完成本地实现与回归，尚未发布；下述新增参数和类型不属于当前 PyPI 0.2.0。
关联：[workplan](issue-21-workplan.md)、[独立评审](issue-21-workplan-review.md)。

## 公开接口

保留现有返回类型。新增参数均为 keyword-only；不修改固定操作身份或持久化声明。

```python
Check = Literal["none", "existence", "format", "checksum"]

Repository.open(dataset_id, *, publication_id=None, resources=None,
                check: Check = "none", validator=None, checksums=None) -> Binding
Candidate.commit(*, check: Check = "none", validator=None, checksums=None) -> DeclarationRecord
Repository.register(declaration, *, operation_id, expected_generation, durable=True,
                    check: Check = "none", resources=None,
                    validator=None, checksums=None) -> DeclarationRecord
Repository.resume_registration(operation_id, *, durable=True, check: Check = "none",
                               resources=None, validator=None, checksums=None) -> DeclarationRecord
```

`validator: Callable[[Path], None]`，通过时返回 None，失败时抛异常。`checksums: Mapping[str, str]` 为 object_id 到 SHA-256 十六进制字符串；复制参数映射，避免调用方修改影响执行。`resources` 与现有读取的资源根映射类型一致。managed 根不能重定向。registered 检查只访问显式绑定文件，不同步外部字节、不获得删除权限。

`prepare` / `resume` 不增加 check；`resume(...).commit(check=...)` 负责恢复后的本次检查。`seal` 仅固定声明和履行最低安装/同步约束，不接受可选健康检查，不作为检查通过缓存。`bind` 保持纯内存构造，`describe` 保持元数据查询。`Binding.files` 和引擎适配器不追加检查。

`check` 第一阶段只接受一个字符串。format/checksum 都以 existence 为基础，但互不包含。治理现有 `check(level=...)` 保持独立的默认 metadata、汇总报告及缺失 checksum 基准的报告语义。

## 参数与目标解析顺序

1. 校验 check 值与关联参数：format 必须且只能提供 validator；checksum 必须且只能提供 checksums；none/existence 禁止携带这两类无关参数。不强制 registered none 提供 resources。
2. 解析必要元数据，固定本次声明和资源根。open 的对象集合为完整 Binding 的唯一对象集，不跟随后续 current 更新，也不是随后 files(keys=...) 的子集。
3. 写入固定声明后，checksum 的键集合必须恰等于最终对象集；缺项、多项、非 64 位十六进制摘要在读取任何数据进行检查前失败。空对象集接受空映射。
4. 检查最终声明全部唯一对象，包含复用对象；别名不重复检查。按 object_id 排序执行，首个失败终止。只有治理接口汇总全范围问题。

参数集合验证之前可能必须读取控制记录、封存声明或同步新增数据；“检查前拒绝”不承诺零控制 I/O 或零持久化动作。调用方可先 `record = writer.seal()` 获取对象身份，然后使用由可信生产过程/外部基准得到的 object_id 摘要调用 commit；现场对目标算摘要再自比对不能当作历史完整性证据。

## 检查与错误

新增 `DataCheckError(AsterStoreError)`；建议从 errors 模块公开，保留原异常链。字段规范：

| 字段 | 值与含义 |
| --- | --- |
| check | 本次检查模式 |
| code | missing / invalid_type / validation_failed / checksum_mismatch / io_error |
| object_id, path | 首个失败对象与本次解析的路径 |
| dataset_id, publication_id, operation_id | 固定声明身份；无操作身份的纯读取不制造操作 ID |
| commit_state | not_attempted / committed / unknown / not_applicable |
| durability | not_requested / not_completed / completed / unknown / not_applicable |

not_attempted 仅表示本次调用未进入 current 替换，不证明同一身份从未被其他调用提交。committed 表示协调确认该操作已有提交结果。unknown 表示证据无法确认，调用方使用状态接口核对。读取的两类状态均为 not_applicable。

| 失败来源 | 结果 |
| --- | --- |
| 非法检查参数/基准键或摘要格式 | ValueError；非可调用 validator 为 TypeError |
| 资源映射缺失 | 现有 ResourceNotBoundError |
| 数据检查失败、回调常规 Exception、读取 I/O 失败 | DataCheckError；保留原异常原因 |
| generation/身份冲突、退役、放弃、控制证据损坏 | 保留现有领域异常，不包装成数据检查失败 |
| 最低安装约束或同步失败 | 保留现有错误分类，不伪装成可关闭的检查 |
| KeyboardInterrupt / SystemExit | 原样传播，照常释放资源，不吞成检查报告 |

成功仍返回原 Binding / DeclarationRecord，不增加隐式报告持久化。检查通过不保活，也不表示 registered 字节或库外篡改不会随后变化。

## 调度与恢复

候选锁顺序维持候选锁 → 仓库锁。用户回调与内容读取不得在仓库全局锁内执行；不改变 reuse 的保护记录持久化时点。内容检查与提交之间的字节稳定性依赖协作式不可变承诺，不增加对恶意并发篡改的保证。

| 状态 | 检查目标 | 失败后的处置 |
| --- | --- | --- |
| 写入中 | 固定声明后使用私有新文件 + 原复用文件 | 本次不发布；已固定成员不因失败重新开放；需改字节用新操作 |
| sealed 未安装 | 同上，每次显式检查均执行 | 重试检查/放弃，不复用上次通过结果 |
| installed 未提交 | 已安装新文件 + 原复用文件 | 保留恢复证据，检查失败本次不发布 |
| committed current/historical | 原提交的确切对象 | 允许显式检查；失败报告 committed，不回滚；遵守既有历史可用性限制 |
| retired/abandoned | 不借检查重建或复活数据 | 保留既有拒绝语义 |

检查失败后，对写入请求在协调下核对是否已有同身份提交；并发 registered 请求可能已经成功。此核对不能覆盖原始检查异常：状态核对失败时携带 unknown 及诊断原因。未发现提交时仅报告 not_attempted，不承诺返回后不会有并发提交。

成功检查后的提交仍在协调下复核请求/generation/权属/退役状态。已有提交重试继续同步该操作所承诺证据，不回退 current。检查失败可不完成本次 weak→strong 升级，此时报告 not_completed；不能把“已提交”误报成“本次强持久升级完成”。

## 最低安装与同步责任映射

| 现有工作 | C 阶段处置 |
| --- | --- |
| _verify_files 的根目录与声明新路径普通文件约束 | none 下保留；逐声明路径及父链核对，拒绝缺失/链接逃逸/目录/FIFO，不递归发现额外文件 |
| _verify_files 的整个 files 目录 rglob 与集合相等 | 移至显式候选盘点，默认不执行 |
| FileSet 结构、身份、generation、复用 protection | 保留事务边界，不受 check 控制 |
| 新数据 fsync、request/sealed 与依赖控制记录同步 | 由 durable 控制；从检查器分离，不能拿全部检查目标作为同步目标 |
| rename 后目的父目录和源目录同步 | 保留顺序与失败传播 |
| operation/object/history/current 写入及目录同步 | 保留创建证据先于唯一可见点 current 的顺序 |
| 已提交 _sync_result、强持久 install 恢复 | 仍验证固定证据与同步新字节；不制造缺失权属记录 |
| 复用数据字节、registered 外部字节同步 | 不执行；复用继承原始字节持久性 |

具体去重必须由边界故障测试证明，不因此前 seal 返回便假设跨进程或弱→强同步已经完成。

## 未声明文件：最低可用治理路径

C 阶段已增加只读 `governance.inspect_candidate(operation_id)`：返回固定操作、private/installed/absent 位置、候选状态、声明新文件、缺失项、未声明项和异常类型；不跟随 symlink，不认领文件，不自动清理。覆盖已提交候选的 installed 命名空间，不能把“不允许 abandon 已提交候选”等同于不能诊断。取得候选锁后再短暂获取仓库协调信息；拒绝活动写入冲突，输出观察时点而非永久健康保证。CandidateInspection.status 复用现有 CandidateStatus；结果模型从 governance.inspection 导出。

首版保持现有 installed cleanup 的拒绝行为及 complete 含义，不引入未经设计的部分清理：

1. extra + installed 未提交：显式放弃，盘点输出残留清单；cleanup 拒绝时说明未删除。调用方核实后在库外移走未知条目，再预览/执行同一候选的清理。库不替调用方确认未知字节的删除权。
2. extra + 已提交：不能 abandon/候选 cleanup。盘点报告未知项；普通 GC 仍只处理有证据且不受保护的对象。调用方在独立维护窗口自行处置未知项，不删除控制证据。
3. private：保留当前显式 abandon 后私有候选 cleanup 的授权模型；不因检查模式扩大 installed 删除范围。异常文件类型继续明确拒绝。

诊断与文档必须能够完成上述恢复步骤后，才允许默认取消全面扫描。自动部分清理作为未来独立扩展，不是 C 的隐含承诺。

## 测试与兼容映射

| 既有证据 | 保持/新增 |
| --- | --- |
| test_seal_checks_declared_files_only_at_write_boundary | missing/symlink/directory 保持最低拒绝；extra 改为默认允许并由候选盘点报告 |
| test_no_data_or_object_registry_io_in_open_and_selection | none 下原样保持；显式检查后 files 仍零 I/O |
| test_reuse_never_reads_or_syncs_old_data | none 原样保持；显式检查允许读旧数据但禁止新增同步 |
| test_strong_retry_syncs_weakly_committed_new_bytes | 保持；加检查失败时未完成升级状态 |
| test_every_persistent_boundary_is_recoverable / managed process tests | 保持；增加 sealed 后升级检查、installed 检查目标、已提交失败不回退 |
| test_unsealed_live_and_killed_writer_both_protect_reuse | 保持保护时点和锁顺序 |
| test_retention_acquisition_and_gc_are_serialized | 保持，过期检查不授权 GC |
| checksum/registered/format inspection tests | 旧 API 不改；新读写严格基准键集合、资源映射、回调异常独立覆盖 |

新增验收：并发相同 registered 请求的状态确认；extra + rename 后故障 + abandon/清理恢复；extra + 成功发布 + GC；检查模式与 durable 交叉；空声明、别名去重、format/checksum 切换；候选诊断不跟随链接、不产生数据删除。

C 必须修改 publishing、API、compatibility 及相关规范的严格盘点承诺并写升级说明。本计划不改变 JSON 格式，但旧 writer/cleanup 对新允许 extra 状态可能拒绝；不能承诺新旧工具行为完全一致。历史压缩和 required_features 迁移归 D 阶段。
