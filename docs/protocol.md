# 磁盘协议

仓库标记使用 `format_version=2` 和库生成的 store_id。持久记录的结构定义见 [JSON Schema](../src/asterstore/schemas/store.json)，运行时 codec 另外检查路径、唯一性和身份关系。

```text
root/.asterstore/
  format.json
  init.lock
  datasets/<sha256(dataset_id)>/
    writer.lock
    head.json
    versions/<version_id>.json
    objects/<unique_prefix>/<relative_path>
    staging/<version_id>/<unique_prefix>/<relative_path>
```

## 记录

- format：format_version、store_id。
- head：dataset_id、revision、current、rollback。
- manifest：dataset_id、version_id、members。
- member：key、path；path 相对本数据集 objects 目录。

members 是有序数组，key 唯一；多个 key 可以指向同一文件。版本 ID 和对象前缀使用 32 位十六进制 UUID。数据集名经 SHA-256 编码为目录名。锁文件持久存在，不应删除或替换。

revision 范围为 1 到 2^63−1。rollback 可空，不能与 current 相同。JSON 拒绝重复字段、额外字段、错误类型和身份不匹配。相对路径禁止绝对路径、空段、点段和反斜线；控制访问和修改预检拒绝软链接及特殊文件。

## 发布顺序

1. 取得数据集修改锁，固定基线 head。
2. 在 staging 写入新文件并关闭写入器。
3. seal 检查所有声明文件，新文件要求单个硬链接；独占安装文件并写入唯一版本清单。
4. 调用方完成候选检查。
5. durable=True 时，同步待保留的文件、清单和父目录。
6. 原子替换整个 head，再同步 head 和目录。

只有 head 确定正式版本，未引用的清单不表示发布成功。第 6 步替换后仍可能抛出同步异常，因此异常结果需要重新检查 head。

## 读取、回退与清理

普通读取依次读取 marker、head 和所选 manifest，不扫描其他版本或预检数据文件。Binding 的路径选择无需磁盘访问。

回退在修改锁内将 current 设为 rollback、rollback 设为空并增加 revision，使用相同的 head 替换机制。

清理在维护窗口和修改锁内完整预检，先同步保留文件、清单与 head，再删除无引用内容。没有 head 时跳过。删除中断后重新计算即可。

部署环境应提供协作式 POSIX 锁、同文件系统硬链接、原子替换和同步能力。文件路径是托管实现的一部分，调用方不应自行改写控制记录或封存文件。
