# Modelryx Development Setup

## Architecture

Modelryx uses a split development model:
- **Backend (Python/FastAPI)**: Runs in Docker with PostgreSQL and worker services
- **Frontend (React/Vite)**: Runs natively on your host machine for fast development

## Quick Start

### 1. Start the backend services:
```bash
docker compose up -d
```

This starts:
- PostgreSQL database
- FastAPI server (http://localhost:8000)
- Background worker for file processing

### 2. Run the frontend development server:
```bash
cd ../front
npm install  # First time only
npm run dev
```

Access the app at **http://localhost:5173/** with hot reload.

### 3. Login to the application:

When you first open the app, you'll see a login dialog:
- **Server URL**: `http://localhost:8000` (or your backend URL)
- **Username**: Optional (no validation yet)
- **Password**: Optional (no validation yet)

Click "Connect" to proceed. The server URL is saved and used for all API calls.

## How It Works

- Frontend dev server (Vite) runs on port 5173
- API calls use the configured server URL from the login dialog
- Edit web code → instant reload (no Docker rebuild)
- Backend stays in Docker with all dependencies isolated
- Both run simultaneously during development

## Testing

For a local Python installation, install the worker SDK before the server:

```bash
python -m pip install -e ./worker-sdk
python -m pip install -e './server[dev]'
```

Run these commands from the repository root. Build the server image from the same root with
`docker build -f server/Dockerfile .` so the build includes the SDK.

```bash
# Run backend tests
docker compose run --rm test

# Run frontend tests (if applicable)
cd ../front
npm test
```

## Troubleshooting

### Access from another computer on the LAN

Open `http://<server-ip>:5173/` and set the login dialog's **Server URL** to
`http://<server-ip>:8000`. `localhost` refers to the computer running the browser.

Allow the frontend's exact origin in the repository root `.env` used by Docker
Compose, preserving any other origins you use. For example:

```dotenv
MODELRYX_CORS_ORIGINS=["http://localhost:5173","http://localhost:5174","http://192.168.50.53:5173"]
```

Apply the change with `docker compose up -d --no-deps api` from the repository root.
A plain container restart does not apply changed Compose environment variables.
For a backend started directly from `server/`, configure `server/.env` instead and
restart that process. Ensure port 8000 is reachable from the client computer.
An OPTIONS response containing `Disallowed CORS origin` means the frontend origin
is missing from the API allowlist; check the scheme, hostname and port.

**API requests failing:**
```bash
docker compose ps              # Check services are running
curl http://localhost:8000/health/live  # Test backend
```

**Change server connection:**
- Click "Change Server" button in the header
- Enter new server URL and reconnect

**Port conflicts:**
- Vite (5173): Will auto-select next available port
- API (8000): Change in compose.yaml if needed
