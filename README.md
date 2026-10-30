# AsterStore

AsterStore 管理普通数据目录的一批文件变更，保存当前发布元数据，并保留撤销最近一次成功发布的材料。行情和 factor 可以共用同一个核心，数据保留自然的 Hive 路径。

每次只处理新增、替换或删除的文件；不构建历史分区的完整文件清单，不维护全局文件归属或版本引用图。核心没有第三方运行依赖。

## 安装与兼容性

要求 Python 3.11 或更高版本，以及支持 POSIX 文件锁、文件与目录同步、原子替换的文件系统。

| 组件 | 依赖声明 | CI 验证版本 |
| --- | --- | --- |
| Python | `>=3.11` | 3.11、3.12、3.13、3.14 |
| Polars（可选） | `>=1.44.2,<3` | 1.44.2、2.0.0、2.1.0 |

CI 验证上述 Python 与 Polars 版本的全部 12 组组合，并在四个 Python 版本上运行完整测试、wheel 构建、sdist 重建和隔离安装。具体检查见[仓库自动化](docs/automation.md)。

从 PyPI 安装：

```bash
pip install asterstore
# 需要使用 Polars 读写 Parquet 时：
pip install 'asterstore[polars]'
```

在源码仓库根目录安装：

```bash
pip install .
pip install '.[polars]'
```

核心不依赖 Polars；可选 extra 仅安装 Polars，由调用方直接读写数据文件。开发环境的 `uv.lock` 固定 Polars 2.1.0；通过 pip 安装时按上述依赖范围解析版本。开发使用 uv 0.13.6 或更高版本，见[贡献指南](CONTRIBUTING.md)。

## 快速开始

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from asterstore import Repository

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = Repository(root / "registry")
    repo.initialize()
    prices = repo.create_dataset("prices", root / "prices")

    with prices.write(expected_revision=0) as batch:
        batch.stage("date=2026-10-14/data.bin").write_bytes(b"day 14")
        # 本例没有并发读者。真实部署需先停止新读取并等待已有读取完成。
        batch.commit(metadata={"ready_through": "2026-10-14"}, quiescent=True)

    assert prices.path("date=2026-10-14/data.bin").read_bytes() == b"day 14"
    prices.rollback(quiescent=True, expected_revision=1)
    assert prices.current().publication is None
    prices.clean()
```

仓库和数据集入口支持软链接目录（如 `~/AsterStore`）；入口解析为真实路径，正式数据目录位置可配置，每个数据集独占一个不重叠的目录。初始化要求空目录；不接管已有数据，不转换旧磁盘协议。

## 发布与读取

`batch.stage(relative_path)` 返回暂存文件，调用方写入完整的新文件并检查。`batch.delete(relative_path)` 声明删除。提交前备份本次变更涉及的旧文件，然后逐文件安装，最后更新发布记录。

准备期间可以读取旧数据。**安装、回退和故障恢复期间，调用方必须暂停该数据集读取并等待已有读取真正完成，包括 LazyFrame.collect()。** `quiescent=True` 是调用方声明，不会暂停读者。文件替换不提供整表快照隔离。

读取使用自然路径和少量当前元数据。`current()` 不固定文件版本。Polars 直接扫描 `date=*/data.parquet` 等明确模式，避免递归扫描隐藏的控制目录。

## 回退与清理

一次回退恢复被替换或删除的旧文件，移除本次新增文件，并恢复原发布元数据。回退消耗回退机会；首次发布可以退回未发布状态。失败发布不会覆盖此前的有效回退材料。

安装或回退报错后，在维护窗口调用 `recover(quiescent=True)` 判断并恢复状态。不要直接重试原提交。`clean()` 只删失效事务材料，不删除正式数据；有效撤销材料始终保留，存在活动事务时拒绝清理。

这是操作回退，不是完整数据备份。全量重算需要为旧文件备份预留相应空间。

## 文档与示例

- [文档导航](docs/README.md)、[API](docs/api.md)、[磁盘协议](docs/protocol.md)
- [发布与回退](docs/publishing.md)、[读取](docs/reading.md)、[清理](docs/cleanup.md)
- [五天增量示例](examples/transactions.py)、[Hive 因子示例](examples/parquet.py)
- [设计记录](docs/history/lightweight-refactor-plan.md)、[规模验证](benchmarks/README.md)
- [开发](CONTRIBUTING.md)、[发行](docs/releasing.md)
