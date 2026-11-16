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
- **Security** - JWT user authentication, role-based access, shared-key worker authentication
- **APIs** - Complete REST API for programmatic access
- **Open Source** - Apache 2.0 licensed, community-driven

## Architecture

Modelryx follows a microservices architecture with these components:

- **Frontend**: React 18+ with TypeScript on Vite, run locally at http://localhost:5174
- **API Server**: FastAPI application on port 8000 with OpenAPI documentation
- **Database**: PostgreSQL 17 with pgvector for vector operations
- **CAD Workers**: Containerized processing workers for format conversion and analysis
- **Object Storage**: Persistent local filesystem volume for CAD files and artifacts

Frontend -> REST API (FastAPI) -> PostgreSQL + CAD Workers + Local Object Storage

See the [platform architecture documentation](docs/architecture/platform-architecture.md) for detailed design.

## Quick Start

### Prerequisites

- **Docker Engine with Docker Compose v2**
- **Node.js** v22.12+ (for the frontend)
- **Python** v3.11+ (for server and workers)
- Sufficient memory and disk for native CAD workloads; the default FreeCAD service limit is 8 GiB per container.

### 1. Clone & Configure

```bash
# Clone the repository
git clone https://github.com/yanggmtl/Modelryx.git
cd Modelryx

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

Use your configured bootstrap credentials:
- **Username:** `MODELRYX_BOOTSTRAP_ADMIN_USERNAME` (template: `admin`)
- **Password:** (from MODELRYX_BOOTSTRAP_ADMIN_PASSWORD in .env)

## Development Setup

Use the split Docker backend/host frontend setup above, or install the worker SDK and server
editable packages for host Python development. The entry point is `modelryx.api.main:app`;
PostgreSQL must include pgvector. See [DEVELOPMENT.md](DEVELOPMENT.md) for database initialization,
environment files, CORS, checks and the supported service names.

### Run without embedded FreeCAD

```powershell
docker compose stop freecad-worker freecad-preprocessor
docker compose up -d --build api general-worker step-worker
.\workers\freecad\scripts\start-freecad-server.ps1 -ListenAddress 0.0.0.0
```

Register the standalone worker in **Administrator > External CAD Workers**. On Docker Desktop,
its host URL is `http://host.docker.internal:8090`. Enabled matching FreeCAD registration takes
precedence; without it, the dedicated standalone URL or embedded processing path remains.
External failure does not automatically retry on embedded FreeCAD. CAD sessions still need the
embedded runtime. See the [reference worker](workers/freecad/README.md).

## Deployment

### Production Deployment

For production deployment:

1. **Read [deployment.md](docs/operations/deployment.md)** - Complete deployment guide
2. **Review [security best practices](docs/security/worker-security.md)** - Worker authentication and security
3. **Configure backups** - Back up matched database and object-volume content and test recovery
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
- Authentication with `X-Modelryx-Worker-Key`
- Deployment options (Docker, native, external)
- Testing and debugging

Example workers included:
- **FreeCAD** - Native FCStd conversion, metadata extraction and standalone external-worker reference
- **STEP** - Universal 3D model processing
- Use the [worker SDK](worker-sdk/README.md) for the generic native-worker contract; STEP uses a separate geometry-service contract.

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
|   |-- freecad/         # Embedded and standalone FreeCAD reference worker
|   |-- step/            # STEP processor (built-in)
|-- worker-sdk/          # Native HTTP protocol and validation helpers
|-- local_cad_agent/     # Native file dialog and client preprocessor dispatch
|-- docs/                # Documentation
|-- scripts/             # Configuration, verification and backup utilities
|-- compose.yaml         # Docker Compose orchestration
```

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy |
| Frontend | React 18+, TypeScript, Vite |
| Database | PostgreSQL 17 with pgvector |
| Object Storage | Persistent local filesystem |
| Processing | General orchestrator, embedded FreeCAD or independent CAD/geometry services |
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
3. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for repository support and contributions
4. See [SECURITY_POLICY.md](SECURITY_POLICY.md) for security reporting

---

**Ready to get started?** See [Quick Start](#quick-start) or [DEVELOPMENT.md](DEVELOPMENT.md).

**Want to deploy to production?** See [docs/operations/deployment.md](docs/operations/deployment.md).

**Building a custom worker?** See [CAD Worker Guide](docs/workers/new-cad-worker-guide.md).