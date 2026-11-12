# Modelryx API Reference

This document describes the main API endpoints for Modelryx. For interactive API exploration, see the Swagger UI at `http://localhost:8000/docs` (development) or `https://yourdomain.com/docs` (production).

## Quick Start

All API requests require:

- **Base URL:** `http://localhost:8000/api` (development) or `https://yourdomain.com/api` (production)
- **Content-Type:** `application/json`
- **Authentication:** JWT bearer token (passed in `Authorization: Bearer <token>` header)

Example request:

```bash
curl -X GET http://localhost:8000/api/items \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json"
```

## Authentication

### Login

**Endpoint:** `POST /auth/login`

Request:
```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response (200 OK):
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "John Doe"
  }
}
```

### Register

**Endpoint:** `POST /auth/register`

Request:
```json
{
  "email": "newuser@example.com",
  "password": "password123",
  "name": "Jane Doe"
}
```

Response (201 Created):
```json
{
  "id": "uuid",
  "email": "newuser@example.com",
  "name": "Jane Doe"
}
```

### Refresh Token

**Endpoint:** `POST /auth/refresh`

Requires valid refresh token in cookie.

Response (200 OK):
```json
{
  "access_token": "new_token_here",
  "token_type": "bearer"
}
```

## Workspaces

Workspaces organize items and manage team access.

### List Workspaces

**Endpoint:** `GET /workspaces`

Query Parameters:
- `skip` (int, default 0) â€?Number of results to skip
- `limit` (int, default 20) â€?Maximum results to return (max 100)
- `search` (string, optional) â€?Filter by name or description

Response (200 OK):
```json
{
  "total": 5,
  "items": [
    {
      "id": "workspace-uuid",
      "name": "Product Design",
      "description": "Main product development workspace",
      "created_at": "2026-11-11T13:40:00Z",
      "updated_at": "2026-11-11T13:40:00Z"
    }
  ]
}
```

### Create Workspace

**Endpoint:** `POST /workspaces`

Request:
```json
{
  "name": "New Project",
  "description": "Project description"
}
```

Response (201 Created):
```json
{
  "id": "workspace-uuid",
  "name": "New Project",
  "description": "Project description",
  "created_at": "2026-11-11T13:40:00Z"
}
```

### Get Workspace

**Endpoint:** `GET /workspaces/{workspace_id}`

Response (200 OK):
```json
{
  "id": "workspace-uuid",
  "name": "Product Design",
  "description": "Main product development workspace",
  "member_count": 5,
  "item_count": 42,
  "created_at": "2026-11-11T13:40:00Z"
}
```

## Items

Items represent CAD models and assemblies.

### List Items

**Endpoint:** `GET /workspaces/{workspace_id}/items`

Query Parameters:
- `skip` (int, default 0)
- `limit` (int, default 20)
- `search` (string) â€?Search by name or description
- `sort` (string) â€?Sort field (name, created_at, updated_at)
- `order` (string) â€?Sort order (asc, desc)

Response (200 OK):
```json
{
  "total": 42,
  "items": [
    {
      "id": "item-uuid",
      "name": "Main Assembly",
      "item_type": "assembly",
      "latest_revision_id": "revision-uuid",
      "latest_revision_number": 3,
      "created_at": "2026-11-11T13:40:00Z",
      "updated_at": "2026-11-11T13:40:00Z"
    }
  ]
}
```

### Create Item

**Endpoint:** `POST /workspaces/{workspace_id}/items`

Request:
```json
{
  "name": "Motor Assembly",
  "description": "Electric motor assembly v1",
  "item_type": "assembly"
}
```

Response (201 Created):
```json
{
  "id": "item-uuid",
  "name": "Motor Assembly",
  "item_type": "assembly",
  "created_at": "2026-11-11T13:40:00Z"
}
```

### Get Item

**Endpoint:** `GET /workspaces/{workspace_id}/items/{item_id}`

Response (200 OK):
```json
{
  "id": "item-uuid",
  "name": "Motor Assembly",
  "description": "Electric motor assembly v1",
  "item_type": "assembly",
  "created_by": "user-uuid",
  "latest_revision": {
    "id": "revision-uuid",
    "revision_number": 3,
    "created_at": "2026-11-11T13:40:00Z",
    "metadata": {
      "geometry": { },
      "assembly_tree": { }
    }
  }
}
```

### Update Item

**Endpoint:** `PUT /workspaces/{workspace_id}/items/{item_id}`

Request:
```json
{
  "name": "Motor Assembly v2",
  "description": "Updated description"
}
```

Response (200 OK):
```json
{
  "id": "item-uuid",
  "name": "Motor Assembly v2",
  "updated_at": "2026-11-11T13:40:00Z"
}
```

### Delete Item

**Endpoint:** `DELETE /workspaces/{workspace_id}/items/{item_id}`

Response (204 No Content)

## Item Revisions

Revisions track versions of items.

