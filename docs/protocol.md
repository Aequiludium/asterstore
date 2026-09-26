# 磁盘格式与声明登记

当前唯一仓库格式为 `kind: "store"`、`format_version: 1`，所有控制记录均使用版本 1。仅支持这一格式，不提供开发旧格式兼容。当前模型同时支持外部登记、受管发布和显式治理；详见[发布](publishing.md)、[治理](governance.md)和[清理](cleanup.md)。

## 入口与边界

```python
from asterstore import (
    Capabilities,
    Declaration,
    FileSet,
    Locator,
    Member,
    Object,
    Repository,
)

repo = Repository("./control")
repo.initialize(store_id="simulation:store", resource_ids=["simulator"])
declaration = Declaration(
    "simulation:heat",
    "run:17",
    FileSet(
        [Object("object:17", Locator("simulator", "output.bin"))],
        [Member("phase:initial", "object:17")],
    ),
    Capabilities.registered(),
)
record = repo.register(declaration, operation_id="import:17", expected_generation=0)
binding = Repository("./control").open(
    "simulation:heat",
    resources={"simulator": "/data/simulation"},
)
paths = binding.files(keys=["phase:initial"])
```

`initialize` 显式创建仓库。store_id 可省略，由库生成；已有仓库省略时使用原身份。需要跨初始化失败精确重试或同时初始化的调用者应提前固定 store_id。同一 store_id 和资源集合可重试，修改身份或资源集合会冲突；不自动升级其他格式。

`register` 必须提供 operation_id 和 expected_generation。初始 generation 为 0；返回 DeclarationRecord 的 generation 为 expected_generation + 1。普通构造 Repository 和内存 bind 仍然没有 I/O。

默认 registered-only marker 只接受 registered 声明；显式开启 managed 的仓库还接受库内候选发布。register 入口始终只接受 registered 声明。登记不扫描、复制、读取、同步或删除外部文件，也不执行 pickle 或其他业务解码器。资源根是打开时的显式部署参数；它可以位于只读挂载，登记操作甚至不需要资源根。`resources={}` 可打开空声明。

## 权威记录与路径

```text
control/.asterstore/
  format.json                                  # StoreRecord，固定 Store 身份和资源命名空间
  gc.lock                                      # 本轮注册提交的协调锁
  registrations/<operation-token>.json         # 不可变操作请求，尚不代表提交
  object-records/<object-token>.json            # Store 范围内不可变的对象身份关联
  datasets/<dataset-token>/
    current.json                               # 完整 DeclarationRecord，原子可见点
    history/<publication-token>.json           # 从已生效 current 归档的完整记录
```

所有 token 使用对应原始 UTF-8 身份的 SHA-256 十六进制字符串。ID 不拼进目录；读回核对身份、Store 和资源归属。对象 ID 在 Store 内唯一；发布 ID 在数据集内唯一；操作 ID 在 Store 内唯一。

StoreRecord 保存 store_id、resource_ids，以及 `required_features=["registered"]`。资源列表按精确字符串排序编码；不保存数据根绝对路径，不把机器部署路径当作资源身份。目前没有修改资源命名空间或接管管理权的接口。

DeclarationRecord 保存 store_id、operation_id、expected_generation、generation 和完整 Declaration：能力、对象表、逻辑成员映射。它既是固定操作请求的内容，也是提交后 current/history 的内容；**记录位于操作目录只表示请求已经固定，不表示发布已经生效。**

ObjectRecord 保存 Store 内 object_id → locator、registered 管理方式与外部字节稳定性。该关联在任何提交之前固定；后续发布甚至另一数据集都不能把相同 object_id 指向不同 locator 或改写其字节稳定性。多个发布可以显式引用相同对象。该身份约束不冻结外部字节、不产生删除权。不同发布可为同一外部位置声明不同对象 ID，库不跨发布按路径或内容自动去重。

操作中断后可能留下尚未公开的操作和对象身份记录。它们是保守的永久身份预留，当前没有自动清理或重用接口；不能把它们当作可删除数据的证据。

## 提交、重试和恢复

提交过程只持有短期控制元数据锁，不包含数据生产阶段：

1. 在任何治理写入前验证声明类型、registered 能力、整数范围和资源引用，核对 marker。
2. 取得仓库 `gc.lock` 独占锁，重读 Store；检查操作、发布与对象身份冲突。
3. 已提交的相同操作返回原记录，不重新选择 current。否则检查 expected_generation 与数据集能力不变。
4. 强持久路径先同步已有 marker 和复用对象的控制记录，再持久化不可变操作请求及新增对象记录。
5. 将旧 current 归档到 history；原子替换完整新 current。
6. 完成当前请求所承诺的同步后返回。任何外部数据文件都不参与本流程。

