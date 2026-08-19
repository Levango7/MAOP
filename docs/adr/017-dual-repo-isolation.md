# ADR-017: Dual-Repository Physical Isolation (MAOP + MAOS)

**Status**: Active
**Date**: 2026-08-20
**Decision Owner**: MAOP Architecture Team
**Related**: ADR-016 (Dual-Edition Architecture)
**Supersedes**: None (refines ADR-016's code visibility mitigation)

## Context

### Background

ADR-016 (2026-07-25) established the dual-edition architecture: single codebase + runtime edition detection. The design explicitly noted a known drawback:

> ⚠️ 企业版代码在个人版仓库中可见（虽然受 Commercial 许可约束）

Mitigation was "Commercial license terms + future code obfuscation."

### Problem

During a security review on 2026-08-20, it was confirmed that:

1. **GitHub repository `Levango7/MAOP` is PUBLIC** — all enterprise code is visible to anyone
2. **`maop` PyPI package had no hatch exclude rule** — `pip install maop` would include `maop/enterprise/` (MIT-licensed!)
3. Enterprise modules (25 files: RBAC, SSO, License validation, CRL, audit, tenant isolation) were accessible for free

This constituted a real commercial/security exposure.

### Constraints

- Must maintain ADR-016's core design: single codebase development, zero-friction upgrade, FeatureFlag gating
- Must not require syncing core code between repositories
- Must preserve `pip install maop-enterprise` as the upgrade path
- Must keep edition detection mechanism intact

## Decision

### Move enterprise code to a private repository

```
Repository 1: Levango7/MAOP (public, MIT)
  ├── py/maop/core/           # Shared core (unchanged)
  ├── py/maop/config/edition.py  # Edition detection (unchanged)
  ├── py/maop/dashboard/      # Dashboard (enterprise routers use conditional imports)
  ├── py/pyproject.toml       # Added [tool.hatch.build] exclude = ["maop/enterprise"]
  └── (py/maop/enterprise/ REMOVED)

Repository 2: Levango7/MAOS (private, Commercial)
  ├── maop/enterprise/        # 25 enterprise modules
  ├── pyproject.toml          # maop-enterprise package, depends on maop
  └── README.md
```

### What stays the same

- **ADR-016 architecture**: Single codebase + runtime edition detection
- **FeatureFlag enum**: 26 flags, unchanged
- **Edition detection**: `maop.enterprise` package importability probe still works
- **Dual package publishing**: `maop` (PyPI, MIT) + `maop-enterprise` (private, Commercial)
- **Zero-friction upgrade**: `pip install maop-enterprise` still works
- **Conditional imports**: All 91 `from maop.enterprise.*` references are already conditional

### What changes

- **Enterprise code location**: `py/maop/enterprise/` → private repo `MAOS`
- **PyPI packaging**: `pyproject.toml` adds `[tool.hatch.build] exclude = ["maop/enterprise"]`
- **Development workflow**: Enterprise development requires cloning both repos
- **CI/CD**: Enterprise tests run only when `maop.enterprise` is importable

## Alternatives Considered

### A. Make MAOP repository private
**Rejected**: Personal edition is MIT-licensed and should remain open for community contribution.

### B. Git submodule for enterprise code
**Rejected**: Submodule reference in public repo would expose the private repo's existence.

### C. Code obfuscation
**Rejected**: Source code still visible in public repo.

### D. Split into two independent projects
**Rejected**: Would require syncing core code, break zero-friction upgrade, and overturn ADR-016.

## Consequences

### Positive

- ✅ Enterprise code physically isolated (not in public repo)
- ✅ `pip install maop` no longer includes enterprise code
- ✅ ADR-016 architecture preserved
- ✅ No core code synchronization needed
- ✅ Personal edition remains open source

### Negative

- ⚠️ Enterprise development requires cloning two repositories
- ⚠️ CI/CD needs to handle conditional enterprise tests
- ⚠️ Version numbers must be kept in sync manually

## Implementation

| Step | Status | Description |
|------|--------|-------------|
| Create private repo `Levango7/MAOS` | ✅ | 2026-08-20 |
| Migrate 25 enterprise files to MAOS | ✅ | 2026-08-20 |
| Add hatch exclude rule to MAOP pyproject.toml | ✅ | 2026-08-20 |
| Remove `py/maop/enterprise/` from MAOP | ✅ | 2026-08-20 |
| Update CI/CD for conditional enterprise tests | ✅ | 2026-08-20 |
| Update README.md dual-edition section | ✅ | 2026-08-20 |

## References

- [ADR-016: Dual-Edition Architecture](016-dual-edition-architecture.md)
- MAOP repository: https://github.com/Levango7/MAOP (public, MIT)
- MAOS repository: https://github.com/Levango7/MAOS (private, Commercial)