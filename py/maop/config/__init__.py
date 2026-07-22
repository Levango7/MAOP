"""MAOP Config — YAML configuration loading and validation.

Loads agents.yaml and rules.yaml from the project config/ directory.
Provides typed Pydantic models for all config sections.
"""

from maop.config.loader import ConfigLoader, load_config

__all__ = ["ConfigLoader", "load_config"]
