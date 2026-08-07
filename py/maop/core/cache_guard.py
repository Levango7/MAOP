"""Backward compatibility — use maop.core.cache instead."""
from maop.core.reliability.cache import (  # noqa: F401
    CacheGuard,
    CacheGuardConfig,
    CacheGuardStats,
    SingleFlight,
)
