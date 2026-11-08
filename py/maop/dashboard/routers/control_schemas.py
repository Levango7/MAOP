"""MAOP Dashboard —— ``/api/control/*`` 的响应模型.

这组路由是 dashboard 里唯一"前端拼 URL"的地方：``ControlPanel.execAction``
用 ``api.post(`/api/control/${action}`)`` 分发，没有任何调用点写出完整路径，
于是 ``test_frontend_response_models.py`` 的抽取器只能看到
``POST /api/control/{}``——后端没有这条路由，这批端点既不进 ``missing`` 也不进
allowlist，棘轮对它们永久失明。前端因此只能靠
``r.msg || r.message || r.detail || t(...)`` 猜形状。

字段逐个照 ``system_service`` 的返回写，不做收窄：

* ``validate`` / ``doctor`` 的 ``result`` 由 ``system_service`` 产出
  （``dict`` 与 ``list[dict]``），键在本仓无法枚举，故只声明容器类型；
* ``provider-health`` 的 ``components`` 来自 ``maop.deploy``（商业覆盖层，
  ADR-017），个人版里根本导入不到，更不能在这里写死键名。

错误路径不受影响：``handle_api_errors`` 的降级返回是 ``JSONResponse``，
FastAPI 对 ``Response`` 实例跳过 response_model 校验。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ControlJobModel(BaseModel):
    """一个控制作业的快照。

    形状持有者是 ``system_service`` 的 job 字典与 ``control_jobs.jsonl``
    历史记录：run 作业有 ``task/stdout/stderr``，maintain 作业只有
    ``action``，历史文件还可能带更早版本写下的键。已知字段之外的键必须
    原样透传，否则抽屉里会出现"字段凭空消失"——故开 ``extra="allow"``，
    不做任何收窄。
    """

    model_config = ConfigDict(extra="allow")

    id: str = ""
    action: str = ""
    status: str = ""
    task: str = ""
    start: str = ""
    finished_at: str | None = None
    exit_code: int | None = None
    trace_id: str = ""
    stdout: str = ""
    stderr: str = ""


class ControlStatusResponse(BaseModel):
    """``GET /api/control/status`` —— 活跃作业与历史记录的合并视图。"""

    status: str = "ok"
    active_jobs: list[ControlJobModel] = []
    jobs: list[ControlJobModel] = []
    count: int = 0


class ControlJobResponse(BaseModel):
    """``GET /api/control/jobs/{job_id}`` —— 单个作业（含 stdout/stderr）。"""

    status: str = "ok"
    job: ControlJobModel


class ControlRunResponse(BaseModel):
    """``POST /api/control/run`` —— 已启动的作业。"""

    status: str = "ok"
    job_id: str = ""
    task: str = ""


class ControlPauseResponse(BaseModel):
    status: str = "ok"
    action: str = "pause"
    paused: bool = False


class ControlResumeResponse(BaseModel):
    status: str = "ok"
    action: str = "resume"
    resumed: bool = False


class ControlStopResponse(BaseModel):
    status: str = "ok"
    action: str = "stop"
    stopped: bool = False


class ControlValidateResponse(BaseModel):
    status: str = "ok"
    job_id: str = ""
    result: dict[str, Any] = {}


class ControlDoctorResponse(BaseModel):
    status: str = "ok"
    job_id: str = ""
    result: list[dict[str, Any]] = []


class ControlCancelResponse(BaseModel):
    status: str = "ok"
    job_id: str = ""


class ControlRefreshResponse(BaseModel):
    status: str = "ok"
    cache: str = ""


class ControlClearCacheResponse(BaseModel):
    status: str = "ok"


class ControlProviderHealthResponse(BaseModel):
    status: str = "ok"
    components: list[dict[str, Any]] = []
