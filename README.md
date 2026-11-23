# Modelryx Platform

**An open-source, AI-powered engineering data management platform** that brings together product data management (PDM), multi-CAD integration, intelligent search, automated CAD processing, and interactive 3D visualization—helping engineering teams manage models, assemblies, revisions, and related information in one collaborative environment.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)

## What is Modelryx?

Modelryx gives engineering teams a shared workspace for CAD models and the product
information around them: Items, revisions, immutable content versions, assemblies,
attachments, reviews, and approvals. Teams can inspect models in the browser,
search engineering metadata, and automate conversion and validation through CAD workers.
Optional AI assistance helps find and explain authorized engineering data.

The current focus is engineering data management and PDM. The extensible architecture
provides a foundation for broader product lifecycle management (PLM).

## Key Features

| Capability | What you can do |
| --- | --- |
| Product data management | Organize Items, revisions, CAD files, metadata, managed BOMs, and product relationships. |
| Engineering collaboration | Coordinate checkout, comments, reviews, and approvals with access controls. |
| CAD processing | Extract metadata, analyze geometry, validate data, and generate previews using STEP and independently deployed native CAD workers. |
| Browser visualization | Inspect 3D models and assembly occurrences, and view available drawing PDFs. |
| Engineering search and AI | Search structured engineering data; optionally add natural-language interpretation, semantic retrieval, and evidence-backed assistance. |
| Integration and extension | Use the REST API and worker protocol to connect applications, automate workflows, and integrate additional CAD systems. |
| Self-hosted deployment | Run the backend with Docker Compose, PostgreSQL with pgvector, and persistent local file storage. |

## Architecture

| Component | Responsibility | Default local address |
| --- | --- | --- |
| React / TypeScript frontend | Engineering workspace and browser previews; runs separately from Compose | http://localhost:5173 |
| FastAPI server | Authentication, engineering records, workflows, and REST API | http://localhost:8000 |
| PostgreSQL 17 + pgvector | Engineering metadata and search vectors | `localhost:5432` (loopback only) |
| General worker | Background processing orchestration and publication | Internal service |
| STEP worker | Neutral-format geometry processing | http://localhost:8080 |
| External CAD workers | Native CAD extraction and conversion; FreeCAD is the included reference | FreeCAD: http://localhost:8090 when installed |
| Local file storage | Source files and generated artifacts | Persistent `object-data` volume |

The API and general worker share platform storage. External CAD workers receive
job-scoped source files over HTTP and do not need platform database credentials
or access to that volume. The optional Local CAD Agent packages native documents
on each user's computer before upload.

See the [platform architecture](docs/architecture/platform-architecture.md) for component boundaries and data flows.

## Initial release scope

Modelryx is in development ahead of its first public release. This repository
provides STEP processing and a separately deployed FreeCAD (`.FCStd`) worker.
Additional CAD systems require independently deployed workers and their own runtime licenses. See the component
guides for environment requirements and processing limitations.

Database setup supports fresh-schema initialization and matching-schema checks.
Automatic schema upgrades and built-in backup/restore are outside the initial
release scope. A public release version and date have not yet been assigned.

## Quick Start

### Prerequisites

- **Docker Engine with Docker Compose v2**
- **Node.js** v22.12+ (for the frontend)
- **Python** v3.11+ only for host Python development; the Docker images supply their own Python runtime.
- Sufficient memory and disk for CAD workloads. If you enable the separate FreeCAD service, its default container memory limit is 8 GiB.

### 1. Clone the repository

```bash
# Clone the repository
git clone https://github.com/Modelryx/Modelryx.git
cd Modelryx
```

### 2. Configure the project

On Windows, run the configuration helper from the repository root in PowerShell:

```powershell
.\scripts\configure-project.ps1
```

The helper prompts for the environment, bootstrap administrator credentials, and an
optional local FreeCAD executable. It generates missing database, JWT, and worker
secrets and creates or updates these local configuration files:

| File | Purpose |
|------|---------|
| `.env` | Docker Compose settings and shared service credentials |
| `server/.env` | Host Python API settings, including a localhost database connection |
| `.modelryx.local.props` | Local FreeCAD executable path for MSBuild tooling |

Existing settings are generally preserved; explicit parameters override them. To
generate configuration without prompts, use:

```powershell
.\scripts\configure-project.ps1 -NonInteractive
```

If no administrator password is already configured, this mode generates one and
stores it in `.env`. Read `MODELRYX_BOOTSTRAP_ADMIN_PASSWORD` there before signing in.
Use `-PostgresPort 5433` if the default host database port is occupied. To install
the optional Windows Local CAD Agent for local file selection, add
`-InstallLocalCadAgent`; see the [agent guide](local_cad_agent/README.md).

