# Changelog

## 0.1.0rc1 — 本地候选，尚未发布

首个有限功能候选；API/协议冻结边界见 [兼容决议](docs/compatibility.md)，未上传包索引。

### Release preparation

- 冻结 0.1 公共 API 和协议证据，CI 拒绝未经评审的签名/格式差异。
- 保留 0.1 系列中的 v3 兼容入口，新应用推荐 v4。
- 发行工具可保存验证过的 wheel/sdist 与 SHA-256 清单。

### Added

- 独立的声明、对象身份、资源映射和轻量 Binding；核心零第三方运行依赖。
- v4 registered 持久登记、managed 原子发布、别名、同数据集复用与候选恢复。
- 显式 metadata/objects 固定保留、条件释放、退役、固定计划回收及中断恢复。
- 候选永久放弃、权属约束的残留清理及原计划恢复。
- 可选 Polars 路径接入、协议 Schema/golden fixtures、本地并发与故障测试。
- uv 构建、wheel/sdist 隔离检查、Python 3.11/3.12 CI、成员与治理规模基线。
- Apache-2.0 许可证、贡献与安全报告说明、远程项目元数据。
- v1/v2 读取与 v3 生命周期兼容；新应用使用 v4，旧仓库没有自动升级。

### Fixed

- 首次 current 写入失败留下的可解释空目录不再阻断全仓治理。
- 完成态 GC 重试重新验证并同步控制证据。
- abandon 在发布永久放弃记录前同步已有候选依赖证据。
- path/alias 增量建立清单，避免逐成员反复全量校验。
- 未知顶层控制条目阻断治理操作。

### Limitations

- 仅有本地 Linux/POSIX 验证；未取得 NFS 写入/GC 或真实断电资格。
- 控制历史长期积累；完成 GC 重试也有全仓验证与同步成本。
- 发布依赖图、Release/Pointer、deterministic 布局、附件关联、压缩及完整审计仍未交付。

证据与首版冻结缺项见 [release-scope](docs/release-scope.md)，具体修复见 [audit-fixes-v4](docs/audit-fixes-v4.md)。
