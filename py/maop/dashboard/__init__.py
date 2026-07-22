"""MAOP Dashboard — FastAPI web dashboard with pure-Python data bridge."""

from maop.dashboard.data_bridge import DataBridge
from maop.dashboard.provider import DashboardProvider, DashboardState, AgentStatus, create_app, _render_html

__all__ = ["DataBridge", "DashboardProvider", "DashboardState", "AgentStatus", "create_app", "_render_html"]

# NOTE: `app` is NOT imported here to avoid circular dependency:
#   server -> routers -> state -> MAOP.dashboard.__init__ -> server
# Use `from maop.dashboard.server import app` directly when needed.