For manual configuration (including Linux and macOS), copy the root template and
generate a separate value for each required secret:

```bash
cp .env.example .env

# Generate secure environment variables
python3 << 'EOF'
import secrets
print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(16)}")
print(f"MODELRYX_JWT_SECRET={secrets.token_urlsafe(32)}")
print(f"MODELRYX_INTERNAL_WORKER_KEY={secrets.token_urlsafe(32)}")
print(f"MODELRYX_BOOTSTRAP_ADMIN_PASSWORD={secrets.token_urlsafe(16)}")
EOF

# Edit .env and replace the corresponding placeholders with these values
```

For a second clone or a fresh test installation, first set a unique
`COMPOSE_PROJECT_NAME` in its `.env`. Clones otherwise share the default
`modelryx-platform` Docker project and existing data. See
[multiple clones and Docker data](DEVELOPMENT.md#multiple-clones-and-existing-docker-data)
for port conflicts and credential reuse.

Before starting services, review these settings in `.env`:

| Setting | What to configure |
|---------|-------------------|
| `POSTGRES_PASSWORD` | Database password; use a generated URL-safe value |
| `MODELRYX_JWT_SECRET` | Secret used to sign authentication tokens |
| `MODELRYX_INTERNAL_WORKER_KEY` | Shared authentication key used by the API and CAD workers |
| `MODELRYX_BOOTSTRAP_ADMIN_USERNAME`, `MODELRYX_BOOTSTRAP_ADMIN_PASSWORD` | Credentials for the initial administrator account |
| `MODELRYX_ENVIRONMENT` | `development` for local setup; `production` enables stricter secret validation |
| `MODELRYX_POSTGRES_PORT` | Host PostgreSQL port (default `5432`) |
| `MODELRYX_SERVER_URL` | Server address reachable by clients (default `http://localhost:8000`) |
| `MODELRYX_CORS_ORIGINS` | JSON array of permitted frontend origins (default `["http://localhost:5173"]`) |

Keep configuration files containing credentials private and out of version control.
Changing `POSTGRES_PASSWORD` in a file does not update an existing database's
password; update the database credentials separately. For a host Python API, also
configure `server/.env` as described in [DEVELOPMENT.md](DEVELOPMENT.md#host-python-api).
Optional AI settings are covered in [AI Support](#ai-support), and external FreeCAD
setup is covered in the [FreeCAD worker guide](workers/freecad/README.md).

After editing configuration on an existing deployment, run `docker compose up -d`
to recreate affected services; a plain restart does not apply changed Compose
environment variables.

### 3. Start the backend and frontend

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

Open http://localhost:5173. The frontend is a React and TypeScript application
served by Vite, with hot reload during development. On the login screen, use
`http://localhost:8000` as the server URL. For a production frontend build, run
`npm run build` in `front-end`; deploy the generated `dist` files with a web
server configured for single-page application routing. Configure the API's
`MODELRYX_CORS_ORIGINS` for the deployed frontend origin.

### 4. Sign in

Use your configured bootstrap credentials:

- **Username:** `MODELRYX_BOOTSTRAP_ADMIN_USERNAME` (template: `admin`)
- **Password:** the value of `MODELRYX_BOOTSTRAP_ADMIN_PASSWORD` in `.env`

### 5. Enable Open CAD File for FreeCAD

To use **Open CAD File** with `.FCStd` files, install FreeCAD, the Local CAD Agent,
and a registered FreeCAD preprocessor on each user's local machine. Docker worker
startup alone does not set up local file selection and dependency packaging.
The client preprocessor must match an enabled server worker registration.

Follow [Enable Open CAD File for FreeCAD](DEVELOPMENT.md#enable-open-cad-file-for-freecad)
for prerequisites, installation commands, registration, and a verification step.

## AI Support

Modelryx supports optional OpenAI, Anthropic Claude, Gemini, and OpenAI-compatible
chat providers. An independently configured OpenAI-compatible embedding gateway
enables semantic retrieval. These integrations support:

- **Natural-language engineering search** - Interpret requests as validated search filters, with optional embeddings for semantic retrieval.
- **Context-aware assistance** - Ask about item metadata, revision history, assemblies, validation findings, and the selected model using authorized project data.
- **Grounded answers and explanations** - Retrieve engineering knowledge and explain revision comparisons or validation findings using supporting evidence.

For an OpenAI-compatible chat and embedding gateway, configure `.env` before
starting the API. For native chat providers, follow the [provider setup guide](docs/ai/providers.md).

```dotenv
MODELRYX_AI_GATEWAY_BASE_URL=https://your-gateway.example/v1
MODELRYX_AI_GATEWAY_API_KEY=replace-me
MODELRYX_AI_CHAT_MODEL=your-chat-model
MODELRYX_AI_EMBEDDING_MODEL=your-embedding-model
MODELRYX_AI_EMBEDDING_DIMENSIONS=1536
```

The gateway must support the OpenAI-compatible `/chat/completions` and `/embeddings`
operations for the corresponding features. Embeddings must have 1536 dimensions to
match the database schema. Keep gateway credentials on the server.

AI configuration is optional. Without a configured provider, structured search, lexical retrieval,
revision comparison, validation, and BOM analysis remain available; generated answers
and vector retrieval require chat and embedding configuration, respectively. AI requests
respect existing access controls, and generated explanations do not change engineering
records. Check `/api/v1/ai/status` for configuration and indexing status.

See the [AI documentation](docs/ai/README.md) for capabilities, setup, and limitations.

## Development Setup

Use the split Docker backend/host frontend setup above, or install the worker SDK and server
editable packages for host Python development. The entry point is `modelryx.api.main:app`;
PostgreSQL must include pgvector. See [DEVELOPMENT.md](DEVELOPMENT.md) for database initialization,
environment files, CORS, checks and the supported service names.

## External FreeCAD server

The main Compose stack contains the API, database, general orchestrator and STEP service.
FreeCAD runs only as a separate HTTP worker, without platform database or object-volume access.
Copy `workers/freecad/.env.example` to `workers/freecad/.env` and set the shared worker key.
Start it with `workers/freecad/scripts/start-freecad-server.ps1`; for Docker Desktop use
`-ListenAddress 0.0.0.0`, then register `http://host.docker.internal:8090` in
**Administrator > External CAD Workers**. Both services must use the same internal worker key.
On another host use a reachable private/TLS URL. See [FreeCAD](workers/freecad/README.md).

Extraction, metadata, geometry and previews run on the external server. Without a registration
or configured external URL, FreeCAD processing is unavailable. Controlled CAD editing sessions
are disabled in both UI and API for this release. Open CAD File requires the separately installed
local preprocessor; it only resolves references, rebases copies and packages source documents.


## Deployment

### Production Deployment

For production deployment:

1. **Read [deployment.md](docs/operations/deployment.md)** - Complete deployment guide
2. **Review [security best practices](docs/security/worker-security.md)** - Worker authentication and security
3. **Set up monitoring** - Health checks and logging

Built-in backup, restore, and automatic schema upgrades are outside the initial
release scope. Plan recovery for both database metadata and stored files; see the
[deployment guide](docs/operations/deployment.md).

Set production values in `.env`, including `MODELRYX_ENVIRONMENT=production`,
`MODELRYX_SERVER_URL`, exact frontend CORS origins, and independent secrets. Configure
TLS and the production frontend as described in the deployment guide, then start
and verify the backend:

```bash
docker compose up -d --build
curl https://your-domain.com/health/ready
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
- **[docs/ai/README.md](docs/ai/README.md)** - AI capabilities, gateway configuration, and limitations
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
|   |-- freecad/         # External FreeCAD worker and separate local preprocessor
|   |-- step/            # STEP processor (built-in)
|-- worker-sdk/          # Native HTTP protocol and validation helpers
|-- local_cad_agent/     # Native file dialog and client preprocessor dispatch
|-- docs/                # Documentation
|-- scripts/             # Configuration and verification utilities
|-- compose.yaml         # Docker Compose orchestration
```

## Community & Support

- [GitHub issues](https://github.com/Modelryx/Modelryx/issues) — Report bugs and request features.
- [Contributing](CONTRIBUTING.md) — Development workflow and contribution guidelines.
- [Code of Conduct](code-of-conduct.md) — Community expectations.
- [Security policy](SECURITY_POLICY.md) — Report security concerns.

## License

Modelryx is licensed under the **Apache License 2.0** - see [LICENSE](LICENSE) for details.

Third-party components retain their own licenses. Modelryx uses FreeCAD,
Open CASCADE Technology, pythonocc-core, and other independently licensed software.
See [third-party notices](THIRD_PARTY_NOTICES.md) for acknowledgments and
[license texts](licenses/third-party/README.md). Before distributing images or
executables, follow the [third-party release guidance](docs/operations/third-party-licensing.md).

## Getting Help

1. Check [DEVELOPMENT.md](DEVELOPMENT.md) for setup issues
2. Review [docs/development/testing.md](docs/development/testing.md) for test and validation
3. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for repository support and contributions
4. See [SECURITY_POLICY.md](SECURITY_POLICY.md) for security reporting

