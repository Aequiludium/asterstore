<h1 align="center">asterstore</h1>

<p align="center">
  本地文件数据管理库<br>
  <strong>轻量读取 · 显式发布 · 可恢复治理</strong>
</p>

<p align="center">
  <a href="https://github.com/Aequiludium/asterstore/actions/workflows/ci.yml"><img src="https://github.com/Aequiludium/asterstore/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-52796F?style=flat-square" alt="Apache-2.0"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="docs/api.md">API</a> ·
  <a href="examples">示例</a> ·
  <a href="docs/design.md">设计</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>

---

数据仍然是普通文件。**asterstore 为它们管理发布、保留与回收**：哪些文件组成一次发布，哪些对象仍被任务使用，哪些残留可以清理。

框架负责生产数据，Polars、Arrow 等引擎负责读取和计算。asterstore 连接这些环节，核心无第三方运行依赖，也不依赖 Aster 或任何业务框架。

## 治理明确，读取轻量

- **发布有边界。** 写入候选后显式提交，以原子切换使新发布可见；未变化的受管对象可以直接复用。
- **读取走短路径。** 打开当前发布只读两份控制记录；绑定后反复选择文件路径，不扫描目录、不逐文件预检、不计算内容哈希。
- **保留由任务决定。** 需要的发布显式保留，使用完成后按引用版本释放；绑定或读取本身不产生保留记录。
- **回收可以恢复。** 先预览，再按固定计划退役与删除；中断后沿原计划恢复，清理范围不扩大。

### 两种管理方式，同一套模型

| | 外部登记 · `registered` | 受管发布 · `managed` |
| --- | --- | --- |
| 数据由谁写入 | 现有程序或其他框架 | 通过候选写入与提交 |
| 如何接入 | 声明已有文件及资源位置 | 创建对象，或复用已提交对象 |
| 可以保留什么 | 发布元数据 | 发布元数据与受管文件 |
| 文件由谁回收 | 外部生产者 | asterstore 按权属和保留关系回收 |

两者共用 `Declaration`、`Binding` 和治理接口。登记外部文件不会改变其生产流程，也不会赋予库删除权。详见[声明与资源绑定](docs/declarations.md)。

## 快速开始

需要 Python 3.11+，在你的 uv 项目中安装：

```bash
uv add asterstore
# 使用 Polars 接口时：
uv add 'asterstore[polars]'
```

从源码开发见[贡献指南](CONTRIBUTING.md)。

**写入一次，显式发布。** 将下面代码保存为 `demo.py`，运行 `uv run python demo.py`：

```python
from asterstore import Repository

store = Repository("./demo-store")
store.initialize(
    resource_ids=["data"],
    managed_resource_id="data",
    lifecycle=True,
)

with store.prepare(
    "measurements",
    publication_id="run:1",
    operation_id="write:1",
    expected_generation=0,
) as writer:
    writer.write_bytes("readings", b"sensor,value\nA,21.5\n", relative_path="part.csv")
    writer.commit()

# 捕获这次发布的成员与路径，交给文件读取或数据引擎。
binding = store.open("measurements")
print(binding.files(keys=["readings"])[0].read_text())
```

`operation_id` 标识一次固定写入请求，`expected_generation` 用于检测并发更新。退出候选上下文不会自动提交；完整的复用与恢复流程见[发布示例](examples/managed_declarations.py)。

<details>
<summary><strong>任务需要保留数据时</strong></summary>

在可能并发回收的场景中，先建立对象保留，再通过该引用读取。以下代码接续上面的示例：

```python
from asterstore import RetentionScope

held = store.governance.retain("analysis", "measurements", "run:1", scope=RetentionScope.OBJECTS)
try:
    inputs = store.governance.open("analysis", expected_revision=held.revision)
    print(inputs.files()[0].read_text())
finally:
    store.governance.release("analysis", expected_revision=held.revision)
```

释放引用不会立即删除文件。回收需显式调用，且仍保护当前发布及其他任务保留的对象。参见[治理示例](examples/governance.py)与[候选清理](examples/cleanup.py)。

</details>

**使用 Polars**，可在源码仓库中运行增量发布、延迟查询与回收的完整示例：

```bash
uv sync --locked --extra polars
uv run --locked --extra polars python examples/parquet.py
```

## 文档导航

| 想了解什么 | 从这里开始 |
| --- | --- |
| 使用接口与读取引擎 | [API](docs/api.md) · [引擎接入](docs/integrations.md) · [示例](examples) |
| 发布、保留与回收如何工作 | [发布](docs/publishing.md) · [治理](docs/governance.md) · [候选清理](docs/cleanup.md) |
| 诊断与显式检查（未发布） | [范围与并发约定](docs/inspection.md) · [示例](examples/inspection.py) |
| 模型与磁盘格式 | [设计原则](docs/design.md) · [项目结构](docs/architecture.md) · [协议](docs/protocol.md) |
| 开发、验证与贡献 | [开发指南](docs/development.md) · [性能验证](benchmarks/README.md) · [贡献指南](CONTRIBUTING.md) |

## 项目状态

**[`0.1.0` 已正式发布](https://pypi.org/project/asterstore/0.1.0/)**。已完成 Aster 真实分钟行情的本地受管接入，验证发布、增量复用、读取、保留与回收；详见[接入验收](docs/aster-integration.md)。主分支新增的[诊断与检查](docs/inspection.md)仍属于未发布增量，使用前请核对版本。

当前验证范围为本地 Linux/POSIX；NFS 等共享挂载需要单独验证。核心以单个数据集发布为提交单位，控制历史尚未压缩。支持范围与后续工作见[首版范围](docs/release-scope.md)、[路线图](docs/roadmap.md)及[格式边界](docs/compatibility.md)。

---

[Apache-2.0](LICENSE) · [变更记录](CHANGELOG.md) · [安全报告](SECURITY.md) · [问题反馈](https://github.com/Aequiludium/asterstore/issues)
