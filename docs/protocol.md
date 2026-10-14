# Format 2 磁盘协议

只支持 format_version=2。格式 1 的 writer/reader 与新库不能混用，无隐式迁移。

```text
root/.asterstore/
  format.json                     # {format_version: 2, store_id: UUID hex}
  init.lock
  datasets/<sha256(dataset_id)>/
    writer.lock                   # 持久锁文件，不删除
    head.json                     # dataset_id, revision, current, rollback
    versions/<version_id>.json    # dataset_id, version_id, members
    objects/<unique_prefix>/<relative_path>
    staging/<version_id>/<unique_prefix>/<relative_path>
```

清单 members 为有序 `{key, path}` 数组。key 唯一，path 相对本数据集 objects，多个成员可引用同一文件。所有版本和对象前缀为库生成的 32 位十六进制 UUID。数据集名通过 SHA-256 编码，不直接用作目录路径。

记录严格拒绝重复 JSON 字段、错误字段/类型、非法路径、重叠文件/目录和身份不匹配。head revision 为 1 到 2^63−1；rollback 可空，不得等于 current。路径不得含空段、`.`、`..`、反斜线或绝对路径。控制访问及修改/清理预检拒绝软链接和特殊文件。

JSON Schema 位于 `src/asterstore/schemas/store.json`，描述形状；codec 补充成员唯一性、路径和身份约束。schema 不能单独证明仓库可删除。

## 提交顺序

1. 修改锁覆盖草稿准备、封存、业务检查及提交。
2. seal 先确认本次新文件只有一个硬链接，再独占安装到 objects，写入唯一版本清单；未被 head 引用的清单无发布权威。
3. durable=True 时同步新 current、将保留的 rollback 清单和文件及父目录。
4. 原子替换整个 head，随后同步 head 及目录；异常可能发生于替换之后。

稳定读依次读取 marker、head、选中 manifest；普通读不扫描历史、不做数据文件预检。

回退使用相同 head 替换，current=旧 rollback、rollback=null、revision+1。它不撤销已开始的读任务。

清理首先完整预检保留集合和托管子树，再同步保留文件/清单及 head，然后删除无引用文件。同步失败不开始删除，删除中断后重新计算。清理不创建永久计划或退役记录。没有 head 的目录不按空集合删除。

协作式 POSIX 锁、原子替换、同步能力是部署前提；没有后台保护服务。调用方须禁止清理期间的新读写并等待旧读取全部完成。外部文件篡改和硬件损坏不在双版本回退保证内。
