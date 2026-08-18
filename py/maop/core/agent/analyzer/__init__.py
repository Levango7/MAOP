"""Task analyzer subpackage.

Redirects ``sys.modules['maop.core.agent.analyzer']`` to the real module
``maop.core.agent.analyzer.analyzer`` so that all public and private
symbols resolve correctly via ``from maop.core.agent.analyzer import xxx``.
"""
from __future__ import annotations

# mypy: ignore-errors
import sys
from typing import TYPE_CHECKING

from . import analyzer as _real_mod

# 将本包在 sys.modules 中替换为真正模块对象，
# 使 mock.patch('maop.core.agent.analyzer.xxx') 能 patch 真正模块
sys.modules[__name__] = _real_mod

if TYPE_CHECKING:
    from .analyzer import *  # noqa: F403
