"""Core utilities shared across MAOP modules.

Currently exposes the async subprocess helper (see ``async_subprocess``).
Kept as a thin package so future cross-module helpers can land here without
adding new top-level directories.
"""

from __future__ import annotations