# MAOP v5.0.0 发布说明

> 发布日期：2026-08-11 ｜ 版本：v5.0.0（major） ｜ 上一版：v4.5.0

## 发布摘要

MAOP v5.0.0 是一个 major release，包含不兼容变更、配置收敛、流式 Agent token 响应增强，以及 Phase 5b 的发布/性能/合规修复（G-08~G-17）。

### ⚠ 不兼容变更

详见 [MIGRATION-5.0.md](MIGRATION-5.0.md)。主要变更：

- 移除 deprecated ≥ 2 版本的 API（`create_app()`、`_render_html()`、legacy keyword routing、`/api/batch`）。
- 短名环境变量加 `DeprecationWarning`（`MAOP_PORT` → `MAOP_DASH_PORT` 等）。

### Phase 5b 新增（G-08~G-17）

| 编号 | 标题 | 交付物 |
|------|------|--------|
| G-08 | 发布流程 | v5.0.0 tag + PyPI + Docker Hub CI 配置 |
| G-09 | 性能压测 | `py/tests/performance/`（k6 + locust）+ `docs/capacity-planning.md` |
| G-10 | LDAP 真实环境验证 | `py/tests/test_ldap_real_env.py` + `docs/ldap-integration-guide.md` |
| G-12 | SLA/支持体系 | `docs/sla.md` + `docs/support-policy.md` |
| G-13 | 隐私政策/DPA | `docs/privacy-policy.md` + `docs/terms-of-service.md` + `docs/dpa.md` + `docs/cla.md` |
| G-14 | PG 高可用 | `deploy/patroni/` + `docker-compose.prod.yml` PG replica + `docs/runbook.md` |
| G-16 | CI Playwright E2E | `.github/workflows/ci.yml` playwright job |
| G-17 | K8s Operator 集成测试 | `py/tests/test_k8s_operator.py`（kind/k3s 支持） |

## 发布流程

### 1. 前置检查

```bash
# 确认版本号统一
grep -r "5.0.0" py/pyproject.toml py/maop/__init__.py \
  dashboard-enterprise/package.json \
  deploy/k8s/operator/Chart.yaml deploy/k8s/operator/values.yaml

# 确认 ruff 0 error
cd py && python -m ruff check maop/ tests/

# 确认 mypy 0 error
python -m mypy maop/ --ignore-missing-imports

# 确认测试通过
python -m pytest tests/ -q --ignore=tests/contract -m "not slow" --cov=maop --cov-fail-under=80

# 确认前端构建
cd ../dashboard-enterprise && npm ci && npm run build
```

### 2. 打 Git tag

```bash
# 创建 annotated tag
git tag -a v5.0.0 -m "MAOP v5.0.0 — major release

Phase 5b: 发布/性能/合规修复 (G-08~G-17)
- G-12 SLA/支持体系
- G-13 隐私政策/DPA
- G-14 PG 高可用 (Patroni)
- G-16 CI Playwright E2E
- G-17 K8s Operator 集成测试 (kind/k3s)
- G-09 性能压测 (k6/locust)
- G-10 LDAP 真实环境验证

详见 CHANGELOG.md"

# 推送 tag（触发 CI 发布）
git push origin v5.0.0
```

### 3. CI 自动发布

推送 `v5.0.0` tag 后，GitHub Actions 自动触发：

1. **lint** → **test** → **frontend** → **e2e**（Playwright）：全部门禁通过。
2. **publish** job：构建 Python wheel + sdist，发布到 PyPI（使用 Trusted Publishing，无需 API token）。
3. **docker** job：构建 Docker 镜像，推送至容器 registry（`maop:5.0.0` + `maop:latest`）。

### 4. PyPI 发布配置

CI 中的 `publish` job 使用 [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)（OIDC），无需 API token：

```yaml
publish:
  permissions:
    id-token: write    # OIDC trusted publishing
  steps:
    - uses: pypa/gh-action-pypi-publish@release/v1
```

**PyPI 配置步骤**（一次性）：

1. 在 [pypi.org](https://pypi.org) 注册项目 `maop`。
2. 在 PyPI 项目设置中添加 GitHub Trusted Publisher：
   - Repository: `maop/maop`
   - Workflow: `ci.yml`
   - Environment:（留空或 `pypi`）

### 5. Docker Hub 配置

CI 中的 `docker` job 使用 `docker/build-push-action`，通过 GitHub Secrets 配置 registry：

```yaml
docker:
  steps:
    - uses: docker/login-action@v3
      with:
        registry: ${{ secrets.REGISTRY_URL || '' }}
        username: ${{ secrets.REGISTRY_USER || '' }}
        password: ${{ secrets.REGISTRY_PASS || '' }}
    - uses: docker/build-push-action@v6
      with:
        tags: |
          ${{ secrets.REGISTRY_URL || 'maop' }}/maop:latest
          ${{ secrets.REGISTRY_URL || 'maop' }}/maop:${{ github.sha }}
```

**Docker Hub 配置步骤**（一次性）：

1. 在 [hub.docker.com](https://hub.docker.com) 创建 `maop` 仓库。
2. 在 GitHub 仓库 Settings → Secrets and variables → Actions 中添加：
   - `REGISTRY_URL`：留空（默认 Docker Hub）或 `ghcr.io`（GitHub Container Registry）。
   - `REGISTRY_USER`：Docker Hub 用户名。
   - `REGISTRY_PASS`：Docker Hub Access Token（非密码）。

### 6. 发布后验证

```bash
# PyPI
pip install maop==5.0.0
python -c "import maop; print(maop.__version__)"

# Docker Hub
docker pull maop/maop:5.0.0
docker run --rm maop/maop:5.0.0 maop --version

# GitHub Release
gh release view v5.0.0
```

### 7. 发布后通知

- 更新 [ROADMAP.md](ROADMAP.md) 标记 v5.0.0 为已发布。
- 更新 [CHANGELOG.md](CHANGELOG.md)（已在 v5.0.0 条目中记录）。
- 通知用户（邮件、Slack、Status 页）。
- 归档 deliverables 至 `deliverables/v5.0.0/`。

## 回滚流程

若发布后发现严重问题：

```bash
# 1. 撤回 PyPI 包（仅限发布后 2 小时内）
# 通过 pypi.org 界面操作（无 CLI 命令）

# 2. 标记 Docker 镜像为 deprecated
docker pull maop/maop:5.0.0
# 在 Docker Hub 界面标记 deprecated

# 3. 发布修复版本
git tag -a v5.0.1 -m "hotfix for v5.0.0"
git push origin v5.0.1

# 4. 通知用户回滚
```