"""租户身份：**唯一的**提取入口 + 跨层传播用的上下文原语。

## 为什么要收口

`request.state.tenant_id` 是请求侧租户身份的唯一来源（由
``maop/core/security/middleware.py`` 的 5 处认证分支写入）。但读它的小函数在各路由里
**手抄了 7 份**：`agent_versions_helpers` / `compliance` / `data` / `feedback` /
`memory` / `notifications` / `rbac`。抄写已经出现分歧 —— `compliance.py:33` 与
`rbac.py:58` 是同名同注释的"G-07 fix"，但前者**无租户即 403（fail-closed）**、
后者回退空串。也就是说"没租户怎么办"这件事，取决于你恰好走到哪个路由。

所以这里给两个**语义明确**的函数，调用方按需要选，不许再手抄：

* :func:`tenant_id_from_request` —— 软读，缺省空串（单租户/个人版）；
* :func:`require_tenant_id` —— fail-closed，缺租户直接 403（合规/审计这类
  "没有租户就没法正确回答"的接口必须用这个）。

## 上下文原语是干什么的

请求处理里拿到的租户身份，默认**传不到**编排链（`Dispatcher.dispatch`）里 ——
所以闸门类的检查拿不到租户。:func:`tenant_context` 提供一个显式的作用域：

    with tenant_context(tid):
        await dispatcher.dispatch(...)      # 下游 current_tenant() 能读到

刻意做成**显式作用域**而不是"中间件顺手设一下"：Starlette 的
``BaseHTTPMiddleware`` 会把下游跑在另一个 task 里，在中间件里设 ContextVar 是否
传播给 endpoint 取决于实现细节 —— 靠那个等于把租户身份挂在一个会随框架版本变化的
行为上。显式作用域在哪儿生效一目了然。

⚠️ 本轮**只提供原语，不改变任何执行路径**；编排链上的租户隔离边界见
``docs/configuration.md`` 的「多租户（企业版）现状与边界」一节。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行时不导入 FastAPI
    from fastapi import Request

#: 当前执行上下文所属租户。空串 = 未指定（单租户 / 个人版）。
_CURRENT_TENANT: ContextVar[str] = ContextVar("maop_current_tenant", default="")


def tenant_id_from_request(request: Request) -> str:
    """从请求状态读租户身份；缺省返回空串（单租户/个人版）。

    只认 `request.state.tenant_id`（认证中间件写入的 JWT claim）—— **绝不**从请求体
    或查询串取租户（那是 G-07 修过的越权口子：调用方可以随便声明自己属于哪个租户）。
    """
    return getattr(request.state, "tenant_id", "") or ""


def require_tenant_id(request: Request) -> str:
    """同 :func:`tenant_id_from_request`，但缺租户时抛 403（fail-closed）。

    用于"没有租户身份就无法正确回答"的接口（合规、审计、RBAC 等）—— 这类接口
    回退成空串会让"取不到身份"静默退化成"看到全部"或"看到空"。
    """
    from fastapi import HTTPException, status

    tenant_id = tenant_id_from_request(request)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id not found in JWT — cannot process this request",
        )
    return tenant_id


def current_tenant() -> str:
    """当前上下文所属租户；未设置时为空串。"""
    return _CURRENT_TENANT.get()


@contextmanager
def tenant_context(tenant_id: str) -> Iterator[str]:
    """在作用域内把租户身份暴露给下游（含编排链）。

    用法::

        with tenant_context(tenant_id):
            await dispatcher.dispatch(...)

    退出时恢复上一层的值（可嵌套）。空串也会被设置 —— "明确知道没有租户"与
    "没人设过"对下游是两回事，作用域语义要如实反映。
    """
    token = _CURRENT_TENANT.set(tenant_id or "")
    try:
        yield tenant_id or ""
    finally:
        _CURRENT_TENANT.reset(token)
