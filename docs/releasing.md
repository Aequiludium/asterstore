# 发行流程

当前准备首个 PyPI 版本 `0.1.0`。[Aster 接入验收](aster-integration.md)通过后，进入发行制品和发布身份验证。旧开发制品不改名重用。

## PyPI 身份：一次性配置

登录最终负责该项目的 PyPI 账号，在 [Publishing](https://pypi.org/manage/account/publishing/) 添加 pending GitHub publisher：

| 字段 | 值 |
| --- | --- |
| PyPI Project Name | `asterstore` |
| Owner | `Aequiludium` |
| Repository name | `asterstore` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

仓库端使用同名 GitHub environment；不需要把长期 PyPI token 放进仓库。PyPI 账号侧配置需账号持有人完成，GitHub 权限不能代替 PyPI 身份。pending publisher 不预留包名，名称最终可用性以首次发布成功为准。[PyPI 官方说明](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)。

## 验证与上传

1. 更新 pyproject.toml、uv.lock 和 CHANGELOG，合并经过 CI/CodeQL 的发行提交。
2. 在 main 手动运行 [Publish](../.github/workflows/publish.yml)，先选择 `publish=false` 做完整演练。
3. 工作流复用质量/协议、Python 3.11/3.12 测试和隔离发行验证。选取同次运行中 Python 3.12 构建的 wheel/sdist，核对版本、文件名、验证项目和 SHA-256，单独保存上传制品。
4. PyPI publisher 已配置后，以 `publish=true` 运行同一工作流。它重新验证目标提交，并仅上传该次验证过的两个文件，不在上传任务中重新构建；OIDC 权限只授予上传任务。
5. 核对 PyPI 的版本及文件哈希，完成从 PyPI 的独立安装验证，再创建对应 Git tag/Release 并记录完成状态。Aster 随后从固定 Git 来源切换到 `asterstore>=0.1.0,<0.2`。

普通 CI、PR、main 推送不会自动上传。Publish 仅允许 main，默认演练；不启用 skip-existing 掩盖部分上传或同版本冲突。若发生部分上传，先对比 PyPI 与本次已验证文件的 SHA-256，再决定如何补齐；不重建同版本的不同字节。

本地也可运行 `tools/check_distribution.py --polars --output-dir dist/0.1.0-verified`。每次使用新目录；验证报告记录来源摘要、许可证、安装结果和文件 SHA-256。自动化权限见[仓库自动化](automation.md)。
