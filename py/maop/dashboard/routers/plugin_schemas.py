"""Response models for the plugin router.

Every payload this router returns is a ``PluginInfo`` dump (see
``plugin_service`` §1), so the envelopes add nothing but the ``status`` key —
reusing ``PluginInfo`` keeps the schema pinned to the producer instead of a
hand-copied subset that can drift.
"""

from __future__ import annotations

from pydantic import BaseModel

from maop.core.agent.plugins_hooks.plugin_manager import PluginInfo


class PluginEnvelope(BaseModel):
    status: str = "ok"
    plugin: PluginInfo


class PluginListEnvelope(BaseModel):
    status: str = "ok"
    plugins: list[PluginInfo]


class PluginDiscoverEnvelope(BaseModel):
    status: str = "ok"
    discovered: list[PluginInfo]
