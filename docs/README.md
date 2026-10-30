# 文档导航

AsterStore 以数据集目录为边界，只管理本次文件变更、当前发布元数据和一次撤销材料。

| 目标 | 文档 |
| --- | --- |
| 初始化和首个发布 | [项目首页](../README.md) |
| 安装、Python 和 Polars 版本要求 | [安装与兼容性](../README.md#安装与兼容性)、[Polars 读取](reading.md) |
| 选择数据目录和文件路径 | [目录与路径](paths.md) |
| 更新、回退和恢复 | [发布与回退](publishing.md) |
| Hive 读取与维护窗口 | [读取](reading.md) |
| 清理失效材料 | [清理](cleanup.md) |
| 查询接口 | [API](api.md) |
| 理解边界、成本和磁盘记录 | [架构](design.md)、[协议](protocol.md) |
| 开发和规模验证 | [贡献指南](../CONTRIBUTING.md)、[基准](../benchmarks/README.md) |
| CI 和发行 | [自动化](automation.md)、[发行](releasing.md) |

示例和测试均使用临时目录。生产目录的清理与重建是单独部署操作，不属于测试步骤。

## 设计历史

[轻量重构设计记录](history/lightweight-refactor-plan.md) 保存需求与取舍依据，不是当前 API 或操作指南。发行记录见 [CHANGELOG](../CHANGELOG.md)。
