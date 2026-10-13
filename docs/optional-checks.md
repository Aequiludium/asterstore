# 可选读写检查（未发布）

以下接口是当前开发实现，尚未进入 PyPI 0.2.0。读、写与运维治理共享对象检查原语，各自决定检查时机与范围。

| check | 行为 |
| --- | --- |
| none（默认） | 无可选数据健康检查 |
| existence | 检查声明全部唯一对象存在且为普通文件 |
| format | existence 后调用 validator(path)，异常表示格式失败 |
| checksum | existence 后与可信 object_id → SHA-256 映射比较 |

format 与 checksum 互不包含，第一阶段仅接受一个选项。validator 只能随 format 提供，checksums 只能随 checksum 提供；基准键必须恰好覆盖全部对象，空声明可传空映射。无关参数、缺失基准、多余键或非法摘要不静默降级。

```python
binding = repo.open("prices")
binding = repo.open("prices", check="existence")
record = writer.commit(check="format", validator=validate_parquet)
with repo.resume("operation-id") as writer:
    record = writer.commit(check="existence")
```

每次显式检查覆盖固定声明全部唯一对象，包括复用对象，别名去重。后续 Binding.files(keys=...) 不补做检查。registered 注册/恢复的显式检查需传 resources；默认注册仍不访问外部字节，强持久注册也不对外部数据执行同步。seal 固定成员后仍允许 commit 请求更强或不同的检查；已封存状态不是健康证明。

格式回调需只读，并在返回前结束检查；不要改写封存数据。回调在仓库全局锁外运行。协作式不可变假设仍适用：检查通过不能阻止库外修改或未来 GC；读取不隐式 retain。具体调用示例见 [optional_checks.py](../examples/optional_checks.py)。

## 失败与提交状态

参数错误使用 ValueError/TypeError，未绑定资源使用 ResourceNotBoundError。数据检查失败抛 DataCheckError，包含 check/code/object_id/path、固定发布身份、commit_state 与 durability。常规回调异常作为 validation_failed 并保留异常链；KeyboardInterrupt/SystemExit 原样传播。控制损坏、冲突、退役和安装/同步错误保留原异常分类。

- 读取检查：commit_state、durability 都为 not_applicable。
- 本次调用尚未尝试发布且未观察到已有提交：not_attempted，不保证并发调用不会随后提交。
- 已观察到同身份提交：committed；检查失败不撤销发布。
- 无法确认状态：unknown，并保留 state_error；使用状态查询和恢复接口继续处理。
- 写检查失败时 durable=True 对应 not_completed，表示本次强持久动作未完成；False 对应 not_requested。不能由 committed 推断本次持久性升级成功。

## 候选盘点与未知条目

`repo.governance.inspect_candidate(operation_id)` 不创建目录/锁文件、不修改数据、不自动保留。与活动写入/治理冲突时抛 RepositoryBusyError。结果包含 status、location、observed_at、declaration_known、declared_files、observed_files、missing_files、undeclared_entries、issues。路径均相对候选 files 或安装命名空间。未封存时只能报告观察文件，不能判定其是否已声明。

盘点不跟随符号链接。候选锁排除写入/cleanup，扫描期间 GC 仍可能回收已安装对象，因此结果仅为观察，不是永久稳定快照或删除依据。

默认提交只验证声明新文件和父链的最低安装约束，不递归寻找额外文件。额外条目可能随目录安装：

1. 未提交安装候选：先 abandon，盘点未知项；installed cleanup 会拒绝没有创建证据的条目并保持原有语义。调用方核实后自行移走未知项，再 preview_cleanup/cleanup。
2. 已提交候选：不能 abandon 或走候选 cleanup。盘点可报告未知项，普通 GC 不会自动删除它们；调用方在独立维护窗口自行处理。
3. 私有候选：保留原有显式放弃后清理规则，异常文件类型继续拒绝。不将私有目录清理授权扩大到安装目录。

可选检查本身不改变磁盘格式，也不提供自动部分清理。独立 D 阶段已实现显式 [控制历史压缩](control-compaction.md)，只有调用 compact 才启用新的必要功能。
