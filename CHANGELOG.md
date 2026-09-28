# Changelog

本轮未发布：增加 `Repository.list_datasets()`、`governance.list_retentions()`，
以及 CandidateStatus 的持久化归属字段。仅显式查询控制记录，不改变存储协议或普通读取成本。

## 0.1.0 — 待发布

首个公开版本，使用唯一的 `store` 格式（format_version=1）。

- Declaration / FileSet 描述精确成员；Binding 在内存中选取路径，无逐读扫描、stat 或 hash。
- 区分 registered 外部登记与 managed 受管写入，外部数据不获得隐式删除权。
- Candidate 提供原生写入路径、增量复用、原子提交、代际冲突和固定请求恢复。
- Governance 提供 metadata/objects 具名保留、条件释放、固定计划回收及失败候选清理。
- 可选 Polars 接口；核心无第三方运行依赖。uv 构建，Apache-2.0，Python 3.11/3.12。
- Aster 真实分钟行情接入已通过：2,505,120 行数据、10,414 条 VWAP 结果一致，验证完整本地生命周期。
- 手动发布流程复用完整 CI，只上传已验证的同一批 wheel/sdist，使用 PyPI Trusted Publishing。

支持本地 Linux/POSIX。不承诺 NFS 写入、真实断电资格、全历史规模、发布依赖图、元数据压缩和完整审计。
