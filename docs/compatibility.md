# 版本与唯一格式边界

`0.2.0` 在 0.1.0 上增加显式诊断和 Polars 2.x 支持，保持已有公开 API 与 `format_version=1`，现有仓库无需迁移。诊断的忙状态错误和类型导入路径见[诊断契约](inspection.md)。版本改动以 CHANGELOG 为准；源码提交不代表 PyPI 已提供该版本。

运行时只实现一种声明模型和一种磁盘格式。仓库标记必须是 `kind: "store"`、`format_version: 1`，并满足完整的字段和必要功能约束。旧开发目录、未知版本、错误 kind、混合记录和未知必要功能均拒绝；没有旧读写器、兼容别名或自动迁移。

调用方可另建仓库、重新生成或显式登记原数据；库不会改写旧标记、接管权属或删除旧数据。registered 可以引用外部文件，但不因此取得其保留或删除权限。

0.1.x 的修复保持已发布的公开 API 与格式语义；后续必要的破坏性变化应在新版本和发行说明中明确。不会为了开发迭代预先堆积多个运行时版本。CI 验证当前 Schema、codec、正反样例和生命周期行为。


## 0.3.0 的检查行为变化

0.3.0 的读写检查实现保持 format_version=1，但默认候选封存不再执行额外文件的全目录盘点。声明的新文件仍须满足路径/类型/存在性最低安装约束，durable 同步承诺不变。旧 writer 或 installed cleanup 面对包含未知条目的新候选可能拒绝操作，不能宣称混用行为完全一致。升级时使用 inspect_candidate 盘点残留，不直接删除控制证据或凭目录位置认领未知文件。

读写 check 参数默认 none，显式检查失败可能发生在已提交重试中；调用方必须读取 DataCheckError.commit_state，不能把所有检查错误当作“发布没有发生”。现有 governance.check 的默认与汇总语义不变。

## 显式控制历史归档（0.3.0 起）

D 阶段新增 control_archive 必要功能，保持 format_version=1。仅调用 compact 才启用；旧 0.2.0 解码器会拒绝新 marker。执行前停用所有旧进程；已有目录不能由旧工具混写，也不能通过删 feature 字段降级。完整 current 仍是原结构，新客户端的默认读取开销不变。API、维护窗口和故障恢复见 [控制历史压缩](control-compaction.md)。

## 数据集下线（0.3.0 起）

显式 decommission 启用 dataset_decommission 必要功能，旧客户端拒绝新 marker。执行前升级所有客户端；没有降级或重新上线操作。新增 tombstone 只用于已下线 current，在线声明格式不变，默认读取仍为两份控制记录。API 与恢复语义见 [数据集下线](dataset-decommission.md)。
