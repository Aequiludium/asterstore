# Security

安全修复优先进入 main，并通过测试与发行流程交付。使用固定提交部署时，应定期检查安全公告。

## 报告问题

疑似漏洞请通过 GitHub 的 [Report a vulnerability](https://github.com/Aequiludium/asterstore/security/advisories/new) 私下提交。普通 bug 使用 [Issues](https://github.com/Aequiludium/asterstore/issues)。若私密报告入口不可用，请在公开 issue 中仅请求私密联系渠道，不公开利用细节。

报告包括版本/提交、平台、最小复现、影响及必要前提。只提供人工构造的样本，不上传凭据或真实业务数据。

## 边界

文件系统拥有者及使用库的进程需相互协作；POSIX 锁不是针对同权限恶意进程的沙箱。已发布文件不可修改，普通读取不逐文件验真。新文件不能硬链接导入外部或其他数据集的数据；外部输出应复制，版本复用通过库接口表达。

清理依赖调用方协调无读写任务的维护窗口。路径逃逸、跨数据集删除、错误回退及保留状态持久化缺陷仍属于需要修复的问题。核心元数据为严格 JSON，不执行业务反序列化器。完整约定见[协议](docs/protocol.md)和[清理](docs/cleanup.md)。
