from itertools import product

import pytest
from sfl.render import Scope, SourceRenderer, source

from .support import OPERATORS, assert_parentheses_necessary, assert_source_roundtrip, compile_text, fingerprint, random_expressions


GOLDEN = {
    "(a + (b * c))": "a + b * c",
    "((a + b) * c)": "(a + b) * c",
    "(a - (b - c))": "a - (b - c)",
    "(a + (b + c))": "a + (b + c)",
    "((a < b) == c)": "(a < b) == c",
    "(a < (b == c))": "a < (b == c)",
    "(len (args[0]))": "len args[0]",
    "((len args)[0])": "(len args)[0]",
    "(((a(b)).value)[0])": "a(b).value[0]",
    "((1).0)": "(1).0",
    "((1).value)": "1.value",
    "(emit a as b)": "emit a as b",
    "((emit a as b) + c)": "(emit a as b) + c",
    "(a + (raise b))": "a + raise b",
    "(len (eval a))": "len eval a",
    "(eval (eval a) with b)": "eval (eval a) with b",
    "(eval (eval a with b) with c)": "eval eval a with b with c",
    "(eval a with_signal (eval b) with_signal c)": "eval a with_signal (eval b) with_signal c",
    "(eval (raise (eval a)) with b)": "eval raise (eval a) with b",
    "((1.5).0)": "1.5.0",
    "((1e2).0)": "1e2.0",
    "((-1).0)": "(-1).0",
    "(-(-a))": "--a",
    "((a + b)[0])": "(a + b)[0]",
    "((a + b)(c))": "(a + b)(c)",
}


@pytest.mark.parametrize(("expression", "expected"), GOLDEN.items())
def test_minimal_rendering(expression, expected):
    original, rendered = assert_source_roundtrip(expression)
    value = original.fields["body"][0].fields["value"].fields["body"].fields["body"][0].fields["value"]
    assert SourceRenderer(value).render(value, Scope()) == expected


@pytest.mark.parametrize(("outer", "inner", "association"), list(product(OPERATORS, OPERATORS, ["left", "right"])))
def test_operator_ast_roundtrip(outer, inner, association):
    expression = f"((a {inner} b) {outer} c)" if association == "left" else f"(a {outer} (b {inner} c))"
    assert_source_roundtrip(expression)


@pytest.mark.parametrize("expression", [pytest.param(expression, id=f"seed-20261102-{position:04d}") for position, expression in enumerate(random_expressions(2200))])
def test_randomized_ast_roundtrip(expression):
    assert_source_roundtrip(expression)


KEYWORD_PARTS = [
    "a", "(a + b)", "(len a)", "(raise a)", "(emit a as b)",
    "(eval a)", "(eval a with b)", "(eval a with_signal b)",
    "(eval a with b with_signal c)", "(eval (eval a) with b)",
    "(eval (raise (eval a)))", "(a + (eval b))",
]
KEYWORD_CONTAINERS = [
    "(eval VALUE with b)", "(eval VALUE with_signal b)",
    "(eval a with VALUE with_signal b)", "(eval a with_signal VALUE with_signal b)",
    "(eval VALUE with b with_signal c)", "(emit a as VALUE)",
    "(raise VALUE)", "(len VALUE)", "(a + VALUE)", "(VALUE + a)",
    "(VALUE[0])", "(VALUE())",
]


@pytest.mark.parametrize(("inner", "container", "tail"), list(product(KEYWORD_PARTS, KEYWORD_CONTAINERS, ["", " + c", "[0]"])))
def test_keyword_boundary(inner, container, tail):
    assert_source_roundtrip("(" + container.replace("VALUE", inner) + ")" + tail)


@pytest.mark.parametrize("expression", [pytest.param(expression, id=f"minimal-{position:03d}") for position, expression in enumerate([*GOLDEN, *random_expressions(180, depth=3, skip=2200)])])
def test_remaining_parentheses_are_necessary(expression):
    original, rendered = assert_source_roundtrip(expression)
    assert assert_parentheses_necessary(original, rendered) >= 1


def test_source_rendering_preserves_implicit_environments():
    document = compile_text("offset := 10\nexport fn (value) { return fn () { return value + offset } } as make")
    original = document.units[0].root
    rendered = source(original)
    assert "with" not in rendered
    assert fingerprint(compile_text(rendered).units[0].root) == fingerprint(original)
