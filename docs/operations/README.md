# Operations Documentation

Guides for deploying, managing, and maintaining Modelryx in production environments.

## Contents

- **[deployment.md](deployment.md)** - Production deployment guide
  - Docker Compose deployment
  - Environment configuration
  - CAD worker resource requirements
  - Health check endpoints
  - Service selection and operational verification

The first published version does not include built-in backup or restore support.
Operators must plan recovery for both database metadata and stored files. Automatic schema upgrades are also outside the initial release scope.

- **[database.md](database.md)** - Database schema and configuration
  - Database schema overview
  - Fresh-schema initialization and upgrade limitations
  - Model ownership and connection configuration
  - Troubleshooting

- **[database-initialization.md](database-initialization.md)** - Fresh-schema initialization, matching-schema checks, and incompatible-schema handling

- **[third-party-licensing.md](third-party-licensing.md)** - Dependency licenses and release packaging

## Quick Links

- **[Back to Documentation Index](../README.md)**
- **[Development Documentation](../development/README.md)**
- **[Security Documentation](../security/README.md)**
- **[Architecture Documentation](../architecture/README.md)**
- **[AI Features Documentation](../ai/README.md)**
- **[CAD Workers Documentation](../workers/README.md)**
