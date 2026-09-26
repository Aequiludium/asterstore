# Changelog

## 0.1.0.dev1 — 开发中，尚未发布

- 删除早期开发协议的读写兼容、双生命周期实现、旧导出与 API 别名。
- 统一 Declaration / FileSet、Candidate 与 Governance；使用 prepare/resume/candidate_status。
- 唯一仓库标记为 kind=store、format_version=1；拒绝旧开发目录，不自动迁移或接管。
- 撤回本地 rc1 的过早冻结；删除历史 API 签名快照，改为验证当前 Schema/codec 和生命周期行为。
- 保留轻量 Binding、registered/managed 权属区分、原子发布、增量复用、固定保留、可恢复回收与候选清理。
- 将引擎、示例、并发恢复、权属和读取成本测试统一到当前模型。
- 保留 uv 构建、Apache-2.0、Python 3.11/3.12 CI、CodeQL 与 Dependabot。

首版冻结前先完成真实应用接入。尚未取得 NFS/真实断电资格；控制历史会积累；依赖图、元数据压缩和完整审计尚未实现。
