# ADR-010: Batch Bugfix — Critical/High/Medium Priority

## Status
**Accepted** (2026-07-15)

Date: 2026-07-12

## Context

Full project review identified 3 Critical bugs, 5 High priority issues, and 6 Medium issues.
This ADR documents the batch fix applied to address them.

## Decision

### P0 Critical (all fixed)
- **BUG-1**: maop.ps1 path `src\src\maop-loop.ps1` → `src\maop-loop.ps1`
- **BUG-2**: Invoke-CmdDriver `exit_code = 0` → `try { $p.ExitCode } catch { 0 }`
- **BUG-3**: maop-loop `$routingKey = "codegen"` → `""` + 5 fallback `codegen` → `chat`

### P1 High (all fixed)
- **H-1**: memory.ps1 evolve trigger sync → async (Start-Job)
- **H-2**: Test-AgentAlive stub → fast CLI check via Get-Command
- **H-3**: Dashboard watchdog dedup via Get-Job check

### P2 Medium (fixed)
- **M-1**: Removed dead `per_agent` from rules.yaml
- **M-1b**: All `codegen` fallbacks in maop-loop replaced with `chat`
- **M-6**: dynamic-router YAML parser → Python bridge
- **H-4**: DAG condition branch skip rewritten (reachability analysis)
- **M-2**: Dashboard token auth (env: MAOP_DASH_TOKEN)
- **M-5**: dag-engine agent_slot → Python bridge

## Consequences

- P0 bugs blocked basic functionality (run command, circuit breaker, routing)
- H-1 improves latency by 2-5s per agent execution
- H-2 prevents wasted timeouts on unavailable agents
- H-3 prevents background Job accumulation
- H-4 makes DAG conditional execution actually work
- M-2 adds optional auth (disabled by default, enabled via env var)
- M-5/M-6 eliminate fragile YAML regex parsing in favor of Python bridge

## Alternatives Considered

- For H-4: Could have patched the existing BFS logic, but a clean rewrite was clearer
- For M-2: Could have added IP whitelist, but token is simpler and more portable
- For M-4 (evolve YAML write): Deferred — line-level replacement is correct for writes

## Items Deferred

- H-5 (PS/Python dashboard dual-track): needs architecture decision
- M-3 (ConvertFrom-Json -AsHashtable): low risk, gradual migration
- M-4 (evolve YAML write via bridge): write logic is correct, read already uses JSON
- L-1 (unified error-schema): risks breaking changes
- L-2 (test coverage for memory/dag-engine): separate effort
- L-4 (Python layer strategy): needs roadmap decision