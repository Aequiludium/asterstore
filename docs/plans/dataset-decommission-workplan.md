# 数据集批量下线实施计划

状态：2026-10-13，规划、实现、自查与本地验收已完成；已进入复审后的分批提交阶段；未推送或发版。验证记录见 [验收报告](../validation/dataset-decommission-2026-10-13.md)。

## 冻结契约

- 输入是明确且非空的 dataset_id → expected_generation 映射；过期时间和筛选由应用决定，不引入读取时 TTL、后台任务或名称通配符。
- 首次执行在 GC 协调锁内检查全部目标存在、在线且 generation 匹配，然后持久化固定批次计划。校验失败不下线任何目标。
- 每个数据集通过原子替换 current 为包含最后完整声明的 dataset_tombstone 下线。最后声明先写入并同步 history。下线不可逆，dataset_id 不复用；未来上线新数据使用新 dataset_id。
- 默认 open 和显式历史 open 均拒绝已下线数据集；describe(dataset, publication_id=...) 仍提供审计。describe(dataset) 拒绝，下线详情由 dataset_status 查询。list_datasets 默认只列在线，可显式包含已下线。
- 默认 current open 仍只读 marker + current，无治理 stat/scan/锁；Binding 和纯内存 bind 不新增检查，已有路径不被远程撤销。
- 新 prepare/新提交被拒绝，未完成候选仍可显式 abandon/cleanup，其已有保护不自动解除。已提交请求重试可以查询原结果并履行持久化，但不恢复在线；退役后沿用既有拒绝规则。
- 不建立新的对象 retention；已有活跃对象 retention 保留，并允许其消费者通过 governance.open 读取。metadata retention 可用于审计，release 不受下线影响。
- 下线解除 current 的保护根；实际 managed 数据回收仍走显式 GC，保留对象与未完成候选继续阻挡删除。registered 外部字节不被删除。
- 批次不是跨数据集原子事务。单数据集可见点是 tombstone 替换；同 operation_id 固定清单并支持恢复。未处理目标已推进 generation 或被另一任务下线，记录为 conflicts，不触及新状态。complete 表示无 pending；调用方还须检查 conflicts，不能把 complete 当作全部下线成功。
- decommission_status 返回 decommissioned/pending/conflicts；resume_decommission 从原计划恢复，不重新筛选数据。不提供强制删除或撤销批次。

## 协议与实现

增加 dataset_decommission 必要功能（要求 lifecycle）、dataset_tombstone 与 decommission_plan 两种严格记录，format_version 保持 1。旧版本拒绝新 feature，不静默解释为普通发布。

计划存于 .asterstore/decommissions/<operation-token>.json。tombstone 内的批次身份、最后发布及计划相互核验；没有 tombstone 的计划允许尚未执行或冲突。历史、原发布操作、权属、退役、retention 的完整证据继续保留。

实施依次接入：codec/schema → head 解析与读取/写入边界 → 固定批次执行/状态/恢复 → inventory/GC/诊断/压缩兼容 → 示例、回归、故障进程、发行制品验证。

## 验收

覆盖 managed/registered、空发布、批量 generation 失败、同 ID 换清单冲突、部分恢复与新发布冲突、current 两读、retain/GC/pending writer 竞态、历史审计、已提交弱→强重试、旧客户端拒绝、归档前后下线与 GC。每个 marker/plan/history/tombstone 持久边界验证中断恢复。实际业务目录不自动执行下线。

## 完成情况

- codec/schema、head 读写边界、固定批次及恢复、inventory/GC/诊断/压缩接入已完成。
- 初次交付新增 24 项回归：包含四个实际子进程持久边界退出、原计划恢复与新代际冲突、保护关系、外部文件、控制记录损坏和归档兼容。
- 自查修复治理检查与 inventory 解码入口不一致导致的重复读取；批次关联核验使用临时索引避免逐目标扫描计划。
- 初次交付的 419 项全量测试、Ruff、mypy、15 类协议检查及独立发行制品安装验证通过。后续独立复审及修复见 [复审记录](dataset-decommission-review.md)。
- 下线语义为永久终止身份；没有自动 TTL、恢复上线、批次原子事务或强制解除 retention。未操作实际业务数据目录。
