import pytest
from sfl.formats import quote, read_ast, read_lex, unquote, write_ast, write_lex
from sfl.lexer import lex
from sfl.model import FormatError, Position, number_nodes

from .support import compile_text, fingerprint, source_info


@pytest.mark.parametrize("value", [bytes([value]) for value in range(256)] + [bytes(range(256)), b'"\\\n\r\t#([{}])'])
def test_byte_literal_roundtrip(value):
    text = quote(value)
    assert text.isascii()
    assert unquote(text) == value
    document = compile_text(text, expression=True)
    assert document.units[0].root.fields["bytes"] == value


def test_lexical_roundtrip_and_positions():
    data = b'item := "a\\n"\r\n# comment\r\nargs.0.1'
    original = lex(data, source_info(data))
    restored = read_lex(write_lex(original), data)
    assert original == restored
    tokens = restored.units[0].tokens
    assert [token.kind for token in tokens] == ["IDENT", "BIND", "STRING", "NEWLINE", "COMMENT", "NEWLINE", "KW_ARGS", "DOT", "INT", "DOT", "INT", "EOF"]
    assert tokens[0].span.start == Position(1, 1)
    assert tokens[2].payload == b"a\n"
    assert tokens[3].span.end == Position(2, 1)
    assert tokens[6].span.start == Position(3, 1)


@pytest.mark.parametrize(("data", "code"), [(b"@", "INVALID_BYTE"), (b'"\\q"', "INVALID_ESCAPE"), (b'"open', "UNTERMINATED_STRING"), (b"1e+", "INVALID_NUMBER")])
def test_lexical_errors_are_serializable(data, code):
    lexical = read_lex(write_lex(lex(data, source_info(data))), data)
    assert lexical.units[0].tokens[0].kind == "ERROR"
    assert lexical.units[0].tokens[0].payload == code.encode("ascii")
    document = compile_text(data, valid=False)
    assert document.units[0].status == "INVALID"
    assert document.units[0].diagnostics[0].code == code


def test_serialized_ast_preserves_associations_and_fields():
    document = compile_text("export fn (first, ...) with [] { return do { return args.0 + self(first) } } as function")
    restored = read_ast(write_ast(document))
    assert fingerprint(document.units[0].root) == fingerprint(restored.units[0].root)
    assert [node.number for node in number_nodes(restored.units[0].root)] == list(range(1, len(number_nodes(restored.units[0].root)) + 1))


@pytest.mark.parametrize("kind", ["lex", "ast"])
def test_headers_appear_once_and_unit_ids_are_integers(kind):
    data = b"export 42 as value\n"
    text = write_lex(lex(data, source_info(data))) if kind == "lex" else write_ast(compile_text(data))
    for header in ["SOURCE_FILE ", "SOURCE_SHA256 ", "SOURCE_MTIME_UTC "]:
        assert text.count(header) == 1
    assert "\nUNIT 1 " in text
    assert source_info(data).sha256 in text


@pytest.mark.parametrize("kind", ["lex", "ast"])
@pytest.mark.parametrize("header", ["SOURCE_FILE", "SOURCE_SHA256", "SOURCE_MTIME_UTC"])
def test_duplicate_headers_are_rejected(kind, header):
    data = b"export 42 as value\n"
    text = write_lex(lex(data, source_info(data))) if kind == "lex" else write_ast(compile_text(data))
    duplicate = next(line for line in text.splitlines() if line.startswith(header + " "))
    corrupted = text.replace("\n\n", "\n" + duplicate + "\n\n", 1)
    with pytest.raises(FormatError):
        read_lex(corrupted, data) if kind == "lex" else read_ast(corrupted)


@pytest.mark.parametrize("kind", ["lex", "ast"])
def test_comments_are_supported_in_cache_text(kind):
    data = b"export 42 as value\n"
    text = write_lex(lex(data, source_info(data))) if kind == "lex" else write_ast(compile_text(data))
    commented = "# explanatory comment\n" + text.replace("\nEND", "\n# end of this unit\nEND")
    restored = read_lex(commented, data) if kind == "lex" else read_ast(commented)
    assert restored.source.sha256 == source_info(data).sha256


@pytest.mark.parametrize("kind", ["lex", "ast"])
@pytest.mark.parametrize("corruption", ["version", "unit", "truncated", "hash"])
def test_corrupt_formats_are_rejected(kind, corruption):
    data = b"export 42 as value\n"
    text = write_lex(lex(data, source_info(data))) if kind == "lex" else write_ast(compile_text(data))
    if corruption == "version":
        text = text.replace(" 1\n", " 99\n", 1)
    elif corruption == "unit":
        text = text.replace("UNIT 1 ", 'UNIT "one" ')
    elif corruption == "truncated":
        text = text.rsplit("END", 1)[0]
    else:
        text = text.replace(source_info(data).sha256, "not-a-hash")
    with pytest.raises(FormatError):
        read_lex(text, data) if kind == "lex" else read_ast(text)
