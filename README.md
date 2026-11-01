# SFL Python Implementation

An SFL lexer, serialized AST compiler, and interpreter written in Python. The project uses uv to manage its environment and lockfile, pins Python 3.13 for development, and supports Python 3.11+. It has no third-party runtime dependencies. The Python package lives in `sfl/`, and example programs live in `sample_source/`.

## Usage

Initialize the environment and run these commands from the project root:

```powershell
uv sync --locked
uv run python -m sfl lex "sample_source/code.sfl"
uv run python -m sfl ast "sample_source/code.sfl"
uv run python -m sfl run "sample_source/fib.sfl"
```

`uv sync` creates `.venv` and installs the project in editable mode. Keep `uv.lock` under version control. Subsequent `uv run` commands check and synchronize the environment automatically. You can also activate the environment and use `python -m sfl` directly:

```powershell
.\.venv\Scripts\Activate.ps1
python -m sfl --help
```

From another working directory, use `uv run --project "path/to/sfl" python -m sfl ...`. There is no need to run pip manually.

| Command | Behavior |
| --- | --- |
| `lex` | Generate or reuse `sflcache/<source-stem>.sflex` beside the source file. |
| `ast` | Ensure the token cache exists, then generate or reuse `.sflast` from the token file. |
| `run` | Ensure both caches exist, then read and execute the serialized AST. |

Generated caches go in `sflcache/`. Existing handwritten format examples under `sample_source/__sflcache__/`, if present, are left untouched. All `.sflex` and `.sflast` files are excluded from version control. Relative source paths are resolved against the current working directory; caches always live beside the source. Paths may contain spaces and non-ASCII characters.

```powershell
uv run python -m sfl run "demo.sfl" --verbose
uv run python -m sfl ast "demo.sfl" --force
uv run python -m sfl run "demo.sfl" -I "path/to/libraries" --print-result
```

`--verbose` reports `BUILD`, `HIT`, and `REFRESH` events on stderr. `--force` rebuilds the caches. By default, `run` only outputs the program's I/O; `--print-result` also prints the module's export dictionary. Exit codes are 0 for success, 1 for source, compilation, or runtime errors, and 2 for invalid command-line arguments.

## Example Program

```sfl
import "stdio" as io

factorial := fn (n) {
    return if n <= 1 then {
        return 1
    } else {
        return n * self(n - 1)
    }
}

io.print(factorial(6))
export factorial
```

The bundled `stdio` library is written in SFL and provides `print(value)`, `write(value)`, and `readline()`. The output functions write byte strings unchanged and use the SFL representation for other values. `print` appends LF. `readline` returns a byte string with a trailing CRLF normalized to LF, retaining the LF. Existing LF endings, embedded carriage returns, and final lines without a newline are preserved. It returns null at EOF.

`import "str"` provides `str.chr(code)`, which accepts an integer from 0 through 255 and returns a one-byte string. For example, `str.chr(65)` returns `"A"`, and `str.chr(0)` returns `"\x00"`. Non-integers, including `65.0`, and out-of-range inputs raise language exceptions that `try` can catch. This function is implemented entirely in SFL without host signals. It converts byte values, not Unicode code points.

