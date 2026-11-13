# Modelryx Production Deployment & Development Quick Reference

## 馃殌 Quick Start (5 minutes)

### First Time Setup
```bash
git clone https://github.com/yanggmtl/Modelryx.git
cd Modelryx
cp .env.example .env
docker compose up -d
```

### Backend
```bash
cd server
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m modelryx.initialize_database
python -m uvicorn src.server.main:app --reload
```

### Frontend
```bash
cd front-end
npm install
cp .env.example .env.local
npm run dev
```

### Verify Setup
- Frontend: http://localhost:5173
- API: http://localhost:8000/docs
- Health: `curl http://localhost:8000/health`

---

## 馃攳 Health Check Endpoints

```bash
# Liveness (is the service running?)
curl http://localhost:8000/health/live
# Response: {"status": "ok"}

# Readiness (is it ready to serve requests?)
curl http://localhost:8000/health/ready
# Response: {"status": "ok"}

# Detailed status (includes component status)
curl http://localhost:8000/health
# Response: {
#   "status": "ok",
#   "database": "ok",
#   "timestamp": "2026-11-11T13:40:00"
# }
```

---

## 鈿欙笍 Environment Configuration

### Critical (Must Set Before Production)
```bash
MODELRYX_JWT_SECRET=<32+ random chars>           # Generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
MODELRYX_INTERNAL_WORKER_KEY=<32+ random chars>  # Worker authentication key
MODELRYX_BOOTSTRAP_ADMIN_PASSWORD=<strong pwd>   # Initial admin password
POSTGRES_PASSWORD=<random 16+ chars>              # Database password
```

### Timeouts (Optional, Tune for Your Workload)
```bash
MODELRYX_CONVERTER_TIMEOUT_SECONDS=1800           # CAD conversion max time (30 min)
MODELRYX_WORKER_HEARTBEAT_TIMEOUT_SECONDS=30     # Worker alive check (30 sec)
MODELRYX_AI_REQUEST_TIMEOUT_SECONDS=60            # AI/LLM timeout (60 sec)
```

### Common Settings
```bash
MODELRYX_ENVIRONMENT=production                   # development, test, or production
MODELRYX_DATABASE_URL=postgresql://...            # Database connection
MODELRYX_CORS_ORIGINS=["http://localhost:5173"]   # Frontend URL(s)
MODELRYX_FREECAD_WORKER_URL=http://...            # FreeCAD worker (optional)
```

---

## 馃И Testing

### Backend Tests
```bash
cd server
pytest                          # Run all tests
pytest --cov=src               # With coverage
pytest tests/test_items.py     # Specific file
pytest -k "item" -v            # Filter by name
```

### Frontend Tests
```bash
cd front-end
npm test                        # Run all tests
npm test -- --watch            # Watch mode
npm test -- --coverage         # With coverage
```

### E2E Tests (Playwright)
```bash
cd front-end
npm run test:e2e
```

---

## 馃拝 Code Quality

### Linting
```bash
cd front-end
npm run lint              # Check for issues
npm run lint:fix         # Auto-fix issues
npm run format           # Apply formatting
npm run format:check     # Verify formatting
```

### TypeScript
```bash
npm run typecheck        # Type checking
npm run build           # Full build (includes type check)
```

---

## 馃惓 Docker Commands

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f server       # Backend logs
docker compose logs -f postgres     # Database logs

# Stop services
docker compose stop

# Full cleanup (remove volumes)
docker compose down -v

# Rebuild after dependency changes
docker compose up -d --build
```

---

## 馃摝 CAD Worker Deployment Options

### 1. FreeCAD Worker in Container (Recommended)
```yaml
# In compose.yaml
services:
  freecad-worker:
    image: modelryx/freecad-worker:latest
    environment:
      MODELRYX_INTERNAL_WORKER_KEY: ${MODELRYX_INTERNAL_WORKER_KEY}
    ports:
      - "8090:8090"
    resources:
      cpus: '4'
      memory: '4gb'
