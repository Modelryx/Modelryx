# Modelryx Documentation

Welcome to the Modelryx documentation. This repository is an open-source engineering data and lifecycle platform for CAD model management and intelligent engineering workflows.

## Contents

### [Architecture](./architecture/)
Core platform architecture and data model documentation.

- **[platform-architecture.md](./architecture/platform-architecture.md)** - Top-level system design, component boundaries, and request flows
- **[partmetadata-v1.md](./architecture/partmetadata-v1.md)** - CAD-neutral metadata specification for parts and assemblies
- **[cad-processing-framework.md](./architecture/cad-processing-framework.md)** - CAD processing pipelines, worker coordination, and artifact handling

### [AI Subsystem](./ai/)
AI-assisted capabilities, from deterministic search to optional model-enhanced features.

- **[overview.md](./ai/overview.md)** - AI subsystem architecture and capability map
- **[assistant.md](./ai/assistant.md)** - Context-aware assistant, intents, and tool boundaries
- **[engineering-search.md](./ai/engineering-search.md)** - Deterministic engineering search, natural-language interpretation, and evaluation harness

### [Workers](./workers/)
CAD worker APIs and integration guidance for the open-source platform.

- **[freecad-worker.md](./workers/freecad-worker.md)** - FreeCAD integration and usage
- **[step-worker.md](./workers/step-worker.md)** - Shared STEP and geometry processing service
- **[new-cad-worker-guide.md](./workers/new-cad-worker-guide.md)** - Guide for implementing custom CAD workers

### [Operations](./operations/)
Production deployment and database management.

- **[deployment.md](./operations/deployment.md)** - Production deployment and operations
- **[database.md](./operations/database.md)** - Database schema and data model

### [Development](./development/)
Development setup, testing, and API reference.

- **[testing.md](./development/testing.md)** - Testing strategy and frameworks
- **[api.md](./development/api.md)** - REST API reference and endpoints

### [Security](./security/)
Security guidelines and best practices.

- **[worker-security.md](./security/worker-security.md)** - CAD worker shared-key authentication, transport and ownership boundaries

## Quick Reference

- **[quick-reference.md](./quick-reference.md)** - Developer and operator quick reference guide

## Getting Started

### First Time?

1. **Set up development environment:** [DEVELOPMENT.md](../DEVELOPMENT.md)
2. **Understand the architecture:** [platform-architecture.md](./architecture/platform-architecture.md)
3. **Learn the data model:** [partmetadata-v1.md](./architecture/partmetadata-v1.md)
4. **Explore AI capabilities:** [ai/overview.md](./ai/overview.md)
5. **Create a CAD worker:** [workers/new-cad-worker-guide.md](./workers/new-cad-worker-guide.md)

### Deploying to Production?

1. **Review deployment guide:** [operations/deployment.md](./operations/deployment.md)
2. **Review release limitations:** Built-in backup and restore support is not included in the first published version.
3. **Understand system requirements:** Hardware, ports, PostgreSQL setup
4. **Secure CAD workers:** [security/worker-security.md](./security/worker-security.md)
5. **Configure environment variables:** Secrets, CORS, TLS
6. **Set up monitoring:** Health checks and logging
7. **Test deployment:** Health check endpoints, sample API calls

### Contributing Code?

1. **Read contribution guide:** [CONTRIBUTING.md](../CONTRIBUTING.md)
2. **Set up development environment:** [DEVELOPMENT.md](../DEVELOPMENT.md)
3. **Write tests:** [development/testing.md](./development/testing.md)
4. **Review API spec:** [development/api.md](./development/api.md)
5. **Follow code style:** Python, TypeScript, and SQL conventions
6. **Submit PR:** Clear title, description, and test coverage

## All Documentation Categories

- **[operations/](./operations/)** - Deployment and database management
- **[development/](./development/)** - Testing, API reference, and development guides
- **[security/](./security/)** - Security guidelines and worker authentication
- **[architecture/](./architecture/)** - System design and data models
- **[ai/](./ai/)** - AI subsystem documentation
- **[workers/](./workers/)** - CAD worker implementation guides

## Project Structure

```
modelryx/
├── server/              # Python FastAPI backend API
├── front-end/           # React TypeScript frontend UI
├── workers/
│   ├── freecad/         # FreeCAD embedded/standalone reference worker
│   └── step/            # STEP processor (built-in)
├── docs/                # Complete documentation
│   ├── operations/      # Deployment and operations
│   ├── development/     # Development and testing
│   ├── security/        # Security guidelines
│   ├── architecture/    # System architecture
│   ├── ai/              # AI subsystem
│   └── workers/         # Worker guides
├── scripts/             # Configuration and verification utilities
└── compose.yaml         # Docker Compose configuration
```

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for:
- Code style guide (Python, TypeScript, SQL)
- How to submit pull requests
- Testing requirements
- Development workflow

Refer to component-specific `README.md` files for detailed setup and development instructions.
