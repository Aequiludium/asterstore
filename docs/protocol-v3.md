# 受管仓库协议 v3：独立逻辑身份

状态：2026-09-25，已实现的开发协议；公开 API 与长期格式仍未冻结。这是[目标规范](spec/README.md)的第一项落地：拆分逻辑身份与物理路径，并建立不兼容变更的版本门槛。registered、Member/Layout、附件、Release/Pointer 和分层保留尚未实现。

## 版本与读写资格

新仓库标记为 `{"format_version":3,"kind":"repository"}`。所有控制记录，包括嵌套的候选及发布记录，都必须与仓库版本一致。未知版本、未知 kind、重复 JSON 键、缺失或额外字段一律拒绝。读取旧格式后重新编码保留原版本，不转换身份或能力。

| 操作 | v1 | v2 | v3 |
| --- | --- | --- | --- |
| `Repository.open`、显式 refresh | 支持原语义 | 支持原语义 | 支持 |
| `candidate_status` | 支持 | 支持 | 支持 |
| `retention.get/open` | 没有引用格式 | 支持已有固定引用 | 支持 |
| 发布、恢复提交、引用变更、回收、候选放弃和清理 | 拒绝 | 拒绝 | 支持 |
| 回收预览、候选残留全量诊断 | 拒绝 | 拒绝 | 支持 |

旧仓库写入在创建锁或其他文件前抛出 `RepositoryUpgradeRequiredError`。状态查询和按引用读取可能创建协调锁文件，但不修改权威 JSON 或数据对象。普通 `open` 不创建锁。

没有自动升级，也没有提供迁移命令。不要只把旧 marker 改成 3：记录、嵌套记录、恢复证据和正在运行的写入器必须一起处理。旧 v1/v2 写入器不认识 v3 marker，应在改变仓库前拒绝。新库继续读取旧格式，不代表新旧库可以共同写一个仓库。

## 三种不同的字符串

| 字段 | 规则 |
| --- | --- |
| `dataset_id`、`publication_id`、引用 `name` | 不透明逻辑身份；精确比较 UTF-8 文本 |
| 候选/操作 ID | 32 位小写十六进制 token；属于内部协调身份 |
| `ObjectRef.key`、候选输出 key | 物理相对路径；继续使用原有严格路径语法 |

逻辑身份必须非空、可编码为 UTF-8，不含 Unicode Cc 控制字符（U+0000–001F、U+007F–009F）。v3 持久记录的每个身份上限为 **4096 个 UTF-8 字节**。允许冒号、斜杠、反斜杠、点段和空格；不 trim、不折叠大小写、不做 Unicode 归一化。例如 `batch:table` 和 `../research:run` 都是合法身份。

内存 `Dataset/Publication/Reference` 不绑定磁盘版本，因此只检查文本合法性；长度上限在 v3 控制记录边界执行，`prepare` 会在任何 I/O 前检查。v1/v2 仍使用旧名称语法和原有无统一字节上限的解释，不能用改 marker 的方式使旧记录获得新语义。

物理路径仍拒绝绝对路径、空段、`.`、`..`、反斜杠、冒号和不可打印字符。允许逻辑 ID 包含这些字符，不会放宽文件定位或删除范围。当前 `ObjectRef.key` 仍是物理键，尚未实现独立的逻辑 Member。

控制路径按原始 UTF-8 字节的 SHA-256 十六进制 token 定位：数据集目录以 dataset ID 为输入，history 文件以 publication ID 为输入，reference 文件以引用名字为输入；读取时核对记录的原始身份。token 不是对象内容 hash，不读取数据文件。

```python
from asterstore import Dataset, Repository

repo = Repository("./new-store")
with repo.prepare(
    Dataset("market:StockQuote"),
    publication_id="sample:run-001:events",
) as candidate:
    candidate.write_bytes("part.csv", b"price\n12.5\n")
    candidate.commit()
```

## 记录、状态与生效点

v3 沿用 [v2 的逐字段记录与目录布局](protocol-v2.md)，将 `format_version` 统一改成 3，并使用上述逻辑身份语法。字段、对象归属、历史能力、锁顺序与持久性约定保持原有含义。

| 过程 | 状态变化与权威生效点 |
| --- | --- |
| 发布 | writing → prepared → committed；封存固定成员，原子替换完整 current 使发布可见 |
| 引用 | absent/released → active → released；revision 条件更新 reference，active 为奇数、released 为偶数 |
| 回收 | planned → running/blocked → complete；持久计划固定范围，先退役再逐文件删除，恢复重新核对保护关系 |
| 候选放弃 | 未提交候选 → abandoned；原子替换 state 后永久禁止提交 |
| 候选清理 | 固定 plan → progress → complete；仅处理该候选私有路径，完成后重试不重复删除 |

这不是新增跨数据集事务或恢复语义；完整边界分别见[发布协议](protocol.md)、[保留设计](retention.md)和[候选清理](candidates.md)。

## Schema 与可执行约束

机器可读 [JSON Schema](../src/asterstore/metadata/schemas/v3.json) 使用 Draft 2020-12，覆盖当前十类控制记录，随 wheel 和 sdist 一起发布。安装后可通过 `importlib.resources.files("asterstore.metadata").joinpath("schemas/v3.json")` 读取。所有 `$ref` 均为文件内部引用，无远程 Schema 依赖。

Schema 负责结构、类型、必需字段、版本、基本身份/路径语法和枚举；Python codec 额外负责以下约束：

- UTF-8 **字节**上限；Schema 的 `maxLength` 是字符数，不能代替字节计数。
- JSON 整数必须以整数形式解码，拒绝 bool 和浮点数；拒绝重复键、NaN/Infinity、无效 UTF-8。
- 物理路径的可打印性、候选归属、父子文件键冲突、复用来源、引用 revision 奇偶性。
- 计划作用域、进度集合互斥、完成状态与错误集合的一致性。

JSON 整数目前没有协议统一的最大值；非 Python 实现必须精确处理，不能经过浮点转换。这是后续格式冻结前仍需决策的数值边界。

Schema 验证通过不证明对象存在、权限正确或允许删除。状态机与运维代码还要核对仓库中的引用、计划和归属。`jsonschema` 仅为开发测试依赖；运行时不加载 Schema，核心仍无第三方运行依赖。

[正样例](../tests/fixtures/protocol/v3/)涵盖十类记录、对象复用和原样来源 ID；[反样例](../tests/fixtures/protocol/v3/invalid/)涵盖控制字符、路径逃逸、未知字段和嵌套版本混用。[协议测试](../tests/test_protocol.py)分别验证 Schema、codec 和旧格式兼容；[完整生命周期测试](../tests/test_collection.py)验证新 ID 跨恢复、引用、共享对象和回收的传递。

普通磁盘 current 绑定仍只读取 marker 和 current 两份控制记录；绑定后的文件选择不探测文件、不重读记录、不自动保留。此阶段没有对生产 AsterStore 进行升级、登记或数据迁移。
