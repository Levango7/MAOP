"""MAOP Plugin — Hook declaration models & lifecycle state.

Provides:
  - PluginState: lifecycle states (discovered/loaded/started/stopped/errored)
  - PluginManifest: Pydantic model for plugin metadata (MAOP-plugin.yaml),
    including the ``hooks`` declaration list consumed by PluginManager to
    bridge plugin callbacks into HookManager.

This module is the data-model layer of the plugin subsystem and has no
dependency on the sandbox or manager layers, avoiding circular imports.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PluginState(str, Enum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    STARTED = "started"
    STOPPED = "stopped"
    ERRORED = "errored"


class PluginManifest(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    entry_point: str = "main.py"
    init_function: str = "MAOP_plugin_init"
    hooks: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    checksum: str = ""
    allowed_imports: list[str] = Field(default_factory=list)
    timeout_seconds: float = 30.0