# AsterStore

AsterStore 为普通文件提供数据集级别的原子发布、一次回退和维护清理。适合定期生成数据、日常读取最新结果、保留上一份成功结果的工作流。

每个数据集有两个版本槽位：`current` 用于日常读取，`rollback` 保存上一次成功发布的 current。文件和描述它们的元数据可以一起发布。核心不解释文件内容，没有第三方运行依赖。

## 安装

要求 Python 3.11+，以及支持 POSIX 文件锁、硬链接和原子替换的文件系统。从当前仓库安装：

```bash
pip install 'git+https://github.com/Aequiludium/asterstore.git@main'
# 需要可选的 Parquet 扫描适配器时：
pip install 'asterstore[polars] @ git+https://github.com/Aequiludium/asterstore.git@main'
```

需要固定部署内容时，将 `main` 换成经过验证的提交 SHA。

## 快速开始

下面的示例使用临时目录，可以直接运行：

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository

with TemporaryDirectory() as temporary:
    repo = Repository(Path(temporary) / "store")
    repo.initialize()

    with repo.prepare("prices", expected_revision=0) as draft:
        draft.allocate("data").write_bytes(b"first result")
        candidate = draft.seal()
        assert candidate.files(keys=["data"])[0].read_bytes() == b"first result"
        head = draft.commit()

    with repo.prepare("prices", expected_revision=head.revision) as draft:
        draft.reuse_current()
        draft.allocate("data").write_bytes(b"second result")
        draft.seal()
        head = draft.commit()

    assert repo.open("prices").files()[0].read_bytes() == b"second result"
    repo.rollback("prices", expected_revision=head.revision)
    assert repo.open("prices").files()[0].read_bytes() == b"first result"

    # 此例没有并行读写；实际应用须先停止新任务并等待已有读取完成。
    repo.clean("prices", quiescent=True)
```

## 工作方式

- **发布**：准备文件，封存完整成员清单，完成业务检查，显式提交。current 与 rollback 一起原子切换。
- **读取**：一次 open 固定一个版本；后续发布不会自动改变这个 Binding。
- **复用**：继承 current 的成员，只写变化的文件。复用限于同一数据集。
- **回退**：将 rollback 提升为 current，清空 rollback；下次成功发布重新保留两个版本。
- **清理**：在调用方协调的维护窗口内，删除两个保留版本之外的文件和清单。

已封存文件必须保持不可变。发布可以与读取并行，清理必须等待全部相关读取结束，包括延迟计算最后一次文件访问。`quiescent=True` 是调用方的声明，不会自动暂停读者。

默认启用持久化同步。提交报错可能发生在原子切换之后，需要读取 head 核实结果。两个版本用于操作回退，不代替备份。

## 文档

- [文档导航](docs/README.md)
- [发布、增量更新与回退](docs/publishing.md)
- [读取与 Parquet](docs/reading.md)
- [维护清理](docs/cleanup.md)
- [API 参考](docs/api.md)
- [架构与边界](docs/design.md)、[磁盘协议](docs/protocol.md)
- [开发指南](CONTRIBUTING.md)、[自动化](docs/automation.md)、[发行](docs/releasing.md)

可运行示例：[双版本](examples/two_versions.py)、[Parquet](examples/parquet.py)。规模检查见 [benchmarks](benchmarks/README.md)。
