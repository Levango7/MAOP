"""MAOP Dashboard — FastAPI web dashboard with pure-Python data bridge."""

from maop.dashboard.data_proxy import DataProxy
from maop.dashboard.provider import (
    AgentStatus,
    DashboardProvider,
    DashboardState,
    _render_html,
    create_app,
)

__all__ = ["AgentStatus", "DashboardProvider", "DashboardState", "DataProxy", "_render_html", "create_app"]

# NOTE: `app` is NOT imported here to avoid circular dependency:
#   server -> routers -> state -> MAOP.dashboard.__init__ -> server
# Use `from maop.dashboard.server import app` directly when needed.
