# 数据集整批下线验收 — 2026-10-13

本次结果针对本地 0.2.0 工作树中尚未发布的下线实现，叠加此前 #21 A–D 变更。此前 D 阶段报告是其当时的独立证据，不作为本次源码哈希证明。初次交付时没有提交、推送或发布，也没有下线用户业务数据。后续按用户要求完成独立复审和本地分批提交；复审记录见 [审查与修复](../plans/dataset-decommission-review.md)。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| `uv run pytest -q` | 419 passed，21.68s；无警告 |
| 新增 `tests/test_decommission.py` | 24 项测试通过 |
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 160 files already formatted |
| `uv run mypy` | 89 个源码文件通过 |
| `uv run python tools/check_protocol.py` | 15 类控制记录通过 |
| `uv run python examples/decommission_datasets.py` | 通过 |
| `uv run python tools/check_distribution.py --polars` | wheel、sdist 重建 wheel、两份独立安装 smoke 及 Polars 2.0.0 通过 |
| HEAD 0.2.0 解码器兼容测试 | 隔离导出已提交源码，确认拒绝 dataset_decommission marker |
| `git diff --check` | 通过 |

制品仍使用现有开发版本号 0.2.0；这里只验证本地构建，不代表更新 PyPI。测试产生的数据均在临时目录中。

## 覆盖与自查

- 批次排序、代际校验、重复操作固定清单，部分完成后新 generation 和另一批次下线冲突。
- 分别在 marker、计划、history、tombstone 持久写入完成后对子进程执行 os._exit，父进程重新加载并恢复。覆盖进程中断，不等同于物理断电存储设备测试。
- 下线后普通/显式历史 open、新发布拒绝，历史 describe 和已提交重试仍可用；current open 两份控制记录，无新增治理读取。
- 对象 retention、元数据 retention、已有消费者、待处理复用候选及后续 abandon/cleanup/GC。
- registered 外部字节不被回收，空发布可下线，缺少/损坏计划、历史和 feature 时 GC 在删除前失败。
- 先归档再下线、下线后 GC 再归档、原提交强持久化重试与下线任务恢复。
- 只读状态查询不写文件；协调锁忙时显式报告。未完成下线计划阻止 compact。

自查修复了两点：治理检查与 inventory 必须都用 decode_head 才能按次复用读取；tombstone 对固定批次的关联核验使用按 dataset_id 索引，避免大批次平方级扫描。所有额外盘点与证据同步留在治理操作，默认读取不增加文件 I/O。

## 明确边界

下线不可逆，dataset_id 不复用；整个批次不保证同时可见。complete 仅代表没有 pending，仍须检查 conflicts。既有 Binding/写入句柄不能撤销；retention 不自动解除，未结束候选继续保护对象。启用功能前须升级所有客户端，不支持旧版本混写或删除 feature 降级。

## 提交前复审增量

独立复审发现并修复归档后 check 误报、首次下线 marker/head 并发交错误报。补充 8 项回归后全套 **427 passed（24.97s）**，下线专项增至 27 项；Ruff、161 文件格式检查、mypy 89 文件、15 类协议均通过。稳定态 current 仍为两读，首次 feature 迁移的交错分支允许一次 marker 刷新。制品复验曾因同时编辑文档触发源码变动保护，冻结源码后重新执行，以完整通过的重跑为准。

冻结文件后的制品重跑完整通过：wheel、sdist 重建、两份独立安装及 Polars 2.0.0。第二批暂存树独立导出后运行 **411 passed（20.27s）** 和 15 类协议检查通过；其不含第三批基准脚本及 16 项基准回归，最终完整树为 427 项。第一批提交为 `50f5002`（设计/接口文档）；第二、三批提交身份可由本分支 Git 历史核对。
