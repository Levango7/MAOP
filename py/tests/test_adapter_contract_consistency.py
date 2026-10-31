"""适配器契约一致性测试 —— MAOP V6 蓝图 M2（``docs/product-v6-blueprint.md``）。

蓝图契约（§5 架构收敛）::

    Adapter 契约：id、版本、探测、健康、能力、调用、输出解析、超时、环境；
    每个一等公民适配器都要过一致性测试。31 个条目不再等于 31 个承诺。

M2 验收（§7）::

    | M2 | 6 个一等公民适配器 + 一致性测试…… | 适配器矩阵通过…… |

--------------------------------------------------------------------------
「6 个一等公民适配器」的确定依据（引用文档/代码来源）
--------------------------------------------------------------------------
本仓库 ``py/maop/core/agent/adapters/`` 下共 7 个 ``AgentAdapter`` 实现
（``__init__.py`` docstring 原文："提供七种开箱即用的 ``AgentAdapter``
实现"）。M2 只要求 **6 个一等公民**，界定依据如下：

1. **协议族基线** —— ``AgentDescriptor.adapter_type``（
   ``core/agent/registry/agent_catalog.py:95``）与
   ``agent_discovery``（``core/agent/discovery/agent_discovery.py:134``）
   对外的官方适配器类型枚举为 ``cli | http | mcp | web``，对应
   ``CLIAdapter`` / ``HTTPAdapter`` / ``MCPBridgeAdapter`` / ``WebAdapter``。
2. **独立 driver 分支** —— ``adapter_factory.build_adapter`` 按
   ``AgentConfig.driver`` 分派（``adapters/adapter_factory.py:65-89``），
   独立分支为 ``desktop_app`` / ``ide_extension`` / ``vibe_coding``。
3. **类层次去重** —— ``VibeCodingAdapter(WebAdapter)``
   （``vibe_coding_adapter.py:103``）直接继承 ``WebAdapter``，复用 Web/HTTP
   协议族，无独立传输层，故归入 Web 协议族，**不单列为独立一等公民**；
   而 ``IDEExtensionAdapter`` 是独立协议（JSON over WebSocket 与
   Companion 通信），保有独立 driver，计为独立一等公民。
   （``DesktopAppAdapter(CLIAdapter)`` 虽继承 CLI，但引入独立的
   IPC/HTTP 回退传输 ``fallback_order``，是独立协议族。）

=> **6 个一等公民适配器 = CLIAdapter、HTTPAdapter、MCPBridgeAdapter、
WebAdapter、DesktopAppAdapter、IDEExtensionAdapter。**

--------------------------------------------------------------------------
契约维度 → 可验证的落地形式（代码当前实现为准，非虚构 API）
--------------------------------------------------------------------------
由于 ``AgentAdapter``（``core/agent/delegation/agent_proxy.py``）当前仅
以 ABC 形式定义了 5 个抽象方法，本测试将蓝图的 9 个契约维度映射到代码中
**真实存在**的落地形式，逐适配器断言：

  - **id**        类名 + ``adapters.__all__`` 公共导出 + 唯一标识；
  - **版本**      公共 API 稳定导出 + 类/模块 docstring（实现可辨识）；
  - **探测**      ``connect()`` 作为可达性探测：不抛异常、返回 ``bool``；
  - **健康**      ``health_check()`` 存在、返回 ``bool``、未配置时为 ``False``；
  - **能力**      5 个契约方法构成统一"能力矩阵"（所有一等公民一致）；
  - **调用**      ``connect``/``execute``/``sync_config``/``disconnect`` 签名
                  一致 + 返回类型一致（``bool``/``str``/``None``）；
  - **输出解析**  ``execute()`` 返回 ``str``，并完成结构化解析（CLI JSON 键、
                  HTTP/Web 点分路径、MCP content 列表、IDE result 字段）；
  - **超时**      配置模型含正的超时字段，且实例读取生效；
  - **环境**      配置 schema 严格化（``extra='forbid'``）+ 凭证字段默认空
                  （无硬编码密钥）+ CLI 显式 ``env``/``blocked_env`` 隔离。

所有外部依赖（subprocess / httpx / websockets / MCPHub）均用
``unittest.mock`` 打桩，不触发真实 CLI / 网络 / WebSocket。
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

import maop.core.agent.adapters as adapters_pkg
from maop.core.agent.adapters import (
    CLIAdapter,
    CLIAdapterConfig,
    DesktopAppAdapter,
    DesktopAppConfig,
    HTTPAdapter,
    HTTPAdapterConfig,
    IDEExtensionAdapter,
    IDEExtensionConfig,
    MCPBridgeAdapter,
    MCPBridgeAdapterConfig,
    WebAdapter,
    WebAdapterConfig,
)
from maop.core.agent.delegation.agent_proxy import AgentAdapter
from maop.core.mcp.mcp_hub_types import ToolResult

#: AgentAdapter ABC 定义的 5 个契约方法 —— 一等公民的"能力矩阵"基线
REQUIRED_METHODS: tuple[str, ...] = (
    "connect",
    "execute",
    "health_check",
    "sync_config",
    "disconnect",
)

#: 期望的一等公民数量（蓝图 M2：6 个）
FIRST_CLASS_COUNT = 6


# ======================================================================
# 公共测试夹具
# ======================================================================


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """去除代理环境变量，保证网络打桩测试与机器代理配置解耦。"""
    for var in (
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)


# ======================================================================
# 打桩辅助
# ======================================================================


def _fake_subprocess_run(stdout: str, returncode: int = 0, stderr: str = ""):
    """构造替换 ``subprocess.run`` 的 mock，返回固定 stdout。"""

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return _run


def _mock_httpx_client_factory(handler: Callable[[httpx.Request], httpx.Response]):
    """替换 ``httpx.Client``：始终返回绑定 ``MockTransport`` 的客户端。

    避免 ``connect()`` 内部构造真实客户端而触网。

    Notes
    -----
    必须在**替换之前**捕获真实的 ``httpx.Client``，否则工厂内部再调用
    ``httpx.Client(...)`` 会命中被替换后的自身，造成无限递归。
    """
    real_client = httpx.Client

    def _factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return real_client(
            transport=httpx.MockTransport(handler),
            base_url=kwargs.get("base_url", "") or "",
        )

    return _factory


# ======================================================================
# 适配器契约规格（spec）
# ======================================================================


@dataclass(frozen=True)
class AdapterSpec:
    """单个一等公民适配器的契约规格。

    Parameters
    ----------
    adapter_id : str
        契约唯一标识（对应 driver / adapter_type 语义）。
    cls : type
        ``AgentAdapter`` 子类。
    config_cls : type
        适配器配置模型（pydantic ``BaseModel``）。
    timeout_fields : tuple[str, ...]
        配置模型中代表"超时"契约的字段名。
    minimal_sync_config : dict
        ``sync_config`` 所需的最小合法配置字典。
    get_config : Callable[[AgentAdapter], Any]
        从适配器实例取出配置对象的访问器（各适配器属性名不统一）。
    build_unconfigured : Callable[[pytest.MonkeyPatch], AgentAdapter]
        构造"未就绪"实例（``connect()`` 必失败，且不触网）。
    build_connected : Callable[[pytest.MonkeyPatch], AgentAdapter]
        构造"已就绪"实例（``connect()`` 为 ``True``，``execute()`` 返回固定值）。
    expected_execute_output : str
        ``execute()`` 的期望返回（验证输出解析）。
    health_connected_true : bool
        已就绪实例 ``health_check()`` 是否应返回 ``True``。
    """

    adapter_id: str
    cls: type
    config_cls: type
    timeout_fields: tuple[str, ...]
    minimal_sync_config: dict[str, Any]
    get_config: Callable[[AgentAdapter], Any]
    build_unconfigured: Callable[[pytest.MonkeyPatch], AgentAdapter]
    build_connected: Callable[[pytest.MonkeyPatch], AgentAdapter]
    expected_execute_output: str
    health_connected_true: bool = True


# ── CLIAdapter ────────────────────────────────────────────────────────


def _build_cli_unconfigured(_mp: pytest.MonkeyPatch) -> CLIAdapter:
    """CLI 未就绪实例：空命令 → ``connect()`` 失败。"""
    return CLIAdapter(CLIAdapterConfig())


def _build_cli_connected(mp: pytest.MonkeyPatch) -> CLIAdapter:
    """CLI 已就绪实例：白名单内 ``python`` + 打桩 ``subprocess.run``。"""
    adapter = CLIAdapter(
        CLIAdapterConfig(
            command=sys.executable,
            output_format="json",
            output_json_key="result",
        )
    )
    assert adapter.connect() is True
    mp.setattr(
        "maop.core.agent.adapters.cli_adapter.subprocess.run",
        _fake_subprocess_run(stdout='{"result": "cli-parsed", "noise": 1}'),
    )
    return adapter


# ── HTTPAdapter ───────────────────────────────────────────────────────


def _http_handler(request: httpx.Request) -> httpx.Response:
    """HTTP 打桩处理器：GET → 200；POST → 回显点分路径结果。"""
    if request.method == "GET":
        return httpx.Response(200, text="ok")
    body = json.loads(request.content)
    return httpx.Response(200, json={"data": {"value": f"echo:{body['prompt']}"}})


def _build_http_unconfigured(_mp: pytest.MonkeyPatch) -> HTTPAdapter:
    """HTTP 未就绪实例：空 ``base_url`` → ``connect()`` 失败（协议缺失）。"""
    return HTTPAdapter(HTTPAdapterConfig())


def _build_http_connected(mp: pytest.MonkeyPatch) -> HTTPAdapter:
    """HTTP 已就绪实例：打桩 ``httpx.Client``，``connect()`` 走 MockTransport。"""
    mp.setattr(httpx, "Client", _mock_httpx_client_factory(_http_handler))
    adapter = HTTPAdapter(
        HTTPAdapterConfig(
            base_url="http://contract.local",
            path="/api/run",
            task_field="prompt",
            response_path="data.value",
        )
    )
    assert adapter.connect() is True
    return adapter


# ── MCPBridgeAdapter ──────────────────────────────────────────────────


def _make_mock_hub() -> MagicMock:
    """构造健康的 mock MCPHub（全异步接口）。"""
    hub = MagicMock()
    hub.health_check = AsyncMock(return_value=True)
    hub.call_tool_by_name = AsyncMock(
        return_value=ToolResult(
            content=[{"text": "mcp-a"}, {"text": "mcp-b"}], is_error=False
        )
    )
    hub.disconnect = AsyncMock(return_value=True)
    return hub


def _build_mcp_unconfigured(_mp: pytest.MonkeyPatch) -> MCPBridgeAdapter:
    """MCP 未就绪实例：无 hub → ``connect()`` 失败。"""
    return MCPBridgeAdapter(MCPBridgeAdapterConfig())


def _build_mcp_connected(_mp: pytest.MonkeyPatch) -> MCPBridgeAdapter:
    """MCP 已就绪实例：健康 mock hub。"""
    adapter = MCPBridgeAdapter(
        MCPBridgeAdapterConfig(server_id="srv-1", tool_name="srv.tool"),
        mcp_hub=_make_mock_hub(),
    )
    assert adapter.connect() is True
    return adapter


# ── WebAdapter ────────────────────────────────────────────────────────


def _web_handler(request: httpx.Request) -> httpx.Response:
    """Web 打桩处理器：GET → 200；POST → 回显 ``response_path`` 结果。"""
    if request.method == "GET":
        return httpx.Response(200, text="ok")
    body = json.loads(request.content)
    return httpx.Response(200, json={"output": f"done:{body['task']}"})


def _build_web_unconfigured(_mp: pytest.MonkeyPatch) -> WebAdapter:
    """Web 未就绪实例：空 ``api_url`` → ``connect()`` 失败。"""
    return WebAdapter(WebAdapterConfig())


def _build_web_connected(mp: pytest.MonkeyPatch) -> WebAdapter:
    """Web 已就绪实例：打桩 ``httpx.Client``。"""
    mp.setattr(httpx, "Client", _mock_httpx_client_factory(_web_handler))
    adapter = WebAdapter(
        WebAdapterConfig(
            api_url="http://contract.local/api",
            response_path="output",
        )
    )
    assert adapter.connect() is True
    return adapter


# ── DesktopAppAdapter ─────────────────────────────────────────────────


def _build_desktop_unconfigured(_mp: pytest.MonkeyPatch) -> DesktopAppAdapter:
    """桌面未就绪实例：无 cli/ipc/http 任一通道 → ``connect()`` 失败。"""
    return DesktopAppAdapter(DesktopAppConfig(app_name="contract-app"))


def _build_desktop_connected(mp: pytest.MonkeyPatch) -> DesktopAppAdapter:
    """桌面已就绪实例：CLI 通道可用（白名单 ``python``）。"""
    adapter = DesktopAppAdapter(
        DesktopAppConfig(
            app_name="contract-app",
            cli_command=sys.executable,
            fallback_order=["cli"],
        )
    )
    assert adapter.connect() is True
    mp.setattr(
        "maop.core.agent.adapters.cli_adapter.subprocess.run",
        _fake_subprocess_run(stdout="desktop-ok"),
    )
    return adapter


# ── IDEExtensionAdapter ───────────────────────────────────────────────


def _ide_config(**override: Any) -> IDEExtensionConfig:
    """构造合法的 IDE 插件配置。"""
    base: dict[str, Any] = {
        "extension_id": "github.copilot",
        "ide_type": "vscode",
        "companion_url": "ws://localhost:7890",
    }
    base.update(override)
    return IDEExtensionConfig(**base)


def _build_ide_unconfigured(mp: pytest.MonkeyPatch) -> IDEExtensionAdapter:
    """IDE 未就绪实例：打桩 ``websockets.connect`` 失败，避免真实连接。"""
    import websockets

    async def _fail_connect(*args: Any, **kwargs: Any) -> Any:
        raise OSError("contract-test: websocket unavailable")

    mp.setattr(websockets, "connect", _fail_connect)
    return IDEExtensionAdapter(_ide_config())


def _build_ide_connected(mp: pytest.MonkeyPatch) -> IDEExtensionAdapter:
    """IDE 已就绪实例：打桩 ``websockets.connect`` 成功 + 命令响应。"""
    import websockets

    async def _ok_connect(*args: Any, **kwargs: Any) -> MagicMock:
        return MagicMock()

    mp.setattr(websockets, "connect", _ok_connect)
    adapter = IDEExtensionAdapter(_ide_config())
    assert adapter.connect() is True
    # 打桩协议边界：Companion 返回成功响应（含 result 字段）
    adapter._send_command = MagicMock(  # type: ignore[method-assign]
        return_value={"success": True, "result": "ide-ok"}
    )
    return adapter


# ======================================================================
# 一等公民适配器契约矩阵
# ======================================================================

ADAPTER_SPECS: tuple[AdapterSpec, ...] = (
    AdapterSpec(
        adapter_id="cli",
        cls=CLIAdapter,
        config_cls=CLIAdapterConfig,
        timeout_fields=("timeout_s",),
        minimal_sync_config={},
        get_config=lambda a: a.config,
        build_unconfigured=_build_cli_unconfigured,
        build_connected=_build_cli_connected,
        expected_execute_output="cli-parsed",
    ),
    AdapterSpec(
        adapter_id="http",
        cls=HTTPAdapter,
        config_cls=HTTPAdapterConfig,
        timeout_fields=("timeout_s",),
        minimal_sync_config={},
        get_config=lambda a: a.config,
        build_unconfigured=_build_http_unconfigured,
        build_connected=_build_http_connected,
        expected_execute_output="echo:contract-probe",
    ),
    AdapterSpec(
        adapter_id="mcp",
        cls=MCPBridgeAdapter,
        config_cls=MCPBridgeAdapterConfig,
        timeout_fields=("timeout_s",),
        minimal_sync_config={},
        get_config=lambda a: a.config,
        build_unconfigured=_build_mcp_unconfigured,
        build_connected=_build_mcp_connected,
        expected_execute_output="mcp-a\nmcp-b",
    ),
    AdapterSpec(
        adapter_id="web",
        cls=WebAdapter,
        config_cls=WebAdapterConfig,
        timeout_fields=("timeout_s",),
        minimal_sync_config={},
        get_config=lambda a: a.config,
        build_unconfigured=_build_web_unconfigured,
        build_connected=_build_web_connected,
        expected_execute_output="done:contract-probe",
    ),
    AdapterSpec(
        adapter_id="desktop_app",
        cls=DesktopAppAdapter,
        config_cls=DesktopAppConfig,
        timeout_fields=("cli_timeout_s", "ipc_timeout_s", "http_timeout_s"),
        minimal_sync_config={"app_name": "contract-app"},
        get_config=lambda a: a._app_config,
        build_unconfigured=_build_desktop_unconfigured,
        build_connected=_build_desktop_connected,
        expected_execute_output="desktop-ok",
        # 未配置 process_name → 进程探测恒为 False
        health_connected_true=False,
    ),
    AdapterSpec(
        adapter_id="ide_extension",
        cls=IDEExtensionAdapter,
        config_cls=IDEExtensionConfig,
        timeout_fields=("command_timeout_s",),
        minimal_sync_config={
            "extension_id": "github.copilot",
            "ide_type": "vscode",
            "companion_url": "ws://localhost:7890",
        },
        get_config=lambda a: a._config,
        build_unconfigured=_build_ide_unconfigured,
        build_connected=_build_ide_connected,
        expected_execute_output="ide-ok",
    ),
)

_IDS = [s.adapter_id for s in ADAPTER_SPECS]


# ======================================================================
# 契约维度 0：一等公民集合本身
# ======================================================================


def test_first_class_adapter_count_is_six() -> None:
    """蓝图 M2 要求恰好 6 个一等公民适配器。"""
    assert len(ADAPTER_SPECS) == FIRST_CLASS_COUNT


def test_first_class_adapter_ids_unique() -> None:
    """6 个一等公民标识唯一且非空（id 契约的整体一致性）。"""
    assert all(spec.adapter_id for spec in ADAPTER_SPECS)
    assert len(set(_IDS)) == FIRST_CLASS_COUNT


# ======================================================================
# 契约维度：id
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_id(spec: AdapterSpec) -> None:
    """id：适配器为 AgentAdapter 子类，且作为公共 API 导出。"""
    assert isinstance(spec.adapter_id, str) and spec.adapter_id
    assert issubclass(spec.cls, AgentAdapter)
    assert spec.cls.__name__ in adapters_pkg.__all__, (
        f"{spec.cls.__name__} 未在 adapters.__all__ 中导出（id 契约漂移）"
    )
    assert spec.config_cls.__name__ in adapters_pkg.__all__


# ======================================================================
# 契约维度：版本
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_version(spec: AdapterSpec) -> None:
    """版本：实现与契约均被文档化（类/模块 docstring 可辨识实现版本）。"""
    assert spec.cls.__doc__ and spec.cls.__doc__.strip(), (
        f"{spec.cls.__name__} 缺少类 docstring"
    )
    module = inspect.getmodule(spec.cls)
    assert module is not None and module.__doc__, (
        f"{spec.cls.__module__} 缺少模块 docstring"
    )


# ======================================================================
# 契约维度：探测
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_probe_unavailable(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测：不可用时 ``connect()`` 不抛异常且返回 ``False``。"""
    adapter = spec.build_unconfigured(monkeypatch)
    result = adapter.connect()
    assert isinstance(result, bool)
    assert result is False


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_probe_available(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测：可用时 ``connect()`` 返回 ``True``（可达性探测成功）。"""
    adapter = spec.build_connected(monkeypatch)
    result = adapter.connect()
    assert isinstance(result, bool)
    assert result is True


# ======================================================================
# 契约维度：健康
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_health_unconfigured_false(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """健康：未就绪时 ``health_check()`` 返回 ``False`` 且不抛异常。"""
    adapter = spec.build_unconfigured(monkeypatch)
    result = adapter.health_check()
    assert isinstance(result, bool)
    assert result is False


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_health_connected_bool(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """健康：已就绪时 ``health_check()`` 返回 ``bool``（契约返回类型一致）。"""
    adapter = spec.build_connected(monkeypatch)
    result = adapter.health_check()
    assert isinstance(result, bool)
    if spec.health_connected_true:
        assert result is True
    else:
        # desktop_app：未配置 process_name 时进程探测恒为 False（合法契约值）
        assert result is False


# ======================================================================
# 契约维度：能力
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_capability_methods_present(spec: AdapterSpec) -> None:
    """能力：每个一等公民都暴露同一组 5 个契约方法。"""
    for method in REQUIRED_METHODS:
        assert callable(getattr(spec.cls, method, None)), (
            f"{spec.cls.__name__}.{method} 缺失（能力矩阵不完整）"
        )


def test_dimension_capability_matrix_uniform() -> None:
    """能力：适配器矩阵一致 —— 6 个适配器的契约方法集合完全相同。"""
    matrix = {
        spec.adapter_id: {
            m for m in REQUIRED_METHODS if callable(getattr(spec.cls, m, None))
        }
        for spec in ADAPTER_SPECS
    }
    assert all(caps == set(REQUIRED_METHODS) for caps in matrix.values()), (
        f"能力矩阵不一致: {matrix}"
    )


# ======================================================================
# 契约维度：调用
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_invoke_signatures(spec: AdapterSpec) -> None:
    """调用：``connect``/``execute``/``sync_config``/``disconnect`` 签名一致。"""
    assert list(inspect.signature(spec.cls.connect).parameters) == ["self"]

    execute_params = list(inspect.signature(spec.cls.execute).parameters)
    assert execute_params[0] == "self"
    assert "task" in execute_params
    assert any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in inspect.signature(spec.cls.execute).parameters.values()
    ), "execute 必须接受 **kwargs（契约签名一致）"

    assert list(inspect.signature(spec.cls.sync_config).parameters) == [
        "self", "config",
    ]
    assert list(inspect.signature(spec.cls.disconnect).parameters) == ["self"]


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_invoke_lifecycle_returns_none(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """调用：``sync_config`` / ``disconnect`` 返回 ``None``（生命周期契约）。"""
    adapter = spec.build_unconfigured(monkeypatch)
    assert adapter.sync_config(dict(spec.minimal_sync_config)) is None
    assert adapter.disconnect() is None


# ======================================================================
# 契约维度：输出解析
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_output_parsing(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """输出解析：``execute()`` 返回 ``str`` 且完成结构化解析。"""
    adapter = spec.build_connected(monkeypatch)
    result = adapter.execute("contract-probe")
    assert isinstance(result, str)
    assert result == spec.expected_execute_output, (
        f"{spec.cls.__name__} 输出解析结果不符（期望 "
        f"{spec.expected_execute_output!r}，得到 {result!r}）"
    )


# ======================================================================
# 契约维度：超时
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_timeout_fields_positive(spec: AdapterSpec) -> None:
    """超时：配置模型声明正的超时字段（契约必需维度）。"""
    assert spec.timeout_fields, "spec 必须声明至少一个超时字段"
    fields = spec.config_cls.model_fields
    for name in spec.timeout_fields:
        assert name in fields, f"{spec.config_cls.__name__} 缺少超时字段 {name}"
        default = fields[name].default
        assert isinstance(default, (int, float)) and default > 0, (
            f"{spec.config_cls.__name__}.{name} 默认超时必须为正数"
        )


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_timeout_effective(
    spec: AdapterSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超时：实例读取到正的超时配置（配置 → 实例生效）。"""
    adapter = spec.build_unconfigured(monkeypatch)
    config = spec.get_config(adapter)
    for name in spec.timeout_fields:
        assert getattr(config, name) > 0


# ======================================================================
# 契约维度：环境
# ======================================================================


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_env_schema_strict(spec: AdapterSpec) -> None:
    """环境：配置 schema 严格化（拒绝隐式漂移，环境/配置显式可控）。"""
    assert spec.config_cls.model_config.get("extra") == "forbid", (
        f"{spec.config_cls.__name__} 必须禁止额外字段（extra='forbid'）"
    )


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_env_no_hardcoded_secret(spec: AdapterSpec) -> None:
    """环境：凭证类字段默认空（不硬编码密钥），需由环境/调用方注入。"""
    credential_markers = ("token", "key", "secret", "credential")
    for name, field in spec.config_cls.model_fields.items():
        if any(marker in name for marker in credential_markers):
            assert field.default in ("", None), (
                f"{spec.config_cls.__name__}.{name} 不得硬编码凭证，"
                f"实际默认 {field.default!r}"
            )


@pytest.mark.parametrize("spec", ADAPTER_SPECS, ids=_IDS)
def test_dimension_env_isolation(spec: AdapterSpec) -> None:
    """环境：CLI 协议族显式隔离子进程环境（``env`` / ``blocked_env``）。"""
    fields = spec.config_cls.model_fields
    if spec.adapter_id == "cli":
        assert "env" in fields and "blocked_env" in fields, (
            "CLIAdapterConfig 必须支持 env/blocked_env 环境隔离"
        )
    else:
        # 非 CLI 协议族无子进程环境，约定其配置不含 env 字段（避免误用）
        assert "env" not in fields