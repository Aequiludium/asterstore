# 显式成员、资源定位与能力声明

状态：2026-09-25，已实现模型、资源绑定与读取，并已接入 [registered 持久登记](protocol.md)。managed 提交也已接入，见 [managed](publishing.md)；直接 metadata/objects 保留已接入，发布依赖闭包仍未实现。这些接口是通用输入模型，不依赖 Aster，也不加载任何业务元数据解码器。

## 模型与身份

```text
Declaration(dataset_id, publication_id, capabilities)
  └── FileSet
        ├── Member(key, object_id)       逻辑成员映射
        └── Object(object_id, Locator)   显式对象表
                               └── resource_id + relative_path

Binding = Declaration + 本次显式提供的 resource_id → root
```

`dataset_id`、`publication_id`、`object_id`、`resource_id`、成员 `key` 都是逻辑字符串，允许冒号、斜杠和点段，保持原始大小写与 Unicode 表示。新模型统一要求非空 UTF-8、不含 Cc 控制字符、不超过 4096 UTF-8 字节。只有 `Locator.relative_path` 使用物理相对路径规则，拒绝绝对路径、空段、`.`、`..`、反斜杠、冒号和不可打印字符。

对象 ID 显式提供，不由文件名或内容 hash 推导。当前模型只在一份 FileSet 中验证对象表一致性；登记已在 Store 范围核验 object_id 与 locator/字节稳定性的不可变关联；managed 创建权属已通过创建者记录接入。构造一个 Object 不证明文件存在，也不取得文件管理权。

FileSet 在构造时复制对象表和成员序列，随后不可变。规则如下：

- 对象 ID 唯一；成员 key 唯一；成员只能引用本对象表中的 ID。
- 每个对象至少被一个成员引用，不允许带入隐藏的未引用对象。
- 多个成员可显式引用同一对象，表达逻辑别名；同一个 locator 不接受多个对象身份。
- 同一资源内的文件路径不能形成父子文件冲突；不同资源的根绑定为同一具体路径或形成父子文件冲突时，Binding 也拒绝。
- 空 FileSet 合法；成员顺序由调用者固定；读取时出现的新文件不会进入旧声明。

路径冲突检查是词法检查，不访问磁盘，不识别不同软链接/硬链接是否指向同一文件。这不是文件系统安全隔离或全局物理去重。

## 能力组合

`Capabilities` 分开表达管理方式、字节稳定性和历史发现策略，不从 `current_only` 推导可变性。

| 构造 | 字节稳定性 | 可支持的保留范围 | 外部写入/删除 |
| --- | --- | --- | --- |
| `Capabilities.managed()` | `COORDINATED_IMMUTABLE` | metadata / objects | 必须参与库的协调协议 |
| `Capabilities.registered()` | `MUTABLE_OR_UNKNOWN` | 仅 metadata | 外部生产者自行管理；库无删除权 |
| `Capabilities.registered(byte_stability=ByteStability.PRODUCER_IMMUTABLE)` | 外部生产者声明不可变 | 仅 metadata | 不因声明不可变就获得协调或删除权 |

两种管理方式都可分别声明 `HistoryAccess.CURRENT_ONLY` 或 `VERSIONED`。历史发现与字节冻结是不同事实；versioned 不承诺无限保留历史。managed 不能声明可变字节，registered 不能声明库协调不可变；字符串或任意布尔组合不被接受。

`retention_scopes`、`require_retention(scope)` 和 `require_managed()` **只检查能力规则**。检查通过不创建引用，不证明所有权，也不授权删除。registered 对象保留请求会抛出 `UnsupportedCapabilityError`，不会自动降级成 metadata 保留。当前 Declaration 已可持久登记，但还没有持久保留服务；保存登记记录和取得保留引用是不同操作，bind 也不获得保留。

`Capabilities` 不承载 durable、部署资格或本次检查结果。这些保证应由具体操作和环境证据报告，不能由一份声明永久升级。

## 绑定和选择

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

declaration = Declaration(
    dataset_id="simulation:temperature",
    publication_id="run:17",
    files=FileSet(
        objects=[Object("object:17", Locator("simulator", "output-0001.bin"))],
        members=[Member("phase:initial", "object:17")],
    ),
    capabilities=Capabilities.registered(),
)
binding = Repository("./control").bind(
    declaration,
    resources={"simulator": "/data/simulation"},
)
paths = binding.files(keys=["phase:initial"])
```

这里不会创建 control，不登记声明、不扫描 simulation、不复制文件、不校验文件存在性或内容。数据资源根必须显式提供；缺失资源抛出 `ResourceNotBoundError`，不会默认为仓库目录。空声明可以传 `resources={}`。未使用的资源映射可存在，但仍检查其文本和路径类型。

资源映射在绑定时复制并词法绝对化，不调用 resolve。随后修改传入的 dict、改变 cwd 或构造新 Binding，都不改变旧绑定的路径。直接使用 `reading.resources.ResourceMap` 也具有同样的捕获语义。

`Binding.files()` 返回按成员首次引用顺序排列的唯一对象路径，多成员别名不会使全部文件读取重复。显式 `files(keys=[...])` 则严格保持调用者顺序与重复项；未知逻辑成员抛出 `UnknownMemberError`，不会尝试按 basename 或物理路径匹配。重复选择不重新定位、不重建索引、不做文件 I/O。

普通读取可直接交给原生引擎。现有 `integrations.polars.scan_parquet` 已接受新 Binding，仍关闭 glob，不加入治理预检。外部文件后续覆盖会被引擎正常读到，缺失文件正常报错；绑定从不宣称 registered 字节是快照。

运行独立示例：

```bash
uv run --locked python examples/declarations.py
```

示例使用模拟结果二进制文件，无金融模型、Parquet 或业务日历依赖。
