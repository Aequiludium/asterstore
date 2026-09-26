# 开发与构建

使用 uv 0.10.7 或更高版本；后端 `uv_build>=0.10.7,<0.13`。最低 Python 3.11，默认开发解释器为 3.12。核心零第三方运行依赖；Polars 为可选 extra，jsonschema 只用于开发校验。

```bash
uv sync --locked --extra polars
uv run --locked --extra polars ruff check .
uv run --locked --extra polars ruff format --check .
uv run --locked --extra polars mypy
uv run --locked --extra polars python tools/check_protocol.py
uv run --locked --extra polars pytest
for example in examples/*.py; do uv run --locked --extra polars python "$example"; done
uv run --locked --extra polars python tools/check_distribution.py --polars --output-dir dist/dev1-verified
```

协议检查验证唯一 Schema、11 类控制记录的 codec 和正反样例，不维护历史 API 快照。测试覆盖外部登记、受管提交、逻辑成员、对象复用、固定保留、GC、候选清理、进程竞争、强制终止恢复和读路径 I/O 边界。

发行检查显式通过 PEP 517 解析声明的后端，构建 wheel/sdist，从 sdist 重建 wheel，在源目录外独立安装验证。输出目录必须不存在，已验证制品和 SHA-256 不会覆盖旧文件；不上传包索引。

性能脚本见 [benchmarks](../benchmarks/README.md)。CI 运行小规模 smoke，不以墙钟时间作为性能门槛。修改依赖用 `uv lock`，不手工调整解析结果。

当前包为 `0.1.0.dev1`，仍需真实应用接入验证；[兼容政策](compatibility.md)不再承诺旧开发实现。
