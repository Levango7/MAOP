## Summary

<!-- Brief description of what this PR does and why -->

## Changes

<!-- List key changes -->

- 
- 

## Testing

<!-- How was this tested? -->

- [ ] Unit tests pass (`pytest` / `vitest`)
- [ ] E2E tests pass (`playwright test`)
- [ ] Manual verification
- [ ] **若改 `py/Dockerfile` / `.dockerignore` / `docker-compose*.yml`**：本地用同一基础镜像构建验证过。PR 上看不到 `Docker build` / `Container Scan (trivy)` / `Compose Smoke`，且它们不在 `CI merge gate` 的 `needs` 里 —— 合并后必须主动盯 master 那三条腿，别等 nightly（见 `docs/ci-gates.md` §4.1）

## Checklist

- [ ] CHANGELOG.md updated (if user-facing change)
- [ ] No new linting errors
- [ ] No security issues introduced
- [ ] Documentation updated (if needed)

## Related Issues / ADRs

<!-- Link any related issues or ADRs -->
