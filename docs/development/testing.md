# Testing Strategy and Guide

This document outlines the testing approach, frameworks, and best practices for Modelryx.

## Testing Overview

Modelryx uses a multi-layered testing strategy:

- **Unit Tests** — Test individual functions and components in isolation
- **Integration Tests** — Test multiple components working together
- **End-to-End (E2E) Tests** — Test complete user workflows
- **Performance Tests** — Verify performance under load
- **Security Tests** — Check for common vulnerabilities

## Test Coverage Goals

- **Overall:** ≥ 80% code coverage
- **Critical paths:** ≥ 95% coverage (auth, data processing, API)
- **UI components:** ≥ 70% coverage

Current coverage is monitored in CI/CD pipeline.

## Python Backend Testing

### Framework: pytest

**Installation:**
```bash
cd server
pip install pytest pytest-cov pytest-asyncio pytest-xdist
```

### Test Structure

```
server/
├── src/
│   └── server/
│       ├── models.py
│       ├── routes/
│       └── services/
└── tests/
    ├── conftest.py           # Shared fixtures
    ├── test_auth.py          # Authentication tests
    ├── test_items.py         # Item CRUD tests
    ├── test_revisions.py     # Revision management tests
    ├── test_cad_workers.py   # Worker integration tests
    └── fixtures/
        ├── sample_data.py    # Test data
        └── db.py             # Database fixtures
```

### Running Tests

```bash
cd server

# Run all tests
pytest

# Run specific test file
pytest tests/test_items.py

# Run specific test
pytest tests/test_items.py::test_create_item

# Run with verbose output
pytest -vv

# Run with coverage report
pytest --cov=src --cov-report=html

# Run in parallel (faster)
pytest -n auto

# Run only failed tests
pytest --lf

# Run only tests that match pattern
pytest -k "item" -v
```

### Example Unit Test

```python
# tests/test_items.py
import pytest
from src.server.models import Item
from src.server.services.item_service import create_item

@pytest.fixture
def sample_workspace_id():
    return "workspace-uuid-123"

@pytest.fixture
def sample_user_id():
    return "user-uuid-456"

def test_create_item(db_session, sample_workspace_id, sample_user_id):
    """Test creating a new item."""
    item_data = {
        "name": "Test Motor",
        "description": "A test motor assembly",
        "item_type": "assembly"
    }
    
    item = create_item(
        workspace_id=sample_workspace_id,
        user_id=sample_user_id,
        data=item_data,
        db=db_session
    )
    
    assert item.id is not None
    assert item.name == "Test Motor"
    assert item.item_type == "assembly"
    assert item.created_by == sample_user_id

def test_create_item_invalid_name(db_session, sample_workspace_id, sample_user_id):
    """Test that creating item with empty name fails."""
    with pytest.raises(ValueError, match="Item name cannot be empty"):
        create_item(
            workspace_id=sample_workspace_id,
            user_id=sample_user_id,
            data={"name": "", "item_type": "part"},
            db=db_session
        )
```

### Example Integration Test

```python
# tests/test_items.py
def test_item_workflow(client, authenticated_user, sample_workspace_id):
    """Test complete item creation and revision workflow."""
    
    # Create item
    response = client.post(
        f"/api/workspaces/{sample_workspace_id}/items",
        headers=authenticated_user.headers,
        json={
            "name": "Assembly",
            "item_type": "assembly",
            "description": "Test assembly"
        }
    )
    assert response.status_code == 201
    item = response.json()
    item_id = item["id"]
    
    # Get item
    response = client.get(
        f"/api/workspaces/{sample_workspace_id}/items/{item_id}",
        headers=authenticated_user.headers
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Assembly"
    
    # Create revision
    response = client.post(
        f"/api/workspaces/{sample_workspace_id}/items/{item_id}/revisions",
        headers=authenticated_user.headers,
        json={"message": "Initial revision"}
    )
    assert response.status_code == 201
    
    # Update item
    response = client.put(
        f"/api/workspaces/{sample_workspace_id}/items/{item_id}",
        headers=authenticated_user.headers,
        json={"name": "Assembly v2"}
    )
    assert response.status_code == 200
    
    # Delete item
    response = client.delete(
        f"/api/workspaces/{sample_workspace_id}/items/{item_id}",
        headers=authenticated_user.headers
    )
    assert response.status_code == 204
```

