import hashlib
import os
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
import sfl
import sfl.cache as cache_module
from sfl.cache import Compiler
from sfl.formats import read_ast, read_lex, write_ast
from sfl.model import SflError
from sfl.runtime import Interpreter


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def source_file(tmp_path):
    source = tmp_path / "source with spaces.sfl"
    source.write_bytes(b"export 42 as value\n")
    return source


def actions(compiler):
    return [(stage, action) for stage, action, path in compiler.events]


def cache_snapshot(paths):
    return [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]


def test_cache_build_hit_and_no_rewrite(source_file, monkeypatch):
    compiler = Compiler()
    compiler.ast(source_file)
    assert actions(compiler) == [("lex", "build"), ("ast", "build")]
    paths = compiler.cache_paths(source_file)
    assert [path.name for path in paths] == ["source with spaces.sflex", "source with spaces.sflast"]
    assert all(path.parent == source_file.parent / "sflcache" for path in paths)
    original = cache_snapshot(paths)

    def forbidden(*arguments, **keywords):
        pytest.fail("A cache hit must not invoke the lexer or parser")

    monkeypatch.setattr(cache_module, "lex", forbidden)
    monkeypatch.setattr(cache_module, "parse", forbidden)
    compiler.events.clear()
    assert Interpreter(compiler).execute(compiler.ast(source_file)).find(b"value") == 42
    assert actions(compiler) == [("lex", "hit"), ("ast", "hit")]
    assert cache_snapshot(paths) == original


def test_timestamp_only_change_refreshes_metadata(source_file, monkeypatch):
    compiler = Compiler()
    compiler.ast(source_file)
    metadata = source_file.stat()
    os.utime(source_file, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 2_000_000_000))

    def forbidden(*arguments, **keywords):
        pytest.fail("Timestamp-only changes must not repeat lexing or parsing")

    monkeypatch.setattr(cache_module, "lex", forbidden)
    monkeypatch.setattr(cache_module, "parse", forbidden)
    compiler.events.clear()
    document = compiler.ast(source_file)
    assert actions(compiler) == [("lex", "refresh"), ("ast", "refresh")]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z", document.source.mtime_utc)
    lex_path, ast_path = compiler.cache_paths(source_file)
    assert read_lex(lex_path.read_text(encoding="utf-8"), source_file.read_bytes()).source == read_ast(ast_path.read_text(encoding="utf-8")).source


