"""Multimodal inference subpackage.

Unified model interface, per-modality handlers, and capability-aware model
routing for text / image / audio / video inputs.

Modules:
    modality_handlers, unified_interface, model_router
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "ModalityType",
    "ModalityInput",
    "BaseModalityHandler",
    "TextHandler",
    "ImageHandler",
    "AudioHandler",
    "VideoHandler",
    "ModalityHandlerRegistry",
    "MultimodalRequest",
    "MultimodalResponse",
    "UnifiedModelInterface",
    "TaskType",
    "ModelCapability",
    "RoutingCriteria",
    "RouteResult",
    "ModelRouter",
]

# 符号 → 子模块名映射（惰性加载，避免循环导入）
_SYMBOL_TO_MODULE: dict[str, str] = {
    "ModalityType": "modality_handlers",
    "ModalityInput": "modality_handlers",
    "BaseModalityHandler": "modality_handlers",
    "TextHandler": "modality_handlers",
    "ImageHandler": "modality_handlers",
    "AudioHandler": "modality_handlers",
    "VideoHandler": "modality_handlers",
    "ModalityHandlerRegistry": "modality_handlers",
    "MultimodalRequest": "unified_interface",
    "MultimodalResponse": "unified_interface",
    "UnifiedModelInterface": "unified_interface",
    "TaskType": "model_router",
    "ModelCapability": "model_router",
    "RoutingCriteria": "model_router",
    "RouteResult": "model_router",
    "ModelRouter": "model_router",
}


def __getattr__(name: str) -> Any:
    """惰性加载子模块符号，避免循环导入。"""
    if name in _SYMBOL_TO_MODULE:
        mod_name = _SYMBOL_TO_MODULE[name]
        mod = importlib.import_module(f".{mod_name}", __name__)
        value = getattr(mod, name)
        globals()[name] = value  # 缓存，下次直接访问
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")