# MAOP Troubleshooting Manual

本手册系统化梳理 MAOP v4.3.0（基于 FastAPI 的 Plan-Execute-Verify 智能体编排框架）在安装、启动、认证、LLM Provider、Agent 执行、记忆与向量搜索、性能、Dashboard、企业版、数据库迁移、日志分析等场景下的常见问题与诊断流程。所有命令示例均可直接复制运行，企业版相关能力需配合 `MAOP_LICENSE_KEY` 启用。

---

## 1. Diagnostic Toolkit

MAOP 内置一整套诊断命令与 API，可在问题发生第一时间快速定位根因。下表汇总了核心入口与适用场景。

| 入口 | 类型 | 适用场景 | 鉴权 |
|------|------|---------|------|
| `maop health` | CLI | 一键健康检查（进程/配置/数据库） | 无 |
| `maop status` | CLI | 框架运行状态查询 | 无 |
| `maop validate` | CLI | 配置文件语法与依赖校验 | 无 |
| `POST /api/control/doctor` | API | Dashboard 自检（agent/provider/db） | Bearer |
| `POST /api/control/provider-health` | API | LLM Provider 健康检查 | Bearer |
| `GET /api/system/diagnostics` | API | 系统级诊断（admin 专用） | admin |
| `GET /api/system/resources` | API | CPU/内存/磁盘/连接池使用 | Bearer |
| `GET /api/health` | API | K8s liveness/readiness probe | 无 |
| `GET /api/prometheus` | API | Prometheus metrics 抓取 | 无 |
| `logs/maop-structured.log` | 文件 | 结构化文本日志 | 无 |
| `MAOP_JSON_LOG=1` | 环境变量 | 启用 JSON 行日志（便于 jq 过滤） | 无 |

### 一键诊断示例

```bash
# CLI 一键诊断（无需启动服务）
maop health
maop status
maop validate

# 通过 API 诊断（默认端口 9079）
curl -s http://127.0.0.1:9079/api/health | jq .
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/system/diagnostics | jq .
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/system/resources | jq .

# 实时日志（结构化文本）
tail -f logs/maop-structured.log

# 启用 JSON 行日志，便于 jq 过滤
MAOP_JSON_LOG=1 MAOP_JSON_LOG_FILE=logs/maop.jsonl maop start

# 查看 Prometheus metrics
curl -s http://127.0.0.1:9079/api/prometheus | grep -E "duration|latency|error"
```

> 推荐流程：先 `maop health` 看整体状态 → `maop validate` 排除配置问题 → 看 `logs/maop-structured.log` → 调用 `/api/system/diagnostics` 拿到完整快照。

---

## 2. Installation & Startup Issues

### 2.1 Python 依赖安装失败

**症状**：`pip install -r requirements.txt` 失败，提示编译错误或找不到匹配的 wheel。
**原因**：Python 版本不符（MAOP 需 3.12+）、网络访问 PyPI 慢、系统缺少编译工具链（如 `build-essential`、`python3-dev`）。
**解决**：

```bash
# 检查 Python 版本（必须 >= 3.12）
python --version

# 使用国内镜像加速
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 使用锁定版本依赖（生产推荐）
pip install -r requirements.lock

# Windows 下若编译失败，安装 Microsoft C++ Build Tools 后重试
# 或优先使用预编译 wheel：pip install --only-binary :all: -r requirements.txt
```

### 2.2 端口被占用

**症状**：启动时报 `Address already in use` 或 `[Errno 98] Address already in use`。
**原因**：上一次 `maop start` 未正常退出，或其他进程占用 9079 端口。
**解决**：

```bash
# Linux/Mac：定位并杀掉占用进程
lsof -i :9079
kill -9 <PID>

# Windows：定位并杀掉占用进程
netstat -ano | findstr :9079
taskkill /PID <PID> /F

# 或更换端口启动
maop start --port 9080
```

### 2.3 SQLite 数据库锁

**症状**：`database is locked`、`sqlite3.OperationalError: database table is locked`。
**原因**：多进程或多 worker 并发写入 SQLite，超出 SQLite 单写者模型。
**解决**：

- 确保单进程模式：`MAOP_WORKERS=1`（默认值，开发环境推荐）。
- 生产环境切换 PostgreSQL 后端（企业版）：`MAOP_DB_BACKEND=postgres`。
- 检查 WAL 模式是否启用（提升并发读）：

```bash
sqlite3 data/maop.db "PRAGMA journal_mode;"
# 若返回 "wal" 则已启用；否则执行：
sqlite3 data/maop.db "PRAGMA journal_mode=WAL;"
```

