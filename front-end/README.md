# Modelryx frontend

The new frontend uses the Modelryx API for sign-in, Home, Library, engineering search, Recent, Favorites, Workspace, Item and revision workspaces, staging, engineering editing, reviews, comparison, notifications, CAD review, AI, and administration. Revision sections include versions, scoped attachments, artifacts, properties, structure, relationships, history, and 3D/2D previews. Library views are account-backed; administrators can publish views for all users. Existing browser-local views remain available until saved to the server. See [the API reference](../docs/development/api.md) and the running OpenAPI schema for engineering contracts.

## Local development

Use Node.js 22.12+ and start the backend using the [root setup guide](../README.md#quick-start).
Run from `front-end/`:

```sh
npm ci
npm run dev
```

Open http://localhost:5173 and enter the reachable API address in the sign-in form
(default: `http://localhost:8000`). Allow the frontend origin through the API's
`MODELRYX_CORS_ORIGINS`; recreate affected API containers after changing Compose settings.

## Checks and production build

```sh
npm run typecheck
npm run lint
npm run test:api
npm run test:upload
npm run build
```

The build writes `dist/`. Configure the deployment web server for single-page
application routing and allow the deployed frontend origin in the API's CORS settings.

The development server binds to loopback by default. See [tooling security](../docs/development/tooling-security.md) for deliberate LAN access and the current development-dependency advisory.

## Tables and previews

`DataTable` accepts TanStack column definitions, data, a stable `getRowId`, and optional per-row actions. It provides column visibility and sizing, sorting, client-side pagination, loading/error/empty states, right-click menus, Shift+F10 and an action button. The Library uses server-side pagination, sorting and multi-select filters. Structure loads one assembly level at a time; other tables paginate and sort supplied rows locally. Set `enableHiding: false` for required columns.

The 3D preview implementation lives in `src/viewer`. PDF previews use the browser PDF viewer.

The 3D preview includes an occurrence navigation list and a right-click menu; changes to selection and visibility affect the viewer only. The hierarchy is shown when the preview package or metadata provides it.

## AI assistant

The revision workspace has a collapsible AI Assistant drawer backed by the read-only
`/ai/assistant/messages` API. It sends the current Item and Revision identifiers on each question.
For assemblies, only uniquely mapped managed BOM occurrences are sent as selected-component context;
viewer highlight and isolate actions use that same mapping. Historical content versions disable the
assistant because the current assistant API has no snapshot-specific context. The standalone AI page
supports broad indexed questions and links evidence to the authorized Item Revision.
