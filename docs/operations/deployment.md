# Production Deployment Guide

This guide covers deploying Modelryx to production environments.

## System Requirements

### Hardware
- **CPU:** 4+ cores (8+ cores recommended)
- **RAM:** 16 GB minimum (32 GB recommended)
- **Storage:** 100 GB SSD (varies by data volume)
- **Network:** Dedicated bandwidth, stable connection

### Software
- **Docker** and **Docker Compose** v24.0+
- **Operating System:** Linux (Ubuntu 22.04 LTS recommended) or compatible
- **PostgreSQL** v14+ (can be managed by Docker Compose)
- **Object Storage:** persistent local `object-data` volume

### Ports Required
- **80** — HTTP (reverse proxy/load balancer)
- **443** — HTTPS (reverse proxy/load balancer)
- **8000** — Modelryx server (internal)
- **8090** — FreeCAD worker (if standalone mode)
- **5432** — PostgreSQL (internal)

## CAD Worker Resource Requirements

Modelryx supports multiple CAD workers. Resource requirements vary by worker type and deployment method.

### FreeCAD Worker

**Deployment Methods:**
- **Containerized** (Docker) — Recommended for cloud/Kubernetes deployments
- **Standalone Server** — Direct service on Linux/Windows server
- **Embedded** — Within main server container for smaller deployments

**Resource Requirements:**

| Deployment | CPU | RAM | Storage | Notes |
|-----------|-----|-----|---------|-------|
| Container (Small) | 2 cores | 2 GB | 5 GB | Single model processing, dev/test |
| Container (Medium) | 4 cores | 4 GB | 10 GB | 2-3 concurrent operations, production |
| Container (Large) | 8+ cores | 8 GB | 20 GB | High-concurrency production workloads |
| Standalone (Linux) | 4+ cores | 4 GB | 10 GB | Process isolation, direct integration |
| Embedded | Shared | Shared | Shared | Small deployments, minimal overhead |

**Network:**
- Minimum **50 Mbps** bandwidth for file transfers (CAD models)
- For distributed deployment, ensure low-latency network (< 50ms) between server and worker

### STEP File Worker

No additional resources beyond the server; runs embedded within Modelryx server.

**Resource Requirements:**
- CPU: Shared with server
- RAM: ~100-500 MB per concurrent operation
- Storage: ~1 GB for cached STEP conversions

### Custom Workers

Resource requirements depend on the CAD application:
- **Memory-intensive** tools (CAD engines) may require 2-4 GB per instance
- **CPU-intensive** operations (meshing, rendering) benefit from 4+ cores
- Plan for peak concurrent operations + 50% overhead

**Deployment Flexibility:**
- Workers can run in containers (Docker/Kubernetes) or directly on the server machine
- No strict deployment method requirement — choose based on your infrastructure
- See [docs/workers/new-cad-worker-guide.md](./workers/new-cad-worker-guide.md) for custom worker deployment

## Pre-Deployment Checklist

- [ ] Server hardware provisioned and tested
- [ ] PostgreSQL database initialized and backed up
- [ ] Persistent object-data volume configured
- [ ] SSL/TLS certificates obtained
- [ ] Firewall rules configured
- [ ] CAD workers sized for expected workload
- [ ] Monitoring and logging setup planned
- [ ] Backup and recovery procedures documented
- [ ] Team trained on deployment and recovery

## Docker Compose Setup

### Production Compose configuration

Use the repository's [compose.yaml](../../compose.yaml), which includes database
database initialization, processing workers, and persistent `postgres-data` and `object-data`
volumes. The API and workers share `/var/lib/modelryx/objects` for file storage.
Deploy the frontend separately as described in the [README](../../README.md).

### Environment Configuration (.env)

Create a `.env` file for production secrets and configuration:

```bash
# Database
POSTGRES_PASSWORD=<strong-random-password>

# Frontend CORS
CORS_ORIGINS=https://yourdomain.com

# FreeCAD Worker
# Leave empty for embedded mode, or set to http://freecad-host:8090
FREECAD_WORKER_URL=

# Optional: External database (instead of Docker Postgres)
# MODELRYX_DATABASE_URL=postgresql://user:pass@external-db:5432/modelryx_db

```

**Security:** Never commit `.env` to Git. Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.).

## Database Setup

### PostgreSQL with pgvector

PostgreSQL must have the pgvector extension for AI embeddings:

```sql
-- Run as superuser once after initialization
CREATE EXTENSION IF NOT EXISTS vector;
```

### Initialize the fresh database

Compose runs the one-shot `initialize-db` service before the API and workers.
To initialize explicitly or verify the same installed schema:

```bash
docker compose run --rm initialize-db
```

This release requires an empty database for a new installation. Initialization
never upgrades or resets an existing database. See
[database-initialization.md](database-initialization.md).

### Backup Strategy

Back up PostgreSQL **and** the `object-data` volume used by the API and processing
workers. Both volumes are required for recovery.

Use the [backup and restore procedure](backup-strategy.md) during a maintenance
window to produce a consistent database/object snapshot.

## HTTPS / TLS Configuration

### Option 1: Let's Encrypt (Automated)

```bash
# Install Certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot certonly --standalone -d yourdomain.com -d www.yourdomain.com

# Certificates located at: /etc/letsencrypt/live/yourdomain.com/
```

