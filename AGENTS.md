# Phosphor EDA — Agent Reference

Python 3.13+ package and CLI for parsing, inspecting, rendering, and querying EDA projects.

## Layout

```
src/phosphor_eda/ — package and CLI
tests/            — pytest suite and in-tree fixtures
tests/upstream/   — external fixture repositories as Git submodules
typings/          — local type stubs
```

## Commands

```shell
git submodule update --init --depth 1 --jobs 8
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked basedpyright
uv run --locked pytest
uv run --locked uv-secure uv.lock
uv build --no-sources
```

## Working rules

- Read the relevant code and existing tests before changing behavior.
- Discuss questions, problems, and options before editing. Act directly on explicit requests.
- Treat warnings as errors. Fix discovered problems rather than dismissing them as pre-existing.
- Preserve unrelated user changes. Do not stash during merges, rebases, or history rewrites.
- Use type hints on every function signature. Do not use `# type: ignore`; fix the type issue.
- Avoid imports inside functions. Surface or correct circular dependencies.
- Never swallow errors; surface, log, or re-raise them with useful context.

## Testing and fixtures

- Follow TDD for behavior changes and prefer valuable integration coverage over test count.
- Use pytest fixtures and match the existing test organization.
- Run the behavior locks when parser, SQL, serialization, or full-project output can change:

  ```shell
  uv run --locked pytest tests/test_sql_behavior_lock.py --run-behavior-locks
  ```

- Before adding fixtures, inspect the existing patterns under `tests/fixtures/` and
  `tests/upstream/`.
- Add external projects as submodules when possible. Preserve their upstream tree, license, and
  provenance rather than copying isolated design files.
- Do not relicense third-party fixtures. Ask before adding material with unclear licensing.

## Packaging and release

- `pyproject.toml` and `uv.lock` are the dependency and package metadata sources of truth.
- Keep fixture submodules out of built distributions.
- Depot CI lives in `.depot/workflows/ci.yml`.
- `.github/workflows/publish.yml` publishes through PyPI Trusted Publishing when a GitHub Release
  tag exactly matches the project version. Do not publish or create a release unless asked.

## Git

- Commit one logical concern at a time with no AI attribution.
- Do not rewrite shared history or force-push without explicit approval.
