# Python Project Standards

Apply these instructions to all Python work in this repository.

## Python and environment

- Target Python 3.10 or newer. Use the more specific version declared by the
  project when applicable.
- Use Poetry or pip for dependency management, following the repository's existing
  dependency files and workflow.
- Always use the project-local `.venv` virtual environment; never install project
  dependencies globally.
- Declare dependencies in `pyproject.toml` or `requirements.txt`, according to the
  existing project setup.

## Code style and typing

- Follow PEP 8.
- Format Python with Black using a line length of 88 characters.
- Lint with Ruff when it is configured; otherwise use Ruff or Flake8.
- Add type hints to all function and method parameters and return values.
- Run mypy when it is configured for the project.
- Avoid unrelated formatting changes during focused work.

## Architecture and layout

- Keep source code under `src/` and tests under `tests/`.
- Keep modules cohesive and separate business logic from entry points such as CLI
  commands, FastAPI routers, and executable scripts.
- Put reusable domain behavior in independently testable modules.
- Limit entry points to input parsing, dependency wiring, invoking business logic,
  and presenting results.

## Error handling and logging

- Never use a bare `except:` clause. Catch the narrowest useful exception type.
- Use the standard `logging` module for diagnostic and operational output instead
  of `print()`.
- Define and raise meaningful custom exceptions for domain-specific failures.
- Preserve exception context with `raise ... from ...` when translating lower-level
  failures into domain exceptions.

## Testing

- Write unit and integration tests with pytest.
- Prioritize high coverage of core business logic and important failure paths.
- Mock external APIs and database connections. Prefer `pytest-mock` for general
  collaborators and `responses` or the HTTP-mocking library already used by the
  project for network calls.
- Keep unit tests deterministic and independent of live network and database
  services.
- Run the smallest relevant test set first, followed by the full test suite when
  practical.
