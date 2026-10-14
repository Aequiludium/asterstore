# 构建与发行

## 本地验证

```bash
uv sync --locked --extra polars
uv run --locked --extra polars python tools/check_distribution.py --polars --output-dir dist/verified
```

输出目录应使用新的路径。脚本验证 wheel、从 sdist 重建的包及源码目录外的隔离安装，并记录制品 SHA-256 和验证结果。

## GitHub 发行

1. 更新版本、锁文件和 CHANGELOG，经 PR 合并到 main。
2. 手动运行 [Publish](../.github/workflows/publish.yml)，选择 `publish=false` 演练完整流程。
3. 确认该提交的测试、构建、隔离安装和制品选择全部成功。
4. 准备正式上传时，在目标 main 提交上运行 `publish=true`。工作流重新验证，并上传同次运行验证过的 wheel 和 sdist。
5. 核对包索引上的版本与文件哈希，验证独立安装，再创建对应 Git tag 和 Release。

工作流只允许 main，上传步骤使用 `pypi` environment 和 OIDC 身份。项目的 Trusted Publisher 应对应仓库 `Aequiludium/asterstore`、工作流 `publish.yml`、环境 `pypi`；实际授权状态须在发行前核实。

上传任务不重建文件，也不启用 skip-existing。同一包版本应对应同一组制品；部分上传失败时先比较已上传文件与验证制品的哈希，再处理剩余文件。

普通 PR 和 main CI 包含制品下载验证，但不执行上传。开发检查与贡献约定见 [CONTRIBUTING](../CONTRIBUTING.md)。
