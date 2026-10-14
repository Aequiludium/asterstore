# 0.4 / 格式 2 不兼容变化

这是新架构，不是格式 1 的自动升级。当前版本拒绝读取、初始化或修改旧 marker，没有生产迁移命令。先在独立空目录验证，不对旧生产目录直接运行写入或清理。

保留 Repository、固定 Binding 与通用文件读写用途；替换 API 为 prepare/allocate/seal/commit、rollback 和 clean。删除 registered 外部登记、任意 publication_id 历史读取、resume、candidate_status、governance、retention、compaction、decommission 及独立 ObjectRecord。

新版本标识由库在 Draft 中生成；业务 run ID 可另存普通成员。Binding 返回 manifest，不再返回旧 DeclarationRecord。expected_generation 改为 head expected_revision，回退也递增 revision。

新旧清理不能共同拥有同一组数据文件的删除权。未来迁移需显式选择 current/安全 rollback、确认文件归属并隔离核验；本次未执行这些操作。

修改前格式 1 测试基线为 427 passed。格式专属测试随旧实现退出，新增格式 2 的行为、并发和故障测试；旧测试数量不代表新协议验证数量，也不能直接比较为兼容性证明。
