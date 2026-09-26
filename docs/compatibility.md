# 0.1 API 与协议冻结决议

状态：2026-09-26，`0.1.0rc1` 本地候选。本文冻结首个有限功能版本的接口与持久语义基线；不表示候选已上传或完整目标规范已交付。范围由 [release-scope](release-scope.md) 定义，机器基线为 [contracts/0.1.json](contracts/0.1.json)。

## 决议与兼容窗口

1. **v4 是推荐入口。** Declaration、显式 FileSet、registered/managed、固定保留、回收及候选清理纳入首版；未实现能力没有兼容承诺。
2. **0.1 系列保留旧接口。** 顶层已有导出均有清单；v3 类型与 prepare/retention/inspection 保留原义，不自动映射到 v4。0.1.x 不删除这些接口，也不增加每次调用的弃用警告。未来移除须在后续次版本给出弃用说明和迁移路径。
3. **版本不同于格式。** `0.1.0rc1` 及后续正式 0.1.0/0.1.x 的冻结范围遵循本契约。历史 `0.1.0.dev0` 开发快照不获得追溯兼容保证，不混用其治理进程。需要接入的旧目录应先在副本上用对应测试和显式治理操作核对；本版没有自动迁移器。
4. **补丁保持语义。** 0.1.x 不改变已有参数含义/默认值、返回字段、枚举值和可捕获错误层次，不改变保留、权属或删除授权规则。可修复实现对既有规范的违背；必须带回归证据并在 CHANGELOG 说明。错误消息文本、内部模块位置、JSON 排版和测量耗时不属于稳定接口。
5. **格式升级必须显式。** 严格解码器不接受未知字段和治理功能，因此即使看似“新增字段”也不能直接写入既有格式。新记录若改变可达性、删除证据或恢复解释，必须升级格式或引入旧实现明确拒绝的新 required feature，并提供兼容矩阵；不能只保留数字 v4 而改写同一记录的意义。

候选阶段如发现必须破坏契约的问题，使用新的候选版本并记录差异，不能悄悄替换同版本制品。正式 0.1.0 发布前需要再次核对该基线；冻结不妨碍修复正确性问题。

## 公开 API 范围

机器基线覆盖顶层导出、类构造签名、公开方法/属性、typing overload、数据类公开字段及只读性、枚举值、异常继承层次，以及可选 `scan_parquet` 签名。返回对象的工厂类型不承诺直接构造：Candidate/ManagedCandidate 从 Repository 获取，Governance/Retention/Inspection 从服务属性获取。其内部可写属性不是调用者可自由修改的协议。

`Repository.open` 保留 `Binding[Publication] | Binding[Declaration]` 的兼容返回；新应用初始化 v4 后按 Declaration 使用。`Binding.files()` 返回去重的全量对象路径，显式 keys 按请求顺序允许重复成员；任何绑定都不隐式获得字节保留。

v4 状态查询是持锁时的观察，不能替代保留；`managed_status`/`registration_status` 与其结果字段纳入契约。`CandidateAbandonedError.candidate_id` 及 `PublicationRetiredError.candidate_id` 在 v4 调用中携带相关 operation_id，保留已有属性名，不为改名制造首版兼容分叉。文件系统和引擎异常仍可能原样传出，不承诺一切异常都属于 AsterStoreError。

子包为内部协作导出的辅助函数、以下划线开头的实现、候选内部构建器、基准探针不是下游稳定入口。对象构造与公开 getter 不能被解释为额外的保留、持久性或校验保证。

## 冻结的持久语义

| 项目 | 决议 |
| --- | --- |
| 核心版本 | 新声明写入使用 v4；保留 v1/v2 读取及独立 v3 生命周期 |
| 必要功能 | registered；registered+lifecycle；managed+registered；lifecycle+managed+registered。含 managed 必须绑定 managed_resource_id |
| 身份与数值 | 逻辑 ID 为 1–4096 UTF-8 字节；不归一化；generation/revision 按 v4 的有符号 64 位边界，布尔和浮点拒绝 |
| 权威性 | current 原子替换是发布可见点；操作记录、候选 seal、构造对象本身不等于提交 |
| 对象身份 | Store 范围内 object_id 固定关联 locator/稳定性/创建权属；不通过路径相似或内容相等自动去重 |
| 重试 | 同 operation_id 固定内容和 generation；冲突不自动变基；成功重试不回退 current |
| 保留 | METADATA 不保护字节，registered 无 OBJECTS 权限；具名固定引用按 revision 条件释放 |
| 删除 | current/对象保留/候选复用保护优先；先持久退役再删除；原计划恢复不扩大范围 |
| 完成重试 | 重新验证并同步控制证据，返回原保存结果；不把重试当作零成本查询 |
| 清理 | 永久 abandon 后才按固定候选清单清理；不删除未知数据或重用操作身份 |
| 历史成本 | 保留控制身份与证据；不承诺任意规模常数时间，暂不压缩、不设自动过期重试窗口 |
| 持久性 | durable=False 接受弱持久性；治理删除前强同步控制证据；共享对象不因复用被重新同步 |

JSON Schema 与正反 golden fixtures 是格式证据。实际语义还由 codec、状态机和故障测试共同保证；schema 摘要匹配不能证明恢复、权属或可达性正确。

## 可执行冻结检查

```bash
uv run --locked --extra polars python tools/check_contract.py
uv run --locked --extra polars pytest
```

检查器比较导出与签名、字段、错误、枚举、功能组合及 Schema/fixtures 摘要。它只报告差异，不自动覆盖基线；正常 PR 不应通过“重生成快照”消除失败。兼容新增也需明确评审清单变化；破坏性修改遵循上述版本门槛。行为正确性继续由既有生命周期与故障测试承担，不用签名快照替代状态机验证。

新增回归用例验证检查器确实拒绝参数、异常层次、枚举、格式证据和旧导出差异，并且不改写基线。CI 和发行检查都运行冻结检查；普通运行时不加载这些文件，不增加读取成本。
