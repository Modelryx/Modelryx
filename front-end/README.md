# Modelryx new frontend

The new frontend uses the Modelryx API for sign-in, Home, Library, engineering search, Recent, Favorites, Workspace, Item and revision workspaces, staging, engineering editing, reviews, comparison, notifications, CAD review, AI, and administration. Revision sections include versions, scoped attachments, artifacts, properties, structure, relationships, history, and 3D/2D previews. Library views are account-backed; administrators can publish views for all users. Existing browser-local views remain available until saved to the server. See [the API reference](../docs/development/api.md) and the running OpenAPI schema for engineering contracts.

Run `npm ci` and `npm run dev` in this folder, then open http://localhost:5173. The API must allow the frontend origin via `MODELRYX_CORS_ORIGINS`; configure additional LAN origins explicitly and recreate the API container. Enter the reachable API address in the sign-in form. Run `npm run typecheck` and `npm run build` to verify.

The development server binds to loopback by default. See [tooling security](../docs/development/tooling-security.md) for deliberate LAN access and the current development-dependency advisory.

`DataTable` accepts TanStack column definitions, data, a stable `getRowId`, and optional per-row actions. It provides column visibility and sizing, sorting, client-side pagination, loading/error/empty states, right-click menus, Shift+F10 and an action button. The Library uses server-side pagination, sorting and multi-select filters. Structure loads one assembly level at a time; other tables paginate and sort supplied rows locally. Set `enableHiding: false` for required columns.

The 3D preview implementation lives in `src/viewer`. PDF previews use the browser PDF viewer.

The 3D preview includes an occurrence navigation list and a right-click menu; changes to selection and visibility affect the viewer only. The hierarchy is shown when the preview package or metadata provides it.

The revision workspace has a collapsible AI Assistant drawer backed by the read-only
`/ai/assistant/messages` API. It sends the current Item and Revision identifiers on each question.
For assemblies, only uniquely mapped managed BOM occurrences are sent as selected-component context;
viewer highlight and isolate actions use that same mapping. Historical content versions disable the
assistant because the current assistant API has no snapshot-specific context. The standalone AI page
supports broad indexed questions and links evidence to the authorized Item Revision.
