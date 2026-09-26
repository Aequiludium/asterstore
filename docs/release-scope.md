# 首版本地发行范围与冻结检查

状态：2026-09-26，包版本 `0.1.0.dev0`。本文整理首版候选范围和当前证据，**不是 API 或协议已冻结、版本已发布的声明**。完整目标规范见 [spec](spec/README.md)，阶段记录见 [roadmap](roadmap.md)。

## 1. 首版交付边界

首版定位为独立的本地文件生命周期管理库：登记外部文件声明，或发布库管理的不可变文件对象；提供轻量绑定、显式固定保留、退役、回收与失败候选恢复。核心零第三方运行依赖；可选 Polars 只负责原生路径接入。业务解码、schema、PIT、调度、数据质量和外部生产者的写入协调由调用方负责。

| 能力 | 当前 v4 行为 | 首版约束 |
| --- | --- | --- |
| 外部目录登记 | register/describe/open；控制根与外部资源根分离 | 不复制、扫描或删除外部数据；不承诺外部字节保留 |
| 受管发布 | 固定 operation_id/generation、完整 FileSet、单数据集原子 current | 调用者关闭原生写入器后 seal；无跨数据集生产事务 |
| 对象复用 | 同仓同数据集复用、多个成员别名 | 不自动去重、不复制或重新同步来源数据；reuse 仍有完整保护快照成本 |
| 普通读取 | current 只加载 marker/current；绑定后选择没有治理文件 I/O | 不轮询、不续租、不逐文件预检；旧 Binding 不是保留根 |
| 固定保留 | 明确 publication_id 与 METADATA/OBJECTS，按 revision 释放 | registered 拒绝 OBJECTS；METADATA 不保护字节；无自动读者租约 |
| 物理回收 | 显式预览、固定计划、先退役后删除、按原计划恢复 | 仅删除受管且有权属证据的文件；未知文件不会自动获得删除授权 |
| 失败候选 | 查询状态、封存恢复、显式 abandon、固定清理与恢复 | 不自动重做未封存生产、不重新变基、不复用已放弃操作身份 |
| 严格控制格式 | v4 Schema/codec、必要功能门槛、Store/对象身份约束 | 普通 open 不是全面审计；未知治理能力必须拒绝 |
| 部署 | 本地 Linux/POSIX 的进程协调和故障注入证据 | NFS 写入/GC、多客户端服务故障及真实断电未取得资格 |

暂不纳入首个有限功能版本：deterministic 布局、业务附件关联、Release/公开 Pointer、发布依赖图、元数据压缩、完整分层审计、自动迁移与 Aster 生产接管。它们继续保留在目标规范中，不用占位接口宣称实现。

## 2. API 与协议边界

新应用使用 v4：

- 数据模型：`Declaration`、`FileSet`、`Member`、`Object`、`Locator`、`Capabilities` 及能力枚举。
- 入口：`Repository.initialize/register/describe/open/bind`，`registration_status/resume_registration`。
- 受管写入：`prepare_managed/resume_managed/managed_status` 及返回的 `ManagedCandidate`。
- 生命周期：`Repository.governance` 的 retain/get/release/open、preview、collect/resume_collection、abandon、preview_cleanup/cleanup/resume_cleanup。
- 可选引擎：`asterstore.integrations.polars.scan_parquet`；原生引擎自行打开 Binding 提供的路径。

当前顶层同时导出旧 `Dataset/Publication/ObjectRef/Candidate/Retention`。它们属于 v3 流程，不能与 v4 声明自动互换。`Repository.prepare/resume/retention/inspection` 仍是旧流程入口，不是 v4 接口的别名。`Repository.open` 因兼容旧协议而保留联合返回类型。此次整理不删除旧导出；冻结前需要明确其稳定性与后续弃用策略。

