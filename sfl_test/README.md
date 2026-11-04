# SFL Regression Tests

This is an independent uv project that depends on `sfl-lang`. Its own `pyproject.toml`, `uv.lock`, and `.venv` keep test dependencies separate from the language project.

## Run

From this directory:

```powershell
uv sync --locked
uv run --locked pytest
```

From the repository root:

```powershell
uv run --project sfl_test --locked pytest sfl_test/tests
```

The `sfl-lang` dependency uses the parent directory as an editable uv source. Changes to the interpreter are visible immediately, without publishing a package or changing `sys.path`. To test another checkout or a released package, update or remove `[tool.uv.sources]`, then run `uv lock` and `uv sync --locked`.

## Scope

- Source rendering: exact output, operator precedence, association, keyword boundaries, numeric member access, deterministic randomized AST round trips, and removal of individual parenthesis pairs to verify their necessity.
- Function representations: reconstruction, captures, null bindings, nested closures, dynamic environments, helper-name collisions, indentation, explicit `with`, `self`, and variadic `args`.
- Language behavior: byte-string updates, type names, reserved identifiers, imports, raises, exceptions, and persistent dictionaries.
- Signals and standard libraries: composition and precedence, registration changes, permissions, standard I/O, and `str.chr`/`str.ord`.
- Serialization and caching: source metadata, token/AST round trips, invalid input, corruption, content changes, timestamp-only updates, cache hits, and forced rebuilds.
- CLI: `lex`, `ast`, `run`, diagnostics, exports, and test-owned SFL examples.

The previous temporary repr validation scripts are split across these tests. Randomized cases use the fixed seed `20261102`; pytest reports each generated case separately. To rerun a failure, copy its node ID from pytest's output.

All generated sources and caches live in pytest temporary directories. Standard libraries are copied from the installed `sfl` package before use. Tests do not require Git, GitHub, network access after dependency installation, a particular user directory, or the editable files under `sample_source`.

The test modules are `test_render.py`, `test_repr.py`, `test_language.py`, `test_signals.py`, `test_lexer_formats.py`, and `test_cache_cli.py`. Shared fixtures and serialization helpers live in `conftest.py` and `support.py`. New regressions belong in the module for the affected behavior.

## Focused Runs

```powershell
uv run --locked pytest tests/test_render.py
uv run --locked pytest tests/test_repr.py
uv run --locked pytest tests/test_cache_cli.py
uv run --locked pytest -k keyword_boundary
uv run --locked pytest -x -vv
```
