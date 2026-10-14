# 双版本协议：生产与消费接口迁移调查

> 范围更新：用户明确本轮只需设计 AsterStore。本文保留为后续集成参考，以下下游适配建议和联合实施批次不属于当前工作，不是 AsterStore 设计/实现验收的前置条件。当前主线见[最小协议](dataset-two-version-protocol.md)。

2026-10-14。只读调查及设计建议，未修改生产端、Aster 封装、依赖或数据，未运行发布/清理。以本次本地源码与 data-team 虚拟环境元数据为证据，不断言其他运行进程使用相同环境。

## 1. 职责边界与版本基线

用户指定生产项目 `/home/huangbogeng/github.com/Aequiludium/workspace/data-team`，消费封装 `/home/huangbogeng/github.com/Aequiludium/aster/aster-data`。链路为：

```text
data-team：拉取、规范化、质量检查、更新计划和修复
    → aster-data DatasetWriter：业务清单、日期成员、分区约定
    → asterstore：文件归属、版本发布、回退、维护清理

asterstore Binding
    → aster-data：业务清单、Datacenter、表读取、日历/PIT 语义
    → 业务消费者
```

data-team 还直接调用 asterstore 的候选恢复和治理内部函数，不能只升级 aster-data 就认为迁移完成。

实际读取 data-team `.venv` 的 distribution 元数据：

| 包 | 安装版本 | Git revision |
| --- | --- | --- |
| asterstore | 0.1.0 | 4f1bfff9da0c947cb60c6c4b0f1293078accc649 |
| aster-data | 0.1.3 | 74db62e3bbc17a74766c556c1cca2fa86427a767 |
| aster-protocol | 0.2.0 | 74db62e3bbc17a74766c556c1cca2fa86427a767 |

data-team/pyproject.toml 的 uv override 固定上述 asterstore revision；本地 aster-data/pyproject.toml 指向另一 asterstore revision `6f2dbad343d0fffdef6df14933311de86fd1c16f`。本地 Aster 有大量既有未提交改动；本次未触碰。data-team 路径 git status 报非 Git 仓库，按现有文件调查，不假定 HEAD 基线。

对比安装文件：DatasetWriter 与本地同名文件完全一致；业务元数据加载器的主要差异是本地额外保留 store_bindings；本地 RetentionSession 在该生产环境不存在。默认连接和 context 也有差异。因此下表区分生产依赖和本地封装新增能力。

## 2. 实际依赖与迁移表

下列路径以相应项目根目录为基准。

| 调用点 | 当前行为 | 双版本适配 |
| --- | --- | --- |
| data-team `data_team/publishing/_store.py:read_state` | describe 后按 publication_id 再 open，取得 generation 和业务 manifest | 一次 open 的 Binding 同时提供 head revision 与 manifest 身份，避免两次独立读取混用状态 |
| aster-data `store/_writer.py:DatasetWriter` | prepare、按日期 path、指定历史版复用、业务 manifest、seal/commit | 保留业务日期检查和包装；底层换 Draft，复用锁内 current，移除持久操作 ID/HistoryAccess |
| data-team `production/_run.py:execute` | 每个 batch 独立 commit；替换时 retain 旧版 | 改为任务内分批计算、数据集级一次正式提交的建议；rollback 取代旧版 retain |
| data-team `production/_stock.py:execute_stock` | 日常更新调用 build_repair，再 publish_repair；依赖参考表旧版本 | 合并日常构建与提交生命周期，参考表上下文见下一节 |
| data-team `repair/_service.py:build_repair/publish_repair` | sealed candidate 退出上下文后，再 resume(operation) commit | 日常路径同一 Draft 内验证提交；独立审核式修复可保留业务产物，发布时重新核查基线并开新 Draft，不恢复库内永久候选 |
| data-team `repair/_service.py:_hold_inputs` | 给父发布和参考表添加持久 OBJECTS retention | 任务协调保护读取期间，必要旧参考数据成为业务自有快照；移除核心 retain/release |
| data-team `repair/_prune.py` | 直接导入 inventory/execute/plan 编码和全仓 gc.lock | 完整替换为逐目标数据集 clean；保留股票范围选择，去掉私有接口耦合 |
| data-team `ops/_cli.py` | recover/retain/release/gc 命令 | 改为状态查询、重跑提示、rollback、按数据集 clean；旧命令不能静默换义 |
| aster-data `store/_metadata.py` | 读取 aster:publication；核对身份；检查 Hive 父目录 | 保留业务附件和相对路径后缀；适配 Binding 字段，不改业务表语义 |
| aster-data `_default.py` | 首次连接缓存 current bindings，显式 reload | 保留固定读取；维护清理后重新连接，旧 Dataset/LazyFrame 不可继续使用 |
| 本地 aster-data `store/session/_session.py` 与 `context.py:session` | 持久 retain/release，进程退出可能留保护记录 | 从新模型退出；不能无声改成空上下文仍宣称保护。已确认的维护协调代替生命周期保护 |

