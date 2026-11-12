# Modelryx Development Setup Guide

This guide walks you through setting up your local development environment to run Modelryx.

## System Requirements

### Minimum Requirements
- **Docker & Docker Compose** ï¿?v24.0+ (for PostgreSQL, object storage, services)
- **Node.js** ï¿?v18.0+ (for React frontend)
- **Python** ï¿?v3.10+ (for server and workers)
- **Git** ï¿?v2.35+ (for version control)
- **RAM** ï¿?8 GB (16 GB recommended)
- **Disk** ï¿?10 GB free space

### Operating Systems
- **Linux** (Ubuntu 20.04+, Fedora, etc.)
- **macOS** (11+, native or with Docker Desktop)
- **Windows** (WSL2 + Docker Desktop recommended)

### Optional Tools
- **VS Code** with Python, TypeScript extensions
- **Postman** or similar (for API testing)
- **DBeaver** or pgAdmin (for database inspection)

## Quick Start (5-10 minutes)

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/Modelryx.git
cd Modelryx
```

### 2. Configure Environment
```bash
# Copy example environment file
cp .env.example .env

# Edit .env if needed (defaults are fine for local dev)
# nano .env  # or open in your editor
```

### 3. Start Services
```bash
# Start Docker Compose services (PostgreSQL, object storage, etc.)
docker compose up -d

# Wait for services to be ready (20-30 seconds)
docker compose logs -f
# Press Ctrl+C when you see "database ready" message
```

### 4. Start Backend Server
```bash
cd server

# Create Python virtual environment
python -m venv venv

# Activate virtual environment
# On Linux/macOS:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
python -m alembic upgrade head

# Start server (runs on port 8000)
python -m uvicorn src.server.main:app --reload
```

### 5. Start Frontend (in another terminal)
```bash
cd front-end

# Copy frontend environment template
cp .env.example .env.local

# Install dependencies
npm install

# Start dev server (runs on port 5173)
npm run dev
```

### 6. Verify Setup
- **Frontend:** Open http://localhost:5173 in your browser
- **API Docs:** Open http://localhost:8000/docs (interactive Swagger UI)
- **Health check:** 
  ```bash
  curl -s http://localhost:8000/health | jq
  ```

### Frontend Configuration

The `front-end/.env.local` file configures frontend behavior:

```bash
# API endpoint (must match backend server address)
VITE_API_URL=http://localhost:8000

# Feature flags (enable/disable features)
VITE_ENABLE_EXPERIMENTAL=false
VITE_ENABLE_AI_FEATURES=true
VITE_ENABLE_MONITORING=false

# Build optimization
VITE_OPTIMIZE_DEPS_INCLUDE=three,fflate
```

Copy from `front-end/.env.example` to get started.

## Detailed Setup

### Environment Configuration

The `.env` file controls all configuration. Key variables:

```bash
# Server
MODELRYX_SERVER_HOST=0.0.0.0
MODELRYX_SERVER_PORT=8000

# Database (PostgreSQL with pgvector extension)
MODELRYX_DATABASE_URL=postgresql://modelryx:modelryx@localhost:5432/modelryx_db
MODELRYX_DATABASE_ECHO=false

# Object Storage (MinIO S3-compatible storage)
MODELRYX_OBJECT_STORE_ENDPOINT=http://localhost:9000
MODELRYX_OBJECT_STORE_ACCESS_KEY=modelryx
MODELRYX_OBJECT_STORE_SECRET_KEY=modelryxsecret

# FreeCAD Worker (optional - set to empty to use embedded)
MODELRYX_FREECAD_WORKER_URL=http://localhost:8090

# AI Features
MODELRYX_ENABLE_AI=true

# CORS (add frontend URL for development)
MODELRYX_CORS_ORIGINS=http://localhost:5174,http://localhost:3000
```

For a production `.env`, see [deployment.md](./docs/operations/deployment.md).

### Database Setup

Database and migrations are managed automatically:

```bash
# Inspect current migrations
python -m alembic current

# Create new migration (after modifying models)
python -m alembic revision --autogenerate -m "add new table"

# View migration history
python -m alembic history

# Rollback last migration
python -m alembic downgrade -1
```

For database schema details, see [docs/database.md](./docs/operations/database.md).

### Running Services with Docker Compose

```bash
# Start all services in background
docker compose up -d

# View logs (live)
docker compose logs -f

# View logs for specific service
docker compose logs -f postgres

# Stop all services
docker compose down

# Remove volumes (resets database)
docker compose down -v

# Rebuild images (after dependency changes)
docker compose up -d --build
```

### Frontend Development

```bash
cd front-end

# Install dependencies
npm install

# Start dev server with hot reload (runs on port 5173)
npm run dev

# Type checking
npm run typecheck

# Linting - Check code quality
npm run lint

# Linting - Fix issues automatically
npm run lint:fix

# Code formatting
npm run format

# Check formatting without applying
npm run format:check

# Build for production
npm run build

# Preview production build
npm run preview
```

**Code Quality Standards:**
- ESLint enforces code quality (no unused variables, proper hook usage, React best practices)
- Prettier ensures consistent formatting (single quotes, trailing commas, 100-char line width)
- TypeScript validates type safety
- Run `npm run lint:fix` before committing to fix auto-fixable issues

The dev server includes hot reload ï¿?changes to React components are automatically reflected in the browser.

### Server Development

```bash
cd server

