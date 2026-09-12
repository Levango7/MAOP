"""MAOP Dashboard API Routers.

Modular route definitions split by domain:
  - data:    query/read endpoints (report, agents, logs, skills, mcp, etc.)
  - control: action endpoints (run, stop, pause, validate, maintain)
  - model:   model management (registry, list, switch, budget, policies)
  - evolve:  self-evolution controls (status, analyze, suggestions)
  - memory:  memory + neural mechanisms (deep, search, trace, attention)
  - system:  framework status, audit, agent config, overview, workflows
  - feedback: user feedback / ratings (submit, list, summary, export, CRUD)
  - analysis: deep data analysis report engine (agent efficiency, task trends,
              resource utilization, cost breakdown, performance bottlenecks,
              KPI summary)
  - agent_versions: Agent 版本管理 / 灰度发布 (版本 CRUD, 激活, 回滚,
              灰度配置, 指标查询; 写操作 admin, 读操作已认证, IDOR 防护)
"""
