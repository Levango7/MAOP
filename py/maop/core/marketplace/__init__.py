"""MAOP Marketplace — plugin/agent marketplace with signed packages.

F1-03 (Marketplace): provides package signing verification and sandboxed
execution for marketplace plugins/agents. The signing module uses
Ed25519 asymmetric signatures (G-01 security fix) and the sandbox module
uses a whitelist environment variable policy (G-02 security fix).
"""

from __future__ import annotations

__all__: list[str] = [
    "sandbox",
    "signing",
]