**current 的原子替换是唯一可见生效点。** 归档可能早于新 current，因此 history 中允许存在与旧 current 相同的副本。没有 current 却出现历史，或历史 generation 不低于 current，视为损坏，不据此重建 current。首次 current 写入留下的空目录，在 generation=0 固定请求解释且没有历史/未知条目时属于未提交残留，不等于丢失已提交历史。

不同请求从同一 generation 竞争，只有一个生效；同一请求并发重试可共同返回同一结果。操作 ID 携带不同内容、发布 ID 被不同请求复用、对象 ID 被重新定位、数据集能力被改变，都会报 `PublicationConflictError`。

`registration_status(operation_id)` 返回 planned / committed / conflict，是协调锁下的控制状态观察，不是数据健康检查。操作请求尚未落盘时，该身份查询报不存在；调用者可重发原始 register 请求。请求落盘后可调用 `resume_registration(operation_id)`；恢复沿用原始 generation，不自动变基。

提交后的响应丢失不等于失败未执行。若较新发布已经生效，对旧操作重试会返回历史结果，绝不回退 current。已提交记录缺少操作或对象身份依据时，重试明确报损坏，不偷偷重建丢失证据。

当前注册提交在仓库级串行协调，优先保证对象 ID 的跨数据集唯一关联；它不是最终吞吐优化结果。同步和控制记录数随声明对象数量增长，尚未取得大规模运维性能结论。

## 读取与保证

- `describe(dataset_id, publication_id=...)` 返回已提交 DeclarationRecord；可以读取 current_only 数据集的历史声明。
- `open(dataset_id, resources=...)` 返回新的 Binding；current 路径只读 marker 与 current 两份控制记录。
- `open(..., publication_id=...)` 尊重 HistoryAccess。CURRENT_ONLY 不允许打开非当前数据；VERSIONED 允许历史声明绑定，但不保证外部旧字节仍存在。
- 普通 open 不查询全仓对象注册表、不扫描历史、不逐文件验证。跨记录身份约束在登记和显式运维边界承担；控制记录损坏的全面检查不属于普通读取职责。
- Binding 捕获资源映射；重新绑定资源不改变已有 Binding。不同应用必须为同一资源提供符合其部署契约的根目录，库不通过内容探测证明这些映射相同。
- `durable=True` 同步本次依赖的控制记录及目录；`False` 省略显式同步，仍有原子可见性与竞争检测。两者都不提升外部字节持久性。

## 结构与拒绝边界

逻辑 ID 为 1–4096 UTF-8 字节，无 Cc 控制字符；generation/revision 使用 `0..2^63-1` 整数，拒绝布尔和浮点。expected_generation 最大为 `2^63-2`，耗尽后拒绝写入。

[Schema](../src/asterstore/metadata/schemas/store.json) 和 [golden fixtures](../tests/fixtures/protocol/) 随 wheel/sdist 发布。UTF-8 JSON 严格拒绝重复键、NaN/Infinity、错误类型、未知字段、未知 kind、混合版本和未知 required_features。Schema 负责结构；codec 另外检查 UTF-8 字节长度、成员闭包、路径冲突和 generation 的关系。

marker 的 required_features 是必要语义门槛。managed 和 lifecycle 是同一格式内必须显式启用的能力。当前编解码器支持 registered 基础组合及 managed、lifecycle 必要功能的四个固定组合，见 [治理](governance.md)。含 managed 的组合必须包含 managed_resource_id。解码器拒绝不认识的组合。

## 验收与剩余工作

[登记测试](../tests/test_registration.py)覆盖权威记录、身份冲突、响应丢失、固定请求重试和每个持久边界的异常；[进程测试](../tests/test_registration_processes.py)覆盖竞争提交、相同请求并发重试，以及对象身份落盘后/current 生效后的进程终止恢复。[独立示例](../examples/registration.py)在另一个 Python 进程重新打开声明。

这些是本地 POSIX 进程与故障注入证据，不证明断电或 NFS 服务故障保证。目标数据不需存在或可写；原生引擎仍负责实际读取时的文件错误。

managed 候选与创建权属已接入同一声明模型，见 [managed](publishing.md)。直接保留及 GC 已接入，见 [治理](governance.md)；仅凭 Capabilities 不能取得保留或删除权限。

## 失败候选清理记录

lifecycle 仓库支持 `managed_cleanup_plan` 和 `managed_cleanup_progress`，两者与永久放弃的 managed_request 关联。它们属于候选创建者的控制目录；逐文件的清理不删除身份记录，也不复用操作 ID。字段、授权与开发版本兼容性见 [清理](cleanup.md)。普通读取不加载这些记录。