| 仓库格式 | 普通读取 | 发布/治理 | 升级 |
| --- | --- | --- | --- |
| v1/v2 | 按旧模型读取；遵守原历史能力 | 新治理修改拒绝；v2 已有固定引用保留 get/open | 无自动升级 |
| v3 | Publication 绑定 | 原 prepare/retention 生命周期 | 不与 v4 混写 |
| v4 registered | Declaration 绑定，打开时捕获资源根 | register；启用 lifecycle 后可直接保留元数据 | 必要功能组合显式初始化 |
| v4 managed | Declaration 绑定，受管根由库固定 | prepare_managed；启用 lifecycle 后保留/回收/候选清理 | 不靠修改 marker 接管旧数据 |

磁盘协议版本、必要功能组合和 Python 包版本是不同维度。相同数字 v4 不表示任意开发快照彼此兼容；例如候选清理增加了必须被治理识别的记录。现阶段保留隔离实验数据，不宣称开发快照间无迁移升级。冻结时需同时固定 Schema、codec、功能组合与恢复状态机。

## 3. 已有验收证据

| 边界 | 可执行证据 | 证据范围 |
| --- | --- | --- |
| 零依赖与发行包 | [check_distribution.py](../tools/check_distribution.py) | wheel/sdist 重建、源码外隔离安装、无引擎核心及可选 Polars |
| 固定声明与轻量读取 | [test_declarations.py](../tests/test_declarations.py)、[test_io_contract.py](../tests/test_io_contract.py)、[test_registration.py](../tests/test_registration.py) | 资源捕获、成员选择、只读控制记录与外部数据边界 |
| 发布与恢复 | [test_managed.py](../tests/test_managed.py)、[test_managed_processes.py](../tests/test_managed_processes.py)、[test_registration_processes.py](../tests/test_registration_processes.py) | CAS、共享对象、原操作恢复、本地竞争与进程终止 |
| 保留与回收 | [test_governance_v4.py](../tests/test_governance_v4.py)、[test_governance_processes.py](../tests/test_governance_processes.py) | 两种保留范围、权属、固定计划、竞争与恢复 |
| 失败候选清理 | [test_managed_cleanup_v4.py](../tests/test_managed_cleanup_v4.py)、[test_managed_cleanup_processes.py](../tests/test_managed_cleanup_processes.py) | 固定文件范围、复用隔离、清理中断恢复 |
| 独立审计修复 | [审计记录](audit-fixes-v4.md)、[回归测试](../tests/test_audit_regressions.py) | 首次发布残留、强同步重试、增量清单、未知控制条目 |
| 规模成本 | [测量说明与结果](../benchmarks/README.md) | 分开观察成员构建、历史/数据集/引用/日志、回收同步；无生产容量承诺 |

审计修复后的 Python 3.11/3.12 全量测试各 535 项通过。测试数量包含旧协议回归，不等于规范 A-01 至 A-28 全部验收。共享挂载与未交付功能的场景仍未通过。

## 4. 发布前剩余事项

1. **API/格式冻结评审。** 确定上述有限范围为首版契约，核对公开错误、返回类型、旧接口稳定性、开发格式兼容边界；不能仅凭测试全绿就宣布冻结。
2. **控制历史成本决策。** 使用规模曲线评估目标部署；当前保留永久身份、候选和回收证据，GC 与完成重试遍历全仓控制记录。首版可公开此限制；尚无基于实际部署负载的容量上限或压缩能力。
3. **开源与发布材料。** 已补齐远程项目链接、维护者组织、贡献指南、安全报告说明与 CHANGELOG。Apache-2.0 及 LICENSE 已确定并纳入制品检查；最终版本号仍待冻结评审后选择。
4. **发行验证与实际发布。** 冻结后重跑锁定环境测试、规模 smoke、wheel/sdist 隔离检查，保存最终制品摘要；然后建立发布来源、标签和上传流程。源码远程为 [Aequiludium/asterstore](https://github.com/Aequiludium/asterstore)，默认分支 main。CI 只有检查任务；GitHub Actions 的实际结果以对应提交的运行记录为准，源码推送不等于包索引发布。

当前能力足以进入有限范围的本地试用准备，但距离公开首版仍有上述收口工作。无需为了首版补齐全部远期目标，也不把未完成目标从规范中删除。
