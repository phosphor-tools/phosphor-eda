# Contributing

Thanks for your interest in phosphor-eda. This project targets Python 3.13+ and
uses [uv](https://docs.astral.sh/uv/).

## Development setup

```shell
git submodule update --init --depth 1 --jobs 8
uv sync --locked
```

The repositories under `tests/upstream/` are external fixture projects managed
as Git submodules; the init step above populates them.

## Checks

Run the full check suite before opening a pull request:

```shell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked basedpyright
uv run --locked pytest
uv run --locked uv-secure uv.lock
uv build --no-sources
```

Behavior-lock tests are skipped by default. Run them when your change can
affect parser, SQL, serialization, or full-project output:

```shell
uv run --locked pytest tests/test_sql_behavior_lock.py --run-behavior-locks
```

## Pull requests

- Keep each pull request focused on one logical concern.
- Add tests for behavior changes and update fixtures following the existing
  patterns under `tests/`.
- Use type hints on every function signature; do not add `# type: ignore` —
  fix the underlying type issue.
- Do not relicense or copy third-party fixtures; add external projects as
  submodules and preserve their upstream tree, license, and provenance.
- Write concise, purposeful commit messages with no AI attribution.
