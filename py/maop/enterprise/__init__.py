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

Backend modules:
  - core/backends_pg.py      PostgreSQL storage (implemented)
  - core/backends_redis.py   Redis cache/queue/lock (implemented, Phase 3.4)
  - core/backends_vault.py   HashiCorp Vault secrets (implemented, Phase 3.3)
  - core/backends_rabbitmq.py RabbitMQ queue (implemented; requires optional pika)
  - core/backends_distributed.py etcd/Consul KV (implemented; requires optional etcd3)

Note: ``FeatureFlag.RABBITMQ`` and ``FeatureFlag.ETCD`` are intentionally
excluded from ``_ENTERPRISE_FEATURES`` in ``config/edition.py`` because
their dependencies (pika / etcd3) are optional extras, not hard
requirements.  The backend modules ARE implemented; enable them via
``MAOP_QUEUE_BACKEND=rabbitmq`` / ``MAOP_KV_BACKEND=etcd``.  See the
docstring of ``config/edition.py`` for the OPTIONAL backends policy.
"""

from __future__ import annotations

import logging

from maop.config.edition import Edition, set_edition

logger = logging.getLogger(__name__)

logger.warning(
    "[enterprise] MAOP Enterprise extension detected — "
    "ENTERPRISE edition activated. "
    "Set MAOP_EDITION=personal to override."
)
set_edition(Edition.ENTERPRISE)

__all__: list[str] = [
    "audit",
    "container",
    "ha",
    "n8n",
    "rbac",
    "sso",
    "tenant",
    "tls_auto",
]
