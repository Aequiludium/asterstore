> 历史文档：以下描述格式 1，不适用于当前格式 2。当前入口为仓库 README、docs/api.md 和 docs/protocol.md。

# 控制历史归档与压缩

此功能自 0.3.0 提供。只有显式调用 compact 才启用新的 `control_archive` 必要功能；普通初始化和读写不自动迁移。

## 使用与范围

```python
# 先停止所有 writer、治理修改和历史查询进程，并释放候选句柄。
result = repo.governance.compact("archive:2026-10", quiescent=True)
print(result.records, result.saved_bytes)
# 失败后重新建立维护窗口，用相同 operation_id 恢复。
result = repo.governance.compact("archive:2026-10", quiescent=True)
```

`quiescent=True` 是调用方对维护窗口的声明，不是自动停写功能。执行取得已有 gc.lock 和已有 candidate writer.lock，锁忙立即报 RepositoryBusyError；不创建或删除锁。仍须停用旧版本进程，不能靠改 marker 撤销旧进程已缓存的状态。

有待恢复发布、未完成候选、未完成 GC 或 cleanup 时，抛 CandidateStateError；已放弃但未清理的候选也会阻止压缩。先用原 API 完成恢复/放弃/清理，再执行 compact。已释放 retention、已退役发布以及已完成治理任务的身份和结果继续保留。

当前实现把终态控制正文压缩到不可变归档段，在原路径留下小型引用。完整 current、marker、对象身份和 retention 记录原样独立保留。默认 current open 仍只读取 marker + current，Binding.files 不新增 I/O。历史查询、写入旧请求检查和治理会解析所需归档块；不会把全仓归档加载到 current 读取路径。

没有请求过期、身份复用或历史删除；原有历史访问能力、退役/放弃拒绝和弱→强恢复语义保持。普通数据文件和未知文件不被这个命令删除。原候选的完整创建声明保存在归档块中，原路径引用仍承担身份入口，不能手动删除候选控制目录或其 writer.lock。

## 固定格式

沿用 format_version=1，通过 marker required_features 追加 `control_archive` 启用；该功能要求 lifecycle。未支持它的程序必须拒绝仓库。旧版本实际拒绝行为有冻结的 0.2.0 解码器测试。

```
.asterstore/
  format.json                       # 显式声明 control_archive
  datasets/<dataset>/current.json   # 始终完整声明
  archives/<content-sha256>.pack    # 不可变，多个独立 zlib 块顺序连接
  archives/<token(compact-id)>.json # 固定 control_checkpoint
  ...原有控制路径...                 # 内联原记录或 archive_reference
```

`archive_reference` 的严格字段为 format_version、kind、archive、offset、length、size、sha256。archive 是整个 pack 的 SHA-256；offset/length 指定压缩块范围，size 是解压长度，sha256 是完整原始正文摘要。三项 extent 只接受非布尔整数，范围在 0..2^63-1，length/size 非零。块读取核验长度、zlib 流结束、无多余尾流和正文摘要；损坏、丢失、链接或特殊文件不能降级为“操作不存在”。禁止嵌套引用。同一次治理 inventory 内可复用已校验的完全相同归档块，缓存不跨调用或协调边界。

只允许 registrations、datasets/history、终态 candidate 的 request/sealed/protection/abandoned/cleanup 记录、retired 和 collections 记录引用归档。current、对象记录、retention 和 marker 不得变成引用。完全相同的原始字节在一个 pack 内只存一次；不同 pack 之间不再压缩，也不实施语义级跨记录去重。

`control_checkpoint` 保存 store_id、operation_id 和完整的路径→reference 映射。路径严格限制为上述控制路径，必须引用单个非空 pack；checkpoint 名字由 operation_id 的 token 决定。它是固定迁移范围和恢复证据，原路径引用加不可变正文是每条逻辑控制记录的权威表示，没有可丢弃索引参与删除授权。

Schema 和 codec 已包含两种新增记录，见 [协议 schema](../src/asterstore/metadata/schemas/store.json)。禁止依赖未知字段自动推断新布局。

## 持久化与恢复

压缩始终采用强持久化，不提供 durable=False。它仅同步控制证据，不把先前弱持久发布的数据字节升级成强持久；调用方仍用原发布重试进行升级。

1. 在维护窗口内验证全仓原有权威关系，确认没有未完成操作、未知控制条目或损坏证据。
2. 构建待迁移记录和不可变 pack，计算引用、checkpoint、pack 的长度及首次 marker 增长。没有正向控制字节收益则抛 ValueError，不修改仓库。
3. 先同步全部继承控制证据（包括之前弱持久的对象身份和 current，不同步数据字节），再原子更新并同步必要功能 marker、持久写入内容地址 pack，最后持久写入固定 checkpoint。新 marker 但尚无归档目录是合法过渡状态。
4. 验证整个 pack 内容地址，并逐条确认全部计划目标的逻辑正文与归档原文一致；完整预检通过后才开始替换。
5. 在每条原路径原子替换引用并同步；全部完成后再次同步引用路径及祖先目录。中断可以留下内联正文与引用并存，两者代表相同完整逻辑记录，不需要全仓指针切换。

| 中断位置 | 同 ID 恢复行为 |
| --- | --- |
| marker 之前或之后，checkpoint 尚未写成 | 没有逻辑记录切换。重新检查当前仓库并规划；若期间有新发布，可以生成不同内容地址 pack |
| pack 已写、checkpoint 尚未写 | 旧 pack 保留且不覆盖；新计划可以引用新 pack，遗留段不被当作删除授权 |
| checkpoint 已持久化 | 固定原目标集合；预检全体正文及 pack，通过后继续逐条替换，不纳入后来出现的记录 |
| 部分引用已替换 | 按 checkpoint 恢复；已经替换的条目验证后同步，剩余条目继续原子替换 |
| 全部完成后的重试 | 验证同一固定计划并同步，返回同一范围的结果；不会把新历史混入旧任务 |

即使执行返回错误，也可能已经启用 feature 或替换了一部分引用；不得据异常推断未发生修改。重试仍要求维护窗口及无未完成任务。损坏权威记录时停止，不能自动丢弃 checkpoint、回退旧 marker 或猜测重建操作。

归档段和 checkpoint 不自动删除，包括 checkpoint 之前中断产生的遗留段。保留不可变旧段避免历史读者持有旧定位时失效；本版仍要求迁移期间停止历史查询。降级不提供“改回 marker”的捷径，必须向前恢复；未来若需要导出旧格式，应另行实现完整验证的导出流程。

## 成本与结果

CompactionResult 从 `asterstore.governance` 导入，包含 operation_id、records、original_bytes、reference_bytes、archive_bytes、checkpoint_bytes、complete 和 saved_bytes 属性。saved_bytes 是该固定范围原文减去引用、pack 和 checkpoint 长度；不含 marker 长度变化、目录/分配单元开销、既有遗留段。重复调用返回的是同一计划的收益，不是本次额外节省。

原路径身份入口保留，所以首次正常压缩增加一个 pack 和一个 checkpoint 文件；**这版降低字节，不减少小文件数量，不把 GC 变成常数成本**。新历史、新身份、checkpoint 以及失败遗留段仍会增长。历史查询增加块读取/解压，治理仍重建完整关系；实测必须包含可能的退步。

本实现有意用逐条等价替换替代初稿的全仓指针切换，避免引入全仓查找索引和双权威合并。后续若要减少身份文件或提供有限重试窗口，需要另外改变身份和查询协议，不能删掉本版引用来模拟。

验证与分项实测见 [D 阶段交付报告](validation/issue-21-d-2026-10-13.md)。