### List Item Revisions

**Endpoint:** `GET /workspaces/{workspace_id}/items/{item_id}/revisions`

Response (200 OK):
```json
{
  "total": 5,
  "items": [
    {
      "id": "revision-uuid",
      "revision_number": 3,
      "created_by": "user-uuid",
      "created_at": "2026-11-11T13:40:00Z",
      "message": "Added bearing assembly",
      "metadata": { }
    }
  ]
}
```

### Create Revision

**Endpoint:** `POST /workspaces/{workspace_id}/items/{item_id}/revisions`

Request:
```json
{
  "message": "Added bearing assembly",
  "metadata": {
    "geometry": {},
    "assembly_tree": {}
  }
}
```

Response (201 Created):
```json
{
  "id": "revision-uuid",
  "revision_number": 4,
  "created_at": "2026-11-11T13:40:00Z"
}
```

### Get Revision

**Endpoint:** `GET /workspaces/{workspace_id}/items/{item_id}/revisions/{revision_id}`

Response (200 OK):
```json
{
  "id": "revision-uuid",
  "revision_number": 3,
  "created_by": "user-uuid",
  "created_at": "2026-11-11T13:40:00Z",
  "message": "Added bearing assembly",
  "metadata": {
    "geometry": {},
    "assembly_tree": {}
  }
}
```

## File Management

Files are attachments to revisions (e.g., CAD files, images, documents).

### Upload File

**Endpoint:** `POST /workspaces/{workspace_id}/items/{item_id}/files`

Request: `multipart/form-data`

```
file: <binary file data>
description: "CAD model file"
file_type: "step"
```

Response (201 Created):
```json
{
  "id": "file-uuid",
  "filename": "motor.step",
  "file_type": "step",
  "size": 1024000,
  "created_at": "2026-11-11T13:40:00Z",
  "download_url": "/api/files/file-uuid/download"
}
```

### Download File

**Endpoint:** `GET /workspaces/{workspace_id}/files/{file_id}/download`

Response: File binary data with appropriate Content-Type header

### Delete File

**Endpoint:** `DELETE /workspaces/{workspace_id}/files/{file_id}`

Response (204 No Content)

## Health Check

**Endpoint:** `GET /health`

No authentication required.

Response (200 OK):
```json
{
  "status": "healthy",
  "database": "connected",
  "object_store": "connected",
  "timestamp": "2026-11-11T13:40:00Z"
}
```

## Error Responses

All error responses follow this format:

```json
{
  "error": "error_code",
  "message": "Human-readable error message",
  "detail": "Additional context (optional)"
}
```

### Common Error Codes

- **400** â€?Bad Request: Invalid parameters or request body
- **401** â€?Unauthorized: Missing or invalid authentication token
- **403** â€?Forbidden: Insufficient permissions for this resource
- **404** â€?Not Found: Resource does not exist
- **409** â€?Conflict: Resource already exists or state conflict
- **500** â€?Internal Server Error: Unexpected server error
- **503** â€?Service Unavailable: Service temporarily unavailable (e.g., database down)

Example error response:

```json
{
  "error": "item_not_found",
  "message": "Item with ID 'xyz' not found in workspace 'abc'",
  "detail": "Verify the item_id and workspace_id are correct"
}
```

## Pagination

All list endpoints support pagination via query parameters:

- `skip` (int, default 0) â€?Number of results to skip
- `limit` (int, default 20, max 100) â€?Maximum results per page

Response format:
```json
{
  "total": 150,
  "skip": 0,
  "limit": 20,
  "items": [ /* results */ ]
}
```

To fetch page 2:
```bash
GET /api/items?skip=20&limit=20
```

## Rate Limiting

- **Requests per minute:** 100 (per user/token)
- **Requests per hour:** 5000 (per user/token)

Rate limit headers:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1642358400
```

## Versioning

API version is included in the URL: `/api/v1/...`

Current version: **v1**

When breaking changes occur, a new version will be released (e.g., `/api/v2/...`) with both versions supported for a transition period.

## Webhook Events (Future)

Webhooks will be available for:
- Item created/updated/deleted
- Revision created
- File uploaded
- CAD processing started/completed

See documentation for webhook configuration and payload details (coming soon).

## SDK & Client Libraries

Official client libraries:

- **Python:** `pip install modelryx-sdk`
- **JavaScript/TypeScript:** `npm install modelryx-sdk`
- **REST:** Use curl, Postman, or any HTTP client

## Additional Resources

- **Swagger UI:** `/docs` (interactive API explorer)
- **OpenAPI Spec:** `/openapi.json` (machine-readable spec)
- **Architecture:** See [docs/architecture/](../architecture/)
- **Data Model:** See [docs/architecture/partmetadata-v1.md](../architecture/partmetadata-v1.md)
- **Development Guide:** See [DEVELOPMENT.md](../../DEVELOPMENT.md)

---

Last updated: 2026-11-11