### Database Fixtures

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from src.server.database import Base

@pytest.fixture(scope="session")
def db_engine():
    """Create in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine

@pytest.fixture
def db_session(db_engine):
    """Provide database session for each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    
    yield session
    
    # Rollback transaction after test
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture
def client(db_session):
    """Provide FastAPI test client."""
    from fastapi.testclient import TestClient
    from src.server.main import app
    
    return TestClient(app)

@pytest.fixture
def authenticated_user(client, db_session):
    """Create test user and return authenticated client."""
    # Create user
    response = client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "password123",
        "name": "Test User"
    })
    
    # Login
    response = client.post("/api/auth/login", json={
        "email": "test@example.com",
        "password": "password123"
    })
    token = response.json()["access_token"]
    
    # Return user with headers
    class AuthenticatedUser:
        headers = {"Authorization": f"Bearer {token}"}
    
    return AuthenticatedUser()
```

## Frontend Testing

### Frameworks: Vitest, React Testing Library

**Installation:**
```bash
cd front-end
npm install --save-dev vitest @testing-library/react @testing-library/jest-dom
```

### Test Structure

```
front-end/
├── src/
│   ├── components/
│   ├── pages/
│   ├── hooks/
│   └── services/
└── src/
    ├── components/
    │   └── __tests__/
    │       └── ItemSearch.test.tsx
    ├── pages/
    │   └── __tests__/
    │       └── Workspace.test.tsx
    └── hooks/
        └── __tests__/
            └── useItems.test.tsx
```

### Running Tests

```bash
cd front-end

# Run all tests
npm test

# Run tests in watch mode
npm test -- --watch

# Run specific test file
npm test -- src/components/__tests__/ItemSearch.test.tsx

# Run with coverage
npm test -- --coverage

# Update snapshots
npm test -- -u
```

### Example Component Test

```typescript
// src/components/__tests__/ItemSearch.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { ItemSearch } from '../ItemSearch';

