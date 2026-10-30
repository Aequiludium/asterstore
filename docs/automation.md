# 仓库自动化

[CI 工作流](../.github/workflows/ci.yml) 在 main 推送和 Pull Request 上运行，也支持手动调用。

| 检查 | 内容 |
| --- | --- |
| Quality and protocol | Ruff、格式、mypy、Schema 与 codec 检查 |
| Polars compatibility | Python 3.11、3.12、3.13、3.14 × Polars 1.44.2、2.0.0、2.1.0，共 12 组测试和示例 |
| Tests | 四个 Python 版本的完整测试、独立示例和容量检查 |
| Distribution | 四个 Python 版本的 wheel 构建、sdist 重建和隔离安装 |
| Release artifact verification | 下载发行制品并验证名称、版本、验收项和哈希 |
| CI gate | 汇总必需任务结果，失败或跳过均不放行 |

具体版本和命令以工作流文件为准。[CodeQL](../.github/workflows/codeql.yml) 独立执行源码扫描，[Dependabot](../.github/dependabot.yml) 配置依赖更新。

完整测试和发行包检查使用锁文件中的 Polars 2.1.0。兼容性任务先同步锁文件，再安装矩阵指定的 Polars 版本，以 `uv run --no-sync` 执行测试和示例，避免重新同步覆盖待验证版本；该步骤不修改锁文件。

四个 Python 版本分别验证 wheel 与从 sdist 重建的包。发行制品下载检查和 Publish 工作流选用 Python 3.12 任务生成的同一组制品，不混用不同任务的构建结果。

[Publish](../.github/workflows/publish.yml) 通过手动触发执行，默认只演练验证流程。普通 CI 不上传包。发行流程见 [releasing.md](releasing.md)。

本地验证通过不代表远端已通过；合并时应检查对应提交的实际运行结果。
