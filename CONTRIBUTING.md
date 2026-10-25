# Contributing to ConsentOS

Thanks for your interest in contributing! This document explains how to get started.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

### Prerequisites

- Docker and Docker Compose v2.15+
- Node.js 20+
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)

### Local Setup

```bash
# Clone the repository
git clone https://github.com/consentos/consentos.git
cd consentos

# Copy the example environment file
cp .env.example .env

# Start all services
make up

# Run database migrations
make migrate

# Seed the known cookies database
make seed

# Verify everything is running
# API:      http://localhost:8000/docs
# Admin UI: http://localhost:5173
# CDN:      http://localhost:8080
```

### Running Tests

```bash
# Start test infrastructure (PostgreSQL + Redis)
make test-infra-up

# Run API tests
make test

# Run with coverage
make test-cov

# Run banner tests
cd apps/banner && npm test

# Run admin UI tests
cd apps/admin-ui && npm test

# Run scanner tests (the consent check tests need Chromium)
cd apps/scanner && playwright install chromium && pytest

# Stop test infrastructure
make test-infra-down
```

#### Consent check test pages

`apps/scanner/tests/consent_pages.py` holds small pages that the consent checks run against in a real browser: one set up correctly, and broken ones that each change one thing. To add one, add a field to `Setup` for the new behaviour if it needs one, handle it in `render()`, and add a `Case` with the checks that should come out differently from the correct page. Every check not listed must pass.

## Making Changes

### Branch Naming

Create a branch from `master` using the convention:

```
<type>/<short-description>
```

Examples: `feat/add-geo-rules`, `fix/consent-cookie-expiry`, `docs/api-examples`

### Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add regional consent mode overrides
fix: correct TC string encoding for vendor consents
docs: document compliance rule engine
chore: update Python dependencies
refactor: simplify cookie classification pipeline
test: add integration tests for scanner API
```

### Code Standards

- **Python:** Type hints everywhere. Linted with Ruff, type-checked with MyPy (strict mode)
- **TypeScript:** Strict mode enabled. Linted with ESLint
- **SQL:** CTEs over subqueries, explicit column lists (no `SELECT *`)
- **Language:** British English in all prose, comments, and UI strings

### Before Submitting

1. Run `make check` (lint + tests) and ensure it passes
2. Add or update tests for any changed behaviour
3. Ensure no secrets or credentials are committed

## Pull Requests

- Keep PRs focused — one logical change per PR
- Write a clear title (under 70 characters) and description
- Link to any related issues
- All CI checks must pass before merge
- PRs require at least one approving review

## Reporting Issues

- Use [GitHub Issues](https://github.com/consentos/consentos/issues) for bugs and feature requests
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

## Licence

By contributing, you agree that your contributions will be licensed under the [Elastic License 2.0](LICENSE).