# Activate virtual environment first
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Start server with auto-reload
python -m uvicorn src.server.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
pytest

# Run with verbose output
pytest -vv

# Run specific test file
pytest tests/test_items.py

# Run tests with coverage
pytest --cov=src --cov-report=html

# Lint and format
black src/
ruff check src/ --fix
```

### CAD Workers Development

See [docs/workers/new-cad-worker-guide.md](./docs/workers/new-cad-worker-guide.md) for detailed guidance on creating custom workers.

To run the FreeCAD worker locally:

```bash
cd workers/freecad

# Install dependencies
pip install -r requirements.txt

# Start worker (runs on port 8090)
python -m server

# Test worker health
curl http://localhost:8090/health
```

## Testing

See [docs/testing.md](./docs/development/testing.md) for comprehensive testing guidance.

Quick test commands:

```bash
# Python backend tests
cd server && pytest

# Frontend tests
cd front-end && npm test

# E2E tests (requires running services)
cd front-end && npm run test:e2e
```

## Troubleshooting

### Port Already in Use

```bash
# Find process using port
lsof -i :8000  # Server
lsof -i :5174  # Frontend
lsof -i :5432  # PostgreSQL
lsof -i :9000  # MinIO

# Kill process
kill -9 <PID>
```

### Database Connection Refused

```bash
# Check PostgreSQL service
docker compose ps postgres

# Check if PostgreSQL is running
docker compose logs postgres

# Restart PostgreSQL
docker compose restart postgres

# Verify connection
psql postgresql://modelryx:modelryx@localhost:5432/modelryx_db
```

### Python Dependencies Issue

```bash
# Clear pip cache and reinstall
pip install --upgrade --force-reinstall -r requirements.txt

# Or create fresh virtual environment
rm -rf venv
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Node Modules Corruption

```bash
cd front-end

# Remove and reinstall
rm -rf node_modules package-lock.json
npm install

# Or clean cache
npm cache clean --force
npm install
```

### Docker Issues

```bash
# Check Docker status
docker ps  # running containers
docker logs <container-name>  # specific container logs

# Reset Docker (careful!)
docker compose down -v  # removes all data
docker compose up -d    # restart fresh
```

### Frontend Not Connecting to Backend

1. Verify server is running: `curl http://localhost:8000/health`
2. Check CORS configuration in `.env` includes frontend URL
3. Check browser console for CORS errors
4. Verify API calls use correct endpoint (usually `http://localhost:8000/api/...`)

### FreeCAD Worker Not Found

- If `MODELRYX_FREECAD_WORKER_URL` is empty, server uses embedded FreeCAD adapter
- If you want standalone worker, set `MODELRYX_FREECAD_WORKER_URL=http://localhost:8090` and start worker separately

## Common Development Workflows

### Adding a New API Endpoint

1. **Create the route** in `server/src/server/routes/`
2. **Add tests** in `server/tests/`
3. **Update API docs** (auto-generated from FastAPI docstrings)
4. **Test with Postman** or `curl`

See [docs/api.md](./docs/development/api.md) for endpoint conventions.

### Adding a New Database Table

1. **Create model** in `server/src/server/models/`
2. **Generate migration** with `alembic revision --autogenerate`
3. **Review and apply migration**
4. **Add API routes** to expose the model

See [docs/database.md](./docs/operations/database.md) for schema guidelines.

### Adding a New React Component

1. **Create component** in `front-end/src/components/`
2. **Add TypeScript interfaces** for props and state
3. **Add tests** in `front-end/src/components/__tests__/`
4. **Integrate into page** components

### Creating a Custom CAD Worker

See [docs/workers/new-cad-worker-guide.md](./docs/workers/new-cad-worker-guide.md).

## IDE Setup

### VS Code

**Recommended Extensions:**
- Python (Microsoft)
- Pylance (Microsoft)
- TypeScript Vue Plugin (Vue)
- ESLint (Microsoft)
- Prettier (esbenp.prettier-vscode)
- Docker (Microsoft)

**Settings (`.vscode/settings.json`):**
```json
{
  "python.linting.enabled": true,
  "python.linting.pylintEnabled": false,
  "python.formatting.provider": "black",
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "ms-python.python",
    "editor.formatOnSave": true
  },
  "[typescript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode",
    "editor.formatOnSave": true
  }
}
```

### PyCharm / IntelliJ IDEA

1. Open project
2. Configure Python interpreter: `Settings > Project > Python Interpreter > Add > Existing Environment > venv/bin/python`
3. Enable Django support: `Settings > Languages & Frameworks > Django`
4. Enable ESLint for frontend code

## Next Steps

1. **Read the architecture docs:** [docs/architecture/platform-architecture.md](./docs/architecture/platform-architecture.md)
2. **Review the API reference:** [docs/api.md](./docs/development/api.md)
3. **Explore existing tests** to understand testing patterns
4. **Check open issues** for things to work on

## Getting Help

- **Documentation:** See [docs/](./docs/) for architecture, API, and worker guides
- **Issues:** Check open issues or create a new one
- **Discussions:** Use GitHub Discussions for questions
- **Code:** Look at existing code for patterns and examples

---

Happy developing! ðŸš€



