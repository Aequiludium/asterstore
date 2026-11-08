# 清理

`dataset.clean(dry_run=True)` 列出失效工作目录的操作 ID，`dataset.clean()` 删除这些目录并返回同样形式的结果。

清理只进入 `.asterstore/work`，保留当前 head.undo 引用的撤销材料。不扫描、删除正式数据，不删除旧因子定义，不推断分区是否有效。

- 数据集写锁防止与发布、回退交错。
- active.json 存在时抛出 RecoveryRequiredError，需先在停读窗口恢复。
- 清理中断可再次调用；有效撤销材料不受影响。
- 清理不触碰正式文件，因此不要求停读；不会自动恢复或修改当前发布状态。
- 两次清理之间过期备份继续占空间；应在成功发布后的维护流程安排 clean。

数据删除通过 batch.delete 明确声明。主动回退后，先前备份也成为失效材料；clean 不会重新建立回退机会。

## 整体删除数据集

```python
# 先停止此数据集的全部读写任务、排队操作和延迟查询。
repo.drop_dataset("factor:basic/capital/Capital", expected_revision=12, quiescent=True)
```

`drop_dataset()` 删除整个登记的独占目录及注册，包含全部历史文件、当前元数据、暂存和撤销材料，不产生新的撤销备份。AsterStore 不解释因子的上线/下线状态；这类业务约束由调用方负责。

失败后在同一维护窗口重试原调用即可续删；不要用 `clean()` 或 `Dataset.recover()` 代替。已持久化删除意图后，续删不依赖可能已删除的 head，也不重新检查 revision。完全不存在时直接成功。完成后不保留删除记录；需要再次使用时显式创建新数据集，并丢弃此前的所有旧句柄。

只在显式整体删除时遍历待删目录，普通读取、发布、回退和清理的访问范围不变。
