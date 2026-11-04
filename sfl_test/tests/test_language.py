import pytest
from sfl.formats import quote
from sfl.imports import resolve_import
from sfl.lexer import KEYWORDS
from sfl.model import number_nodes
from sfl.render import representation
from sfl.runtime import RaisedValue, RuntimeError
from sfl.values import Dictionary, canonical_key, equal, integer_text, parse_integer

from .support import compile_text


@pytest.mark.parametrize(("expression", "expected"), [
    ('type 42', b"int"), ('type -42', b"int"), ('type 1.5', b"float"),
    ('type inf', b"float"), ('type (inf - inf)', b"float"), ('type "a"', b"str"),
    ('type []', b"dict"), ('type {}', b"null"), ('type null', b"null"),
    ('type fn () with [] {}', b"fn"), ('type (1 < 2)', b"int"),
    ('do {\na := 42\nreturn type a\n}', b"int"),
])
def test_type_keyword(harness, expression, expected):
    assert harness.evaluate(expression) == expected


@pytest.mark.parametrize(("expression", "expected"), [
    ('"abc"[0 := 65]', b"Abc"), ('"abc"[3 := 33]', b"abc!"),
    ('"abc"[1 := {}]', b"ac"), ('"abc"[1 := null]', b"ac"),
    ('"abc"[9 := {}]', b"abc"), ('"abcd"[1 := {}, 1 := {}]', b"ad"),
    ('""[0 := 0]', b"\x00"), ('"abc"[0 := 255]', b"\xffbc"),
    ('"hello, " + "neko" + "!"', b"hello, neko!"),
    ('"abc"[2]', b"c"), ('"abc"[9]', None), ('"abc".0', b"a"),
])
def test_byte_strings(harness, expression, expected):
    assert harness.evaluate(expression) == expected


@pytest.mark.parametrize(("expression", "code"), [
    ('"abc"[1.0 := 65]', "INVALID_KEY"), ('"abc"["x" := 65]', "INVALID_KEY"),
    ('"abc"[0 := -1]', "INVALID_BYTE"), ('"abc"[0 := 256]', "INVALID_BYTE"),
    ('"abc"[0 := 1.0]', "INVALID_BYTE"), ('"abc"[0 := "A"]', "INVALID_BYTE"),
    ('"abc"[0 := []]', "INVALID_BYTE"), ('"abc"[-1 := 65]', "INDEX_OUT_OF_RANGE"),
    ('"abc"[4 := 65]', "INDEX_OUT_OF_RANGE"), ('"abc"[-1 := {}]', "INDEX_OUT_OF_RANGE"),
])
def test_invalid_string_updates(harness, expression, code):
    with pytest.raises(RuntimeError) as caught:
        harness.evaluate(expression)
    assert caught.value.code == code


@pytest.mark.parametrize("keyword", list(KEYWORDS))
@pytest.mark.parametrize("template", ["NAME := 1", "export fn (NAME) {} as function", 'import "stdio" as NAME'])
def test_keywords_cannot_bind(keyword, template):
    document = compile_text(template.replace("NAME", keyword), valid=False)
    assert document.units[0].status == "INVALID"
    assert document.units[0].diagnostics[0].code == "RESERVED_NAME"


@pytest.mark.parametrize("keyword", list(KEYWORDS))
def test_keyword_module_requires_alias(keyword):
    with pytest.raises(ValueError, match="alias"):
        resolve_import((keyword + ".sfl").encode("ascii"), None)


@pytest.mark.parametrize(("filename", "binding"), [
    (b"stdio", b"stdio"), (b"_helper2.sfl", b"_helper2"),
    (b"some-folder/helper.sfl", b"helper"), (b"A1.sfl", b"A1"),
])
def test_valid_import_names(filename, binding):
    path, alias = resolve_import(filename, None)
    assert path.suffix == ".sfl"
    assert alias == binding


@pytest.mark.parametrize("filename", [b"my-module.sfl", b"123.sfl", b"two words.sfl", b"a.b.sfl", "中文.sfl".encode("utf-8")])
@pytest.mark.parametrize("mode", ["import", "local"])
def test_unusual_import_names_need_alias(filename, mode):
    statement = mode + " " + quote(filename)
    invalid = compile_text(statement, valid=False)
    assert invalid.units[0].diagnostics[0].code == "IMPORT_ERROR"
    assert compile_text(statement + " as module").units[0].status == "VALID"


def test_imported_module_and_alias(harness, tmp_path):
    helper = tmp_path / "my-module.sfl"
    helper.write_text("export 42 as answer\n", encoding="ascii")
    main = tmp_path / "main.sfl"
    main.write_text('local "my-module.sfl" as helper\nexport helper.answer as value\n', encoding="ascii")
    assert harness.interpreter.run(main).find(b"value") == 42


def test_library_import(harness, tmp_path):
    (harness.library / "custom.sfl").write_text("export 23 as value\n", encoding="ascii")
    main = tmp_path / "main.sfl"
    main.write_text('import "custom"\nexport custom.value as answer\n', encoding="ascii")
    assert harness.interpreter.run(main).find(b"answer") == 23


def test_local_import_does_not_fall_back_to_stdlib(harness, tmp_path):
    main = tmp_path / "main.sfl"
    main.write_text('local "stdio"\n', encoding="ascii")
    with pytest.raises(RuntimeError) as caught:
        harness.interpreter.run(main)
    assert caught.value.code == "MODULE_NOT_FOUND"


