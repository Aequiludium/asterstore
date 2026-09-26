# 项目结构与模块边界

第一版是单个 Python 发行包，以 uv 管理环境和依赖，以 `uv_build` 构建。支持 Python 3.11 起的语法与标准库；默认开发解释器为 3.12。核心当前没有第三方运行时依赖。

目标能力的演进边界见 [元数据规范](spec/metadata.md#5-库结构约束)。下文只描述当前源码；registration 已接入 v4；独立 catalog、layouts/graph 能力尚未全部实现。

## 源码结构

```text
src/asterstore/
├── __init__.py           # 面向使用者的公开导出
├── py.typed
├── errors.py
├── repository.py        # 入口与操作编排
├── metadata/            # 声明、协议编码与兼容
├── reading/             # 绑定与文件选择
├── registration/        # v4 外部声明持久登记与恢复
├── publishing/          # 候选、提交与发布恢复
├── retention/           # 引用、可达性与回收
├── inspection/          # 显式检查与报告
├── storage/             # 文件布局、原子操作与协调
└── integrations/        # 可选引擎接入
```

目录表示能够独立维护的职责。每个子包的 `__init__.py` 导出允许其他子包使用的接口，带 `_` 的文件属于该子包内部实现。跨子包导入使用接口，不直接引用其他子包的私有文件。

顶层 `asterstore` 导出用户入口与常用类型。其他子包导出的内部协作接口不自动成为稳定公共 API；对外范围以 [API 文档](api.md) 为准，当前开发版本尚未承诺兼容冻结。

`repository.py` 只组织操作。发布状态机属于 `publishing`，引用与 GC 一起属于 `retention`，显式检查属于 `inspection`。发布恢复与回收恢复分别由各自所属模块维护，不设一个理解所有流程的全局 recovery 模块。

## 当前已实现的部分

- `metadata`：不可变声明、候选/发布记录和严格的版本化 JSON 编解码。
- `storage`：路径布局、原子写入、目录同步、POSIX 锁和控制记录访问；运维专用 inventory 扫描不进入数据目录。
- `reading`：绑定声明、读取 current/历史、复用路径并限制选择范围。
- `publishing`（包括内部来源选择模块）：来源选择、共享成员封存、generation 冲突检测、原子提交、状态查询与恢复。
- `retention.references`：具名引用、revision 重试/冲突、条件释放和受保护绑定。
- `inspection`：候选私有残留扫描、字节统计和未知位置报告，不自动修复。
- `retention.cleanup`：已放弃候选的固定范围清理及重试。
- `retention.collection`：元数据关系计算、回收预览、持久执行/恢复和阻塞原因报告。
- `registration`、`publishing.managed` 与 `publishing.transactions`：v4 声明登记、候选管理及共享提交边界。
- `retention.governance`：v4 固定保留、治理扫描、回收及失败候选清理。
- `Repository`：提供 v4 初始化、登记、发布、读取和 governance 入口，同时保留旧 prepare/retention/inspection 流程。

`inspection` 已提供显式候选残留诊断；`integrations/polars/` 提供可选的明确成员 Parquet 扫描接口。完整审计尚未实现；引用、预览和物理回收已提供真实 API，候选状态查询不等同于全仓审计。

## 依赖边界

`metadata` 仅依赖标准库和库的异常类型，构造声明不执行文件 I/O。`storage` 使用记录编解码及路径约定；`reading` 和 `publishing` 使用两者的公开协作接口。`repository` 只负责入口编排。

普通文件选择不得调用检查、恢复或引用变更。引擎适配器依赖核心接口，核心不反向导入适配器。导入包不加载数据引擎、不连接数据源、不创建仓库。

## 开发文件

- `pyproject.toml` 集中包元数据、构建设置、开发依赖和检查配置。
- `uv.lock` 由 uv 生成并随项目保存；`.venv`、缓存和 `dist` 不进入版本控制。
- `tests/` 验证可观察行为与 I/O 成本约定，不按每个私有辅助函数机械建测试。
- `examples/` 只包含当前能够执行的示例。
- `benchmarks/` 保存测量方法及后续测量脚本，不将行为测试宣称为性能数据。
- `tools/check_distribution.py` 检查直接构建的 wheel、从 sdist 重建的 wheel，以及独立环境中的安装。
- CI 配置在 Linux 上检查 Python 3.11 和 3.12。本地 POSIX 协调已实现，共享挂载资格仍需单独验证。

## 保留子包结构

引用管理和回收各自封装状态转换，通过接口协作，设计见 [保留协议](retention.md)。当前结构为：

```text
retention/
├── __init__.py
├── _service.py             # Repository.retention 的入口编排
├── references/
│   ├── __init__.py         # 具名引用操作接口
│   └── _operations.py      # 创建、读取、释放与 revision 协调
├── cleanup/
│   ├── __init__.py         # 候选私有文件清理接口
│   └── _operations.py      # 固定计划、逐文件删除与重试
└── collection/
    ├── __init__.py         # 预览、执行和恢复接口
    ├── _models.py          # 不可变预览报告
    ├── _reachability.py    # 纯元数据关系计算
    ├── _preview.py         # 锁内观察与报告
    └── _execution.py       # 退役、删除、进度和原计划恢复
```

上述文件均已实现。引用、退役等持久记录由 metadata 定义，文件系统原语留在 storage；retention 不另造序列化和锁体系。公开的数据检查能力仍由 inspection 承担，不混入引用建立或普通读取。storage 的删除原语只处理确切受管普通文件，不递归清理；哪些对象可以删除由 collection 在独占锁内决定。

身份规则封装在 `metadata/identity/`，以包入口导出语义校验；物理对象键继续独立校验。`metadata/schemas/v3.json` 是随发行包安装的静态协议资源，普通读取不加载它。

新模型按封闭职责放在 `metadata/capabilities/`（合法能力组合）、`metadata/membership/`（对象表和成员映射）、`metadata/declarations/`（输入声明）。`reading/resources/` 捕获纯词法资源映射；新旧输入共享 Binding，不另建一个引擎读取栈。新模型已接入内存绑定与 v4 registered 持久登记，managed 发布也已接入，直接保留状态机也已接入，发布依赖图仍待实现。

`metadata/protocol/` 定义新模型的 v4 权威记录与 codec；`storage/registry/` 只访问控制记录；`registration/` 编排登记、冲突与恢复。普通 open 根据完整 marker 选择协议，已绑定读取共享同一 Binding；v3 managed 状态机不会读写 v4 仓库。

`publishing/managed/` 负责 v4 候选和创建权属；`publishing/transactions/` 为 managed 与 registered 共用提交顺序、对象身份检查和 current 可见点。`storage/registry/` 保存权威记录，不通过数据目录推断管理权。

`retention/governance/` 封装 v4 直接保留、权威记录遍历、回收与恢复；它复用 storage 的精确删除和 metadata/protocol 的严格记录编解码。普通读取不调用全仓遍历。

## v4 失败候选清理

`retention/governance/cleanup/` 封闭清理的预览、权属选择与恢复执行，对外由 Governance 装配；不将清理状态机散入候选写入器。`metadata/protocol/_cleanup.py` 定义严格 plan/progress，`storage/registry/_cleanup.py` 负责身份关联读取，文件删除继续复用 storage 的逐文件原语。治理 inventory 验证清理证据，已清理的未提交对象不再计入待保护或待 GC 对象。所有扫描只在显式治理边界发生。详见 [清理规范](cleanup-v4.md)。
