# Modelryx Platform

**An open-source CAD data management and processing platform** built for engineering teams to manage, collaborate, and process CAD files at scale.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)

## What is Modelryx?

Modelryx is a web-based platform for managing CAD (Computer-Aided Design) data with integrated support for multiple CAD systems. It provides:

- **Unified CAD Management** - Store, version, and organize CAD files from multiple sources
- **Automated Processing** - Convert and analyze CAD data with containerized processing workers
- **Collaboration** - Team-based access control, commenting, and approval workflows
- **Extensible Architecture** - Build custom CAD workers for your tools and formats
- **Modern Web UI** - React-based browser interface for seamless interaction
- **REST API** - Full programmatic access for automation and integration

## Key Features

- **Multi-CAD Support** - Built-in support for FreeCAD and STEP formats, extensible to others
- **Version Control** - Track revisions, assemblies, and metadata
- **3D Preview** - Real-time GLB/3D model previewing and interaction
- **Worker Framework** - Extensible system for adding new CAD formats and tools
- **Containerized Deployment** - PostgreSQL backend and persistent local object storage
- **Security** - HMAC authentication, role-based access, HTTPS support
- **APIs** - Complete REST API for programmatic access
- **Open Source** - Apache 2.0 licensed, community-driven

## Architecture

Modelryx follows a microservices architecture with these components:

- **Frontend**: React 18+ with TypeScript on Vite, run locally at http://localhost:5174
- **API Server**: FastAPI application on port 8000 with OpenAPI documentation
- **Database**: PostgreSQL 14+ with pgvector for vector operations
- **CAD Workers**: Containerized processing workers for format conversion and analysis
- **Object Storage**: Persistent local filesystem volume for CAD files and artifacts

Frontend -> REST API (FastAPI) -> PostgreSQL + CAD Workers + Local Object Storage

See the [platform architecture documentation](docs/architecture/platform-architecture.md) for detailed design.

## Quick Start

### Prerequisites

- **Docker & Docker Compose** v24.0+
- **Node.js** v22.0+ (for the frontend)
- **Python** v3.11+ (for server and workers)
- **8+ GB RAM**, **10+ GB disk space**

### 1. Clone & Configure

```bash
# Clone the repository
git clone https://github.com/Modelryx/Modelryx.git
cd modelryx

# Generate secure environment variables
python3 << 'EOF'
import secrets
print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(16)}")
print(f"MODELRYX_JWT_SECRET={secrets.token_urlsafe(32)}")
print(f"MODELRYX_INTERNAL_WORKER_KEY={secrets.token_urlsafe(32)}")
print(f"MODELRYX_BOOTSTRAP_ADMIN_PASSWORD={secrets.token_urlsafe(16)}")
EOF

# Copy to .env and edit as needed
cp .env.example .env
# nano .env  # Edit and fill in the generated values
```

### 2. Start the backend and frontend

Start the API, database, and workers from the repository root:

```bash
docker compose up --build
```

In a separate terminal, install and start the frontend:

```bash
cd front-end
npm ci
npm run dev
```

Open http://localhost:5174. The frontend is a React and TypeScript application
served by Vite, with hot reload during development. On the login screen, use
`http://localhost:8000` as the server URL. For a production frontend build, run
`npm run build` in `front-end`; deploy the generated `dist` files with a web
server configured for single-page application routing. Configure the API's
`MODELRYX_CORS_ORIGINS` for the deployed frontend origin.

### 3. Login

Default credentials (change immediately!):
- **Username:** admin
- **Password:** (from MODELRYX_BOOTSTRAP_ADMIN_PASSWORD in .env)

## Development Setup

For local development with hot reloading:

