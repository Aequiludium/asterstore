# 仓库自动化

当前配置以 [.github/workflows/ci.yml](../.github/workflows/ci.yml) 为准：

- Ruff、格式、mypy 和格式 2 协议校验。
- Python 3.11/3.12 测试、独立示例和双版本容量检查。
- Polars 版本兼容矩阵。
- wheel、sdist 重建与隔离安装验证，以及下载制品的版本/哈希检查。
- `CI gate` 汇总必需任务结果。

[Dependabot](../.github/dependabot.yml) 管理依赖更新；[CodeQL](../.github/workflows/codeql.yml) 执行源码扫描。实际远端运行结果和仓库保护设置以 GitHub 为准，本地配置不证明远端检查已通过。

[Publish](../.github/workflows/publish.yml) 为显式手动工作流，默认演练；普通 CI 不上传 PyPI。发布前遵循[发行流程](releasing.md)，不要重复上传不同字节的同一版本。
