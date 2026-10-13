# managed：创建权属、发布与恢复

## 开始使用

```python
from asterstore import HistoryAccess, Repository

repo = Repository("./simulation")
repo.initialize(
    store_id="simulation",
    resource_ids=["results", "external"],
    managed_resource_id="results",
)
with repo.prepare(
    "heat",
    publication_id="run:1",
    operation_id="produce:1",
    expected_generation=0,
    history=HistoryAccess.VERSIONED,
) as writer:
    writer.write_bytes("initial", b"initial temperature", relative_path="initial.bin")
    record = writer.commit()

binding = repo.open("heat")
paths = binding.files(keys=["initial"])
```

完整示例见 [managed_declarations.py](../examples/managed_declarations.py)。原生写入器可使用 `writer.path(key, relative_path=...)`，例如将返回路径交给 Parquet writer。逻辑成员键、对象身份与物理相对路径独立；对象 ID 由库分配，既不是成员键，也不是内容哈希。`alias(key, member=...)` 增加同一对象的逻辑别名。候选会话捕获不可变 Store 配置；逐成员 path/alias 使用增量身份和路径索引，不重复读取 marker 或全量构造 FileSet。seal 时统一构造并完整验证不可变 FileSet。reuse 的持久保护仍需要完整成员快照，其成本随当前清单增长。

`resource_ids` 必须包含 managed_resource_id。marker 固定 `required_features=["registered", "managed"]` 和 managed_resource_id。原来只实现 registered 的 读写器拒绝这个 marker；现有 registered-only 仓库不会隐式取得 managed 功能。此阶段没有就地开启功能或升级命令。

当前只支持一个库内 managed 资源，根固定为 `<repository>/.asterstore/managed-data`，以保证安装目录位于同一文件系统。外部资源仍可通过 registered 登记。managed open 自动捕获库内资源根，禁止通过 resources 将其重定向；外部 registered open 继续显式提供资源根。库不接管外部目录或已有文件。

## 创建证据与可见性

```text
.asterstore/
  managed-candidates/<operation-token>/
    writer.lock              # 候选会话独占锁，锁文件不可删除
    request.json             # 不可变 ManagedRequest；在发出任何数据路径前固定
    files/<relative-path>    # 私有写入目录
    sealed.json              # 完整且不可变的 DeclarationRecord
  managed-data/<operation-token>/<relative-path>
  registrations/<operation-token>.json  # 两种发布共用的固定提交请求
  object-records/<object-token>.json
  datasets/<dataset-token>/current.json
```

ManagedRequest 固定 Store、操作、数据集、发布、expected_generation 和 history；不能恢复后修改基准 generation。操作 ID 的命名空间与 registered 登记共用，已保留的操作身份不能跨入口重新使用。

managed ObjectRecord 保存 `management=managed`、`byte_stability=coordinated_immutable` 和 creator_operation_id；locator 必须位于创建操作的安全 token 子目录。registered 记录禁止包含创建权属。相同对象 ID 的 locator、管理方式、创建者和字节稳定性都不可更改。能力声明本身不授予删除权。

候选不会因为退出上下文而提交或清理。写入者必须在 seal 前关闭文件句柄并停止修改；封存后不再发出写路径。封存保留本候选声明新文件的最低安装约束：必须存在、为普通文件，路径及父链不能通过符号链接重定向。默认不再递归盘点额外文件；未声明条目可能随整个目录安装，但不进入绑定、不自动获得 GC 删除权。`commit(check="existence" | "format" | "checksum")` 可显式检查最终声明全部唯一对象（含复用对象），默认 `check="none"` 不做可选健康检查。format 需 validator，checksum 需完整可信 object_id 摘要映射。遗留打开句柄或库外篡改不在协作式写入保证内。

提交顺序：验证固定请求、generation 和对象身份；持久化提交请求；原子安装私有目录；写入新对象的创建证据；归档旧 current；原子替换新 current。**current 替换仍是唯一公开生效点。** 目录存在、对象记录存在、封存存在，都不单独表示发布成功。

