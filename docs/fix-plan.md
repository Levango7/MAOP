# MAOP 修复方案设计

**项目路径**：`F:\Nexus\MAOP\`
**对应审查报告**：`docs/audit-report-corrected.md`
**方案编制日期**：2026-08-22
**方案性质**：本文件仅设计修复方案，不实施任何代码修改。

---

## 第1章 P0 级修复方案（必须修复，9 条）

### 1.1 H1 — CLI 静默无输出

#### 1.1.1 问题分析

**根因**：`py/maop/cli.py` 的多个命令处理函数体全部为 `pass`，未实现任何输出或业务逻辑调用。

**证据**：
- `py/maop/cli.py:88` — 函数体 `pass`
- `py/maop/cli.py:89-90` — 函数体 `pass`
- `py/maop/cli.py:92` — 函数体 `pass`
- `py/maop/cli.py:105-110` — 函数体 `pass`
- `py/maop/cli.py:130` — 函数体 `pass`

**影响范围**：用户通过 CLI 调用任何命令均无反馈，无法判断命令是否执行成功，严重影响可用性与运维体验。

#### 1.1.2 修复方案

**文件**：`py/maop/cli.py`

**修改内容**：
1. 为每个 `pass` 函数补充实际业务调用与 `click.echo()` 输出。
2. 引入统一的命令执行包装器，自动输出执行结果与耗时。
3. 对尚未实现的命令，输出明确的"未实现"提示并以非零退出码退出（而非静默 `pass`）。

**代码示例：CLI 命令骨架（Python）**
```python
import click
import time
from functools import wraps