session 的存在只证明本地封装支持此能力，不证明生产消费者都调用它；其他项目的实际使用仍需在实施迁移时搜集。

## 3. 必须保留的三项实际能力

### 3.1 业务元数据作为普通文件成员

`aster:publication` 不是可删除的调试注释。它包含 schema、coverage、ready_through、PIT/存储约定以及修复信息，是 Datacenter 读取入口。

新核心继续允许任意文件格式作为普通成员；Aster 封装解释该 JSON，核心不理解其业务字段。清单和这个文件要一致地指向同一版本。库分配 version-id 后，包装层再构造 PublicationManifest，operation/run ID 可另作业务报告身份；不再要求它们全部相同。

### 3.2 Hive 分区路径

当前加载器明确要求日文件的父目录是 `date=YYYY-MM-DD`（或 descriptor 指定列）。新 allocate 不能只输出随机扁平文件名。

建议保留受限 relative_path：`objects/<库生成唯一前缀>/data/date=YYYY-MM-DD/data.parquet`。库控制唯一前缀和归属，包装层控制安全相对后缀；拒绝绝对路径、父路径逃逸和软链接。业务路径规则无需进入核心。临时拉取文件仍在 staging，最终只安装声明成员，不能顺带安装 `.source.parquet` 等中间产物。

### 3.3 参考表的历史比较输入

`market_identity.py:IdentityContext.load` 按明确 publication_id 读取参考表，`production/_stock.py` 会用上次行情 manifest 的 references 重建旧上下文，与新的代码关系比较，再判断是否需要历史行情修复。源表更新多次后，仅靠它的 current/rollback 不保证仍能得到行情上次使用的版本。

这是业务依赖历史输入，不是跨数据集物理共享；但直接删除任意历史读取会破坏现有逻辑。不能把旧 references 偷换为最新数据，也不能只保存 hash，因为差异比较需要旧关系内容。

建议把用于比较的最小完整 IdentityContext（intervals/owners 及算法需要的字段、规则版本与来源身份）作为行情数据集自己的普通成员保存；它和行情 current/rollback 一起保留，在同一数据集内复用。首次迁移从旧参考发布构建并验证比较结果。这样不要求核心保留第三个数据集的历史，也不恢复引用图。此方案尚待实现前验证，不能假设任意摘要都足以重建现有比较语义；另计日线/分钟线各自拥有副本的成本。

## 4. 一次更新不等于一次发布

`production/_run.py` 在 batch 循环中 commit，`production/_stock.py` 也逐 batch 构建并发布；普通计划默认 batch_size=20。历史补全或大量修复时，一次任务会创建多个累计完整版本。这与此前实测成员数 21→41→61 的现象相符，但本次未重跑任务，不把所有历史记录归因于同一个调用。

若新协议照搬这个流程，rollback 可能只是同一次更新的上一批中间结果，而不是上次日常更新的安全版。

建议生产调度语义改为：计算和验证可按批次进行，正式发布在该数据集本次目标完成后只做一次。由此 rollback 才对应前一次成功更新。失败重跑成本增加是明确取舍；可把已计算文件留在生产工作目录减少重算，但它们不是已发布版本，发布前仍重新核查基线和业务完整性。不为加速重跑增加核心持久候选事务。

## 5. 维护窗口的封装落实

停止新的读取还不够：默认 Datacenter 缓存、旧 Dataset 对象和延迟执行的 LazyFrame 都可能固定旧文件路径。任务必须结束到最后一次 collect；清理后新任务重新绑定，长驻进程需 reload/重建相关句柄。reload 不会修复已发出的旧句柄。

不改业务查询、日历和 PIT 算法，不增加每次 read 的版本轮询。维护协调属于任务调度边界，核心 clean 的 quiescent 参数不制造自动保护。

## 6. 建议实施批次（尚未开始）

1. asterstore 新格式、Binding/Draft、head 原子发布和回退；故障注入与有限版本正确性。
2. aster-data Writer/业务文件/分区路径适配；数据读取与业务 manifest 身份一致，保留固定 Binding 行为。
3. data-team 一次任务一次发布、修复流程整合、参考上下文快照与报告身份分离；比较原有业务结果。
4. 数据集 clean、调用方维护流程与私有治理依赖移除；验证无旧读任务、保留两个版本和中断重试。
5. 隔离迁移与联合验收后再切换依赖和生产。旧数据和控制记录在明确切换前不变更，新旧 GC 不同时管理同一组文件。

验收至少覆盖：日文件与单文件数据集、增量复用、业务检查失败不推进 head、多个计算 batch 仅一次正式提交、参考表更新超过两次仍可比较上次行情上下文、回退、维护后重建消费者、缺失附件/非法分区路径拒绝。

本次不扩大到 aster-factor 等其他封装的源码审计；如果新格式也用于因素仓库，它们必须在生产切换前单独纳入兼容检查，不能将本次两条链路的调查当作全 Aster 已兼容。
