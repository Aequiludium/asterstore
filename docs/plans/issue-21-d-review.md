# Issue #21 D 独立复审记录

日期：2026-10-13。评审者：用户指定的 GPT-6.1 Sol 子代理 review_issue21_d。只读评审，未修改工作区。范围包括归档格式、控制读取接入、固定计划恢复、权属、幂等、持久化和 current 开销。

## 首轮发现及修复

P2：原实现用 compact operation_id 命名 pack。pack 已持久化但 checkpoint 尚未写入时中断，期间若恢复普通发布，同 ID 重试根据新 inventory 生成不同 pack，触发 immutable 冲突，任务无法继续。评审者独立复现；没有造成数据丢失，但不满足恢复预期。

修复：pack 改为内容 SHA-256 地址，checkpoint 仍由 operation_id 定位。checkpoint 落盘前没有逻辑记录切换，重试可重新规划；旧遗留段不覆盖、不删除。checkpoint 已持久化后固定目标，解析器强制单个非空归档，恢复完整校验 pack 内容地址。新增 test_orphan_pack_retry_after_intervening_publication 固定此行为。

## 第二轮与最终复审

评审者运行当时全部 26 项 compaction 测试，并独立执行：两轮压缩，中间新增发布和 reuse 历史对象，再次重试两轮 compact；5 个 managed 发布逐一由弱持久升级至强持久；最后执行 inspect。全部通过，未发现新 P0/P1/P2 阻断缺陷。

收尾增加 candidate writer.lock 防护后再次复审：非阻塞获取避免与 writer→gc 既有顺序形成阻塞死锁，ExitStack 释放已获锁，缺失持久锁的候选请求拒绝。新增活跃 writer 单测通过。marker 增长纳入正收益门槛，在任何写入之前检查。

最终结论：D 实现通过独立只读复审，未发现新阻断缺陷。非阻断口径提醒已落实：CompactionResult.saved_bytes 不扣 marker 增长，正式结果使用控制文件物理长度的前后差，并在 API 文档说明字段范围。

主代理还完成全套回归、13 类协议验证、空间/延迟复测、wheel/sdist 隔离安装和安装后归档恢复验证。完整记录见 [D 交付报告](../validation/issue-21-d-2026-10-13.md)。复审不代表已经验证真实断电、网络文件系统或生产规模。

最终还补充继承控制证据同步：在正收益检查后、marker/pack/checkpoint 写入前同步 inventory 控制路径，避免弱持久来源的身份/current 证据未落盘。评审者只读核对次序与数据边界，无新增阻断项。主代理新增相应回归并跑通当时 394 项测试。

复测发现单次 inventory 重复解压同一归档块，新增仅限每个 ControlReader 的完整键缓存后再次独立复审，针对单测通过。每次读取仍验证引用路径及结构，首次解析完整校验；缓存不跨调用。最终全套测试为 395 项，复审无新增阻断项。
