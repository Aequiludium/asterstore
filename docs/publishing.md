# 发布与回退

## 首次发布

Repository 构造函数只解析根路径。显式调用 `initialize()` 创建仓库；首次初始化要求空目录。再次调用会检查仓库标记，并按 durable 参数完成同步。

一次发布在 Draft 上下文中完成：

```python
with repo.prepare("prices", expected_revision=0) as draft:
    draft.allocate("data", relative_path="data.parquet").write_bytes(payload)
    candidate = draft.seal()
    validate(candidate)  # 调用方的检查；检查失败应抛出异常。
    head = draft.commit()
```

此片段中的 payload 和 validate 由调用方提供。实际写入器必须在 seal 前关闭。完整示例见 [two_versions.py](../examples/two_versions.py)。

seal 记录文件归属、安装默认 staging 文件并固定成员清单，但不改变 current。候选 Binding 可用于检查完整数据。commit 才是发布生效点。退出上下文不会自动提交，也不会自动清除失败产物。

## 增量更新

进入空 Draft 后先调用 `reuse_current()`，再通过 allocate 替换或增加成员，通过 remove 移除成员。省略 reuse_current 即可构建完全替换的成员集。

```python
binding = repo.open("prices")
with repo.prepare("prices", expected_revision=binding.revision) as draft:
    draft.reuse_current()
    draft.allocate("day:2026-10-14", relative_path="date=2026-10-14/data.bin").write_bytes(b"data")
    draft.seal()
    head = draft.commit()
```

成员移除和替换不立即删除已发布文件。它们仍可能被 rollback 或已有读取使用，统一交给维护清理。

业务元数据也可以写入普通成员。`draft.version_id` 在发布前已确定，可以写入元数据；更新数据时应一起更新对应的业务说明。

外部输出可用 adopt 原位接管；新文件可用 allocate(path=...) 指定存放位置，详见[路径与归属](paths.md)。也可复制到默认 allocate 返回的路径。新文件封存时必须只有一个硬链接；复用使用清单引用，不通过外部硬链接导入。seal 后不得修改文件或继续使用仍可写的文件描述符。

## 并发与失败

同一数据集的修改锁覆盖整个 Draft 上下文。已有状态参与计算时，应传入它的 revision；首次发布可要求 revision 为 0。不传 expected_revision 时，以取得锁后的 current 为基线。

任何 seal 或 commit 异常都会终止该 Draft。提交异常不一定意味着未发布：head 替换后，后续同步仍可能失败。保存 version_id，重新 describe：

- current 等于该 ID：该版本当前已生效。
- rollback 等于该 ID：该版本曾生效且仍被保留。
- 两者都不等：仅凭当前 head 无法证明该请求从未成功，尤其是其他发布已经继续进行时。

观察到 head 不等于确认同步已成功。需要持久性保证时，应先处理同步故障。不要在失败 Draft 上重试提交；成功 commit 在同一个仍存活的 Draft 内可重复返回结果。

## 回退

```python
head = repo.describe("prices")
head = repo.rollback("prices", expected_revision=head.revision)
```

回退要求 rollback 存在且 revision 未变化。成功后 revision 增加，rollback 为空。已经开始的读取仍绑定原版本；后续 open 读取回退后的 current。

每次 commit 都推进两个槽位。库不识别计算批次或每日任务；一次 commit 就是一次发布。

prepare 和 rollback 默认 `durable=True`。关闭同步仍保留原子切换，但不保证机器故障后的持久性。涉及自定义路径的 seal 仍同步归属清单，以免失去文件回收依据。