### 2.4 配置加载失败

**症状**：启动日志出现 `ConfigLoader failed` 警告，部分 agent 或 model 未注册。
**原因**：`config/agents.yaml` 或 `config/models.yaml` 缺失、YAML 语法错误、字段类型不匹配。
**解决**：

```bash
# 校验全部配置
maop validate

# 单独检查 YAML 语法
python -c "import yaml; yaml.safe_load(open('config/agents.yaml'))"
python -c "import yaml; yaml.safe_load(open('config/models.yaml'))"

# 查看 ConfigLoader 加载明细
MAOP_LOG_LEVEL=DEBUG maop start 2>&1 | grep -i config
```

### 2.5 TLS 证书错误

**症状**：`SSLError`、`certificate verify failed`、客户端无法连接 HTTPS 端点。
**原因**：自签名证书未受客户端信任、证书已过期、`MAOP_TLS_CERT` 路径错误。
**解决**：

```bash
# 开发环境：生成自签名证书
openssl req -x509 -newkey rsa:4096 -keyout tls.key -out tls.crt -days 365 -nodes

# 启用 MAOP 内置 TLS
export MAOP_TLS=1
export MAOP_TLS_CERT=/path/to/tls.crt
export MAOP_TLS_KEY=/path/to/tls.key

# 生产环境：使用 Let's Encrypt + Nginx 反向代理，MAOP_TLS=0（由 Nginx 终结 TLS）
```

---

## 3. Authentication & Authorization Issues

### 3.1 登录失败

**症状**：`POST /api/auth/login` 返回 401，错误信息 `Invalid credentials` 或 `User disabled`。
**原因**：用户名/密码错误、用户被禁用、JWT secret 变更导致旧 token 失效、PBKDF2 校验异常。
**解决**：

```bash
# 检查鉴权环境变量
echo $MAOP_AUTH               # 必须为 enabled
echo $MAOP_JWT_SECRET         # 必须非空且 >= 32 字符

# 重置 admin 密码（CLI）
maop reset-password admin

# 或直接编辑 users.db（应急场景）
sqlite3 data/users.db "UPDATE users SET password_hash='<new_hash>' WHERE username='admin'"
```

### 3.2 JWT Token 过期

**症状**：API 返回 401，错误信息 `Token expired`。
**原因**：JWT 默认有效期较短（通常 1h），未及时刷新。
**解决**：

```bash
# 重新登录获取新 token
TOKEN=$(curl -s -X POST http://127.0.0.1:9079/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}' | jq -r .token)

# 或刷新 token（如启用了 refresh 机制）
curl -X POST http://127.0.0.1:9079/api/auth/refresh \
  -H "Authorization: Bearer $TOKEN"
```

### 3.3 权限不足

**症状**：API 返回 403，错误信息 `Admin required`。
**原因**：当前用户角色不是 admin，尝试访问管理类端点。
**解决**：

```bash
# 查看当前用户角色
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/auth/status | jq .

# 提升为 admin（需已登录 admin 账户）
curl -X PUT http://127.0.0.1:9079/api/auth/users/username \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"admin"}'
```

### 3.4 PBKDF2 验证慢

**症状**：登录响应时间 > 500ms，CPU 占用瞬时升高。
**原因**：MAOP 4.3.0 将 PBKDF2 迭代次数提升到 600k（遵循 OWASP 2023 建议），属预期行为。
**解决**：生产环境确保服务器 CPU 充足；仅开发环境可临时降低：

```bash
# 仅开发环境（生产禁用，会降低安全性）
export MAOP_PBKDF2_ITERATIONS=260000
```

---

## 4. LLM Provider Issues

### 4.1 LLM 调用失败

**症状**：`LLMProviderError`、`TimeoutError`、`ConnectError`。
**原因**：API Key 无效/过期、网络访问受限、上游限流（429）。
**解决**：

```bash
# 检查所有 provider 健康
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/control/provider-health | jq .

# 查看 API Key 列表
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/model/key/list | jq .

# 测试上游连通性
curl -s https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" | jq '.data | length'
```

### 4.2 熔断器触发

**症状**：`CircuitBreakerOpenError`，所有 LLM 调用立即失败。
**原因**：连续失败次数达到阈值（默认 5 次），熔断器进入 OPEN 状态。
**解决**：

```bash
# 查看熔断器状态
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/subsystems | jq '.circuit_breaker'

# 等待恢复时间窗口过去（默认 60s），或重启服务重置
maop stop && maop start
```