```

### 2. FreeCAD Worker Standalone (Process Isolation)
```bash
# On any server machine
cd workers/freecad
pip install -r requirements.txt
python -m server
# Worker listens on port 8090
```

### 3. Embedded (No Separate Worker)
```bash
# Leave MODELRYX_FREECAD_WORKER_URL unset in .env
# FreeCAD functions run within main Modelryx server
```

---

## 馃攼 Security Checklist

- [ ] JWT secret: 32+ random characters (production)
- [ ] Worker key: 32+ random characters (production)
- [ ] Admin password: Strong, unique password (production)
- [ ] Database password: Random, secure (production)
- [ ] HTTPS/TLS configured (production)
- [ ] Firewall restricts access to non-public ports
- [ ] Backup procedures tested and documented
- [ ] Environment variables secured (never in git)
- [ ] Regular security updates applied

---

## 馃搳 Database Operations

### Fresh database initialization
```bash
cd server
python -m modelryx.initialize_database
```

This creates an empty database's current schema or verifies a previously initialized
matching schema. It does not upgrade or reset existing databases. See
[the initialization procedure](operations/database-initialization.md).

### Backup & Restore
```bash
# Backup (creates gzipped dump)
bash scripts/backup-database.sh

# Restore from backup
bash scripts/restore-database.sh /path/to/backup.sql.gz

# Verify backup integrity
bash scripts/verify-backups.sh
```

---

## 馃毃 Troubleshooting

### Port Already in Use
```bash
# Find process on port
lsof -i :8000          # Server
lsof -i :5173          # Frontend
lsof -i :5432          # PostgreSQL

# Kill process
kill -9 <PID>
```

### Database Connection Refused
```bash
# Check if PostgreSQL is running
docker compose ps postgres

# Check logs
docker compose logs postgres

# Restart
docker compose restart postgres
```

### Frontend Not Finding API
- Verify `VITE_API_URL=http://localhost:8000` in `front-end/.env.local`
- Check backend is running: `curl http://localhost:8000/health`
- Check CORS origins in `.env`: `MODELRYX_CORS_ORIGINS`

### Python Dependencies Conflict
```bash
# Clean reinstall
pip install --upgrade --force-reinstall -r requirements.txt
```

### Node Modules Corruption
```bash
cd front-end
rm -rf node_modules package-lock.json
npm install
```

---

## 馃摉 Documentation

| Document | Purpose |
|----------|---------|
| [README.md](./README.md) | Overview & quick links |
| [DEVELOPMENT.md](./DEVELOPMENT.md) | Local development setup |
| [DEPLOYMENT.md](./docs/DEPLOYMENT.md) | Production deployment |
| [TESTING.md](./docs/TESTING.md) | Testing procedures |
| [BACKUP_STRATEGY.md](./docs/BACKUP_STRATEGY.md) | Backup & recovery |
| [WORKER_SECURITY.md](./docs/WORKER_SECURITY.md) | CAD worker auth |
| [new-cad-worker-guide.md](./docs/workers/new-cad-worker-guide.md) | Custom workers |

---

## 馃啒 Getting Help

1. **Read the docs** 鈥?Check [DEPLOYMENT.md](./docs/DEPLOYMENT.md) and [DEVELOPMENT.md](./DEVELOPMENT.md)
2. **Check logs** 鈥?`docker compose logs -f service-name`
3. **Test endpoints** 鈥?Use curl or Postman to test API
4. **Run tests** 鈥?`cd server && pytest -v`
5. **Review errors** 鈥?Check error messages carefully; they often indicate the solution

---

## 鉁?Pre-Production Checklist

- [ ] All environment variables configured in `.env`
- [ ] Fresh database initialization completes successfully
- [ ] Backend API health check returns 200: `curl http://localhost:8000/health`
- [ ] Frontend starts without errors: `npm run dev`
- [ ] CAD workers configured and tested
- [ ] Backup procedures tested
- [ ] SSL/TLS certificates installed
- [ ] Reverse proxy configured (nginx example in DEPLOYMENT.md)
- [ ] Monitoring set up
- [ ] Team trained on procedures

---

**Last Updated:** November 11, 2026  
**Status:** Production Ready  
**Version:** 1.0