新对象安装和控制提交在仓库协调锁内执行。候选会话先取得候选锁，提交时再取得仓库锁，避免恢复者持有仓库锁等待同一候选造成死锁。数据生产期间不持有仓库锁。恢复时检查文件清单和同步仍可能延长提交临界区；尚无大规模并发吞吐结论。

## 复用与恢复

`writer.reuse(publication_id=None, keys=None)` 从同数据集已提交的 managed 声明选择逻辑成员；省略 publication_id 选择当前发布；current_only 数据集不允许复用已非当前的历史发布。保留原对象 ID、locator 和创建者，不复制、不读取、不同步旧数据字节；复用继承原对象的持久性。它不能将 registered 文件转换成 managed 对象。

`candidate_status(operation_id)` 仅查询控制状态：writing、prepared、conflict、current、historical，以及启用治理后的 retired、abandoned。它不是数据健康审计。`resume(operation_id)` 只打开已封存候选；尚未封存的进程死亡后，应使用新操作重新生产，退出写入器后可显式 abandon，再使用 governance.cleanup 清理其私有输出。

安装后、current 前进程死亡可从已安装目录继续；current 生效后响应丢失可返回同一提交。之后已有更新发布时，恢复旧操作不会回退 current。generation 已过期则报冲突，不自动变基，不安装该候选的数据。已提交记录缺失创建证据时明确报损坏，不重新制造权属依据。

`durable=True` 同步本候选新数据、依赖控制记录和相关目录；对弱持久提交进行强持久恢复，会同步该操作创建的字节。此举不会提升复用对象的原始持久性。`durable=False` 保留原子可见性与竞争控制，省略显式同步。

## 读取成本与当前限制

普通 managed open 仍只读取 marker 和 current，不扫描数据或对象注册表；Binding 成员选择没有文件系统 I/O。完整权属检查发生在写入、复用和显式恢复边界，不转嫁到每次读取。

全部 历史、操作与对象创建记录仍会保留。启用 lifecycle 的仓库已支持直接保留、GC 和候选放弃，完整语义见 [治理](governance.md)。残留物理清理见 [清理](cleanup.md)，待发布的 [控制历史压缩](control-compaction.md) 可归档完整正文但保留身份入口；不能仅根据 managed 标记或目录扫描授权删除。

测试覆盖提交边界异常、同 generation 多进程竞争、封存后/安装后/current 后强制终止进程、幂等恢复、对象复用、外部资源权限隔离和读取 I/O 次数。这些是本地 POSIX 证据，不构成 NFS、断电或磁盘损坏资格证明。


## 可选检查与恢复（未发布）

`seal()` 固定声明，不缓存健康检查结果。`resume(...).commit(check=...)` 每次执行本次请求的检查，已安装未提交的候选使用安装后的路径。耗时检查和用户回调在全局仓库锁外执行；调用方必须保持封存字节不变。检查后提交仍确认请求、generation 和权属，检查结果不授权删除。

首次提交检查失败不由本次调用推进 current。已提交重试检查失败会抛 `DataCheckError` 并携带 `commit_state="committed"`，不回退 current；若状态查询失败则为 unknown。`durability="not_completed"` 表示本次强持久升级未完成，不能因为操作已提交就推断升级成功。none 不关闭安装/同步约束。registered 的 register/resume_registration 也接受显式检查，需提供 resources，默认仍不访问外部数据。

未声明文件使用 `governance.inspect_candidate(operation_id)` 盘点。installed cleanup 仍拒绝超出创建证据的文件；调用方核实、移走未知条目后可重新预览/清理。已提交候选不能 abandon/cleanup，未知条目也不会被普通 GC 自动删除。详见[检查说明](optional-checks.md)及[可执行示例](../examples/optional_checks.py)。
