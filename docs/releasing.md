# 候选制品与正式发行

当前候选版本为 `0.1.0rc1`，采用 Apache-2.0。源码、包制品、GitHub Release 和 PyPI 项目是不同交付物；本地构建成功不等于已经发布。

## 1. 维护职责与入口

维护组织为 [Aequiludium](https://github.com/Aequiludium)。普通问题/设计在 [Issues](https://github.com/Aequiludium/asterstore/issues) 讨论，贡献流程见 [CONTRIBUTING](../CONTRIBUTING.md)，漏洞按 [SECURITY](../SECURITY.md) 私密报告；若私密入口不可用，仅在公开 issue 请求私密联系，不披露细节。没有承诺响应时限。

0.1 系列按 [兼容决议](compatibility.md) 维护；安全和正确性修复优先进入当前维护分支。正式发布前由维护者检查远程权限、私密报告入口和 CI 状态。当前公开推送仍需先完成明确的范围授权，不因有本地候选而自动上传。

## 2. 本地候选检查

```bash
uv sync --locked --extra polars
uv run --locked --extra polars ruff check .
uv run --locked --extra polars ruff format --check .
uv run --locked --extra polars mypy
uv run --locked --extra polars python tools/check_contract.py
uv run --locked --extra polars pytest
uv run --locked --extra polars python tools/check_distribution.py --polars --output-dir dist/0.1.0rc1
```

同时在 Python 3.11/3.12 运行契约与测试，CI 中的独立示例和规模 smoke 也必须通过。对新版本通过 uv 更新项目版本和 lock，不手改依赖解析结果。旧版测量报告保留其实际环境与源码摘要，不把一次版本修改当成重新测量。

`--output-dir` 必须是新目录，只能位于 dist 下或源码树外；工具拒绝覆盖已有制品。它保存实际通过验证的 wheel、sdist 和 verification.json，不在验证后重新构建另一份上传。源码构建、sdist 重建的 wheel 分别做无引擎隔离安装；传入 `--polars` 后各自再验证可选引擎。LICENSE 正文与包许可证/仓库元数据也有校验。

verification.json 包含版本、源码内容摘要、运行解释器、实际执行的制品检查、文件大小/SHA-256 和重建 wheel 摘要。记录不冒充完整 pytest 或远程 CI 证据；这些结果单独核对。校验过程中源码变化会中止制品导出。目录缺少 verification.json 就不算完整验证制品。

## 3. 正式发行步骤

1. 决定候选转正式的版本号，更新 CHANGELOG，确认没有未解决的冻结差异、错误保证或生产迁移承诺。
2. 将最终源码提交到已授权的远程，确认对应提交的 Python 3.11/3.12 CI 全部成功；本地 dirty 状态的制品可以用于评审，但不能直接当作最终发布来源。
3. 在最终源码上重新执行上面的发行检查，保存制品、verification.json 与测试/CI 链接，人工核对文件摘要。
4. 维护者配置包索引凭据或 Trusted Publishing，并确认 asterstore 名称实际可注册。历史公开查询不能保证名称可用；第一次上传成功才是注册结果。
5. 获得发布授权后上传已经验证过的同一组制品，再核对索引元数据、许可证及下载摘要，并从索引安装验证。创建对应版本标签和发行说明，关联准确的源码提交。

当前 CI 只做验证；本文不创建上传凭据、自动发布任务、GitHub Release 或 PyPI 上传。后续启用自动发布时，部署环境审批和发布来源必须绑定最终标签/提交，不能直接把所有 main 推送都解释为发布授权。
