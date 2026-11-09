# 磁盘协议：format 4

本协议不读取或转换其他格式。记录 Schema 位于 `src/asterstore/schemas/store.json`；校验命令为 `python tools/check_protocol.py`。

```text
登记目录/.asterstore/
  registry.json           # format、kind、dataset name → 绝对目录；可选 dropping
  registry.lock           # 用于登记和整体删除

数据集目录/
  <调用方相对文件路径>
  .asterstore/
    dataset.json          # format、kind、name
    writer.lock
    head.json             # revision、operation、publication、metadata、undo
    active.json           # 仅发布安装、回退或永久删除维护期间存在
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

## 永久删除业务路径的维护事务

`Dataset.prune()` 使用已有 dataset 写锁，不获取登记锁。在调用方停止该 dataset 全部使用的窗口内执行：

1. 获取写锁并校验 expected_revision，向调用方提供 before State 和新操作 ID。调用方在同一锁内选择目标并生成 metadata。
2. 提交前检查全部显式目标和本 dataset 的旧 work 子树，拒绝越界、软链接、特殊文件、硬链接别名、嵌套控制路径和跨文件系统路径。重复或父子重叠目标拒绝，不存在目标允许。
3. 原子持久化 `active.json = {kind: "prune", id, before: State, metadata, paths: [相对业务路径], work: [旧操作ID]}`。此意图同时记录最终业务元数据及明确删除范围；不创建新 work、record 或 backup。
4. 同步意图所在控制目录后，删除全部业务目标及记录中的全部旧 work 目录，同步被删目录／文件的父目录。清除旧 work 包括整份有效 undo 和全部失效材料。
5. 原子写 head：`revision=before.revision+1, operation=publication=id, metadata=意图中元数据, undo=null`。同步 head 与控制目录，再移除并同步 active。

恢复入口在已有活动事务分支中识别 prune。head 必须完整等于 before 或预期的新 State，否则拒绝。恢复只按意图继续删除和补同步，不读取可能已部分删除的旧事务 record/backup，不恢复已删数据，不重验首次 CAS。目标及父目录已不存在时同步最近仍存在的父目录；head 已更新时保持其身份不变，继续完成同步和意图清除。不存在 active 的 recover 仍沿用原逻辑。

active 存在期间，现有 current 检查已阻止普通发布、回退、清理及整体删除；读者协调仍由调用方负责，已构造的文件查询不会被自动撤回。日常读取／发布／清理没有新增控制查询、锁或扫描；只有已有活动事务恢复分支增加类型分派。写入 prune 意图后异常必须用 recover 续删，不能新开 prune 跳过未完成操作。

本扩展保持 format 4 和原 State 字段不变。旧客户端不能恢复新的 active 记录形状，会拒绝而非按可回退发布处理；因此维护及其恢复必须使用含 prune 支持的客户端。完成后旧客户端可读取原形状的 head（undo=null）。该能力从 0.5.5 起提供，0.5.4 不包含它；维护及恢复客户端的最低版本为 0.5.5。调用方业务 metadata 的最低版本另行约束。

## 整体删除与续删

`Repository.drop_dataset()` 在调用方停止目标数据集全部使用的维护窗口内工作。锁顺序为登记锁 → 数据集写锁；日常数据集操作仍只使用原有写锁和控制记录。

1. 持有登记锁，检查登记路径不会包含仓库控制区或与其他登记目录重叠，拒绝真实路径的软链接重定向。
2. 第一次删除获取数据集写锁，检查身份、活动事务和 expected_revision。
3. 原子写入 registry.json 的可选 `dropping` 对象：名称 → 开始删除时的 revision；原 datasets 路径绑定保留。同步完成后才开始删除。
4. 持有锁递归删除整个独占目录，包含 writer.lock 和全部控制记录，同步父目录。
5. 一次原子登记更新同时移除 datasets 和 dropping 中的该名称。dropping 为空时省略此属性。

删除意图存在时，列表隐藏该名称，登记打开报告删除中，登记创建仍保留其名称和路径占用。其他数据集不需要读取此删除状态。目录内部的软链接或特殊文件被拒绝；调用方排除外部目录变更，不得在数据集内嵌套挂载其他文件系统。

中断后重试读取原路径绑定和 dropping，继续删除残余目录，不再打开 dataset.json/head.json、不再校验 revision 或重建已经删除的写锁。目录已不存在时补做父目录同步，再移除登记。登记更新后同步失败时，再次调用补做登记目录同步。恢复方向始终为继续删除。

dropping 是 format 4 登记记录的可选扩展，Schema 同步收录；没有删除操作时记录形状不变。旧客户端不识别此扩展，不能参与删除维护窗口或其中断恢复；必须先用支持 drop_dataset 的客户端完成续删。删除完成后记录恢复原形状。不提供跨旧句柄的代际隔离：调用方在同名或同路径重建前丢弃旧句柄。
