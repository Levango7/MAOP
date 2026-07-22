"""MAOP Dashboard API Routers.

Modular route definitions split by domain:
  - data:    query/read endpoints (report, agents, logs, skills, mcp, etc.)
  - control: action endpoints (run, stop, pause, validate, maintain)
  - model:   model management (registry, list, switch, budget, policies)
  - evolve:  self-evolution controls (status, analyze, suggestions)
  - memory:  memory + neural mechanisms (deep, search, trace, attention)
  - system:  framework status, audit, agent config, overview, workflows
"""
