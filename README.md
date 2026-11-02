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

`import "str"` provides `str.chr(code)`, implemented entirely in SFL without host signals. For integers from 0 through 255 it returns a one-byte string: `str.chr(65)` returns `"A"`, and `str.chr(0)` returns `"\x00"`. Larger non-negative integers are encoded as multiple bytes in little-endian order. It operates on byte values, not Unicode code points.

`str.ord(value)` emits the `"str.ord"` signal and returns the integer value of exactly one byte, from 0 through 255. For example, `str.ord("A")` returns `65`, `str.ord("0")` returns `48`, and `str.ord("\xFF")` returns `255`. Non-strings raise `TYPE_ERROR`; empty or multi-byte strings raise `INVALID_LENGTH`. These are language errors that `try ... except ... then ...` can handle. A non-ASCII Unicode character encoded as multiple UTF-8 bytes is not a single byte. The default signal handler includes this signal. Custom configurations can include `StrSignalHandler()` in their handler list, register the signal explicitly, or derive from `StandardSignalHandler`. Restricted `eval` calls need `with_signal "str.ord"` to permit it.

`import "name"` searches directories supplied with `-I`, then `SFL_PATH` (separated by the platform's path-list separator), then the bundled standard library. `local "name"` searches only beside the current source file. A missing `.sfl` suffix is added automatically. Both forms support `as alias`. Circular imports raise `IMPORT_CYCLE`.

Without `as`, the final filename, after adding the optional suffix, must be an identifier followed by `.sfl`: `[A-Za-z_][A-Za-z0-9_]*\.sfl`. Its stem becomes the local binding. Directory names do not affect this check. Filenames containing spaces, hyphens, extra dots, non-ASCII letters, or a leading digit require an explicit `as` alias. Keyword stems such as `if` also require an alias, since keywords cannot be variable names. An alias must itself be a non-keyword identifier. Invalid import bindings are rejected while building the AST, before looking up or executing the target module.

| Statement | Binding or result |
| --- | --- |
| `import "stdio"` | `stdio` (the `.sfl` suffix is added) |
| `local "_helper2.sfl"` | `_helper2` |
| `local "some-folder/helper.sfl"` | `helper` |
| `import "my-module.sfl" as module` | `module` |
| `local "123.sfl" as data` | `data` |
| `local "my-module.sfl"` | `IMPORT_ERROR`: an `as` alias is required |
| `import "if.sfl"` | `IMPORT_ERROR`: an `as` alias is required |

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
- The brackets in `[]` must be adjacent. Dictionary update entries may be separated by newlines or commas. Function parameters and arguments use commas, with trailing commas allowed. A final `...` parameter permits additional arguments; `args` exposes all arguments inside the function body.
- Precedence, from highest to lowest, is postfix, prefix, multiplication/division/remainder, addition/subtraction, and comparison. Arithmetic is left-associative. Comparison chains require explicit parentheses rather than Python-style chaining.
- Ordinary newlines separate statements. Parentheses and call arguments allow line continuation, while embedded brace-delimited bodies retain their own statement boundaries.
- `eval` parses one expression, which may be a block or `do` expression containing statements. Its default environment and signal allowlist are empty. Use parentheses to clarify the boundaries of complex `eval` and `emit` operands.
- `repr` produces an expression that SFL `eval` can read, including closure captures and null bindings. NaN is represented as `(inf - inf)`. Reconstructed functions preserve call behavior, not object identity.
- `try ... except name then ...` catches values thrown by `raise` and built-in language errors. Built-in errors are dictionaries containing `code`, `message`, and, when available, `source`, `line`, and `column`. Internal return signals and unexpected host failures are not caught as language exceptions.

### Function Arguments

Inside a function body, `args` returns a persistent dictionary of that call's actual arguments, indexed from zero. It includes arguments bound to named parameters as well as any additional arguments. Both `args.0` and `args[0]` access the first argument. This works in fixed-arity functions too.

```sfl
collect := fn (...) with [] {
    return args
}

add := fn (left, ...) with [] {
    return left + args[1]
}

export collect(10, "hello", 30) as collected
export collect() as empty
export add(10, 20) as sum
```

Here `collected` is `[][0 := 10, 1 := "hello", 2 := 30]`, `empty` is `[]`, and `sum` is `30`.

- `fn (...)` accepts zero or more arguments. `fn (first, ...)` requires at least one argument; `fn (first, second, ...)` requires at least two. Named parameters bind the corresponding leading arguments.
- Without `...`, the argument count must match the parameter count exactly. A count mismatch raises the catchable language error `ARGUMENT_COUNT`.
- `...` can appear once, as the final item in a parameter list, with an optional trailing comma. It is neither a named parameter nor a call-site spread operator. Forms such as `fn (..., last)` and `fn (..., ...)` are syntax errors.
- Each call has its own argument dictionary. Rebinding a named parameter or updating a dictionary derived from `args` leaves the original arguments unchanged. Nested blocks and exception handlers retain the current call's arguments; nested functions use their own arguments. Returned or explicitly captured argument dictionaries remain usable after the call returns.

`args` follows the ordinary dictionary rules: null argument values have no stored entry, but later arguments retain their original indices. For example, `collect(null, 7)` returns `[][1 := 7]`. `args[0]` then returns null, while `args.0` raises `MISSING_MEMBER`. Accordingly, `len args` counts stored, non-null arguments.

`args` is a reserved keyword and is valid only within a lexically enclosing function body, including its nested blocks. Using it at module scope or in a top-level function's `with` expression is rejected with `INVALID_ARGS`. A nested function's `with` expression can use its enclosing function's `args`. An `eval` string has its own lexical scope: pass an argument dictionary under another name through `with` to access it there, or define a function inside the evaluated expression.

Serialized ASTs store `args` as an `ARGS` node whose `function` reference points to the nearest enclosing function. `FUNCTION` nodes record `variadic=YES` or `variadic=NO`, with `params` listing only named parameters. Source rendering and `repr` preserve variadic signatures. Existing compilation caches rebuild automatically when the updated compiler first reads them.

### Function Representation

Function `repr` results use two spaces for each unmatched opening `(`, `[`, or `{` at the start of a line. Leading closing brackets reduce the indentation before that line is written. Brackets inside string literals do not affect indentation. Nested function values inside dictionaries and captured environments follow the same rule.

Every function defined by a function's representation has an explicit `with` environment, including nested function literals and captured function values. A function without dependencies uses `with ([])`. For example, a function capturing `offset := 10` can be represented as:

```sfl
(fn (value) with (([])[
      "offset" := 10
  ]) {
    return (value + offset)
})
```

The environments preserve definition-time snapshots rather than relying on names in the caller of `eval`. Null captures use non-null holder dictionaries and are restored inside the function body, preserving the distinction between a null binding and an undefined name. Generated environments for nested implicit closures also preserve null parameters and local bindings. Explicit environment expressions are evaluated once, including when a nested closure needs to inherit their dynamically determined names. Variadic signatures, `args`, and `self` retain their behavior after reconstruction.

### Raising and Catching Values

`raise value` evaluates its operand once, then throws that exact SFL value. Integers, floats, byte strings, dictionaries, functions, and null are all supported. The handler receives the original value, without wrapping or conversion; dictionary and function identity are preserved.

```sfl
result := try {
    raise "bad input"
} except error then {
    return error
}
```

Here `result` is `"bad input"`. An `except` handler requires a binding name followed by `then` and a handler block. It catches explicit raises and built-in language errors. A handler can throw again with `raise error`, or throw a different value; the nearest enclosing active handler catches it.

`raise` consumes the following expression, so `raise 1 + 2` throws `3`. It also works in expression positions such as `return raise "failed"`. An error while evaluating the operand propagates normally. Statements after an executed raise are skipped, and exceptions propagate through functions, `eval`, and imports. A normal `return` is not caught by `except`.

An uncaught raise exits the CLI with status 1 and reports `RAISED_VALUE`, its source location, and the value's SFL representation. Python callers receive `sfl.runtime.RaisedValue`; its `.value` property contains the original thrown value. `raise` is a reserved keyword. Serialized ASTs store raises as `RAISE` nodes with a `value` expression; handlers use the existing `TRY` node.

### Reserved Names

Keywords cannot be used as variable names, function parameters, exception bindings, or explicit `import`/`local` aliases. These declarations raise `RESERVED_NAME` while building the AST, with the keyword and its source location in the diagnostic. Names are case-sensitive: `type` is reserved, while `Type` and `type_name` are valid identifiers.

| Statement | Result |
| --- | --- |
| `type := 1` | `RESERVED_NAME` |
| `args := 1` | `RESERVED_NAME` |
| `if := 1` | `RESERVED_NAME` |
| `import "stdio" as return` | `RESERVED_NAME` |
| `local "helper.sfl" as type` | `RESERVED_NAME` |
| `fn (type) {}` | `RESERVED_NAME` |
| `local "type.sfl"` | `IMPORT_ERROR`: an explicit non-keyword alias is required |
| `local "type.sfl" as module` | Valid binding named `module` |

These are static errors: the entire source unit is rejected before any statements or imports execute. A `try` in that source cannot defer the check; a `try` can catch errors raised while compiling an `eval` string. Cached AST identifier fields are checked against the same keyword table. String keys such as `[]["type" := 1]` and string export names such as `export 1 as "type"` remain valid.

### Type Inspection

`type value` evaluates its operand once and returns its SFL type name as a byte string. It has the same prefix precedence as `len` and `repr`: indexing, member access, and calls bind more tightly, while arithmetic and comparisons bind less tightly. Use `type (a + b)` to inspect the result of an arithmetic expression. Undefined variables and other operand errors propagate normally.

| Expression | Result |
| --- | --- |
| `type 42` | `"int"` |
| `type 1.5` | `"float"` |
| `type "hello"` | `"str"` |
| `type []` | `"dict"` |
| `type (fn () {})` | `"fn"` |
| `type null` | `"null"` |
| `type {}` | `"null"` |
| `type inf` | `"float"` |

Variables work the same way: after `a := 42`, `type a` returns `"int"`. Floating-point infinities and NaN have type `"float"`; comparisons have type `"int"`. `type` is a reserved keyword, so it cannot be used as a variable or parameter name. Importing `type.sfl` requires an explicit alias, such as `local "type.sfl" as module`.

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
from sfl import SignalHandler, StdioSignalHandler, StrSignalHandler, compile_source, run_source
from sfl.runtime import Interpreter

document = compile_source("demo.sfl")


class MySignals(SignalHandler):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.register(b"custom", self.custom)

    def custom(self, value):
        self.calls.append(value)
        return value


custom = MySignals()
signals = [StdioSignalHandler(), StrSignalHandler(), custom]
exports = run_source("demo.sfl", signals=signals)
interpreter = Interpreter(signals=signals)
exports = interpreter.run("demo.sfl")
```

`SignalHandler` manages signal registration and dispatch, starting with an empty registry:

- `register(name, handler)` registers or replaces a handler. Names must be bytes, and handlers must be callable.
- `unregister(name)` removes a registration and does nothing if the name is absent.
- `supports(name)` reports whether the handler recognizes a signal name, independently of its return value.
- `handle(name, value)` dispatches a signal, returning null (Python `None`) if it is not registered.

`StdioSignalHandler` subclasses `SignalHandler` and binds `stdio.print`, `stdio.write`, and `stdio.readline` to its instance methods `print`, `write`, and `readline`. `StrSignalHandler` is a separate `SignalHandler` subclass that binds `str.ord` to its `ord` method. `StandardSignalHandler` combines them through `CompositeSignalHandler`, exposing the instances as `.stdio` and `.strings`. Subclass `StandardSignalHandler` to retain all bundled signals, or `SignalHandler` to start with an empty registry.

Both `Interpreter` and `run_source` accept a single handler or an iterable of handlers through `signals=`, including lists, tuples, and generators. An iterable is consumed once into a `CompositeSignalHandler`; its members must be `SignalHandler` instances. The original instances are retained, including their state, and later calls to their `register` and `unregister` methods affect subsequent dispatches. When several handlers support the same name, the last one supplied wins. Returning null still counts as handling the signal: it does not trigger fallback to earlier handlers. Subclasses implementing dynamic dispatch should override both `supports` and `handle`.

`CompositeSignalHandler(handlers)` is also available from `sfl` for explicit or nested composition. Registrations on the composite itself take precedence over its members. Removing such a registration reveals the member handler again.

`signals=None` creates a default `StandardSignalHandler`. Pass `StdioSignalHandler()` for only standard I/O or `StrSignalHandler()` for only string signals. Both entry points also accept the original mapping form, such as `signals={b"custom": lambda value: value}`. A mapping is copied into a new `SignalHandler`. Custom lists, handlers, and mappings replace the defaults; bundled signals are not added automatically. Pass `signals=[]`, `signals={}`, or `SignalHandler()` to disable all default signals.

Handlers receive one SFL value and return one SFL value: `None`, int, float, bytes, `Dictionary`, or `Function`. The interpreter checks eval signal permissions before dispatch and validates the returned value afterward. Denied signals never reach the handler, and `with_signal` cannot expand an outer context's permissions. Permitted but unregistered signals return null.

A handler may raise `sfl.model.SflError` for an expected language error. The interpreter converts it into a catchable runtime error and attaches the emitting expression's source location. Existing runtime errors and explicitly raised values propagate unchanged; unexpected host failures are not converted.

## Project Layout

- `sfl/lexer.py`: byte-oriented tokenization.
- `sfl/parser.py`: AST construction.
- `sfl/formats.py`: plain-text cache serialization and validation.
- `sfl/cache.py`: cache invalidation and atomic writes.
- `sfl/imports.py`: shared import filename and binding validation.
- `sfl/values.py`: runtime values and persistent dictionaries.
- `sfl/runtime.py`: AST interpretation.
- `sfl/signals.py`: extensible signal handler classes.
- `sfl/render.py`: source rendering and value representations.
- `sfl/stdlib/`: standard libraries written in SFL.
