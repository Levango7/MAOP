# P2/P3 文档一致性审核报告

> 审核时间：2026-08-26
> 审核范围：MAOP 文档（README、CHANGELOG、ROADMAP、api-reference、database-schema、ADR 等）
> 审核人：DocsAuditAgent（Task 491）

## 1. 审核概览

| 维度 | 发现数 |
|------|--------|
| 版本号一致性 | 0（全部一致 5.1.0） |
| 覆盖率宣称 vs 实际 | 0（一致：82% / FLOOR=80） |
| schema 文档表数 | 1（宣称 117 vs 实际 101） |
| API 文档端点不一致 | 15+ |
| docs 冗余/索引问题 | 3 |
| ADR 状态不一致 | 3 |
| 链接有效性 | 0 |
| 拼写/格式 | 2 |

**合计：P2 问题 10 个，P3 问题 7 个。**

## 2. P2 问题清单

### P2-Doc-01：database-schema.md 宣称 117 张表 vs 实际 101 张
- **严重度**：P2
- **位置**：`docs/database-schema.md:4`
- **问题描述**：宣称"代码库实际含 117 张 distinct 表"，实际 grep CREATE TABLE 仅 101 张。
- **修复建议**：更新为 101 张。

### P2-Doc-02：database-schema.md 中 32 个模块路径过时
- **严重度**：P2
- **位置**：`docs/database-schema.md` 全文
- **问题描述**：所有模块路径引用的是 v4.5.0 重构前的旧路径（如 `core/auth.py` → 实际 `core/security/auth.py`）。
- **修复建议**：批量更新所有模块路径为当前实际路径。

### P2-Doc-03：docs/README.md 宣称 346 个端点 vs 实际 445+
- **严重度**：P2
- **位置**：`docs/README.md:18`
- **问题描述**：宣称"346 个端点"，实际有 445+ 个路由定义。
- **修复建议**：更新为实际端点数。

### P2-Doc-04：api-reference.md 中 chat.py 端点完全不一致
- **严重度**：P2
- **位置**：`docs/api-reference.md` chat 端点部分
- **问题描述**：文档列出 POST /api/chat/start、POST /api/chat/message 等，实际是 POST /api/chat、POST /api/chat/stream 等，完全不匹配。
- **修复建议**：根据实际代码重新生成 chat 端点文档。

### P2-Doc-05：api-reference.md 中多处 HTTP 方法不一致
- **严重度**：P2
- **位置**：`docs/api-reference.md` 多处
- **问题描述**：至少 8 处 HTTP 方法不一致：/api/model/quota/status (文档 POST 实际 GET)、/api/model/select (文档 POST 实际 GET)、/api/model/health/check (文档 GET 实际 POST)、/api/control/provider-health (文档 GET 实际 POST)、/api/cost/pricing/{model} (文档 GET 实际 PUT)、/api/permission/check (文档 POST 实际 GET)、/api/memory/search (文档 POST 实际 GET)、/api/audit/filter (文档 POST 实际 GET)。
- **修复建议**：逐一核对并修正 HTTP 方法。

### P2-Doc-06：api-reference.md 中多处端点路径不一致
- **严重度**：P2
- **位置**：`docs/api-reference.md` 多处
- **问题描述**：hook 端点 prefix /api/hooks vs 实际 /api/hook；protocol 端点 prefix /api/protocols vs 实际 /api/protocol；mcp connect/disconnect 路径不一致；permission/rules DELETE 路径不一致；subagent/transcript 路径不一致；memory/trace 路径不一致；n8n/webhook 路径不一致。
- **修复建议**：逐一核对并修正端点路径。

### P2-Doc-07：api-reference.md 中 /api/batch 标记 deprecated 但实际已移除
- **严重度**：P2
- **位置**：`docs/api-reference.md:138`
- **问题描述**：文档标注 deprecated，实际代码中已完全移除。
- **修复建议**：删除该端点文档条目。

### P2-Doc-08：ADR-005 状态不一致
- **严重度**：P2
- **位置**：`docs/adr/README.md` vs `docs/adr/005-*.md`
- **问题描述**：README 中标 Accepted，实际文件中标 Superseded by ADR-009。
- **修复建议**：更新 README 中 ADR-005 状态为 Superseded。

### P2-Doc-09：ADR-016 状态不一致
- **严重度**：P2
- **位置**：`docs/adr/README.md` vs `docs/adr/016-*.md`
- **问题描述**：README 中标 Accepted，实际文件中标 Active。
- **修复建议**：更新 README 中 ADR-016 状态为 Active。

### P2-Doc-10：archive/README.md 索引中 prd/hld 路径错误
- **严重度**：P2
- **位置**：`docs/archive/README.md`
- **问题描述**：索引说 prd-three-phase-roadmap.md 和 hld-three-phase-roadmap.md 在 plans/ 子目录，实际在 docs/ 根目录。
- **修复建议**：更新索引路径。

## 3. P3 问题清单

### P3-Doc-01：ADR-010 缺少 Status 节
- **严重度**：P3
- **位置**：`docs/adr/010-*.md`
- **修复建议**：添加 `## Status` 节。

### P3-Doc-02：archive/README.md 说 ADR 001-016，实际有 017
- **严重度**：P3
- **位置**：`docs/archive/README.md:8`
- **修复建议**：更新为 ADR 001-017。

### P3-Doc-03：api-reference.md 有 UTF-8 BOM
- **严重度**：P3
- **位置**：`docs/api-reference.md:1`
- **修复建议**：移除 BOM。

### P3-Doc-04：design-system-legacy.md 未在 archive 索引中列出
- **严重度**：P3
- **位置**：`docs/archive/README.md`
- **修复建议**：在索引中添加该文件。

### P3-Doc-05：api-reference.md 中多个端点未列出
- **严重度**：P3
- **位置**：`docs/api-reference.md`
- **问题描述**：audit.py 有 16 个端点但文档只列 2 个；sso.py 有 17 个但文档只列 5 个；agents 子目录有 24 个但文档只列 12 个；stream.py 有 5 个但文档只列 3 个。
- **修复建议**：补充缺失的端点文档。

### P3-Doc-06：docker-compose.yml 与 prod.yml 中 prometheus 版本不一致
- **严重度**：P3
- **位置**：`docker-compose.yml:271` (v2.51.0) vs `docker-compose.prod.yml:500` (v2.53.0)
- **修复建议**：统一版本。

### P3-Doc-07：Dockerfile 使用 requirements.txt 而 CI 使用 requirements.lock
- **严重度**：P3
- **位置**：`Dockerfile:25`
- **修复建议**：统一使用 requirements.lock。