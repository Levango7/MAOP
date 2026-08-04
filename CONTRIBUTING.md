# Contributing to MAOP

Thank you for your interest in contributing to MAOP! This document outlines the process for contributing to the project.

## Development Setup

### Prerequisites

- Python >= 3.10
- Node.js >= 18 (for dashboard frontend)
- Git

### Initial Setup

```bash
# Clone the repository
git clone git@github.com:Levango7/MAOP.git
cd MAOP

# Create virtualenv and install dependencies
make install

# Install pre-commit hooks (optional but recommended)
pip install pre-commit
pre-commit install
```

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes

Follow the existing code style and conventions. The project uses:
- **ruff** for linting and formatting
- **mypy** for type checking
- **pytest** for testing

### 3. Run Checks

Before committing, ensure all checks pass:

```bash
# Lint
make lint

# Type check
cd py && python -m mypy maop/ --no-error-summary

# Tests
make test

# Frontend tests (if touching dashboard)
cd dashboard-enterprise && npm test
```

### 4. Commit

Write clear, conventional commit messages:

```
<type>(<scope>): <subject>

<body>
```

Types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `security`
Scopes: module name (e.g., `memory`, `mcp`, `enterprise`, `dashboard`)

Example:
```
feat(memory): add semantic cache eviction policy

LRU eviction with TTL fallback for episodic layer.
```

### 5. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a pull request on GitHub.

## Code Style

### Python

- Type hints required on all public functions
- `async`/`await` for I/O operations
- Pydantic v2 for data models
- No bare `except:` — always catch specific exceptions
- No `# type: ignore` without a comment explaining why

### TypeScript (Dashboard)

- Strict mode enabled
- Vue 3 Composition API with `<script setup>`
- No `any` types without explicit justification

### General

- No secrets, keys, or credentials in code
- No `print()` in library code — use `logging`
- Functions < 80 lines preferred; refactor if > 120
- Files < 500 lines preferred; split if > 800

## Testing

### Backend Tests

```bash
cd py && python -m pytest tests/ -q
```

- Unit tests in `py/tests/`
- Integration tests in `py/tests/integration/`
- Contract tests in `py/tests/contract/`
- Every new feature must include tests
- Every bug fix must include a regression test

### Frontend Tests

```bash
cd dashboard-enterprise && npm test
```

- Vitest + Vue Test Utils
- Tests co-located with components (`*.test.ts`)

## Security

- **Never commit secrets**: API keys, passwords, private keys, JWT secrets
- Use `.env` files (gitignored) for local configuration
- Report security vulnerabilities privately — do not open public issues
- All external input must be validated (Pydantic models preferred)
- SQL queries must use parameterized statements

## Project Structure

```
MAOP/
├── py/                 # Python backend
│   ├── maop/           # Main package
│   │   ├── core/       # Core orchestration (MCP hub, memory, agents)
│   │   ├── config/     # Configuration & edition management
│   │   ├── enterprise/ # Enterprise features (CRL, licensing, audit)
│   │   ├── memory/     # Memory subsystem
│   │   └── ...
│   └── tests/          # Test suite
├── dashboard-enterprise/  # Vue 3 frontend
├── monitoring/         # Prometheus, Grafana configs
├── docker-compose.yml  # Development
├── docker-compose.prod.yml  # Production
└── Makefile            # Development tasks
```

## Release Process

1. Update version in `py/pyproject.toml`
2. Update `CHANGELOG.md`
3. Tag: `git tag v<X.Y.Z>`
4. Push tags: `git push --tags`

## Questions?

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Be respectful and constructive in all discussions