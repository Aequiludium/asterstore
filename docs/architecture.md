# 项目结构

只维护一套声明模型、一条提交协议和一套治理状态机。

```text
src/asterstore/
├── __init__.py          # 公开类型与 Repository
├── repository.py        # 初始化、绑定、登记、发布与治理入口
├── errors.py
├── metadata/
│   ├── capabilities/    # 合法保证组合
│   ├── declarations/    # Declaration
│   ├── membership/      # Member、Object、Locator、FileSet
│   ├── identity/        # 逻辑身份与物理路径验证
│   ├── protocol/        # 唯一持久格式、权威记录及严格 codec
│   └── schemas/         # store.json
├── reading/             # Binding、持久读取与资源根捕获
├── registration/        # 外部声明登记与操作恢复
├── publishing/          # Candidate、对象创建权属、封存及恢复
│   └── transactions/    # registered/managed 共用提交生效点
├── governance/          # 固定保留、扫描、退役、回收及恢复
│   └── cleanup/         # 已放弃候选的清理
├── storage/             # 锁、精确删除、原子写入与同步
│   └── registry/        # 控制路径、权威记录读写
└── integrations/        # 可选引擎适配；核心无引擎依赖
```

registered 和 managed 是同一模型的两种管理方式，区别在对象创建权属和可提供的保证，不是两代协议。二者共用声明记录和 current 提交逻辑。

metadata 不做文件 I/O；storage 不决定业务或治理策略；publishing/registration/governance 编排显式状态转换。reading 只读取必要控制记录，已绑定成员选择没有治理 I/O。完整清单校验在声明构造与封存边界完成，候选逐成员添加使用增量索引。

子包接口用于内部协作；公开入口以 [API](api.md) 为准。Schema 和 fixtures 是当前结构证据，权属、恢复、同步顺序与并发语义由行为测试验证。
