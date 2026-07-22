"""MAOP Enterprise Extension Package.

When this package is importable, ``config.edition.detect_edition()`` will
automatically detect ENTERPRISE edition and enable all enterprise features.

This package is shipped separately as ``maop-enterprise`` (pip install)
and depends on ``maop`` core.  It MUST NOT be present in the base
``maop`` package — its mere existence is the edition signal.

Submodules:
  - rbac.py          Role-Based Access Control
  - tenant.py        Multi-tenant isolation
  - audit.py         Enterprise audit logging
  - sso.py           SSO / SAML / OIDC integration
  - tls_auto.py      TLS auto-configuration
  - container.py     Docker/K8s orchestration
  - ha.py            High availability

Backend modules (re-exported from core/ for enterprise enhancements):
  - core/backends_pg.py      PostgreSQL storage
  - core/backends_redis.py   Redis cache/queue
  - core/backends_rabbitmq.py RabbitMQ queue
  - core/backends_vault.py   HashiCorp Vault secrets
  - core/backends_distributed.py etcd/Consul KV
"""

from __future__ import annotations

import logging

from maop.config.edition import Edition, set_edition

logger = logging.getLogger(__name__)

set_edition(Edition.ENTERPRISE)
logger.info("[enterprise] MAOP Enterprise extension loaded — all enterprise features enabled")

__all__: list[str] = [
    "rbac", "tenant", "audit", "sso",
    "tls_auto", "container", "ha",
]