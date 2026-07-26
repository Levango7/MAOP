"""MAOP Delegate — Agent dispatch via config-driven driver registry.

Mirrors delegate-plugin.ps1: CLI, wrapper, powershell, cmd drivers
with circuit-breaker protection and unified error schema.
"""

from maop.delegate.dispatcher import Dispatcher, DispatchResult

__all__ = ["DispatchResult", "Dispatcher"]
