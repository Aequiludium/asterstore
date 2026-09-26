# 仓库自动化与维护设置

## CI 与合并门槛

[CI](../.github/workflows/ci.yml) 在 main 推送、PR 和手动触发时运行。质量/协议、Python 3.11/3.12 测试和双版本隔离发行验证分别执行；统一的 `CI gate` 只有在全部任务成功时才通过，失败、取消或意外跳过均不能获得成功门槛。

普通工作流只有 contents:read，checkout 不保留凭据；官方 Actions 固定到完整提交 SHA。每个任务设置超时，同一分支/PR 的旧 CI 可被取消。测试报告保留 14 天，验证过的 wheel/sdist/verification.json 保留 30 天；制品名字包含解释器版本和提交身份。这些是验证产物，不会自动上传 PyPI 或创建正式 Release。

main 采用 PR 合并、要求分支同步且 `CI gate` 通过、解决讨论、线性历史、禁止强推和删除。当前维护方式允许零个额外审批，避免唯一维护者无法审批自己的 PR；不启用自动合并。后续增加独立维护者时再提高审查人数。规则所需检查绑定 GitHub Actions 的应用身份。

## 依赖与安全自动化

[Dependabot](../.github/dependabot.yml) 使用 uv 原生 ecosystem 更新 uv.lock，每周一北京时间 09:00 检查 Python 依赖和 GitHub Actions。每种 ecosystem 最多 5 个更新 PR；开发依赖及普通 Actions 的 minor/patch 更新分组，major 更新单独评审。CodeQL 的 init/analyze 共享配置，必须保持相同版本，因此所有版本更新（包括 major）均归入专用 codeql 组，在同一个 PR 中升级。Bot 的 PR 使用相同 CI 和合并门槛，不自动批准或合并。

[CodeQL](../.github/workflows/codeql.yml) 在 main、PR、每周和手动触发时扫描 Python；只授予该任务上传扫描结果需要的 security-events 权限。CodeQL 首版先作为可见安全检查，尚未将其分析结果设成 main 必过门槛。依赖漏洞提醒/安全更新、密钥扫描/推送保护和私密漏洞报告由仓库设置启用。扫描不替代数据权属和生命周期测试。

依赖 Bot 的第一次扫描、更新 PR 与安全告警以 GitHub 实际结果为准；启用配置不代表没有漏洞，也不保证每次检查都会产生更新。

## 发布与权限

仓库只允许 squash 合并并自动清理已合并分支；默认工作流令牌只读，不允许工作流批准 PR。代码变更由维护者评审，正式发布仍按[发行流程](releasing.md)选择版本、来源和已经验证的制品。

独立 [Publish](../.github/workflows/publish.yml) 只支持 main 上的手动触发，默认演练。完整复用 CI 后，验证并选择同次运行的制品；只有显式 publish=true 的上传任务获得 id-token:write，并使用 pypi environment 的 Trusted Publisher。PyPI 账号侧配置见[发行流程](releasing.md)。普通 CI 不获得上传权限。仓库 Git 操作使用维护者已配置的 SSH 授权。

参考：[uv 的 Dependabot 接入](https://docs.astral.sh/uv/guides/integration/dependabot/)、[GitHub ruleset 规则](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)。