`import "name"` searches directories supplied with `-I`, then `SFL_PATH` (separated by the platform's path-list separator), then the bundled standard library. `local "name"` searches only beside the current source file. A missing `.sfl` suffix is added automatically. Both forms support `as alias`. Circular imports raise `IMPORT_CYCLE`.

`sample_source/code.sfl` includes `local "stdio"`, so running the complete file requires an actual `stdio.sfl` beside it. The bundled library does not substitute for a local import. The `lex` and `ast` commands do not require imported files to exist.

## Cache Format and Invalidation

Each cache contains one set of source headers followed by `UNIT 1` for the complete module:

```text
SFLEX 1
SOURCE_FILE "../demo.sfl"
SOURCE_SHA256 "<64-character uppercase hexadecimal digest>"
SOURCE_MTIME_UTC "2026-11-01T00:00:00.000000000Z"
```

AST files start with `SFLAST 1` and use the same source headers. `SOURCE_FILE`, `SOURCE_SHA256`, and `SOURCE_MTIME_UTC` each appear exactly once per file. The timestamp comes from the source file and uses UTC with nine fractional-second digits. The hash covers the original bytes, without normalizing CRLF or removing a BOM.

- Every invocation reads and hashes the source. An unchanged mtime alone does not establish cache validity.
- When the source content, stage implementation fingerprint, and cache structure are valid, the cache is read without rerunning the lexer or parser, or rewriting files.
- If only the source mtime changes, the source headers are refreshed without repeating lexical or syntactic analysis.
- `.sflast` also records a fingerprint of its input tokens. Changed tokens invalidate the AST cache.
- Compiler fingerprints and content checksums use `# SFL_*` comments rather than additional data records.
- Missing, corrupt, or unknown cache formats, implementation changes, and source changes rebuild the affected stages. Writes use temporary files and atomic replacement.
- Lexical errors still produce `.sflex` files containing `ERROR` tokens. `ast` and `run` also write `STATUS INVALID` and `DIAG` records; invalid ASTs are never executed.

Only compilation results are cached. Every `run` executes the main module again, while each imported module executes once per run. Imports from restricted eval contexts do not reuse unrestricted module instances.

## Language Semantics

- Values include null, arbitrary-precision integers, Python floating-point numbers, byte strings, persistent dictionaries, and functions. Comparisons return integer 0 or 1.
- Dictionaries are immutable treaps with structural sharing. Numerically equivalent keys, such as `1` and `1.0`, share an entry. Assigning null deletes a key; NaN cannot be a key. Numeric keys sort before byte-string keys.
- `:=` evaluates its right-hand side before shadowing an existing binding. Brace-delimited blocks have local environments. Functions capture a snapshot at definition time; an explicit `with` environment replaces the outer environment entirely.
- `return` exits only the nearest brace-delimited block. `self` refers to its enclosing function. Function bodies, unselected if branches, and unused exception handlers are evaluated only when needed.
- Blocks do not implicitly return their final expression. A block without an executed `return` returns null. A module returns its export dictionary.
- `obj[key]` returns null for a missing key; `obj.key` raises an exception. String indexing uses non-negative integer keys and returns one-byte strings. Numerically equivalent floating-point keys work too; negative indices do not wrap around.
- Byte strings support persistent updates with `text[index := value]`, returning a new string without changing the original. Update indices must be integers; values must be integer bytes or null. See the rules below.
- The brackets in `[]` must be adjacent. Dictionary update entries may be separated by newlines or commas. Function parameters and arguments use commas, with trailing commas allowed.
- Precedence, from highest to lowest, is postfix, prefix, multiplication/division/remainder, addition/subtraction, and comparison. Arithmetic is left-associative. Comparison chains require explicit parentheses rather than Python-style chaining.
- Ordinary newlines separate statements. Parentheses and call arguments allow line continuation, while embedded brace-delimited bodies retain their own statement boundaries.
- `eval` parses one expression, which may be a block or `do` expression containing statements. Its default environment and signal allowlist are empty. Use parentheses to clarify the boundaries of complex `eval` and `emit` operands.
- `repr` produces an expression that SFL `eval` can read, including closure captures and null bindings. NaN is represented as `(inf - inf)`. Reconstructed functions preserve call behavior, not object identity.
- `try` catches language exception dictionaries containing `code`, `message`, and, when available, `source`, `line`, and `column`. Internal return signals and unexpected host failures are not caught as language exceptions.

### Byte-String Updates

`text[index := value]` operates on byte positions, not Unicode characters:

- `index` must be a non-negative integer. Unlike read indexing, floating-point indices such as `1.0` are rejected.
- `value` must be an integer from 0 through 255, or null (`{}` or `null`). A one-byte string such as `"A"` is not accepted as the value.
- Writing an existing position replaces its byte. Writing at `len text` appends a byte. Writing beyond the end raises an exception instead of creating gaps.
- Null deletes the byte and shifts later bytes left. Deleting a missing non-negative position leaves the string unchanged.
- Multiple update entries run from left to right, each against the result of the previous entry. The original string and any captured copies remain unchanged.
- Invalid index types raise `INVALID_KEY`, invalid byte values raise `INVALID_BYTE`, and negative or gap-creating indices raise `INDEX_OUT_OF_RANGE`. These are language exceptions that `try` can catch.

| Expression | Result |
| --- | --- |
| `"abc"[1 := 90]` | `"aZc"` |
| `""[0 := 65]` | `"A"` |
| `"abc"[3 := 100]` | `"abcd"` |
| `"abc"[1 := {}]` | `"ac"` |
| `"abc"[9 := {}]` | `"abc"` |
| `"abcd"[1 := {}, 1 := {}]` | `"ad"` |

For example, `content := content[(len content) - 1 := {}]` removes the last byte of a non-empty input line.

The interpreter does not sandbox resource usage or file access for untrusted programs. Recursion and memory limits are governed by Python and the operating system.

## Python API and Signal Extensions

```python
from sfl import SignalHandler, StdioSignalHandler, compile_source, run_source

document = compile_source("demo.sfl")


class MySignals(StdioSignalHandler):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.register(b"custom", self.custom)

    def custom(self, value):
        self.calls.append(value)
        return value


signals = MySignals()
exports = run_source("demo.sfl", signals=signals)
```

`SignalHandler` manages signal registration and dispatch, starting with an empty registry:

- `register(name, handler)` registers or replaces a handler. Names must be bytes, and handlers must be callable.
- `unregister(name)` removes a registration and does nothing if the name is absent.
- `handle(name, value)` dispatches a signal, returning null (Python `None`) if it is not registered.

`StdioSignalHandler` subclasses `SignalHandler` and binds `stdio.print`, `stdio.write`, and `stdio.readline` to its instance methods `print`, `write`, and `readline`. Subclass it to retain standard I/O while adding signals or overriding those methods. Subclass `SignalHandler` to start with an empty registry. Handler instances can retain state and are passed to the interpreter without being copied.

`signals=None` creates a default `StdioSignalHandler`. Both `run_source` and `Interpreter` also accept the original dictionary form, such as `signals={b"custom": lambda value: value}`. A dictionary is copied into a new `SignalHandler` and replaces the entire registry; standard I/O is not added automatically. Pass `SignalHandler()` to disable all default signals.

Handlers receive one SFL value and return one SFL value: `None`, int, float, bytes, `Dictionary`, or `Function`. The interpreter checks eval signal permissions before dispatch and validates the returned value afterward. Denied signals never reach the handler, and `with_signal` cannot expand an outer context's permissions. Permitted but unregistered signals return null.

## Project Layout

- `sfl/lexer.py`: byte-oriented tokenization.
- `sfl/parser.py`: AST construction.
- `sfl/formats.py`: plain-text cache serialization and validation.
- `sfl/cache.py`: cache invalidation and atomic writes.
- `sfl/values.py`: runtime values and persistent dictionaries.
- `sfl/runtime.py`: AST interpretation.
- `sfl/signals.py`: extensible signal handler classes.
- `sfl/render.py`: source rendering and value representations.
- `sfl/stdlib/`: standard libraries written in SFL.