Update `nginx.conf` to use certificate:

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    location / {
        proxy_pass http://server:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

### Option 2: Self-Signed Certificate (Development Only)

```bash
# Generate self-signed certificate (valid 365 days)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
```

## Deployment Steps

### 1. Prepare Server

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker and Docker Compose
sudo apt-get install -y docker.io docker-compose

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Clone Repository

```bash
git clone https://github.com/yanggmtl/Modelryx.git /opt/modelryx
cd /opt/modelryx
```

### 3. Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit with production values
nano .env
```

### 4. Build and Start Services

```bash
# Build images (or pull pre-built)
docker compose build

# Start services
docker compose up -d

# Monitor startup
docker compose logs -f
```

### 5. Initialize Database

```bash
# Verify database initialization
docker compose run --rm initialize-db

# Verify health
curl http://localhost:8000/health
```

### 6. Configure Reverse Proxy

```bash
# Copy nginx configuration
cp nginx.conf /etc/nginx/sites-available/modelryx

# Enable site
sudo ln -s /etc/nginx/sites-available/modelryx /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

### 7. Verify Deployment

```bash
# Check services
docker compose ps

# Test API
curl https://yourdomain.com/api/health

# Test database
docker compose exec postgres psql -U modelryx -d modelryx_db -c "SELECT version();"

# Test object storage
```

## Monitoring and Health Checks

### Health Check Endpoint

Modelryx provides a health check endpoint for monitoring:

```bash
curl http://localhost:8000/health
```

Response (200 OK):
```json
{
  "status": "healthy",
  "database": "connected",
  "object_store": "connected",
  "timestamp": "2026-11-11T13:40:00Z"
}
```

### Logging

View logs from services:

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f server

# Follow new logs only
docker compose logs --follow
```

### Monitoring Stack (Optional)

Add to `compose.yaml` for monitoring:

```yaml
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
```

## Updating Deployment

### Zero-Downtime Update

```bash
# 1. Pull latest code
git pull origin main

# 2. Rebuild images
docker compose build

# 3. Update services (one at a time)
docker compose up -d --no-deps --build server
docker compose up -d --no-deps --build nginx

# 4. Verify health
curl https://yourdomain.com/api/health

# 5. Check logs for errors
docker compose logs -f server
```

### Database schema compatibility

This fresh-install release has no historical upgrade or downgrade commands.
`docker compose run --rm initialize-db` creates an empty database or verifies a
matching initialized schema. It refuses incompatible schemas without changing them.
Do not use a different-schema release against an existing deployment without a
separately tested data-preserving upgrade process.

## Disaster Recovery

### Database Recovery

```bash
# Restore from backup
docker compose exec -T postgres psql -U modelryx modelryx_db < modelryx_YYYYMMDD_HHMMSS.sql
```

### Full System Recovery

```bash
# 1. Stop current services
docker compose down

# 2. Remove corrupted volumes (be careful!)
docker volume rm modelryx_postgres_data

# 3. Restore backup data
docker compose up -d postgres
docker compose exec -T postgres psql -U modelryx modelryx_db < backup.sql

# 4. Restart all services
docker compose up -d
```

## Performance Tuning

### PostgreSQL

Edit `compose.yaml` PostgreSQL environment:

```yaml
environment:
  POSTGRES_INITDB_ARGS: "-c shared_buffers=256MB -c max_connections=200"
```

### Increase connection limits
```

### Server

Set resource limits in `compose.yaml`:

```yaml
server:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
      reservations:
        cpus: '1'
        memory: 2G
```

## Troubleshooting

### Services Won't Start

```bash
# Check logs
docker compose logs

# Verify ports are available
netstat -tlnp | grep 8000

# Check disk space
df -h
```

### Database Connection Refused

```bash
# Verify PostgreSQL is running
docker compose ps postgres

# Check logs
docker compose logs postgres

# Restart service
docker compose restart postgres
```

### High CPU/Memory Usage

```bash
# Monitor resource usage
docker compose stats

# Check for stuck processes
docker compose top server

# Restart service
docker compose restart server
```



# Verify bucket exists

# Check logs
```

## Security Best Practices

1. **Change default passwords** — Set strong, unique credentials in `.env`
2. **Enable HTTPS** — Use Let's Encrypt or self-signed certificates
3. **Restrict network access** — Use firewall rules to limit port access
4. **Regular backups** — Automate daily database and file backups
5. **Monitor logs** — Review logs regularly for suspicious activity
6. **Update regularly** — Keep Docker images and OS packages current
7. **Use secrets manager** — Don't store credentials in `.env` file; use proper secret management
8. **Enable database authentication** — Use PostgreSQL user roles appropriately
9. **Rotate credentials** — Periodically rotate passwords and API keys
10. **Audit access** — Log and review access to sensitive operations

## Support and Documentation

- **Architecture:** See [docs/architecture/platform-architecture.md](../architecture/platform-architecture.md)
- **API Reference:** See [docs/API.md](./API.md)
- **Testing:** See [docs/TESTING.md](./TESTING.md)
- **Issues:** Report problems on GitHub Issues

---

For production support and guidance, contact the Modelryx team or community.
