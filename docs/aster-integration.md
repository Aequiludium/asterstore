# Aster 首个真实数据集验收

2026-09-26，Aster 的 `aster-data[store]` 适配层接入 asterstore `0.1.0.dev1`（核心提交 `d120c598c2552d8b0c5bd79c29dae4d8b99bd44e`）。首版准备保持同一核心实现与磁盘格式。

数据为 `md.stock.kline_minute` 的 2026-09-11、2026-09-14 两个交易日。源 delivery 只读，Parquet 复制到新的本地 managed 仓库；生产配置和源文件均未修改。

- 显式检查 **2,505,120 行**的 schema、键和分区。
- 通过 Aster 原生行情接口得到 **10,414 条 VWAP**，与原 delivery 入口结果一致。
- 首日发布后增量复用首日对象，追加第二日；复用路径完全相同。
- 同日重写产生新对象，旧版本延迟查询在 OBJECTS 保留和 GC 后仍可 collect。
- 释放保留后，旧对象被回收，current 及复用对象仍可读取。
- 封存后恢复、旧 generation 冲突、失败候选放弃/清理均通过。
- 源文件前后 SHA-256 一致。机器记录见 [verification.json](validation/aster-integration.json)。

Aster 业务 JSON 作为普通受管成员与数据一起提交。核心未增加交易日、PIT、因子、pickle 或其他 Aster 专用分支。读取器使用固定的成员路径，绑定后的读取不追加文件完整性校验。

这是一个真实数据集的完整本地生命周期验收，不是整个 Aster 的治理替换。旧因子治理、生产目录切换、全历史规模、NFS 写入和真实断电仍需单独验证。0.1.0 不承诺这些范围。
