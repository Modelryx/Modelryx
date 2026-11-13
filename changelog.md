# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (Planned features and improvements go here)

### Changed
- (Changes to existing functionality)

### Deprecated
- (Soon-to-be removed features)

### Removed
- (Removed features)

### Fixed
- (Bug fixes)

### Security
- (Security vulnerability fixes)

---

## [1.0.0] - 2026-11-11

### Added

#### Core Platform
- **Aggregated Health Check Endpoints** - `/health`, `/health/live`, `/health/ready` for production monitoring and Kubernetes deployment
- **Timeout Configuration** - Externalized timeout settings for CAD processing, worker heartbeats, and AI requests
- **Connection Pool Configuration** - Database connection pool settings for production deployments

#### Documentation
- **Reorganized Documentation Structure** - Logical categorization into Operations, Development, and Security folders
- **Production Deployment Guide** - Complete deployment procedures with CAD worker resource requirements
- **CAD Worker Security Model** - Detailed authentication and authorization framework
- **Architecture Documentation** - Platform design, data model, and processing pipelines
- **AI Subsystem Documentation** - Assistant capabilities, search functionality, and tool interfaces
- **Development Guides** - Setup, testing, and API reference for developers

#### Infrastructure
- **Docker Compose Configuration** - Complete orchestration for all components
- **Environment Configuration** - Safe .env.example with all required variables
- **Database Migrations** - Managed schema versioning with Alembic

#### Code Quality
- **ESLint Configuration** - TypeScript/JavaScript linting for frontend code quality
- **Prettier Configuration** - Code formatting standards for consistency
- **Testing Framework** - Comprehensive unit and integration test coverage

### Features

#### Backend (Python/FastAPI)
- REST API with OpenAPI/Swagger documentation
- PostgreSQL database with ORM and migrations
- CAD worker coordination and artifact management
- AI-assisted search and engineering workflows

#### Frontend (React/TypeScript)
- Modern UI for CAD model management
- Real-time updates and collaboration features
- Responsive design for desktop engineering workflows
- Integration with AI features

#### CAD Workers
- FreeCAD integration for STEP conversion and metadata extraction
- Generic STEP processor for model normalization
- Extensible worker framework for custom CAD tools

### Documentation

- README with quick start and feature overview
- DEVELOPMENT.md with local setup instructions
- CONTRIBUTING.md with code style and contribution guidelines
- 22+ detailed documentation files organized by category
- API reference with example requests and responses
- Deployment guide with production best practices
- Security documentation with threat models and mitigations

### Security

- No hardcoded credentials or secrets
- Externalized configuration with .env
- HMAC-SHA256 worker authentication
- JWT-based API authentication
- Database connection pooling with timeout protection
- Input validation and sanitization across all endpoints

---

## Release Guidelines

### Major Version (X.0.0)
- Breaking changes to API or data model
- Significant architectural changes
- Major feature additions

### Minor Version (X.Y.0)
- New features (backward compatible)
- Non-breaking API enhancements
- Documentation improvements

### Patch Version (X.Y.Z)
- Bug fixes
- Security updates
- Performance improvements
- Documentation typos

---

## How to Contribute

See [CONTRIBUTING.md](./CONTRIBUTING.md) for:
- How to submit pull requests
- Code style guidelines
- Testing requirements
- Commit message conventions

## Security

See [security-policy.md](./security-policy.md) for information on:
- How to report security vulnerabilities responsibly
- Security best practices for deploying Modelryx
- Known security considerations and mitigations
