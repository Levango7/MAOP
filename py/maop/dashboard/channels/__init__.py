"""DataProxy channel mixins — split from data_proxy.py for maintainability.

Each Mixin contributes a cohesive group of async endpoints to :class:`DataProxy`.
The base class (defined in ``maop.dashboard.data_proxy``) provides ``__init__``
and the SQLite pool/query helpers; mixins only declare endpoint methods and
rely on ``self._root`` / ``self._query_*`` / ``self._record_latency`` etc.
being supplied by the base.
"""

from __future__ import annotations

from maop.dashboard.channels.mcp import McpMixin
from maop.dashboard.channels.models import ModelsMixin
from maop.dashboard.channels.prompts import PromptsMixin
from maop.dashboard.channels.routing import RoutingMixin
from maop.dashboard.channels.security import SecurityMixin
from maop.dashboard.channels.skills import SkillsMixin

__all__ = [
    "McpMixin",
    "ModelsMixin",
    "PromptsMixin",
    "RoutingMixin",
    "SecurityMixin",
    "SkillsMixin",
]