def command_handler(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.monotonic()
        try:
            result = func(*args, **kwargs)
            elapsed = (time.monotonic() - start) * 1000
            click.echo(f"[OK] {func.__name__} 完成 ({elapsed:.1f}ms)")
            return result
        except NotImplementedError:
            click.echo(f"[ERROR] 命令 {func.__name__} 尚未实现", err=True)
            raise SystemExit(2)
        except Exception as exc:
            click.echo(f"[FAIL] {func.__name__} 失败: {exc}", err=True)
            raise SystemExit(1)
    return wrapper

@command_handler
def plan(args):
    # 替换原 pass：调用 maop_plan 主流程
    from maop.maop_plan import run_plan
    return run_plan(args)
```

#### 1.1.3 风险评估

**风险**：
- 补充实现可能引入新的业务逻辑 bug。
- 退出码变更可能影响依赖现有退出码的脚本。

**回归测试建议**：
- 为每个 CLI 命令编写 smoke test，验证非空输出与正确退出码。
- 在 CI 中新增 `pytest tests/test_cli.py -k "smoke"`。
- 验证现有 shell 脚本（`maop.ps1`、`start.sh`）对退出码的依赖。

---

### 1.2 H3 — 覆盖率门禁从未执行

#### 1.2.1 问题分析

**根因**：`ci.yml:172-177` 的覆盖率门禁步骤被条件化为 `if python -c "import maop.enterprise"`，而 `maop.enterprise` 包在当前代码树中不存在（`pyproject.toml:120-121` hatch exclude 也排除了该路径），导致条件永远为假，门禁步骤永远跳过。

**证据**：
- `.github/workflows/ci.yml:172-177` — `if: python -c "import maop.enterprise"` 条件
- `pyproject.toml:120-121` — hatch exclude 配置
- `py/maop/enterprise/` 目录不存在

**影响范围**：覆盖率回退无任何 CI 阻断，质量持续下降不可见。

#### 1.2.2 修复方案

**文件 1**：`.github/workflows/ci.yml`

**修改内容**：
- 移除 `if: python -c "import maop.enterprise"` 条件，使覆盖率门禁无条件执行。
- 将覆盖率门禁拆分为两个独立步骤：核心包门禁（始终执行）与企业包门禁（仅当 enterprise 存在时执行）。

**代码示例：CI 覆盖率门禁（YAML）**
```yaml
# 原：if: python -c "import maop.enterprise"  # 永远跳过
# 修改后：
- name: 核心包覆盖率门禁
  run: |
    coverage report --fail-under=80
    coverage xml -o coverage-core.xml

- name: 企业包覆盖率门禁（可选）
  if: ${{ hashFiles('py/maop/enterprise') != '' }}
  run: |
    coverage report --include="maop/enterprise/*" --fail-under=70
```

**文件 2**：`pyproject.toml`

**修改内容**：
- 移除或修正 `pyproject.toml:120-121` 的 hatch exclude 配置，使其与实际包结构一致。

#### 1.2.3 风险评估

**风险**：
- 启用门禁后首次运行可能因当前覆盖率低于阈值而失败，阻塞 CI。
- 需先测量当前真实覆盖率，合理设置 `--fail-under` 阈值。

**回归测试建议**：
- 本地先运行 `coverage run -m pytest && coverage report` 测量基线。
- 阈值设置为基线 - 2%（留缓冲），后续逐步提升。
- 在 PR 中先验证 CI 通过后再合并。

---

### 1.3 H4 — 企业版零测试

#### 1.3.1 问题分析

**根因**：`ci.yml:131-153` 显式 `--ignore` 22 个企业版测试文件，且 21 个文件使用 `importorskip` 在运行时跳过（因 `maop.enterprise` 包不存在）。

**证据**：
- `.github/workflows/ci.yml:131-153` — `--ignore` 列表 22 个文件
- 21 个测试文件含 `pytest.importorskip("maop.enterprise")`

**影响范围**：企业版功能完全无测试覆盖，回归不可见。

#### 1.3.2 修复方案

**方案选择**：由于 `maop.enterprise` 包不在当前代码树中，修复策略为"诚实标注 + 移除虚假门禁"。

**文件 1**：`.github/workflows/ci.yml`

**修改内容**：
- 移除 `--ignore` 列表中对不存在文件的引用（避免误导）。
- 新增显式步骤：检测 `py/maop/enterprise/` 是否存在，若不存在则输出明确告警并跳过企业测试（而非通过 ignore 静默跳过）。

**代码示例：CI 企业测试门禁（YAML）**
```yaml
- name: 企业版测试（条件执行）
  run: |
    if [ -d py/maop/enterprise ]; then
      pytest tests/enterprise/ -v
    else
      echo "::warning::maop.enterprise 包不存在，跳过企业版测试"
    fi
```

**文件 2**：`tests/enterprise/` 下 21 个测试文件

**修改内容**：
- 将 `importorskip` 改为显式 `pytest.skip(reason="maop.enterprise 未发布")`，并在测试报告中显式统计跳过数。

#### 1.3.3 风险评估

**风险**：低。主要风险为修改 CI 配置时 YAML 语法错误。

**回归测试建议**：
- 在 PR 中验证 CI YAML 语法（`yamllint` 或 `actionlint`）。
- 验证企业版包存在时测试能正常执行（可临时创建空 `__init__.py` 验证）。

---

### 1.4 H6 — Docker 前端白页壳（构建产物未入库）

#### 1.4.1 问题分析

**根因**：前端构建产物（assets）被 `.gitignore` 排除，Dockerfile 未在镜像构建阶段执行前端构建，导致 fresh clone 或 CI 环境中 assets 缺失。

**证据**：
- `.gitignore` 排除 `dashboard/dist/` 等构建产物
- Dockerfile 未包含前端构建步骤
- 本地有 170 个 assets 文件，但 fresh clone 后缺失

**影响范围**：Docker 部署后前端白页，生产环境不可用。

#### 1.4.2 修复方案

**方案**：采用多阶段构建，在 Docker 镜像构建阶段执行前端构建。

**文件**：`Dockerfile`（项目根目录）

**修改内容**：
1. 新增前端构建阶段（`FROM node:20-alpine AS frontend-builder`）。
2. 复制 `dashboard/` 源码并执行 `npm ci && npm run build`。
3. 在最终阶段复制构建产物到 nginx 静态目录。

**代码示例：Dockerfile 多阶段构建（Dockerfile）**
```dockerfile
# 新增前端构建阶段
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# 最终阶段
FROM nginx:1.27-alpine AS runtime
# ...原有配置...
COPY --from=frontend-builder /build/dist /usr/share/nginx/html
```

**备选方案**：若前端构建依赖复杂，可改为 CI 中构建产物并作为 artifact 发布，Dockerfile 从 artifact 拉取。

#### 1.4.3 风险评估

**风险**：
- 前端构建依赖（node_modules）可能引入供应链风险。
- 构建时间增加，影响 CI 时长。
- Node 版本与本地开发版本不一致可能导致构建失败。

**回归测试建议**：
- 在 CI 中新增 `docker build` 步骤，验证镜像可构建。
- 构建后启动容器，curl 验证 `index.html` 与至少一个 asset 可访问。
- 验证前端功能 smoke test（可与 H5 的 Playwright job 集成）。

---

### 1.5 H7 — 生产 compose 首启无法登录

#### 1.5.1 问题分析

**根因**：`docker-compose.prod.yml:374-397` 的环境配置未设置 `MAOP_ADMIN_PASSWORD`，而 `auth.py:124-130` 在 production 模式下若管理员密码未配置则抛出 `RuntimeError`。

**证据**：
- `docker-compose.prod.yml:374-397` — 无 `MAOP_ADMIN_PASSWORD`
- `py/maop/auth.py:124-130` — `raise RuntimeError(...)`

**影响范围**：生产环境首次启动后无法登录管理后台，系统不可用。

#### 1.5.2 修复方案

**文件 1**：`docker-compose.prod.yml`

**修改内容**：
- 在 `environment` 中新增 `MAOP_ADMIN_PASSWORD`，从 `.env` 或 secrets 读取，不硬编码。

**代码示例：docker-compose.prod.yml 环境变量（YAML）**
```yaml
environment:
  # ...原有配置...
  MAOP_ADMIN_PASSWORD: ${MAOP_ADMIN_PASSWORD:?必须设置管理员密码}
```

> 使用 `:?` 语法：若变量未设置，compose 启动时报错并退出，避免静默启动后不可用。

**文件 2**：`.env.example`

**修改内容**：
- 新增 `MAOP_ADMIN_PASSWORD=` 占位行并附注释说明生成方式。

**文件 3**：`docker-compose.prod.yml` 新增 `secrets` 段（推荐）

**代码示例：docker secrets 配置（YAML）**
```yaml
secrets:
  maop_admin_password:
    file: ./secrets/maop_admin_password.txt

services:
  maop:
    secrets:
      - maop_admin_password
    environment:
      MAOP_ADMIN_PASSWORD_FILE: /run/secrets/maop_admin_password
```

**文件 4**：`py/maop/auth.py:124-130`

**修改内容**：
- 支持 `MAOP_ADMIN_PASSWORD_FILE` 读取（Docker secrets 标准）。
- 启动时校验密码强度（最小长度、复杂度），弱密码时告警。

#### 1.5.3 风险评估

**风险**：
- 现有部署若依赖静默启动（如初次部署后手动配置），`:?` 会导致启动失败。
- 密码强度校验可能拒绝现有弱密码。

**回归测试建议**：
- 测试未设置密码时 compose 启动应明确报错（而非静默启动后不可用）。
- 测试设置密码后首启可登录。
- 验证 `MAOP_ADMIN_PASSWORD_FILE` 读取路径正确。

---

### 1.6 H9 — TLS+PG schema 硬断

#### 1.6.1 问题分析

**根因**：
1. `nginx.prod.conf:33-34` 硬编码 `cert.pem` 路径，且使用 named volume 挂载证书目录（named volume 首次挂载为空卷，证书不存在）。
2. Dockerfile 未 `COPY alembic.ini`，导致 `docker-entrypoint.sh:10-17` 检测不到 `alembic.ini` 而跳过数据库迁移，PG schema 不初始化。

**证据**：
- `nginx.prod.conf:33-34` — `ssl_certificate cert.pem;`
- `docker-compose.prod.yml` — named volume 挂载
- `Dockerfile` — 无 `COPY alembic.ini`
- `docker-entrypoint.sh:10-17` — `if [ -f alembic.ini ]` 条件

**影响范围**：生产环境 TLS 不可用（nginx 启动失败或降级），数据库 schema 不初始化（应用报错）。

#### 1.6.2 修复方案

**文件 1**：`nginx.prod.conf`

**修改内容**：
- 将硬编码证书路径改为环境变量化（通过 nginx `include` 模板或 envsubst）。

**代码示例：nginx 证书路径模板（nginx.conf）**
```nginx
ssl_certificate ${MAOP_TLS_CERT_PATH};
ssl_certificate_key ${MAOP_TLS_KEY_PATH};
```

**文件 2**：`docker-compose.prod.yml`

**修改内容**：
- 将 named volume 改为 bind mount，明确指向宿主机证书目录。
- 新增启动前校验：证书文件不存在时 compose 启动失败并给出明确指引。

**代码示例：docker-compose 证书 bind mount（YAML）**
```yaml
volumes:
  - ${MAOP_TLS_CERT_DIR:?必须设置证书目录}:/etc/nginx/certs:ro
environment:
  MAOP_TLS_CERT_PATH: /etc/nginx/certs/fullchain.pem
  MAOP_TLS_KEY_PATH: /etc/nginx/certs/privkey.pem
```

**文件 3**：`Dockerfile`

**修改内容**：
- 新增 `COPY alembic.ini ./` 与 `COPY py/maop/migrations ./migrations`。

**代码示例：Dockerfile 复制迁移文件（Dockerfile）**
```dockerfile
COPY alembic.ini ./
COPY py/maop/migrations ./migrations
```

**文件 4**：`docker-entrypoint.sh`

**修改内容**：
- 强化迁移步骤：检测 `alembic.ini` 不存在时报错退出（而非静默跳过）。

#### 1.6.3 风险评估

**风险**：
- bind mount 要求宿主机路径存在，部署文档需更新。
- 迁移执行可能改变现有数据库状态，需备份。

**回归测试建议**：
- 在干净环境（无既有数据库）测试首启，验证 schema 初始化。
- 在已有数据库环境测试迁移幂等性。
- 验证证书缺失时启动报错信息清晰。

---

### 1.7 H10 — HA 未接线+备份无 off-box

#### 1.7.1 问题分析

**根因**：
1. `docker-compose.prod.yml:383` 设置 `MAOP_PG_HOST=postgres`（直连主 PG），而非通过 haproxy（`MAOP_PG_HOST=haproxy`），HA 代理层被绕过。
2. `patroni.yml` 未在 compose 中挂载到 patroni 容器，配置为死配置。
3. `db_backup.py:178` 备份逻辑仅在本地同卷执行（`VACUUM INTO` + `shutil.copy2`），无 S3 或远程上传，不满足 off-box 备份要求。

**证据**：
- `docker-compose.prod.yml:383` — `MAOP_PG_HOST=postgres`
- `patroni.yml` 未挂载
- `py/maop/db_backup.py:178` — 本地 copy

**影响范围**：HA 形同虚设，主库故障时应用不切换；备份与原数据同卷，卷故障时备份同时丢失。

#### 1.7.2 修复方案

**文件 1**：`docker-compose.prod.yml`

**修改内容**：
- 将 `MAOP_PG_HOST` 改为 `haproxy`。
- 为 patroni 服务挂载 `patroni.yml`。

**代码示例：docker-compose HA 接线（YAML）**
```yaml
services:
  maop:
    environment:
      MAOP_PG_HOST: haproxy  # 原: postgres
      MAOP_PG_PORT: 5432
  patroni:
    volumes:
      - ./patroni.yml:/etc/patroni/patroni.yml:ro
```

**文件 2**：`py/maop/db_backup.py`

**修改内容**：
- 新增 off-box 上传后端：S3（通过 `boto3`）与可选 SFTP。
- 备份完成后校验远程对象存在性，失败时告警。
- 保留本地副本可选（通过配置开关）。

**代码示例：备份上传 S3（Python）**
```python
import boto3
from botocore.exceptions import ClientError

def upload_to_s3(local_path: str, bucket: str, key: str) -> None:
    s3 = boto3.client("s3")
    s3.upload_file(local_path, bucket, key)
    # 校验
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise RuntimeError(f"S3 上传校验失败: {exc}") from exc

def backup_database(config):
    local_path = _local_vacuum_into(config)  # 原有逻辑
    if config.offbox_backend == "s3":
        upload_to_s3(local_path, config.s3_bucket, config.s3_key)
        if not config.keep_local:
            Path(local_path).unlink()
```

**文件 3**：`.env.example`

**修改内容**：
- 新增 `MAOP_BACKUP_S3_BUCKET`、`MAOP_BACKUP_S3_PREFIX` 等配置项。

#### 1.7.3 风险评估

**风险**：
- 切换 `MAOP_PG_HOST` 到 haproxy 后，若 haproxy 配置错误，应用无法连接数据库。
- S3 上传需 IAM 凭证，凭证管理引入新风险。
- 备份失败可能导致数据丢失风险不被发现。

**回归测试建议**：
- 在 staging 环境验证 haproxy 路由正确（主库故障时切换到备库）。
- 测试 S3 上传与校验流程（使用 mock S3 如 `moto`）。
- 测试备份失败时告警触发。
- 验证 patroni.yml 挂载后集群初始化正确。

---

### 1.8 M1 — 路由引用不存在的 claude

#### 1.8.1 问题分析

**根因**：`agents.yaml` 中未定义 `claude` agent，但 routing 配置在 13 处引用该 agent 名，且 `maop_plan.py:36,222,237` 硬编码 `agent = "claude"`。

**证据**：
- `agents.yaml` — 无 claude 定义
- routing 引用：行 497, 542, 557, 576, 585, 588, 599, 601, 627, 655, 695, 708, 721
- `py/maop/maop_plan.py:36,222,237` — `agent = "claude"`

**影响范围**：调度器在匹配到 claude 路由时找不到 agent 定义，任务失败或静默跳过。

#### 1.8.2 修复方案

**方案选择**：需明确 claude agent 是否应存在。两种路径：

**路径 A（若 claude 应存在）**：
- 在 `agents.yaml` 中补充 claude agent 定义（含 adapter、model、api_key 引用等）。

**路径 B（若 claude 不应存在）**：
- 将所有 routing 引用与硬编码改为实际可用的 agent（如 `codex` 或配置化）。

**推荐路径 B + 配置化**：

**文件 1**：`py/maop/maop_plan.py`

**修改内容**：
- 移除硬编码 `agent = "claude"`，改为从配置读取默认 agent。

**代码示例：maop_plan 默认 agent 配置化（Python）**
```python
# 原：agent = "claude"
# 修改后：
DEFAULT_AGENT = os.getenv("MAOP_DEFAULT_AGENT", "codex")

def plan(...):
    agent = config.get("default_agent", DEFAULT_AGENT)
```

**文件 2**：`agents.yaml` 的 routing 段

**修改内容**：
- 将 13 处 `claude` 引用替换为 `${MAOP_DEFAULT_AGENT}` 或具体可用 agent 名。
- 引入 routing 校验：启动时验证所有引用的 agent 名在 `agents.yaml` 中存在定义，缺失时报错。

**文件 3**：新增 `py/maop/config/agents_validator.py`

**代码示例：agent 引用校验（Python）**
```python
def validate_routing(agents_yaml: dict, routing_yaml: dict) -> list[str]:
    defined = {a["name"] for a in agents_yaml["agents"]}
    errors = []
    for route in routing_yaml["routes"]:
        if route.get("agent") not in defined:
            errors.append(f"路由 {route['name']} 引用未定义 agent: {route.get('agent')}")
    return errors
```

#### 1.8.3 风险评估

**风险**：
- 替换 agent 后，任务执行行为可能变化（不同 agent 能力差异）。
- 配置化引入新的配置缺失风险。

**回归测试建议**：
- 新增单元测试：`validate_routing` 应捕获所有未定义引用。
- 集成测试：执行一个完整 plan，验证 agent 调用成功。
- 在 CI 中新增启动时 routing 校验步骤。

---

### 1.9 M6 — 前端安全债

#### 1.9.1 问题分析

**根因**：
1. `dashboard/src/api.js:4` 将 token 存入 `localStorage`，易受 XSS 攻击窃取。
2. `dashboard/src/composables/useDagProgress.js:110` 将 token 通过 URL query 传递，易被日志、浏览器历史、Referer 泄漏。

**证据**：
- `dashboard/src/api.js:4` — `localStorage.setItem('token', ...)`
- `dashboard/src/composables/useDagProgress.js:110` — URL query 含 token

**影响范围**：token 泄漏导致会话劫持，安全风险高。

#### 1.9.2 修复方案

**文件 1**：`dashboard/src/api.js`

**修改内容**：
- 将 token 从 `localStorage` 迁移到 `httpOnly cookie`（需后端配合设置）。
- 前端不再直接读取 token，改为依赖 cookie 自动携带。

**代码示例：api.js 使用 cookie 认证（JavaScript）**
```javascript
// 原：localStorage.setItem('token', token)
// 修改后：token 由后端 Set-Cookie httpOnly 携带，前端不接触
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE,
  withCredentials: true,  // 携带 cookie
});
// 移除手动 Authorization header 设置
```

**文件 2**：`dashboard/src/composables/useDagProgress.js`

**修改内容**：
- 移除 URL query 中的 token，改用 EventSource 的 `withCredentials` 或在 WebSocket 握手时依赖 cookie。

**代码示例：useDagProgress 移除 URL token（JavaScript）**
```javascript
// 原：url = `/api/dag/progress?token=${token}`
// 修改后：
const evtSource = new EventSource('/api/dag/progress', { withCredentials: true });
```

**文件 3**：后端 `py/maop/auth.py`

**修改内容**：
- 登录成功后设置 `Set-Cookie: maop_token=...; HttpOnly; Secure; SameSite=Strict`。
- 新增 CSRF 保护（如双重提交 cookie 或 token）。

#### 1.9.3 风险评估

**风险**：
- 切换到 cookie 认证后，现有已签发 token 失效，用户需重新登录。
- CSRF 防护需配套，否则引入新风险。
- 跨域场景需正确配置 CORS + credentials。

**回归测试建议**：
- 验证登录后 cookie 设置正确（HttpOnly、Secure、SameSite）。
- 验证 XSS 攻击无法读取 token（`document.cookie` 在 httpOnly 下不可读）。
- 验证 DAG progress 连接不再在 URL 中暴露 token。
- 新增 CSRF 测试。

---

## 第2章 P1 级修复方案（应修复，8 条）

### 2.1 H2 — CI 红门（doc_reconcile）

#### 2.1.1 问题分析

**根因**：README 中记载子包数为 17，但实际代码树中有 18 个子包，`doc_reconcile` 检查发现不一致并以 `exit 1` 阻断 CI。

**证据**：README 17 vs 实际 18 子包。

**影响范围**：CI 红门持续失败，阻断所有 PR 合并。

#### 2.1.2 修复方案

**文件**：`README.md`

**修改内容**：
- 将子包数从 17 更新为 18。
- 补充新增子包的说明段落。
- 在 CI 中新增 `docs/reconcile` 失败时的自动修复提示（输出 diff）。

#### 2.1.3 风险评估

**风险**：低。仅文档更新。

**回归测试建议**：
- 运行 `make doc-reconcile` 验证通过。
- 验证 README 子包列表与 `py/maop/*/` 目录一致。

---

### 2.2 H8 — 8 个 MAOP_* 指标无调用方

#### 2.2.1 问题分析

**根因**：`monitoring.py:530-537` 定义了 8 个 `MAOP_*` 业务指标（如 plan duration、route score 等），但全代码树无任何位置调用这些指标的 `.inc/.set/.observe` 方法。

**证据**：
- `py/maop/monitoring.py:530-537` — 8 个指标定义
- 全库 58 处 `.inc/.set/.observe` 调用均针对其他指标，无 MAOP_* 调用

**影响范围**：8 个核心业务指标无法观测，运维盲区。

#### 2.2.2 修复方案

**文件 1**：`py/maop/monitoring.py`

**修改内容**：
- 为每个指标补充文档注释，说明预期调用位置与语义。

**文件 2**：在对应业务逻辑处补充指标调用。

**代码示例：指标调用补充（Python）**
```python
# py/maop/maop_plan.py 中补充：
from maop.monitoring import MAOP_PLAN_DURATION

def plan(...):
    start = time.monotonic()
    # ...原有逻辑...
    MAOP_PLAN_DURATION.observe(time.monotonic() - start)
```

**文件 3**：新增 `tests/test_monitoring.py`

**修改内容**：
- 验证每个 MAOP_* 指标在对应业务流程中被至少调用一次。

#### 2.2.3 风险评估

**风险**：低。指标调用为只读副作用，不影响业务逻辑。

**回归测试建议**：
- 使用 `prometheus_client` 的 `CollectorRegistry` 测试模式验证指标值变化。
- 在 staging 环境验证 Prometheus 可抓取到非零值。

---

### 2.3 M2 — 环境变量脱节

#### 2.3.1 问题分析

**根因**：
1. 规范名为 `MAOP_TLS_ENABLED`，但代码读取 `MAOP_TLS`（`server.py:286`, `state.py:101`, `cli.py:38`）。
2. `MAOP_HA_BACKEND` 在主代码 `py/maop` 中 0 引用（仅测试 `test_enterprise_ha.py` 引用，但 `maop.enterprise` 包不在当前代码树）。

**证据**：
- `py/maop/server.py:286` — `os.getenv("MAOP_TLS")`
- `py/maop/state.py:101` — `os.getenv("MAOP_TLS")`
- `py/maop/cli.py:38` — `os.getenv("MAOP_TLS")`
- 规范名见 `.env.example`：`MAOP_TLS_ENABLED`

**影响范围**：按文档配置 `MAOP_TLS_ENABLED=true` 不生效，TLS 不启用。

#### 2.3.2 修复方案

**文件 1**：`py/maop/server.py:286`、`py/maop/state.py:101`、`py/maop/cli.py:38`

**修改内容**：
- 统一读取 `MAOP_TLS_ENABLED`，并支持向后兼容（读取 `MAOP_TLS` 作为 fallback 并告警）。

**代码示例：环境变量统一读取（Python）**
```python
def get_tls_enabled() -> bool:
    val = os.getenv("MAOP_TLS_ENABLED")
    if val is None:
        val = os.getenv("MAOP_TLS")
        if val is not None:
            import warnings
            warnings.warn(
                "MAOP_TLS 已弃用，请改用 MAOP_TLS_ENABLED",
                DeprecationWarning,
                stacklevel=2,
            )
    return val.lower() in ("1", "true", "yes")
```

**文件 2**：新增 `py/maop/config/env.py` 集中管理环境变量读取。

**文件 3**：`MAOP_HA_BACKEND` 处理：
- 若企业版未发布，从 `.env.example` 中移除该变量并标注"企业版专属"。
- 或在主代码中补充 `MAOP_HA_BACKEND` 读取逻辑（若 HA 功能应存在于核心包）。

#### 2.3.3 风险评估

**风险**：
- 现有部署使用 `MAOP_TLS` 的，迁移期需双名支持。

**回归测试建议**：
- 测试 `MAOP_TLS_ENABLED=true` 与 `MAOP_TLS=true` 均能启用 TLS。
- 测试 `MAOP_TLS` 触发 DeprecationWarning。

---

### 2.4 M3 — 根目录解析不一致

#### 2.4.1 问题分析

**根因**：6 处代码读取 `MAOP_ROOT_DIR`，但 Dockerfile 设置的是 `MAOP_ROOT`，变量名不一致导致代码读不到值，回退到默认路径。同时 `data/maop.db`（610304 字节）和 `py/data/maop.db`（36864 字节）都存在，数据库路径混乱。

**证据**：
- 读取 `MAOP_ROOT_DIR`：`maop_plan.py:132`, `dispatch_core.py:414`, `route_scorer.py:413`, `auth.py:219`, `routing_preview.py:38,98`
- 设置 `MAOP_ROOT`：`Dockerfile:47`
- `data/maop.db` 610304 字节、`py/data/maop.db` 36864 字节

**影响范围**：Docker 环境下根目录解析失败，数据文件路径错误。

#### 2.4.2 修复方案

**文件 1**：`py/maop/config/env.py`（新增集中管理）

**修改内容**：
- 统一 `get_root_dir()` 函数，同时支持 `MAOP_ROOT_DIR` 与 `MAOP_ROOT`（向后兼容）。

**代码示例：根目录统一解析（Python）**
```python
from pathlib import Path

def get_root_dir() -> Path:
    val = os.getenv("MAOP_ROOT_DIR") or os.getenv("MAOP_ROOT")
    if val is None:
        raise RuntimeError("MAOP_ROOT_DIR 未设置")
    return Path(val).resolve()
```

**文件 2**：上述 6 处代码改为调用 `get_root_dir()`。

**文件 3**：`Dockerfile:47`

**修改内容**：
- 将 `ENV MAOP_ROOT=...` 改为 `ENV MAOP_ROOT_DIR=...`（或同时设置两者）。

**文件 4**：数据库路径治理

**修改内容**：
- 确定唯一数据库位置（推荐 `data/maop.db`）。
- 删除 `py/data/maop.db` 或将其改为测试专用 fixture。
- 在文档中明确数据库路径配置。

#### 2.4.3 风险评估

**风险**：
- 数据库路径变更可能影响现有部署。
- 需数据迁移指引。

**回归测试建议**：
- 测试 `MAOP_ROOT` 与 `MAOP_ROOT_DIR` 均能正确解析。
- 验证数据库路径唯一性。
- 在 Docker 环境验证根目录正确。

---

### 2.5 M4 — pause 空壳（读取端缺失）

#### 2.5.1 问题分析

**根因**：`control.py:63-75` 的 `pause()` 创建 `.maop_pause` 标记文件，但全代码树无任何位置读取该文件，调度器/执行器未检查标记，进程不暂停。

**证据**：`py/maop/control.py:63-75` — 写入 `.maop_pause`；全树无读取该文件的位置。

**影响范围**：用户调用 pause 后系统继续执行，行为与预期不符。

#### 2.5.2 修复方案

**文件 1**：`py/maop/engine.py`（或调度主循环）

**修改内容**：
- 在任务派发前检查 `.maop_pause` 是否存在，存在则等待或跳过。

**代码示例：调度器 pause 检查（Python）**
```python
from pathlib import Path
import time

def check_pause(root_dir: Path) -> None:
    pause_file = root_dir / ".maop_pause"
    while pause_file.exists():
        logger.info("系统已暂停，等待恢复...")
        time.sleep(1)

def dispatch_task(task):
    check_pause(get_root_dir())
    # ...原有派发逻辑...
```

**文件 2**：`py/maop/control.py`

**修改内容**：
- `resume()` 函数删除 `.maop_pause` 文件。
- 补充 pause/resume 状态查询 API。

#### 2.5.3 风险评估

**风险**：
- pause 检查引入调度延迟（每任务 1 秒 sleep）。
- 需确保 pause 文件清理（异常退出后残留）。

**回归测试建议**：
- 测试 pause 后新任务不执行。
- 测试 resume 后任务恢复。
- 测试异常退出后 pause 文件清理。

---

### 2.6 M5 — DAG 循环静默绕过

#### 2.6.1 问题分析

**根因**：`maop_plan.py:335-339` 在检测到 DAG 循环时，将循环节点强行排入执行序列（绕过循环检查），而 `engine_utils.py:191-198` 的正确循环报错逻辑被绕过。

**证据**：
- `py/maop/maop_plan.py:335-339` — 循环节点强行排入
- `py/maop/engine_utils.py:191-198` — 正确报错但被绕过

**影响范围**：含循环的 DAG 会无限执行或产生非预期行为。

#### 2.6.2 修复方案

**文件**：`py/maop/maop_plan.py:335-339`

**修改内容**：
- 移除循环节点强行排入逻辑，改为调用 `engine_utils` 的循环检测并报错退出。

**代码示例：DAG 循环检测（Python）**
```python
# 原：循环节点强行排入
# 修改后：
from maop.engine_utils import detect_cycle

def plan(...):
    graph = build_dag(tasks)
    cycle = detect_cycle(graph)
    if cycle:
        raise ValueError(
            f"DAG 存在循环: {' -> '.join(cycle)}。"
            f"请检查任务依赖配置。"
        )
    # ...继续正常排程...
```

#### 2.6.3 风险评估

**风险**：
- 现有含循环的 DAG（若有意为之的循环重试）会失败。
- 需评估是否有合法循环场景。

**回归测试建议**：
- 测试含循环的 DAG 报错并给出清晰路径。
- 测试无循环 DAG 正常排程。
- 新增 `tests/test_dag_cycle.py` 覆盖各种循环模式。

---

### 2.7 Low-2 — .env.sandbox 入库

#### 2.7.1 问题分析

**根因**：`.env.sandbox` 被 git 跟踪，可能含敏感配置（API key、token 等）。

**证据**：`git ls-files` 确认 `.env.sandbox` 被跟踪。

**影响范围**：敏感信息泄漏风险。

#### 2.7.2 修复方案

**文件 1**：`.gitignore`

**修改内容**：
- 新增 `.env.sandbox` 到 `.gitignore`。

**文件 2**：`.env.sandbox`

**修改内容**：
- 审查内容，若含敏感值则从 git 历史中移除（`git filter-repo` 或 BFG）。
- 替换为 `.env.sandbox.example`（仅含占位符）。

**操作步骤**：
```bash
# 1. 从跟踪中移除
git rm --cached .env.sandbox
# 2. 添加到 .gitignore
# 3. 若含敏感信息，清理历史
git filter-repo --path .env.sandbox --invert-paths
# 4. 创建 example
cp .env.sandbox .env.sandbox.example
# 编辑 .env.sandbox.example 替换所有值为占位符
git add .env.sandbox.example .gitignore
```

#### 2.7.3 风险评估

**风险**：
- 历史清理会重写 git 历史，需团队协调强制 pull。
- 若含真实凭证，需立即轮换。

**回归测试建议**：
- 验证 `git ls-files` 不再包含 `.env.sandbox`。
- 验证 `.env.sandbox.example` 存在且无敏感值。
- 若轮换凭证，验证新凭证可用。

---

### 2.8 Low-4 — haproxy 无认证 admin

#### 2.8.1 问题分析

**根因**：`haproxy.cfg:50` 配置 `stats admin if TRUE` 且 `bind *:7000`，admin 端点无认证暴露。

**证据**：`haproxy.cfg:50` — `stats admin if TRUE` + `bind *:7000`。

**影响范围**：任何人可访问 haproxy admin 端点，执行管理操作（如禁用后端服务器）。

#### 2.8.2 修复方案

**文件**：`haproxy.cfg`

**修改内容**：
- 为 stats 端点添加认证。
- 限制 bind 地址（仅内网或 localhost）。

**代码示例：haproxy stats 认证（haproxy.cfg）**
```haproxy
frontend stats
    bind 127.0.0.1:7000  # 原: *:7000
    mode http
    http-request auth realm haproxy-stats unless { http_auth(haproxy_users) }
    stats enable
    stats uri /
    stats admin if TRUE

userlist haproxy_users
    user admin password <hashed-password>
```

> 密码使用 `mkpasswd` 生成 hash，不存明文。

#### 2.8.3 风险评估

**风险**：
- 现有监控脚本若直接访问 stats 端点，需更新凭证配置。
- bind 改为 127.0.0.1 后远程监控需通过 SSH 隧道或反向代理。

**回归测试建议**：
- 验证未认证访问返回 401。
- 验证认证后可访问 stats。
- 验证 bind 限制生效（外部不可达）。

---

## 第3章 P2 级修复方案（建议修复，3 条）

### 3.1 H5 — e2e `|| true` 掩盖非 exit 5 失败

#### 3.1.1 问题分析

**根因**：`ci.yml:193` 使用 `|| true` 掩盖所有退出码，而非仅消除 exit 5 噪音。

**证据**：`.github/workflows/ci.yml:193` — `|| true`。

**影响范围**：主 job 中 e2e 步骤的非 exit 5 失败（如 import 错误）被掩盖。

#### 3.1.2 修复方案

**文件**：`.github/workflows/ci.yml:193`

**修改内容**：
- 将 `|| true` 改为精确过滤 exit 5。

**代码示例：精确退出码过滤（YAML）**
```yaml
- name: e2e 测试（容忍无浏览器环境）
  run: |
    set +e
    npx playwright test
    code=$?
    set -e
    if [ $code -ne 0 ] && [ $code -ne 5 ]; then
      exit $code
    fi
```

#### 3.1.3 风险评估

**风险**：低。

**回归测试建议**：
- 模拟 exit 5：CI 应通过。
- 模拟 exit 1：CI 应失败。

---

### 3.2 M7 — 一个月 4 版本 + 6 个禁用 agent

#### 3.2.1 问题分析

**根因**：8/11-8/14 四天内发布 4 个版本，发布节奏过快；`agents.yaml` 中 6 个 agent 配置为 `enabled: false`。

**证据**：CHANGELOG.md 4 天 4 版本；agents.yaml 6 个 `enabled: false`。

**影响范围**：版本质量难以保证；禁用 agent 增加配置噪声。

#### 3.2.2 修复方案

**文件 1**：`CHANGELOG.md` + 发布流程文档

**修改内容**：
- 制定发布节奏规范（如每两周一个 minor 版本）。
- 在 `CONTRIBUTING.md` 中补充发布流程 checklist。

**文件 2**：`agents.yaml`

**修改内容**：
- 评估 6 个禁用 agent：移除不计划的，标注计划的（含预计启用日期）。

#### 3.2.3 风险评估

**风险**：低。治理性改进。

**回归测试建议**：无代码变更，无需回归测试。

---

### 3.3 Low-5 — prometheus 重复注册

#### 3.3.1 问题分析

**根因**：`static.py:136` 与 `_register_routes.py:469` 重复定义同一 prometheus 指标，导致注册冲突或指标覆盖。

**证据**：`py/maop/static.py:136` 与 `py/maop/_register_routes.py:469` 重复定义。

**影响范围**：指标值可能被覆盖或注册时报错。

#### 3.3.2 修复方案

**文件**：`py/maop/static.py:136` 或 `py/maop/_register_routes.py:469`（保留一处）

**修改内容**：
- 移除其中一处的重复定义，统一到单一注册点。
- 推荐保留 `_register_routes.py` 中的定义（路由注册中心）。

**代码示例：移除重复注册（Python）**
```python
# py/maop/static.py 中移除：
# REQUEST_COUNT = Counter(...)  # 删除此行

# 改为导入：
from maop._register_routes import REQUEST_COUNT
```

#### 3.3.3 风险评估

**风险**：低。需确认两处定义参数一致。

**回归测试建议**：
- 启动应用验证无注册冲突警告。
- 验证指标值正确累加。

---

## 第4章 修复方案汇总

### 表：修复方案优先级汇总

| 优先级 | 数量 | 编号 | 预计工作量 |
|--------|------|------|-----------|
| P0 | 9 | H1, H3, H4, H6, H7, H9, H10, M1, M6 | 大（涉及核心调度、生产部署、安全） |
| P1 | 8 | H2, H8, M2, M3, M4, M5, Low-2, Low-4 | 中（涉及配置、文档、可观测性） |
| P2 | 3 | H5, M7, Low-5 | 小（涉及 CI 信号、治理、重复定义） |
| **合计** | **20** | — | — |

> **说明**：合计 20 条因 H5 在 P2 中保留（部分属实的残留风险需修复），与确认问题 19 条的差异在于 H5 的"残留风险修复"单列。

### 表：修复涉及文件清单

| 文件路径 | 涉及问题编号 |
|---------|-------------|
| `py/maop/cli.py` | H1, M2 |
| `py/maop/maop_plan.py` | M1, M5, H8 |
| `py/maop/auth.py` | H7, M6, M3 |
| `py/maop/server.py` | M2 |
| `py/maop/state.py` | M2 |
| `py/maop/monitoring.py` | H8 |
| `py/maop/db_backup.py` | H10 |
| `py/maop/control.py` | M4 |
| `py/maop/engine_utils.py` | M5 |
| `py/maop/static.py` | Low-5 |
| `py/maop/_register_routes.py` | Low-5 |
| `py/maop/config/env.py`（新增） | M2, M3 |
| `py/maop/config/agents_validator.py`（新增） | M1 |
| `.github/workflows/ci.yml` | H3, H4, H5 |
| `pyproject.toml` | H3 |
| `docker-compose.prod.yml` | H7, H9, H10 |
| `Dockerfile` | H9, M3 |
| `docker-entrypoint.sh` | H9 |
| `nginx.prod.conf` | H9 |
| `haproxy.cfg` | Low-4 |
| `agents.yaml` | M1, M7 |
| `README.md` | H2 |
| `.env.example` | H7, H10, M2 |
| `.gitignore` | Low-2 |
| `.env.sandbox` | Low-2 |
| `CHANGELOG.md` | M7 |
| `CONTRIBUTING.md` | M7 |
| `dashboard/src/api.js` | M6 |
| `dashboard/src/composables/useDagProgress.js` | M6 |
| `tests/test_cli.py`（新增） | H1 |
| `tests/test_monitoring.py`（新增） | H8 |
| `tests/test_dag_cycle.py`（新增） | M5 |

### 表：修复顺序建议

| 阶段 | 修复内容 | 理由 |
|------|---------|------|
| 第1阶段 | Low-2（.env.sandbox 入库） | 安全风险，立即处理 |
| 第2阶段 | H7, H9, H10（生产部署三件套） | 生产不可用，最高优先级 |
| 第3阶段 | M6（前端安全债） | 安全风险 |
| 第4阶段 | H6（Docker 前端构建） | 生产部署可用性 |
| 第5阶段 | H1, M1, M5（调度核心） | 核心功能正确性 |
| 第6阶段 | H3, H4（CI 门禁） | 质量保障 |
| 第7阶段 | H2, M2, M3, M4（配置与文档） | 一致性 |
| 第8阶段 | H8, Low-4, Low-5（可观测性与安全） | 运维完善 |
| 第9阶段 | H5, M7（CI 信号与治理） | 工程改进 |

---

## 第5章 修复方案审核要点

1. **每个修复方案均包含**：问题分析（根因 + 证据）、修复方案（文件 + 代码）、风险评估、回归测试建议。
2. **未实施任何代码修改**：本文件仅为方案设计，实际修改需经审核后由开发人员实施。
3. **依赖关系**：M3（根目录解析）应先于 H9（TLS+PG schema）修复，因 H9 涉及路径配置。
4. **测试先行**：建议为每个 P0 修复先编写回归测试，再实施修复（TDD）。
5. **分批合并**：建议按第 4 章修复顺序分批 PR，每批 PR 经 CI 验证后合并，避免大合并风险。

---

**方案编制人**：修正报告编制与修复方案设计专家（GLM-5.2）
**方案状态**：已完成，待审核
**对应报告**：`docs/audit-report-corrected.md`