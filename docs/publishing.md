# 发布与回退

完整可运行示例见 [two_versions.py](../examples/two_versions.py)。先 prepare，再写入和 seal；业务检查候选 Binding 后显式 commit。退出上下文不提交，不自动删除失败产物。

每一次 commit 都把旧 current 作为 rollback。库不识别“一次每日任务”或多个计算批次；调用方应在业务希望原子生效的位置提交。

回退必须传入观察到的 revision。它将 rollback 提升为 current，并清空 rollback，不把故障版本当成安全版本。下次成功发布恢复两个槽位。

响应丢失后 describe 并比较 version_id。current 命中意味着当前已发布；rollback 命中意味着曾被发布并仍被保留；两者都不命中时不能永久判断旧请求结果，应基于最新状态重跑。

默认强持久化；prepare/rollback 的 durable=False 保留原子可见性，但不承诺机器故障后的持久性。清理不会直接信任弱提交做删除，而先同步有效状态。