### 4.3 OmniRoute 路由失败

**症状**：OmniRoute 调用返回 502/503，日志显示 `No available provider`。
**原因**：上游 provider 全部不可用、fallback 链配置错误、路由策略与 provider 健康不匹配。
**解决**：

```bash
# 查看最近路由决策
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9079/api/routing/decisions/recent?limit=10" | jq .

# 查看 provider 列表与健康状态
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/model/providers | jq .

# 检查 fallback 链配置
cat config/models.yaml | grep -A 10 fallback
```

---

## 5. Agent Execution Issues

### 5.1 Agent 无响应

**症状**：任务长时间停留在 pending 状态，无 progress 更新。
**原因**：Agent 健康检查失败、Worker Pool 耗尽、依赖的 LLM Provider 不可用。
**解决**：

```bash
# 查看所有 agent 健康状态
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/agents | jq '.[] | {name, healthy, enabled}'

# 健康检查所有 agent
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/agents/health-check-all

# 查看失败任务日志
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9079/api/failures?limit=20" | jq .
```

### 5.2 Worker Pool 阻塞

**症状**：任务持续排队但无 worker 拾取，队列深度持续增长。
**原因**：worker 数量不足、worker 死锁、长任务阻塞 worker。
**解决**：

```bash
# 查看任务队列
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/live | jq '.queue'

# 检查并发配置
cat config/agents.yaml | grep -A 5 worker_pool

# 重启 worker / 触发刷新
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/control/refresh
```

### 5.3 沙箱权限拒绝

**症状**：`PermissionError`、`SandboxViolation`，插件尝试访问受限路径。
**原因**：插件尝试访问未在 `sandbox.allowed_paths` 中的路径。
**解决**：编辑 `config/agents.yaml`，在 `sandbox.allowed_paths` 中添加所需路径，然后 `maop validate` 校验并重启服务。

### 5.4 DAG 循环依赖

**症状**：启动或运行时报 `ValueError: Cycle detected: A -> B -> A`。
**原因**：Agent DAG 配置中存在循环边。
**解决**：检查 DAG 配置（`config/agents.yaml` 中的 `dag` 段或 `depends_on` 字段），移除构成循环的依赖边，然后重新 `maop validate`。

---

## 6. Memory & Vector Search Issues

### 6.1 向量搜索结果为空

**症状**：`/api/vector/search` 返回空数组，但知识库中应有匹配项。
**原因**：向量索引未构建、embedding 维度不匹配、查询向量与索引模型不一致。
**解决**：

```bash
# 查看向量库统计
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/vector/stats | jq .

# 触发重建索引
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/knowledge/vector/index \
  -H "Content-Type: application/json" -d '{}'
```

### 6.2 记忆未注入

**症状**：任务执行日志中无 `[Memory Context]` 输出，Agent 行为像无记忆。
**原因**：记忆模块未启用、相关度阈值过高、向量库为空。
**解决**：

```bash
# 查看记忆统计
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/memory/stats | jq .

# 手动搜索记忆
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9079/api/memory/search?q=test" | jq .
```

---

## 7. Performance Issues

### 7.1 API 响应慢

**症状**：API 响应时间 > 1s，用户感知明显延迟。
**原因**：LLM 调用阻塞、数据库慢查询、内存缓存未命中率高。
**解决**：

```bash
# 查看 Prometheus metrics（duration/latency）
curl -s http://127.0.0.1:9079/api/prometheus | grep -E "duration|latency"

# 查看 timeseries 趋势
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/timeseries | jq .

# 检查系统资源
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/system/resources | jq .
```

### 7.2 内存占用高

**症状**：进程内存持续增长，触发 OOM 或 GC 频繁。
**原因**：向量库全量加载、LRU 缓存配置过大、历史日志/任务堆积未清理。
**解决**：

- 调小 `cache.max_size`（在 `config/agents.yaml` 中配置）。
- 启用日志轮转：`export MAOP_LOG_ROTATION=1`。
- 定期清理 dead letters 与历史任务：`POST /api/control/maintain`。

### 7.3 异步事件循环阻塞

**症状**：API 间歇性卡顿，单请求慢但 CPU/内存正常。
**原因**：异步路由中调用了同步阻塞 API（如 `subprocess.run`、同步 `requests.post`）。
**解决**：升级到 4.3.0+，已将 `system.py` 的 `subprocess.run` 改为 `asyncio.create_subprocess_exec`；自定义插件中若需调用子进程，统一使用 `asyncio.create_subprocess_exec` 或 `loop.run_in_executor`。

