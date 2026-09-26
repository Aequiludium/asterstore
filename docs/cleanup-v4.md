# v4 失败候选物理清理

状态：2026-09-26，开发实现。适用于显式启用 `managed` 和 `lifecycle` 的 v4 仓库。本功能处理已放弃候选自己的输出；已提交数据的退役与回收使用 [governance.collect](governance-v4.md)。

## 使用与授权边界

先退出 writer 上下文、关闭原生写入器与文件句柄，再执行：

```python
repository.governance.abandon("failed-operation")
preview = repository.governance.preview_cleanup("failed-operation")
result = repository.governance.cleanup("failed-operation")
# 若调用报错或进程中断，使用同一个候选操作 ID：
result = repository.governance.resume_cleanup("failed-operation")
```

`abandon` 是不可逆的控制状态变化，本身不删除文件。`cleanup` 不隐式放弃候选；尚未放弃、已经提交（包括历史与退役发布）的候选没有清理资格。未知候选也不会因清理请求产生空候选目录。

一个候选永久对应一份清理计划，直接使用候选 `operation_id`，无第二套清理身份。首次 `cleanup` 持久保存固定文件清单；再次调用与 `resume_cleanup` 均只恢复原计划。没有原计划时 `resume_cleanup` 报 `PublicationNotFoundError`。

| 返回类型 | 字段与含义 |
| --- | --- |
| `ManagedCleanupPreview` | operation_id、location、files、planned；有计划时返回原清单，否则只扫描而不写计划 |
| `ManagedCleanupResult` | operation_id、location、deleted_files、missing_files、complete |

`location` 为 `private` 或 `installed`，文件名均相对于对应候选的私有 files 根或受管安装根。`complete=True` 只说明原清单已处理完；重试不会扫描新增文件，完成后新增或被库外重新写回的文件不会被再次删除。完整性审计、未知孤儿认领和目录压缩不属于这个接口。

## 两种创建权属

1. **private**：有效请求和放弃记录证明 `.asterstore/managed-candidates/<operation-token>/files/` 是候选私有输出命名空间。首次建计划时枚举其中普通文件，包含尚未封存的原生写入器临时输出；无需伪造完整发布声明。未写出的声明路径不算实际残留。
2. **installed**：提交可能在安装新文件后、替换 current 前失败。只能依据同一请求的封存声明，选择 locator 前缀属于该操作的新文件；复用对象的 locator 属于来源操作，绝不进入此计划。实际安装目录若含封存清单外的文件，首次清理拒绝执行。封存声明允许记录已经缺失的新文件，其结果为 missing。

同时出现私有和安装命名空间、安装输出缺少封存证据、路径含符号链接或特殊文件、控制记录不一致，均拒绝首次建计划。已安装对象存在创建记录却与私有清理计划冲突，也视为损坏。控制面遍历会核对发布、对象创建记录、候选保护、放弃及已有治理计划；候选创建的对象被其他发布或活动候选使用时拒绝清理。

清理只 unlink 确切普通文件，逐级检查受管祖先目录，不跟随符号链接，不递归删除目录。空目录、writer.lock、请求、封存、对象身份、操作预留及治理证据全部保留。registered 文件从未获得删除资格。

## 锁与持久化顺序

始终先取得候选独占 writer.lock，再取得仓库 gc.lock；等待写入者时不持有仓库锁。preview 使用仓库共享锁，执行使用独占锁。候选放弃后，符合协议的写入者不能恢复或重新提交，GC 与其他写入者也不能在检查和删除之间改变权威记录。

删除前强制同步创建、放弃、固定计划和相关控制证据，不继承原写入的 `durable=False`。每个 unlink 都同步其父目录。累计结果在 1、2、4、8……个文件处以及完成时持久化，避免每删一个文件都重写整个累计结果。

进程可能在 unlink 后、进度保存前死亡：重试将未记账但已不存在的文件记录为 missing。这是保守恢复语义，不能据此重建精确的历史删除次数。已保存的结果不重复执行，完成结果可重复读取。若最后一次原子替换已成功但同步报错，重试仍会重新同步已保存的完成记录和控制证据。中途任何错误都可能发生在部分文件已删除之后，应使用原候选 ID 恢复。

普通 `open`、Binding 选择与引擎读取不增加清理检查、数据 stat 或内容校验。显式清理的成本包括全仓控制面遍历及首次目标命名空间扫描；当前仍保留所有控制历史，不承诺任意历史规模的恒定开销。

## Wire format 与兼容

```
.asterstore/managed-candidates/<operation-token>/
├── request.json
├── protection.json
├── sealed.json                  # 可选，installed 清理必需
├── abandoned.json               # 必需，必须与 request 完全一致
├── cleanup-plan.json            # immutable
├── cleanup-progress.json        # atomic replace
├── writer.lock
└── files/                       # private 路径根，安装后整体移走
```

新增 `managed_cleanup_plan`、`managed_cleanup_progress` 两种严格 v4 JSON 记录，纳入打包 Schema 和 golden fixtures。plan 内嵌完整原请求、location 及不重复且无文件祖先冲突的路径清单；progress 身份与路径集合必须匹配计划，complete 必须覆盖整个计划。

仍使用既有 `lifecycle` 必要功能，不自动升级现有仓库。尚不认识这些新记录的上一开发实现会因未知候选控制文件而拒绝治理扫描；它不会跳过新证据继续 GC。原有 `abandoned.json` 已阻止旧生命周期写入者恢复提交。v3 的候选清理接口和记录独立保留；本轮不修改其含义。开发协议和 API 尚未冻结。

本地测试覆盖异常注入、SIGKILL 后恢复、清理竞争及等待写入者时的锁顺序；这不是 NFS、多主机协调或真实断电测试。协作式文件管理不防护绕过库协议的并发写入或遗留可写句柄。
