"""MAOP Dashboard — FastAPI web dashboard with pure-Python data bridge."""

from maop.dashboard.data_proxy import DataProxy
from maop.dashboard.provider import (
    AgentStatus,
    DashboardProvider,
    DashboardState,
)

__all__ = ["AgentStatus", "DashboardProvider", "DashboardState", "DataProxy"]

# v5.0.0: create_app() and _render_html() removed (deprecated since v4.0.0).
# Use maop.dashboard.server:app for production.

# NOTE: `app` is NOT imported here to avoid circular dependency:
#   server -> routers -> state -> MAOP.dashboard.__init__ -> server
# Use `from maop.dashboard.server import app` directly when needed.