def test_content_change_with_same_mtime_rebuilds(source_file):
    compiler = Compiler()
    compiler.ast(source_file)
    metadata = source_file.stat()
    source_file.write_bytes(b"export 43 as value\n")
    os.utime(source_file, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    compiler.events.clear()
    document = compiler.ast(source_file)
    assert actions(compiler) == [("lex", "build"), ("ast", "build")]
    assert Interpreter(compiler).execute(document).find(b"value") == 43
    assert document.source.sha256 == hashlib.sha256(source_file.read_bytes()).hexdigest().upper()


@pytest.mark.parametrize("stage", ["lex", "ast"])
@pytest.mark.parametrize("corruption", ["truncated", "version", "checksum", "deleted"])
def test_corrupt_or_missing_cache_rebuilds_only_affected_stages(source_file, stage, corruption):
    compiler = Compiler()
    compiler.ast(source_file)
    path = compiler.cache_paths(source_file)[stage == "ast"]
    text = path.read_text(encoding="utf-8")
    if corruption == "deleted":
        path.unlink()
    elif corruption == "truncated":
        path.write_text("broken", encoding="utf-8")
    elif corruption == "version":
        path.write_text(text.replace(" 1\n", " 99\n", 1), encoding="utf-8")
    else:
        path.write_text(text.replace('"42"', '"99"'), encoding="utf-8")
    compiler.events.clear()
    document = compiler.ast(source_file)
    expected = [("lex", "build"), ("ast", "hit")] if stage == "lex" else [("lex", "hit"), ("ast", "build")]
    assert actions(compiler) == expected
    assert Interpreter(compiler).execute(document).find(b"value") == 42


def test_changed_token_fingerprint_rebuilds_ast(source_file):
    compiler = Compiler()
    compiler.ast(source_file)
    _, path = compiler.cache_paths(source_file)
    document = read_ast(path.read_text(encoding="utf-8"))
    document.input_hash = "0" * 64
    path.write_text(write_ast(document), encoding="utf-8")
    compiler.events.clear()
    compiler.ast(source_file)
    assert actions(compiler) == [("lex", "hit"), ("ast", "build")]


def test_changed_implementation_fingerprint_rebuilds_ast(source_file):
    compiler = Compiler()
    compiler.ast(source_file)
    compiler.keys["ast"] = "0" * 64
    compiler.events.clear()
    compiler.ast(source_file)
    assert actions(compiler) == [("lex", "hit"), ("ast", "build")]


def test_forced_rebuild(source_file):
    Compiler().ast(source_file)
    compiler = Compiler(force=True)
    compiler.ast(source_file)
    assert actions(compiler) == [("lex", "build"), ("ast", "build")]


@pytest.mark.parametrize("data", [b"export 42 as value\r\n", b"\xef\xbb\xbfexport 42 as value\n"])
def test_metadata_hashes_exact_source_bytes(source_file, data):
    source_file.write_bytes(data)
    compiler = Compiler()
    compiler.ast(source_file)
    for path in compiler.cache_paths(source_file):
        text = path.read_text(encoding="utf-8")
        assert hashlib.sha256(data).hexdigest().upper() in text
        for header in ["SOURCE_FILE ", "SOURCE_SHA256 ", "SOURCE_MTIME_UTC "]:
            assert text.count(header) == 1
        assert "\nUNIT 1 " in text


def test_invalid_ast_is_cached_but_never_executed(source_file):
    source_file.write_bytes(b"type := 1\n")
    compiler = Compiler()
    document = compiler.ast(source_file)
    assert document.units[0].status == "INVALID"
    compiler.events.clear()
    document = compiler.ast(source_file)
    assert actions(compiler) == [("lex", "hit"), ("ast", "hit")]
    with pytest.raises(SflError) as caught:
        Interpreter(compiler).execute(document)
    assert caught.value.code == "RESERVED_NAME"


def test_source_change_during_compilation_is_retried(source_file, monkeypatch):
    original_lex = cache_module.lex
    calls = []

    def changing_lex(data, info):
        calls.append(data)
        if len(calls) == 1:
            source_file.write_bytes(b"export 43 as value\n")
        return original_lex(data, info)

    monkeypatch.setattr(cache_module, "lex", changing_lex)
    compiler = Compiler()
    document = compiler.ast(source_file)
    assert len(calls) == 2
    assert Interpreter(compiler).execute(document).find(b"value") == 43


def test_run_executes_again_even_when_compilation_is_cached(harness, source_file):
    source_file.write_text('emit 7 as "echo"\n', encoding="ascii")
    harness.interpreter.run(source_file)
    harness.interpreter.run(source_file)
    assert harness.events == [7, 7]


@pytest.fixture
def run_cli(tmp_path, library):
    def run(command, source, *, data=None, extra=(), expected_status=0):
        environment = dict(os.environ)
        environment.pop("SFL_PATH", None)
        environment.pop("PYTHONPATH", None)
        arguments = [sys.executable, "-m", "sfl", command, str(source), "-v"]
        if command == "run":
            arguments.extend(["-I", str(library)])
        result = subprocess.run([*arguments, *extra], input=data, capture_output=True, cwd=tmp_path, env=environment, timeout=30)
        assert result.returncode == expected_status, (result.stdout, result.stderr)
        return result

    return run


def test_cli_lex_ast_run_and_cache_reuse(harness, tmp_path, run_cli):
    source = tmp_path / "function.sfl"
    source.write_bytes((FIXTURES / source.name).read_bytes())
    lexical = run_cli("lex", source)
    assert b"BUILD" in lexical.stderr and b".sflex" in lexical.stdout
    ast = run_cli("ast", source)
    assert b"HIT" in ast.stderr and b"BUILD" in ast.stderr and b".sflast" in ast.stdout
    result = run_cli("run", source, extra=("--print-result",))
    assert b"HIT" in result.stderr and b"BUILD" not in result.stderr
    exports = harness.evaluate(result.stdout.decode("ascii"))
    assert harness.call(harness.call(exports.find(b"make"), (2, 3, 4, 5))) == 15
    paths = Compiler().cache_paths(source)
    original = cache_snapshot(paths)
    assert run_cli("run", source, extra=("--print-result",)).stdout == result.stdout
    assert cache_snapshot(paths) == original
    forced = run_cli("run", source, extra=("--force", "--print-result"))
    assert forced.stdout == result.stdout and b"BUILD" in forced.stderr


@pytest.mark.parametrize(("name", "data", "expected"), [
    ("hello_world.sfl", b"neko\n", b"hello world!\nplease input your name: hello, neko!\n"),
    ("hello_world.sfl", b"neko\r\n", b"hello world!\nplease input your name: hello, neko!\n"),
    ("fib.sfl", None, b"fib(9) = 55\n"),
])
def test_cli_examples(tmp_path, run_cli, name, data, expected):
    source = tmp_path / name
    source.write_bytes((FIXTURES / name).read_bytes())
    assert run_cli("run", source, data=data).stdout == expected
    cached = run_cli("run", source, data=data)
    assert cached.stdout == expected
    assert b"HIT" in cached.stderr


@pytest.mark.parametrize(("command", "text", "code"), [
    ("lex", "@", b"INVALID_BYTE"), ("ast", "type := 1", b"RESERVED_NAME"),
    ("run", 'raise "failed"', b"RAISED_VALUE"), ("run", "1 / 0", b"ARITHMETIC_ERROR"),
])
def test_cli_errors(tmp_path, run_cli, command, text, code):
    source = tmp_path / "invalid.sfl"
    source.write_text(text, encoding="ascii")
    result = run_cli(command, source, expected_status=1)
    assert code in result.stderr
    assert b"Traceback" not in result.stderr


def test_cli_missing_source(tmp_path, run_cli):
    result = run_cli("run", tmp_path / "missing.sfl", expected_status=1)
    assert b"IO_ERROR" in result.stderr


def test_version_command(tmp_path):
    result = subprocess.run([sys.executable, "-m", "sfl", "--version"], capture_output=True, cwd=tmp_path, check=True, timeout=30)
    assert result.stdout.strip() == ("sfl " + sfl.__version__).encode("ascii")


def test_public_api(source_file):
    document = sfl.compile_source(source_file)
    assert document.units[0].status == "VALID"
    assert sfl.run_source(source_file).find(b"value") == 42


def test_sfl_is_an_installed_dependency():
    assert version("sfl-lang") == sfl.__version__
