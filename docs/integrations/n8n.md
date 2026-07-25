# n8n Integration (Enterprise Only)

n8n is a workflow automation tool that provides MAOP with external trigger
and SaaS integration capabilities. This integration is **Enterprise only** —
Personal edition cannot use n8n integration.

## Architecture

n8n is positioned as the "external trigger + SaaS integration layer":

```
┌─────────────────────────────────────────────────────────────┐
│  External World (SaaS / Events)                              │
│  GitHub / Slack / Jira / Email / Cron / DB changes           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  n8n Layer (Deterministic workflow orchestration)            │
│  - Listens to 400+ trigger sources                           │
│  - Data extraction / format conversion / routing             │
│  - Calls MAOP at decision points                             │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP API
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  MAOP Layer (Intelligent orchestration + agent execution)    │
│  - LLM reasoning / agent scheduling / DAG orchestration      │
│  - Circuit breaker / failover / priority / SLA               │
│  - Returns structured decision results                       │
└─────────────────────────────────────────────────────────────┘
```

## Setup

### 1. Start n8n (Docker Compose)

```bash
# Set required environment variables
export N8N_PASSWORD=your-secure-password

# Start with n8n profile
docker compose --profile n8n up -d
```

### 2. Configure MAOP

n8n integration is enabled by default in Enterprise edition. Configure via environment variables:

```bash
N8N_BASE_URL=http://localhost:5678
N8N_API_KEY=your-n8n-api-key  # Get from n8n UI: Settings > API
```

### 3. Verify connectivity

```bash
curl http://localhost:9079/api/n8n/health
# {"n8n_reachable": true, "base_url": "http://localhost:5678"}
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/n8n/webhook` | Receive webhook from n8n (no auth) |
| GET | `/api/n8n/workflows` | List n8n workflows (admin) |
| POST | `/api/n8n/workflows/{id}/trigger` | Trigger a workflow (admin) |
| GET | `/api/n8n/executions/{id}` | Get execution status (admin) |
| GET | `/api/n8n/health` | Check n8n connectivity (admin) |

## Integration Patterns

### Pattern A: n8n as Inbound Trigger (Recommended)

n8n listens to external events and calls MAOP for intelligent processing:

1. **n8n workflow**: GitHub PR opened → HTTP Request node → MAOP `/api/n8n/webhook`
2. **MAOP processes**: Webhook handler suggests agent + capability
3. **n8n continues**: Uses MAOP's response in subsequent nodes

### Pattern B: MAOP as Intelligent Node

n8n workflows call MAOP when LLM decision is needed:

1. **n8n workflow**: Customer feedback → Switch node
2. **Switch calls MAOP**: HTTP Request to `/api/delegate` with `agent=claude`
3. **MAOP returns**: Classification + suggested action
4. **n8n routes**: Based on MAOP's classification

### Pattern C: MAOP Triggers n8n

MAOP agents can trigger n8n workflows during execution:

```python
from maop.enterprise.n8n import N8nClient

with N8nClient() as client:
    execution = client.trigger_workflow(
        "workflow-123",
        data={"pr_url": "https://github.com/..."},
    )
```

## Feature Flag

n8n integration is gated behind `FeatureFlag.N8N_INTEGRATION`:

```python
from maop.enterprise.n8n import require_n8n_feature

# Raises FeatureNotAvailable in Personal edition
require_n8n_feature()
```

To disable in Enterprise edition:

```python
from maop.config.edition import set_feature_override, FeatureFlag
set_feature_override(FeatureFlag.N8N_INTEGRATION, False)
```