describe('ItemSearch', () => {
  it('renders search input', () => {
    render(<ItemSearch onSearch={() => {}} />);
    const input = screen.getByPlaceholderText('Search items...');
    expect(input).toBeInTheDocument();
  });

  it('calls onSearch when form is submitted', () => {
    const onSearch = vi.fn();
    render(<ItemSearch onSearch={onSearch} />);
    
    const input = screen.getByPlaceholderText('Search items...');
    fireEvent.change(input, { target: { value: 'motor' } });
    
    const form = input.closest('form')!;
    fireEvent.submit(form);
    
    expect(onSearch).toHaveBeenCalledWith('motor');
  });

  it('displays loading state', () => {
    render(<ItemSearch isLoading onSearch={() => {}} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
```

### Example Hook Test

```typescript
// src/hooks/__tests__/useItems.test.tsx
import { renderHook, waitFor } from '@testing-library/react';
import { useItems } from '../useItems';

describe('useItems', () => {
  it('fetches items on mount', async () => {
    const { result } = renderHook(() => useItems('workspace-123'));
    
    expect(result.current.isLoading).toBe(true);
    
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    
    expect(result.current.items).toHaveLength(3);
  });
});
```

## CAD Worker Testing

### Testing CAD Processing

```python
# tests/test_cad_workers.py
import pytest
from src.workers.freecad.processor import FreeCADProcessor

@pytest.fixture
def freecad_processor():
    return FreeCADProcessor()

def test_process_step_file(freecad_processor, tmp_path):
    """Test STEP file processing."""
    # Create sample STEP file
    step_file = tmp_path / "test.step"
    step_file.write_bytes(b"... step file content ...")
    
    # Process file
    result = freecad_processor.process(str(step_file))
    
    # Verify output
    assert result["status"] == "success"
    assert "geometry" in result
    assert "assembly_tree" in result
    assert result["geometry"]["bounds"] is not None

def test_invalid_file_handling(freecad_processor, tmp_path):
    """Test error handling for invalid files."""
    invalid_file = tmp_path / "invalid.txt"
    invalid_file.write_text("not a CAD file")
    
    result = freecad_processor.process(str(invalid_file))
    
    assert result["status"] == "error"
    assert "error_message" in result
```

## E2E Testing

### Framework: Playwright (Optional)

```bash
cd front-end
npm install --save-dev @playwright/test
```

### Example E2E Test

```typescript
// e2e/workspace.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Workspace Creation', () => {
  test('user can create and view workspace', async ({ page }) => {
    // Login
    await page.goto('http://localhost:5174/login');
    await page.fill('input[name="email"]', 'test@example.com');
    await page.fill('input[name="password"]', 'password123');
    await page.click('button[type="submit"]');
    
    // Wait for dashboard
    await page.waitForURL('**/dashboard');
    
    // Create workspace
    await page.click('button:has-text("New Workspace")');
    await page.fill('input[name="name"]', 'E2E Test Workspace');
    await page.fill('input[name="description"]', 'Test workspace');
    await page.click('button:has-text("Create")');
    
    // Verify workspace created
    await expect(page).toHaveURL('**/workspaces/**');
    await expect(page.locator('h1')).toContainText('E2E Test Workspace');
  });
});
```

## Continuous Integration

Tests run automatically on:
- **Push to main:** Full test suite
- **Pull requests:** Relevant test suite
- **Scheduled:** Nightly full suite + performance tests

See `.github/workflows/test.yml` for CI configuration.

## Performance Testing

### Load Testing (Optional)

```bash
# Install locust
pip install locust

# Run load test
locust -f tests/load_tests.py --host http://localhost:8000
```

Example load test:

```python
# tests/load_tests.py
from locust import HttpUser, task, constant

class ModelryxUser(HttpUser):
    wait_time = constant(1)
    
    def on_start(self):
        # Login
        response = self.client.post("/auth/login", json={
            "email": "test@example.com",
            "password": "password123"
        })
        self.token = response.json()["access_token"]
    
    @task
    def list_items(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        self.client.get(
            "/workspaces/workspace-id/items",
            headers=headers
        )
```

## Best Practices

1. **Isolate tests** — Each test should be independent
2. **Use fixtures** — Reuse common setup across tests
3. **Mock external services** — Don't depend on external APIs
4. **Clear test names** — Describe what is being tested
5. **Test happy path and error cases** — Both success and failure
6. **Keep tests fast** — Aim for < 100ms per test
7. **Avoid flaky tests** — Use proper waits, don't rely on timing
8. **Document complex tests** — Explain non-obvious test logic
9. **Review test coverage** — Identify gaps and add tests
10. **Refactor tests** — Keep test code clean and maintainable

## Debugging Tests

```bash
# Python: Drop into debugger
pytest tests/test_items.py -v -s --pdb

# Python: See print statements
pytest -s

# TypeScript: Debug in VS Code
# Add breakpoint in test file, then run with debugger
```

## Common Testing Issues

### Database Locking
- Use in-memory SQLite for tests instead of PostgreSQL
- Each test runs in isolated transaction

### Async Tests
- Use `pytest-asyncio` for async Python tests
- Use `@testing-library/react` built-in async utilities for React

### External Dependencies
- Mock HTTP requests with `responses` (Python) or `msw` (TypeScript)
- Mock file I/O for file upload tests

## Related Documentation

- **Development Guide:** [DEVELOPMENT.md](../../DEVELOPMENT.md)
- **API Reference:** [API.md](./API.md)
- **Architecture:** [docs/architecture/](../architecture/)

---

Last updated: 2026-11-11
