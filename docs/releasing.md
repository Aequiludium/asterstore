# 发行流程

当前处于 `0.1.0.dev1` 开发阶段。此前本地 rc1 的冻结已撤回；不发布或重新标记那些旧制品。

1. 完成[首版接入验收](release-scope.md)，确定 API 和持久结构的实际支持范围。
2. 选择新的候选版本，更新 pyproject.toml、uv.lock 和 CHANGELOG，明确破坏性变更及目录处理方式。
3. 通过质量、协议、Python 3.11/3.12 测试与 CodeQL；从目标提交执行 `tools/check_distribution.py --polars --output-dir dist/<版本>`，保存验证产物。
4. 核对 verification.json 的来源、文件 SHA-256、许可证和安装结果。正式发布前再次明确包索引、发布身份及目标版本，只上传已验证的同一批制品。

CI 不自动上传 PyPI，也不创建 Release。尚未配置发布凭据或 Trusted Publishing。实际 PyPI 名称可用性以首次注册/发布结果为准。

每次验证使用新目录；保留旧候选制品作为历史证据，不覆盖同名版本。仓库自动化与权限见[自动化说明](automation.md)。