---

## 8. Dashboard Issues

### 8.1 前端 404

**症状**：访问 `/` 返回 404，`/assets/*` 全部 404。
**原因**：`dashboard-enterprise/dist/` 未构建或路径配置错误。
**解决**：

```bash
cd dashboard-enterprise
npm install
npm run build
# 确认 dist/ 目录存在且包含 index.html
ls dist/index.html
```

### 8.2 WebSocket 断开

**症状**：实时数据不更新，浏览器控制台报 `WS connection closed`。
**原因**：Nginx `proxy_read_timeout` 过短、JWT 过期、反向代理未透传 `Upgrade` 头。
**解决**：

- 检查 Nginx 配置：`proxy_read_timeout 3600s;` 并透传 `Upgrade` / `Connection` 头。
- 检查 JWT 是否过期，过期则重新登录。
- 浏览器控制台 Network 面板查看 WS 帧与关闭码。

### 8.3 CSP 违规

**症状**：前端样式/脚本不加载，浏览器控制台报 CSP 违规。
**原因**：CSP 策略过严，未包含所需 `style-src` / `script-src` 来源。
**解决**：

```bash
# 查看累计 CSP 违规
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/csp-violations | jq .

# 调整 CSP 配置（config/settings.py）
# 开发环境可临时禁用：MAOP_CSP_DISABLED=1
```

---

## 9. Enterprise Edition Issues

### 9.1 License 校验失败

**症状**：启动日志出现 `License invalid`，自动降级为 Personal 版，企业端点 404。
**原因**：license key 缺失/过期/签名不匹配、系统时钟偏差。
**解决**：

```bash
# 检查 license
export MAOP_LICENSE_KEY=$(cat data/license.key)
maop validate

# 查看 edition 与 feature flag
curl -s http://127.0.0.1:9079/api/info/edition | jq .

# 重新签发 license（参考 docs/enterprise/license-issuance-guide.md）
python scripts/generate_license.py --customer "..." --edition enterprise
```

### 9.2 多租户资源隔离失败

**症状**：租户 A 能看到租户 B 的数据。
**原因**：部分查询未带 `tenant_id` 过滤、RBAC 配置错误、共享数据库未做行级隔离。
**解决**：

- 排查所有数据库查询，确保带 `tenant_id` 过滤。
- 检查 RBAC 配置：`GET /api/rbac/grants`。
- 升级到 PostgreSQL 后端：`export MAOP_DB_BACKEND=postgres`，利用 schema 或 RLS 实现强隔离。

### 9.3 SSO 登录失败

**症状**：`/api/sso/authorize` 重定向失败或 IdP 报错。
**原因**：SAML IdP metadata URL 错误、`config/sso.yaml` 配置缺失、ACS URL 未在 IdP 注册。
**解决**：

- 检查 SAML IdP metadata URL 可达性。
- 检查 `config/sso.yaml` 中的 `entity_id`、`acs_url`、`idp_metadata_url`。
- 查看 `/api/sso/config` 返回值是否与 IdP 侧一致。

### 9.4 n8n 集成不可用

**症状**：`/api/n8n/*` 返回 404。
**原因**：未启用企业版 license、`MAOP_N8N_BASE_URL` 未配置、n8n 服务不可达。
**解决**：

```bash
# 检查 FeatureFlag
curl -s http://127.0.0.1:9079/api/info/edition | jq .features

# 检查 n8n 连接
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/n8n/health | jq .

# 配置 n8n base URL
export MAOP_N8N_BASE_URL=http://n8n:5678
```

---

## 10. Database Migration Issues

### 10.1 Alembic 迁移失败

**症状**：`alembic upgrade head` 报错，常见 `Target database is not up to date`。
**原因**：迁移版本冲突、数据库被手动修改、迁移脚本顺序错乱。
**解决**：

```bash
# 查看当前版本
alembic current

# 查看迁移历史
alembic history

# 回滚到上一版本
alembic downgrade -1

# 强制标记为已迁移（慎用，仅在确认数据库结构一致时使用）
alembic stamp <revision>
```

### 10.2 PostgreSQL 连接失败

**症状**：`OperationalError: could not connect to server`。
**原因**：PG 服务未启动、`MAOP_PG_DSN` 配置错误、防火墙拦截。
**解决**：

