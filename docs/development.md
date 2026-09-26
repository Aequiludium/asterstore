# 开发与构建

使用 uv 0.10.7 或更高版本；构建后端限制为 `uv_build>=0.10.7,<0.11`。Python 最低版本为 3.11，`.python-version` 选择 3.12 作为默认开发环境。

## 环境与依赖

```bash
uv sync --locked --extra polars
```

开发依赖集中在 `dependency-groups.dev`。其中 jsonschema 验证随包发布的 v3/v4 Schema 和正反协议样例，不是核心运行依赖。核心没有第三方运行依赖；`polars` extra 用于真实 Parquet 接入、测试和类型检查。只开发核心时可不安装 extra，pytest 会跳过引擎测试；完整检查使用 extra。增加实际依赖后通过 `uv lock` 更新锁文件，不手工编辑锁文件。

uv 管理项目 `.venv`。`uv.lock` 用于开发与 CI 环境的可重复解析，下游安装库时仍由发行包中的依赖元数据决定约束。构建依赖由 `[build-system]` 单独约束。

## 本地检查

```bash
uv run --locked --extra polars ruff check .
uv run --locked --extra polars ruff format --check .
uv run --locked --extra polars mypy
uv run --locked --extra polars pytest
uv run --locked --extra polars python examples/binding.py
uv run --locked --extra polars python examples/declarations.py
uv run --locked --extra polars python examples/registration.py
uv run --locked --extra polars python examples/publication.py
uv run --locked --extra polars python examples/retention.py
uv run --locked --extra polars python examples/reuse.py
uv run --locked --extra polars python examples/collection.py
uv run --locked --extra polars python examples/candidate_cleanup.py
uv run --locked --extra polars python examples/parquet.py
```

检查另一解释器时可为命令显式指定 `--python 3.11`，或设置 `UV_PYTHON=3.11`。CI 为各矩阵项设置解释器，避免被 `.python-version` 覆盖成同一版本。

测试使用临时目录。`test_io_contract.py` 验证内存绑定和重复选择不执行文件 I/O，磁盘 current 绑定仅打开两个控制文件且不探测数据文件。发布及保留测试覆盖基准冲突、进程竞争、封存进程被终止后恢复、current 切换前后异常、引用 revision 重试和独占预览等待。预览测试禁止数据对象目录访问，并检查损坏元数据返回 blocked。回收测试覆盖共享文件、来源退役后的候选恢复、控制同步失败、unlink 后 SIGKILL、计划范围及回收与引用的进程竞争。候选残留测试覆盖永久放弃、清理计划范围、符号链接拒绝、写入竞争和清理进程 SIGKILL 后恢复。写入测试当前要求本地 POSIX 环境；这些测试不证明断电或 NFS 故障保证，也不代表引擎不需要访问 Parquet footer。

## 发行包检查

```bash
uv build --no-sources
uv run --locked --extra polars python tools/check_distribution.py --polars
```

第一条命令输出到 `dist/`。第二条使用临时目录构建 wheel 与 sdist，从 sdist 再构建 wheel，并分别在独立虚拟环境中安装和检查公开 API、类型标记及无引擎依赖的绑定、持久发布、重新打开、引用、复用、预览、实际回收、候选残留治理及完成重试流程。每个安装先在没有引擎的环境验证核心和适配器导入，再由 `--polars` 安装 wheel 的真实 extra 并执行 Parquet 示例。不传 `--polars` 时仅验证零引擎环境。安装检查在源码目录之外运行，并使用 Python isolated 模式。

该脚本不上传发行包。当前 CI 配置只执行检查，不包含发布步骤。项目采用 Apache-2.0；发行检查同时验证许可证正文、包元数据与项目链接。正式发行仍需完成 API/协议冻结和版本选择。

## 子包协作约定

- 跨子包使用对方 `__init__.py` 导出的接口，不导入私有实现文件。
- 导入和声明构造不读取数据、不创建目录。
- 新增显式检查放在 inspection 边界，不塞进普通读取流程。
- 新增候选、引用或删除操作时，同时实现其协调、失败和恢复语义。
- 实现尚未完成的能力不导出返回假成功的占位方法。

参考：[uv 构建后端](https://docs.astral.sh/uv/concepts/build-backend/)、[项目依赖](https://docs.astral.sh/uv/concepts/projects/dependencies/)、[发行包构建](https://docs.astral.sh/uv/guides/package/)。

## 生命周期基线

```bash
uv run --locked --extra polars python benchmarks/lifecycle.py --output /tmp/asterstore-baseline.json
```

该脚本记录平台、Python、规模、缓存说明和 durable 设置，测量发布、绑定、引用和预览；直接/绑定路径读取使用同一批字节文件，不涉及数据引擎。样例结果和限制见 [benchmarks](../benchmarks/README.md)。不要把单次本地小样本解释为生产吞吐或 NFS 资格。

运行新模型受管发布示例：`uv run python examples/managed_declarations.py`。

运行新治理示例：`uv run python examples/governance_v4.py`。

## v4 治理规模检查

```bash
uv run --locked python benchmarks/governance_scale.py --history 1 3 --datasets 2 --references 0 2 --logs 0 2 --repeats 1
```

CI 使用上述小规模组合验证测量脚本及保护/回收断言；不以墙钟耗时设置性能门槛。完整测量方式与局限见 [benchmarks](../benchmarks/README.md)。首版公开入口、协议兼容和发行缺项见[首版范围](release-scope.md)。
