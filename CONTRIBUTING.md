# Contributing to Modelryx

Thank you for your interest in contributing to Modelryx! This is an open-source engineering data and lifecycle platform, and we welcome contributions from the community.

## Code of Conduct

Be respectful, inclusive, and professional in all interactions. We are committed to providing a welcoming and inspiring environment for all participants.

## Getting Started

### Fork and Clone

1. **Fork the repository** on GitHub
   - Click the "Fork" button at https://github.com/Modelryx/Modelryx

2. **Clone your fork locally**
   ```bash
   git clone https://github.com/YOUR-USERNAME/Modelryx.git
   cd Modelryx
   ```

3. **Add upstream remote** (to stay in sync)
   ```bash
   git remote add upstream https://github.com/Modelryx/Modelryx.git
   git remote -v  # verify both origin and upstream are configured
   ```

### Set Up Development Environment

See [DEVELOPMENT.md](./DEVELOPMENT.md) for complete setup instructions.

Quick start:
```bash
# Configure .env secrets as described in DEVELOPMENT.md, then start services
docker compose up -d --build

# Start frontend dev server (in another terminal)
cd front-end && npm install && npm run dev
```

## Making Changes

### Create a Feature Branch

```bash
# Fetch latest changes
git fetch upstream
git checkout main
git merge upstream/main

# Create feature branch (use descriptive name)
git checkout -b fix/issue-description
# or
git checkout -b feature/new-capability
```

### Code Style Guide

#### Python (Server & Workers)

- **Python version:** 3.10+
- **Style:** PEP 8 with `black` formatting
- **Type hints:** Use type hints for all function signatures and module-level variables
- **Docstrings:** Use Google-style docstrings for modules, classes, and public functions

Example:
```python
from typing import Optional

def process_cad_model(model_id: str, options: Optional[dict] = None) -> dict:
    """Process a CAD model and return metadata.
    
    Args:
        model_id: Unique identifier for the model.
        options: Optional processing configuration.
    
    Returns:
        Dictionary containing processed model metadata.
    
    Raises:
        ValueError: If model_id is empty.
    """
    if not model_id:
        raise ValueError("model_id cannot be empty")
    return {}
```

- **Imports:** Group in order: standard library, third-party, local imports (separated by blank lines)
- **Max line length:** 100 characters
- **String quotes:** Use double quotes for strings

#### TypeScript / React (Frontend)

- **TypeScript version:** 5.x+
- **Style:** ESLint + Prettier (config in front-end/package.json)
- **Type safety:** Strict mode enabled; avoid `any` type
- **Component naming:** PascalCase for components, camelCase for utilities
- **File naming:** Match component name (e.g., `ItemSearch.tsx` for component `ItemSearch`)

Example:
```typescript
interface SearchProps {
  query: string;
  onSearch: (results: SearchResult[]) => void;
  isLoading?: boolean;
}

export function ItemSearch({ query, onSearch, isLoading }: SearchProps): React.ReactElement {
  // Implementation
  return <div>Search component</div>;
}
```

- **Imports:** Organize logically (React, UI libraries, local components, utilities)
- **Props:** Define interfaces ending with `Props`
- **CSS:** Follow the existing stylesheets and component conventions in `front-end/src`.

#### SQL & Database

- **Query style:** Use uppercase keywords (SELECT, FROM, WHERE, JOIN)
- **Naming:** Snake_case for table/column names
- **Comments:** Document non-obvious logic

Use mapped SQLAlchemy entities and the actual constraints/indexes in `server/src/server/modelryx/models`.
Do not add copied workspace/revision DDL that does not match the current models. Database initialization
creates a fresh schema or verifies a matching one; schema changes need a qualified upgrade process.

### Commit Messages

Use clear, descriptive commit messages:

- **Format:** `<type>: <description>`
- **Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
- **Description:** Start with lowercase, present tense, concise (50 chars or less)
- **Body (optional):** Explain *why*, not *what* (wrapped at 72 chars)

Examples:
```
feat: add metadata normalization for STEP imports
fix: handle null geometry in CAD worker dispatcher
docs: update FreeCAD deployment modes
test: add tests for worker HTTP contract validation
```

### Testing Requirements

Before submitting a pull request:

1. **Run relevant tests** (see [testing guide](./docs/development/testing.md))
   ```bash
   # Python (server & workers)
   docker compose -p modelryx-check --env-file .env.example --profile test run --build --rm test pytest -q
   
   # TypeScript (frontend)
   cd front-end && npm run typecheck && npm run build
   ```

2. **Add tests for your changes**
   - Unit tests for new functions/components
   - Integration tests for API endpoints
   - E2E tests for user workflows (if applicable)

3. **Verify no regressions**
   ```bash
   # Select the suites appropriate to the change from docs/development/testing.md
   ```

4. **Lint your code**
   ```bash
   # Python
   python -m ruff check server/src/server server/tests
   python -m ruff format --check server/src/server server/tests
   
   # TypeScript
   cd front-end && npm run lint
   ```

## Submitting a Pull Request

### Before You Push

1. **Ensure your branch is up to date**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Test locally** (see Testing Requirements above)

3. **Verify code quality**
   - No linting errors
   - Type checking passes
   - Tests pass

### Create the PR

1. **Push your branch**
   ```bash
   git push origin fix/issue-description
   ```

2. **Open a Pull Request on GitHub**
   - Use a clear title: `feat: add worker health check endpoint`
   - Reference related issues: `Closes #123`
   - Describe what changed and why
   - Include screenshots for UI changes

### PR Description Template

```markdown
## Description
Brief explanation of changes and why they were made.

## Related Issue
Closes #ISSUE_NUMBER

## Changes
- Change 1
- Change 2

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests passed
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guide
- [ ] Documentation updated (if needed)
- [ ] No breaking changes
- [ ] Tests pass locally
```

## Code Review Process

1. **Maintainers review** your PR for:
   - Code quality and consistency
   - Test coverage
   - Architecture and design
   - Documentation
   - Security concerns

2. **Respond to feedback**
   - Engage in discussion respectfully
   - Implement requested changes
   - Push updates to the same branch

3. **Merge**
   - Maintainers merge the PR once approved
   - Branch is automatically deleted

## Areas Where We Need Help

- **Documentation:** Help improve docs, add examples, fix typos
- **Testing:** Expand test coverage, improve test reliability
- **Performance:** Profile and optimize hot paths
- **Bug fixes:** Report issues with clear reproduction steps
- **New features:** Check open issues for feature requests, discuss large changes first
- **CAD workers:** Extend worker SDK, add new worker implementations
- **Translations:** Internationalize UI and documentation

## Reporting Bugs

1. **Check existing issues** first
2. **Create a new issue** with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Python/Node version, etc.)
   - Error logs or screenshots

## Requesting Features

1. **Check open issues/discussions**
2. **Open an issue** describing:
   - The use case or problem you're solving
   - Proposed solution
   - Alternative approaches you've considered
   - Any potential impacts

## Questions?

- **GitHub Issues:** For bug reports and features
- **GitHub Discussions:** For questions and general discussion
- **Documentation:** See [docs/](./docs/) for architecture and API details

## License

By contributing to Modelryx, you agree that your contributions will be licensed under the same license as the project.

---

Thank you for making Modelryx better! 🚀