```bash
# 检查连接
psql -h $PG_HOST -U $PG_USER -d $PG_DB -c "SELECT 1;"

# 检查环境变量
echo $MAOP_DB_BACKEND   # 必须 = postgres
echo $MAOP_PG_DSN       # 形如 postgresql://user:pass@host:5432/maop

# 测试网络连通性
nc -zv $PG_HOST 5432
```

---

## 11. Log Analysis

### 11.1 日志结构

MAOP 使用结构化 JSON 日志，包含 `trace_id` 用于全链路追踪：

```json
{"ts": "2026-07-26T08:12:34.567Z", "level": "INFO", "logger": "MAOP.loop", "msg": "Plan generated", "trace_id": "abc123", "module": "maop_loop", "func": "run", "line": 254}
```

### 11.2 常见日志查询

```bash
# 查看所有 ERROR 日志
grep '"level":"ERROR"' logs/maop.jsonl | jq .

# 按 trace_id 过滤全链路
grep '"trace_id":"abc123"' logs/maop.jsonl | jq .

# 统计 ERROR 数量
grep '"level":"ERROR"' logs/maop.jsonl | wc -l

# 查看特定 logger 输出
grep '"logger":"MAOP.loop"' logs/maop.jsonl | jq .
```

### 11.3 日志脱敏

MAOP 4.3.0+ 自动脱敏以下密钥格式，原始值不会出现在日志中：

| 原始模式 | 脱敏后 |
|---------|--------|
| `sk-[a-zA-Z0-9]{20,}` | `[REDACTED:openai_key]` |
| `AKIA[0-9A-Z]{16}` | `[REDACTED:aws_key]` |
| `api_key=xxx` | `[REDACTED:api_key]` |
| `password=xxx` | `[REDACTED:password]` |
| `secret=xxx` | `[REDACTED:secret]` |
| `Bearer xxx` | `[REDACTED:bearer_token]` |

> 若日志中出现明文密钥，说明脱敏规则未覆盖该格式，请通过 GitHub Issue 反馈。

---

## 12. Common Error Codes

| HTTP | Code | 含义 | 解决方案 |
|------|------|------|---------|
| 400 | INVALID_REQUEST | 请求参数错误 | 检查请求体 JSON 格式与字段 |
| 401 | UNAUTHORIZED | 未认证或 token 失效 | 重新登录获取 JWT |
| 403 | FORBIDDEN | 权限不足 | 需要 admin 角色 |
| 404 | NOT_FOUND | 资源不存在或 Personal 版访问 Enterprise 端点 | 检查 URL 或升级企业版 |
| 409 | CONFLICT | 资源冲突（如重复创建） | 检查资源是否已存在 |
| 422 | VALIDATION_ERROR | Pydantic 校验失败 | 检查请求体字段类型 |
| 429 | RATE_LIMITED | 触发限流 | 调整 `MAOP_RATE_LIMIT` |
| 500 | INTERNAL_ERROR | 服务器内部错误 | 查看日志排查 |
| 502 | BAD_GATEWAY | 上游 LLM provider 错误 | 检查 provider 健康 |
| 503 | SERVICE_UNAVAILABLE | 熔断器开启或维护模式 | 等待熔断恢复 |

### 状态码对照（内部状态）

| 状态 | ✅ 健康 | ❌ 异常 |
|------|---------|---------|
| Agent | `healthy: true` | `healthy: false` |
| Provider | `provider.status: ok` | `provider.status: error` |
| CircuitBreaker | `state: closed` | `state: open` |
| Database | `conn: ok` | `conn: error` |

---

## 13. Getting Support

若以上手册无法解决问题，可通过以下渠道获取支持：

- **GitHub Issues**: https://github.com/Levango7/MAOP/issues
- **架构决策记录（ADR）**: `docs/adr/`，重点参考 004-security-hardening、012-routing-refactor、016-dual-edition-architecture。
- **综合审计报告**: `docs/archive/audits/comprehensive-audit-report.md`
- **企业版 license 签发**: `docs/enterprise/license-issuance-guide.md`

### 提交 Issue 时请附带以下信息

```bash
# 1. MAOP 版本
maop --version

# 2. 诊断输出
maop health > diagnose.txt 2>&1
maop status >> diagnose.txt 2>&1
maop validate >> diagnose.txt 2>&1

# 3. 系统诊断快照（admin）
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9079/api/system/diagnostics > diagnostics.json

# 4. 相关日志（脱敏后，截取前后 100 行）
grep '"trace_id":"<相关trace_id>"' logs/maop.jsonl | head -200 > relevant.log
```

附上复现步骤、期望行为与实际行为，将极大加快问题定位速度。
