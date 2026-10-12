# Changelog

## 0.2.0 — 2026-10-12

- 诊断锁竞争及首次写入竞态统一抛 `RepositoryBusyError`；诊断返回类型集中从 `asterstore.governance.inspection` 导入。
- 增加显式治理状态快照、对象保护原因和只读分层检查；不修改磁盘格式或普通读取路径。
- 回收计划的退役记录验证使用临时内存索引，保留完整记录比较，消除逐条扫描计划的平方级成本。
- 增加单任务回收/清理状态查询；完整检查按次复用解码结果，已完成回收重试只核对本任务证据并同步完成记录。实际删除前仍执行完整关系检查与证据持久化。
- 同步发行后的路线图与文档，保持下游依赖来源由下游决定。
- 可选 Polars 支持范围扩展至 `>=1.32,<3`；Python 3.11/3.12 覆盖 Polars 1.32.0、1.44.2 和 2.0.0。
- CI 增加真实发行制品下载与完整性校验；更新 Ruff、mypy、CodeQL 和 Actions 依赖。
- 磁盘格式仍为 `format_version=1`，无需迁移；首次 GC 性能、控制历史压缩及 #21 的职责边界重构不属于本次交付。

## 0.1.0 — 2026-09-28

首个公开版本，使用唯一的 `store` 格式（format_version=1）。

- Declaration / FileSet 描述精确成员；Binding 在内存中选取路径，无逐读扫描、stat 或 hash。
- 区分 registered 外部登记与 managed 受管写入，外部数据不获得隐式删除权。
- Candidate 提供原生写入路径、增量复用、原子提交、代际冲突和固定请求恢复。
- Governance 提供 metadata/objects 具名保留、条件释放、固定计划回收及失败候选清理。
- `Repository.list_datasets()`、`governance.list_retentions()` 和 CandidateStatus 归属字段提供显式控制记录查询，不增加普通读取成本。
- 可选 Polars 接口；核心无第三方运行依赖。uv 构建，Apache-2.0，Python 3.11/3.12。
- Aster 真实分钟行情接入已通过：2,505,120 行数据、10,414 条 VWAP 结果一致，验证完整本地生命周期。
- 手动发布流程复用完整 CI，只上传已验证的同一批 wheel/sdist，使用 PyPI Trusted Publishing。

支持本地 Linux/POSIX。不承诺 NFS 写入、真实断电资格、全历史规模、发布依赖图、元数据压缩和完整审计。