```bash
# Terminal 1: Backend
cd server
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install ../worker-sdk
pip install -e ".[dev]"
python -m uvicorn modelryx.api.main:app --reload

# Terminal 2: Frontend
cd front-end
npm install
npm run dev  # http://localhost:5174

# Terminal 3: Database (if not using Docker)
docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:15
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for detailed setup instructions.

## Deployment

### Production Deployment

For production deployment:

1. **Read [deployment.md](docs/operations/deployment.md)** - Complete deployment guide
2. **Review [security best practices](docs/security/worker-security.md)** - Worker authentication and security
3. **Configure backups** - Use provided backup scripts for data safety
4. **Set up monitoring** - Health checks and logging

Quick start:
```bash
# Set production values in .env
MODELRYX_ENVIRONMENT=production
MODELRYX_SERVER_URL=https://your-domain.com
# ... set all required secrets ...

# Start services
docker compose up -d

# Verify deployment
curl https://your-domain.com/health
```

See [S3 object operations](docs/development/s3-operations.md) for the optional Python
storage component. Application file workflows currently use local storage.

## Adding Custom CAD Workers

Modelryx is designed to be extended with custom CAD workers for your tools and formats.

Start with the [New CAD Worker Guide](docs/workers/new-cad-worker-guide.md) which includes:
- Worker architecture and contract
- HTTP API specification
- Authentication (HMAC-based)
- Deployment options (Docker, native, external)
- Testing and debugging

Example workers included:
- **FreeCAD** - Built-in STEP conversion and metadata extraction
- **STEP** - Universal 3D model processing
- Both are fully documented examples for extending

## Documentation

- **[CONTRIBUTING.md](CONTRIBUTING.md)** - How to contribute
- **[quick-reference.md](docs/quick-reference.md)** - Developer & operator quick reference
- **[docs/README.md](docs/README.md)** - Documentation index
- **[docs/development/api.md](docs/development/api.md)** - REST API reference
- **[docs/operations/deployment.md](docs/operations/deployment.md)** - Production deployment
- **[docs/security/worker-security.md](docs/security/worker-security.md)** - Worker security model
- **[docs/workers/](docs/workers/)** - Worker implementation guides
- **[docs/architecture/](docs/architecture/)** - System architecture docs

## Project Structure

```
modelryx/
|-- server/              # Python/FastAPI backend API
|-- front-end/           # React/TypeScript/Vite UI
|-- workers/
|   |-- freecad/         # FreeCAD worker (built-in)
|   |-- step/            # STEP processor (built-in)
|-- docs/                # Documentation
|-- scripts/             # Utility scripts (backup, deploy)
|-- compose.yaml         # Docker Compose orchestration
```

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy |
| Frontend | React 18+, TypeScript, Vite |
| Database | PostgreSQL 14+ with pgvector |
| Object Storage | Persistent local filesystem |
| Processing | Docker (containerized workers) |
| CAD | FreeCAD SDK, STEP format processing |

## Community & Support

- **Issues** - Report bugs and request features on GitHub
- **Discussions** - Ask questions and share ideas
- **Contributing** - See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Code of Conduct** - See [code-of-conduct.md](code-of-conduct.md)

## License

Modelryx is licensed under the **Apache License 2.0** - see [LICENSE](LICENSE) for details.

The platform includes integrations with:
- **FreeCAD** - LGPL v2.1 (open source)
- **STEP format** - Open standard (ISO 10303)

## Getting Help

1. Check [DEVELOPMENT.md](DEVELOPMENT.md) for setup issues
2. Review [docs/development/testing.md](docs/development/testing.md) for test and validation
3. Search existing [Issues](../../issues)
4. Ask in [Discussions](../../discussions)
5. See [security-policy.md](security-policy.md) for security reporting

---

**Ready to get started?** See [Quick Start](#quick-start) or [DEVELOPMENT.md](DEVELOPMENT.md).

**Want to deploy to production?** See [docs/operations/deployment.md](docs/operations/deployment.md).

**Building a custom worker?** See [CAD Worker Guide](docs/workers/new-cad-worker-guide.md).