def test_circular_import(harness, tmp_path):
    first = tmp_path / "first.sfl"
    first.write_text('local "second"\n', encoding="ascii")
    (tmp_path / "second.sfl").write_text('local "first"\n', encoding="ascii")
    with pytest.raises(RuntimeError) as caught:
        harness.interpreter.run(first)
    assert caught.value.code == "IMPORT_CYCLE"


@pytest.mark.parametrize("value", [None, 12, 1.5, b"error", Dictionary().with_item(b"answer", 42)])
def test_raise_preserves_value_identity(harness, value):
    assert harness.evaluate("try { raise value } except error then { return error }", {b"value": value}) is value
    with pytest.raises(RaisedValue) as caught:
        harness.evaluate("raise value", {b"value": value})
    assert caught.value.value is value


def test_raise_preserves_function_identity(harness):
    value = harness.evaluate("fn () with [] {}")
    assert harness.evaluate("try { raise value } except error then { return error }", {b"value": value}) is value


def test_raise_evaluates_once_and_skips_following_statements(harness):
    result = harness.evaluate('try {\nraise emit 7 as "echo"\nemit 99 as "echo"\n} except error then { return error }')
    assert result == 7
    assert harness.events == [7]


def test_raise_consumes_expression_and_can_be_rethrown(harness):
    assert harness.evaluate("try { raise 1 + 2 } except error then { return error }") == 3
    assert harness.evaluate("try {\ntry { raise 4 } except error then { raise error + 1 }\n} except outer then { return outer }") == 5


def test_runtime_errors_are_catchable(harness):
    assert harness.evaluate("try { return 1 / 0 } except error then { return error.code }") == b"ARITHMETIC_ERROR"
    assert harness.evaluate("try { return 7 } except error then { return 9 }") == 7


@pytest.mark.parametrize(("expression", "expected"), [
    ('(fn (first, ...) with [] { return args.0 })(7, 8)', 7),
    ('(fn (...) with [] { return args[1] })(7, 8)', 8),
    ('(fn (...) with [] { return args.0.1 })([] [1 := 9])', 9),
    ('(fn (...) with [] { return len args })()', 0),
    ('(fn (first, ...) with [] { return args[0] })(null)', None),
    ('(fn (first, ...) with [] { return len args })(null, 2)', 1),
    ('(fn (...) with [] { return (fn (...) with [] { return args.0 })(9) })(3)', 9),
])
def test_function_args(harness, expression, expected):
    assert harness.evaluate(expression) == expected


@pytest.mark.parametrize(("text", "code", "expression"), [
    ("args", "INVALID_ARGS", True), ("self", "INVALID_SELF", True),
    ("fn () with args {}", "INVALID_ARGS", True),
    ("fn (..., value) {}", "SYNTAX_ERROR", True),
    ("fn (value, value) {}", "DUPLICATE_PARAMETER", True),
    ("return 1", "INVALID_RETURN", False),
])
def test_invalid_function_syntax(text, code, expression):
    document = compile_text(text, expression=expression, valid=False)
    assert document.units[0].diagnostics[0].code == code


@pytest.mark.parametrize("expression", ['(fn (value) with [] {} )()', '(fn () with [] {})(1)', '(fn (value, ...) with [] {})()'])
def test_argument_count(harness, expression):
    with pytest.raises(RuntimeError) as caught:
        harness.evaluate(expression)
    assert caught.value.code == "ARGUMENT_COUNT"


def test_args_and_self_ast_associations():
    document = compile_text("fn (first, ...) with [] { return do { return args.0 + self(first) } }", expression=True)
    function = document.units[0].root
    assert function.fields["variadic"] == "YES"
    for node in number_nodes(function):
        if node.kind in {"ARGS", "SELF"}:
            assert node.fields["function"] is function
        if node.kind == "RETURN":
            assert node.fields["target"].kind == "BLOCK"


def test_eval_has_its_own_args_scope(harness):
    with pytest.raises(RuntimeError) as caught:
        harness.evaluate('(fn (...) with [] { return eval "args" })(1)')
    assert caught.value.code == "INVALID_ARGS"


def test_closures_capture_definition_time_snapshot(harness):
    assert harness.evaluate("do {\nvalue := 1\nfunction := fn () { return value }\nvalue := 2\nreturn function()\n}") == 1


def test_persistent_dictionary_updates():
    original = Dictionary().with_item(1, b"original").with_item(b"key", 3)
    updated = original.with_item(1.0, b"changed")
    deleted = updated.with_item(b"key", None)
    assert original.find(1) == b"original"
    assert updated.find(1) == updated.find(1.0) == b"changed"
    assert list(deleted.items()) == [(1, b"changed")]
    assert equal(original, Dictionary().with_item(b"key", 3).with_item(1.0, b"original"))


@pytest.mark.parametrize("key", [None, float("nan"), Dictionary(), True])
def test_invalid_dictionary_keys(key):
    with pytest.raises(ValueError):
        canonical_key(key)


def test_large_integers_roundtrip(harness):
    text = b"9" * 5000
    value = parse_integer(text)
    assert integer_text(value) == text
    assert representation(harness.evaluate(text.decode("ascii"))) == text
