# Git Branch Strategy

MAOP adopts a **trunk-based development** model with release tags.

## Branch Model

| Branch | Purpose | Protection |
|--------|---------|-----------|
| `master` | Production-ready, always deployable | Require PR + CI pass |
| `develop` | Integration branch for next release | Require CI pass |
| `feature/*` | Feature development (short-lived) | None |
| `fix/*` | Bug fix branches | None |
| `hotfix/*` | Urgent production fixes | Require PR to master + develop |
| `release/*` | Release stabilization | Require PR to master |

## Workflow

### Feature Development

```bash
git checkout develop
git checkout -b feature/your-feature
# ... develop ...
git push -u origin feature/your-feature
# Create PR to develop
```

### Bug Fix

```bash
git checkout develop
git checkout -b fix/issue-description
# ... fix ...
git push -u origin fix/issue-description
# Create PR to develop
```

### Hotfix (urgent production issue)

```bash
git checkout master
git checkout -b hotfix/critical-issue
# ... fix ...
git push -u origin hotfix/critical-issue
# Create PR to master AND develop
```

### Release

```bash
git checkout develop
git checkout -b release/v4.5.0
# ... stabilize, fix bugs only ...
git checkout master
git merge release/v4.5.0
git tag -a v4.5.0 -m "Release v4.5.0"
git push origin master --tags
git checkout develop
git merge release/v4.5.0
git branch -d release/v4.5.0
```

## Version Tags

Tags follow [Semantic Versioning](https://semver.org/): `vMAJOR.MINOR.PATCH`

- **MAJOR**: Breaking changes (edition restructure, API breaking)
- **MINOR**: New features (backward-compatible)
- **PATCH**: Bug fixes (backward-compatible)

### Existing Tags

| Tag | Date | Description |
|-----|------|-------------|
| `v4.4.1` | 2026-08-05 | Production-ready, dual-edition, security-hardened |

## Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `security`, `perf`
Scopes: module name (e.g., `memory`, `mcp`, `enterprise`, `dashboard`)

### Examples

```
feat(memory): add semantic cache eviction policy
fix(mcp): handle transport timeout on SSE reconnect
security(docker): pin Vault to 1.17, add Redis requirepass
test(backends): add fail-fast design conflict tests
```

## Code Review

All PRs require:
1. CI pipeline passes (12 jobs)
2. At least 1 approval (for master/develop)
3. No new linting errors
4. Test coverage maintained ≥80%

## Pre-commit Hooks

Install pre-commit hooks:

```bash
pip install pre-commit
pre-commit install
```

See `.pre-commit-config.yaml` for configured hooks.