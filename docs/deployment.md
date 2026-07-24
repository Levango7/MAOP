# MAOP Deployment Guide

## Quick Start (Docker Compose)

```bash
# Copy environment file
cp .env.example .env
# Edit .env and set MAOP_JWT_SECRET

# Start production stack
docker compose -f docker-compose.prod.yml up -d

# Check health
curl http://localhost:9079/api/health
```

## Kubernetes Deployment

### Health Checks

MAOP provides two endpoints for K8s probes:

```yaml
livenessProbe:
  httpGet:
    path: /api/health
    port: 9079
  initialDelaySeconds: 15
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /api/health
    port: 9079
  initialDelaySeconds: 5
  periodSeconds: 10
```

### Resource Limits

```yaml
resources:
  limits:
    cpu: "2000m"
    memory: "2Gi"
  requests:
    cpu: "500m"
    memory: "512Mi"
```

## Manual Installation (Linux/macOS)

```bash
# Clone and install
git clone <repo-url> maop
cd maop
pip install -e py/

# Run migrations
python -c "from maop.core.migrations import run_migrations; run_migrations('.')"

# Start
./start.sh
```

## TLS Configuration

For production TLS:

```bash
# Generate self-signed certificate (development)
MAOP_TLS=1 ./start.sh

# For production, use Let's Encrypt with Nginx:
# 1. Set MAOP_TLS=0 (let Nginx handle TLS)
# 2. Configure Nginx with certbot
# 3. Use docker-compose.prod.yml which includes Nginx
```