# 磁盘协议：format 4

本协议不读取或转换其他格式。记录 Schema 位于 `src/asterstore/schemas/store.json`；校验命令为 `python tools/check_protocol.py`。

```text
登记目录/.asterstore/
  registry.json           # format、kind、dataset name → 绝对目录
  registry.lock           # 只用于登记

数据集目录/
  <调用方相对文件路径>
  .asterstore/
    dataset.json          # format、kind、name
    writer.lock
    head.json             # revision、operation、publication、metadata、undo
    active.json           # 仅安装或恢复期间存在
    work/<操作ID>/
      staged/<相对路径>    # 新文件，安装时移动到正式路径
      backup/<相对路径>    # 仅本次涉及的旧文件
      record.json         # id、before State、entries(path, existed)
      restore             # 恢复时的临时副本
```

入口目录允许软链接，打开仓库、创建或直接打开数据集时解析为真实路径；登记文件保存真实绝对目录，后续打开登记不会追随该真实目录被替换出的软链接。

ID 是 32 位十六进制字符串。数据路径为规范的相对 POSIX 路径，不能包含 `..`、空组件或 `.asterstore`，不能指向软链接、特殊文件或硬链接别名。正式路径必须与数据集控制目录位于同一文件系统。

## 发布顺序

1. 数据集写锁覆盖准备到提交；读取协调由调用方完成。
2. 同步候选文件，复制并同步涉及的旧文件与备份目录。
3. 持久化 work/ID/record.json：保存原 State 和每个变更路径的原存在状态。
4. 持久化 active.json：`kind=publish, id=ID, source=ID, before=原 State`。此后 current 拒绝读取。
5. 逐文件替换或删除，同步变化目录。未改变历史文件不访问。
6. 原子写 head.json：revision+1，operation/publication/undo 均为 ID，metadata 为新值。
7. 移除并同步 active.json。旧工作目录等待显式 clean；有效撤销材料为 head.undo 指定目录。

写 JSON 使用同目录临时文件、文件 fsync、原子 replace、父目录 fsync。创建目录逐级同步父目录。安装新文件使用同文件系统 replace，并同步源、目标目录。

提交成功响应前 head 和移除活动记录均完成同步。head 替换后发生异常可能已经提交，必须恢复确认。

## 发布恢复

持有写锁，在维护窗口读取活动记录和变更记录：

- head.operation 与活动 id 一致：验证操作状态，保留已提交数据。
- head 与 before 一致：安装未提交，恢复所有旧文件，删除原来不存在的路径。head 及它原有的 undo 不变。
- 其他状态或记录损坏：停止，不猜测。

恢复先校验全部备份，再恢复文件。恢复旧文件先复制备份到 work 内的 restore 临时文件再替换，源备份保留，所以中断后可重复执行。完成后重新同步 head 与控制目录，再移除活动记录，避免之前目录同步失败留下不确定状态。

无 active 的 recover 也先同步 head 与控制目录，再返回成功，以收敛上次活动记录删除后的同步失败。无 active 的孤立 staging 是未开始安装的准备残留，可清理。发现 active 的 clean 拒绝工作。

## 主动回退

持久化 `kind=rollback, id=新操作ID, source=head.undo, before=当前 State`。按 source 对应 record 恢复旧文件或删除新增文件。然后写入：revision+1、operation=新操作ID、publication/metadata=旧业务状态、undo=null。

回退中断后继续完成回退，不尝试恢复到回退之前，也不支持 redo。已经提交的回退仅补做同步和活动记录清除。首次发布回退到 publication=null，revision 仍递增。

## 清理与保留

clean 只遍历本数据集 work 的工作目录及待删目录内容，不遍历正式数据和其他数据集。保留 head.undo，拒绝活动事务，删除其余明确的工作目录。清理不恢复过去的回退机会。实际删除工作目录之前，先同步 head 与控制目录，确保活动记录的消失已经持久化；同步失败时不删除恢复材料。预览不执行这一步。

两次 clean 之间旧备份和记录仍占空间，定期 clean 才使保留材料收敛为一份。清理期间无需停读正式数据，但必须持有数据集写锁。

初始化要求空目录，不做非空目录接管。初始化中断可能留下未登记的控制目录，不能自动递归删除；部署时核对该空数据目录的初始化残留后重新建立。目录登记不提供跨仓库并发初始化保证。
