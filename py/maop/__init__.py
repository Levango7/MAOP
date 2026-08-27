"""MAOP — Agent Orchestration Framework (Python rewrite)."""

__version__ = "5.1.0"

# Namespace package: allow maop-enterprise to contribute subpackages
# under the maop.* namespace without conflicting.
__path__ = __import__('pkgutil').extend_path(__path__, __name__)
