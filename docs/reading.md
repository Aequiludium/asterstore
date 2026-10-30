# 读取与 Hive

调用 `dataset.current()` 获取当前发布元数据，通过数据集目录读取正式文件。该方法不枚举分区，也不返回永久文件快照；发现活动安装或恢复记录时拒绝读取。

## 安装与兼容性

```bash
pip install 'asterstore[polars]'
```

可选依赖支持 `polars>=1.44.2,<3`，CI 验证 Python 3.11、3.12、3.13、3.14 与 Polars 1.44.2、2.0.0、2.1.0 的全部 12 组组合。开发锁文件固定 Polars 2.1.0；使用其他受支持版本不需要 AsterStore 适配器。

## 自然路径读取

以下代码中的 `dataset` 是已打开的数据集。Polars 直接扫描正式业务路径：

```python
import polars as pl

state = dataset.current()
frame = pl.scan_parquet(dataset.root / "date=*/data.parquet", hive_partitioning=True)
result = frame.collect()
```

## 维护窗口

调用方必须保证从读取状态到最后一次文件访问之间不进入安装、回退或恢复。`current()` 的一次状态检查不能消除检查后发布的竞争；这由调用方的维护窗口协调保证。

不要递归扫描整个数据集目录；隐藏 `.asterstore` 内可能有旧备份和新候选。使用与业务布局一致的明确 glob，例如行情的 `date=*/data.parquet`、factor 的 `version=v1/date=*/data.parquet`。

不需要 AsterStore 的 Polars 适配器。可运行示例见 [parquet.py](../examples/parquet.py)。业务 schema、日期过滤与输入身份由调用方处理。
