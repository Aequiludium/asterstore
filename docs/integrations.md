# 可选引擎接入

核心仍为零第三方运行时依赖。第一个适配器是 `asterstore.integrations.polars` 子包，安装 `asterstore[polars]` 后调用；导入核心、`integrations` 或适配器本身都不加载 Polars。Polars 在实际构造扫描计划时才导入。

```bash
uv sync --locked --extra polars
uv run --locked --extra polars python examples/parquet.py
```

项目外可使用 `uv add 'asterstore[polars]'` 安装已发布版本；当前项目尚未发布，开发验证使用本地目录或构建出的 wheel。extra 声明 `polars>=1.32,<2`，锁文件记录 Polars 1.44.2，已在 Python 3.11/3.12 运行全部测试；依赖下界 Polars 1.32.0 也在 Python 3.11 通过接入测试及示例。Python 核心仍要求 3.11 及以上。

## 读取入口

```python
from asterstore.integrations.polars import scan_parquet

binding = repository.open("prices")
query = scan_parquet(binding).filter(pl.col("price") > 10).select("price")
result = query.collect()
```

`scan_parquet(binding, *, keys=None, hive_partitioning=False)` 返回原生 `polars.LazyFrame`：

- 只将 `binding.files(keys=keys)` 的明确成员交给引擎。keys 使用发布声明中的完整对象 key，保留选择顺序和重复项。
- 固定 `glob=False`，使 `part[1].parquet` 等合法文件名按字面值读取，不让通配展开改变发布的成员范围。
- 默认关闭 Hive 路径字段推断。调用者明确使用 `hive_partitioning=True` 后，引擎可从 `day=01` 等目录读取分区字段；候选目录本身不代表业务分区。
- 适配器不读取 schema、检查文件存在、刷新 current、写引用或自动恢复。缺失文件、损坏文件和 schema 冲突由引擎按其规则报错。
- 空发布或空选择抛出 `ValueError`。目前声明不包含 schema，因此不能推导一个带类型的空表。

更完整的引擎参数直接使用 `pl.scan_parquet(list(binding.files()), glob=False, ...)`。适配器只提供小型便利接口，不复制引擎全部参数，也不接管优化、投影和过滤。写入直接使用 `DataFrame.write_parquet(candidate.path(...))`，再显式 commit。

这里的“不预检”只约束治理层；Polars 可以在计划构造、schema 推导或执行期间读取 footer、访问文件元数据。适配器的 I/O 契约测试替换引擎入口，仅隔离治理层的调用；真实引擎测试单独验证结果。

## LazyFrame 与保留时间

构造扫描计划不代表数据已经读完。普通 Binding 与 LazyFrame 都不会自动保留对象；旧发布可被回收，已经创建的计划也可能在 collect 时失败。

允许并发回收的任务，应先 `retention.retain(name, dataset_id)` 获取具名引用，使用返回的 `held.binding` 创建查询，并在查询实际执行完毕后显式 release。不要先 open 再假定 retain 选中了同一 current，也不要在 collect 之前释放引用。异常情况下可以保留名字作为可诊断的未完成任务，再由任务或运维显式处理。

[独立生产者/消费者示例](../examples/parquet.py) 展示：两分区初始发布、保留旧读者、只更新一个分区并复用另一个、两位消费者得到各自结果、释放引用后仅回收被替换文件。全部使用临时目录，不依赖 Aster。

当前适配器面向声明为普通文件的 Parquet 对象；调用方擅自将声明路径替换成目录或修改文件字节，不属于受管发布保证。接入层不会为了防范外部篡改而增加逐文件审计。

## 验证与测量

`tests/test_polars.py` 使用真实 Parquet 验证字面路径、选择/重复选择、Hive 显式启用、共享文件与保留引用、延迟执行期间的回收，以及缺失文件错误；普通导入和发行包核心环境仍不安装引擎。

[读取规模测量](../benchmarks/README.md#parquet-读取与清单规模) 分别记录显式 open、缓存路径选择、Python 绑定内存峰值，以及相同文件和查询的直接/适配读取。它不代表全仓治理规模、冷缓存、共享挂载或稳定加速倍数。

引擎行为依据：[Polars scan_parquet](https://docs.pola.rs/api/python/stable/reference/api/polars.scan_parquet.html)、[Hive 路径字段](https://docs.pola.rs/user-guide/io/hive/)。版本兼容性以实际安装测试为准。

## 新声明的逻辑成员选择

`scan_parquet` 同时接受 `Binding[Publication]` 和 `Binding[Declaration]`。新声明的 keys 指逻辑成员名，与磁盘文件名无关；全部文件按首次成员引用顺序去掉显式对象别名造成的重复，指定 keys 时保留调用者的重复选择。外部文件的可变性由 Capabilities 明确报告，adapter 不将 registered 绑定转为字节快照。示例与持久化限制见[声明模型](declarations.md)。
