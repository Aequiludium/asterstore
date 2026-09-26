# Security

asterstore 目前为开发版本 `0.1.0.dev0`，尚无稳定版本支持周期。安全修复优先进入当前开发分支；正式版本的支持范围会在发布时说明。

## 报告问题

疑似漏洞请通过 GitHub 的 [Report a vulnerability](https://github.com/Aequiludium/asterstore/security/advisories/new) 私下提交给维护者。普通 bug 使用 [Issues](https://github.com/Aequiludium/asterstore/issues)。如果私密报告入口不可用，请在公开 issue 中仅请求私密联系渠道，不公开漏洞细节或利用代码。

报告建议包括受影响版本/提交、平台与挂载方式、最小复现、影响范围、必要前提，以及是否可能误删数据、越过受管路径或破坏保留关系。只提供人工构造的样本，不上传凭据或真实业务数据。维护者会按实际能力处理，不承诺尚未建立的响应时限。

## 安全与治理边界

仓库假设文件系统拥有者及使用库的进程相互协作，POSIX 锁用于协调；它不是针对同权限恶意进程的访问控制或沙箱。普通读取不逐文件验真，registered 声明不能阻止外部生产者修改数据。

上述边界不使误删或授权判断错误成为可接受行为。受管/外部数据隔离、固定删除范围、对象归属、保留和恢复状态机的缺陷仍应报告。核心控制记录为严格 JSON，不执行应用 pickle/插件反序列化器。完整保证见 [首版范围](docs/release-scope.md) 和 [治理协议](docs/governance-v4.md)。
