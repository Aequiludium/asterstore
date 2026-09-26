# Contributing

欢迎通过 [Issues](https://github.com/Aequiludium/asterstore/issues) 报告问题、讨论设计，通过 Pull Request 提交修改。漏洞请使用 [安全报告流程](SECURITY.md)。

asterstore 当前处于 `0.1.0.dev0` 开发阶段。新功能围绕 [v4 首版范围](docs/release-scope.md) 展开；协议、删除授权、保留语义和公开 API 的修改应先说明问题、兼容影响与恢复路径。目标规范中的未交付能力不等于现有 API。

## 开发环境

Python 3.11 或 3.12，本地 Linux/POSIX 环境，uv 0.10.7 或更高版本：

```bash
git clone https://github.com/Aequiludium/asterstore.git
cd asterstore
uv sync --locked --extra polars
uv run --locked --extra polars ruff check .
uv run --locked --extra polars ruff format --check .
uv run --locked --extra polars mypy
uv run --locked --extra polars pytest
```

构建与源码外隔离安装检查：

```bash
uv run --locked --extra polars python tools/check_distribution.py --polars
```

依赖变化通过 `uv lock` 生成锁文件。普通代码修改无需更新锁文件。完整示例和规模 smoke 命令见 [开发指南](docs/development.md) 及 [CI](.github/workflows/ci.yml)。

## 修改原则

- 核心保持业务无关和零第三方运行依赖；可选引擎放在 integrations。
- 普通 Binding 选择保持无治理 I/O；不要为每次读取增加扫描、保留写入、存在性或内容检查。
- 子包通过明确接口协作；私有实现不是公共兼容承诺。
- 发布、引用、删除的行为变化要说明生效点、锁顺序、异常后可见状态、原操作重试与恢复范围。
- 修改持久格式时同步检查 Schema、codec、golden fixtures、必要功能门槛和兼容文档；不能只改格式标记绕过版本限制。
- 回归测试验证可观察的语义与真实失败边界。性能结论提供规模、环境和成本分解，不依赖单次墙钟阈值。
- 试验使用临时或专用隔离目录。实际数据迁移和生产治理不是测试步骤。

PR 描述包括具体问题、变化后的行为、相关验证及仍存在的限制。请区分本地进程故障证据与真实断电/共享挂载资格，也区分开发版本通过验证与正式发行。

## 问题报告

普通问题请提供 Python/asterstore 版本、操作系统与文件系统、协议版本及必要功能、最小复现、预期和实际结果。涉及中断操作时附上脱敏后的操作阶段与异常，避免上传真实数据、访问凭据或未脱敏业务元数据。

## 许可证

本项目采用 [Apache-2.0](LICENSE)。提交贡献前请确认你有权以该许可证贡献相应代码和材料；第三方材料保留其原有归属与许可说明。
