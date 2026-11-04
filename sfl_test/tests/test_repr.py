import math
from itertools import product

import pytest
from sfl.formats import quote
from sfl.render import representation
from sfl.values import Dictionary, Function

from .support import OPERATORS, assert_indentation


@pytest.mark.parametrize(("outer", "inner", "association"), list(product(OPERATORS, OPERATORS, ["left", "right"])))
def test_reconstructed_arithmetic_preserves_behavior(harness, outer, inner, association):
    body = f"(a {inner} b) {outer} c" if association == "left" else f"a {outer} (b {inner} c)"
    harness.compare("fn (a, b, c) with [] { return " + body + " }", [(7, 3, 2), (1e16, -1e16, 1.0), (-5, 2, -3), (1, 0, 2)])


BASIC_CASES = [
    pytest.param('do {\noffset := 10\nreturn fn (value) { return value + offset }\n}', (0,), ("VALUE", b"10", []), id="capture-zero"),
    pytest.param('do {\noffset := 10\nreturn fn (value) { return value + offset }\n}', (31,), ("VALUE", b"41", []), id="capture"),
    pytest.param('fn (a, b, c) with [] { return a + (b + c) }', (1e16, -1e16, 1.0), ("VALUE", b"0.0", []), id="float-association"),
    pytest.param('fn (content) with [] { return "hello, " + content + "!" }', (b"neko",), ("VALUE", b'"hello, neko!"', []), id="concatenation"),
    pytest.param('fn (content) with [] { return "hello, " + content + "!" }', (b"",), ("VALUE", b'"hello, !"', []), id="empty-concatenation"),
    pytest.param('fn (value) with [] { return value[0 := 65][1 := 66] }', (b"123",), ("VALUE", b'"AB3"', []), id="byte-update"),
    pytest.param('fn (value) with [] { return value[0 := 65][1 := 66] }', (b"xx",), ("VALUE", b'"AB"', []), id="byte-update-short"),
    pytest.param('fn (value) with [] { return [] ["key" := value].key }', (21,), ("VALUE", b"21", []), id="member"),
    pytest.param('fn (value) with [] { return [] ["key" := value].key }', (None,), ("ERROR", "MISSING_MEMBER", []), id="missing-member"),
    pytest.param('fn (value) with [] { return len repr -value }', (21,), ("VALUE", b"3", []), id="prefixes"),
    pytest.param('fn (value) with [] { return len repr -value }', (-1.5,), ("VALUE", b"3", []), id="prefixes-float"),
    pytest.param('fn (value) with [] { return if value > 0 then { return 1 } else { return 2 } }', (3,), ("VALUE", b"1", []), id="conditional-true"),
    pytest.param('fn (value) with [] { return if value > 0 then { return 1 } else { return 2 } }', (-3,), ("VALUE", b"2", []), id="conditional"),
    pytest.param('fn (value) with [] { return do {\nresult := value + 1\nreturn result * 2\n} }', (3,), ("VALUE", b"8", []), id="do"),
    pytest.param('fn (value) with [] { return do {\nresult := value + 1\nreturn result * 2\n} }', (-3,), ("VALUE", b"-4", []), id="do-negative"),
    pytest.param('fn (value) with [] { return try { raise value + 1 } except error then { return error * 2 } }', (3,), ("VALUE", b"8", []), id="except"),
    pytest.param('fn (value) with [] { return try { raise value + 1 } except error then { return error * 2 } }', (-3,), ("VALUE", b"-4", []), id="except-negative"),
    pytest.param('fn (value) with [] { return raise value }', (3,), ("RAISE", b"3", []), id="raise-integer"),
    pytest.param('fn (value) with [] { return raise value }', (b"error",), ("RAISE", b'"error"', []), id="raise-string"),
    pytest.param('fn (value) with [] { return raise value }', (None,), ("RAISE", b"null", []), id="raise-null"),
    pytest.param('fn (value) with [] { return (emit value as "echo") + (emit 2 as "echo") }', (3,), ("VALUE", b"5", [3, 2]), id="signal-order"),
    pytest.param('fn (value) with [] { return (emit value as "echo") + (emit 2 as "echo") }', (-3,), ("VALUE", b"-1", [-3, 2]), id="signal-order-negative"),
    pytest.param('fn (value) with [] { return emit value + 1 as "echo" }', (3,), ("VALUE", b"4", [4]), id="greedy-signal"),
    pytest.param('fn (value) with [] { return emit value + 1 as "echo" }', (-3,), ("VALUE", b"-2", [-2]), id="greedy-signal-negative"),
    pytest.param('fn (value) with [] { return eval "value + 1" with [] ["value" := value] }', (3,), ("VALUE", b"4", []), id="eval-environment"),
    pytest.param('fn (value) with [] { return eval "value + 1" with [] ["value" := value] }', (-3,), ("VALUE", b"-2", []), id="eval-environment-negative"),
    pytest.param('fn () with [] { return (1).0 }', (), ("ERROR", "TYPE_ERROR", []), id="numeric-member"),
    pytest.param('fn (number, ...) with [] { return if number <= 0 then { return len args } else { return self(number - 1, args.1, args[2]) } }', (4, 5, 6), ("VALUE", b"3", []), id="self-and-args"),
    pytest.param('fn (number, ...) with [] { return if number <= 0 then { return len args } else { return self(number - 1, args.1, args[2]) } }', (0, 5, 6), ("VALUE", b"3", []), id="self-and-args-base"),
    pytest.param('fn (first, ...) with [] { return args[0 := first + 1] }', (2,), ("VALUE", b"[][0 := 3]", []), id="variadic-minimum"),
    pytest.param('fn (first, ...) with [] { return args[0 := first + 1] }', (2, 3, None, b"x"), ("VALUE", b'[][0 := 3, 1 := 3, 3 := "x"]', []), id="variadic"),
    pytest.param('do {\nvalue := null\nreturn fn () { return type value }\n}', (), ("VALUE", b'"null"', []), id="null-capture"),
    pytest.param('do {\nvalue := null\nreturn fn (value) { return type value }\n}', (9,), ("VALUE", b'"int"', []), id="shadowed-capture"),
    pytest.param('do {\nvalue := null\nreturn fn (value) { return type value }\n}', (None,), ("VALUE", b'"null"', []), id="null-parameter"),
]


@pytest.mark.parametrize(("function_text", "arguments", "expected"), BASIC_CASES)
def test_reconstructed_functions(harness, function_text, arguments, expected):
    original, restored = harness.compare(function_text, [arguments])
    assert harness.outcome(original, arguments) == harness.outcome(restored, arguments) == expected


@pytest.mark.parametrize("inner_environment", ["", " with []"])
def test_nested_eval_environment_ownership(harness, inner_environment):
    inner = "eval " + quote(b'"value"') + inner_environment
    function_text = 'fn () with [] { return eval (' + inner + ') with [] ["value" := 42] }'
    original, restored = harness.compare(function_text, [()])
    assert harness.call(original) == harness.call(restored) == 42


def test_nested_eval_signal_ownership(harness):
    inner = "eval " + quote(b'"emit 7 as \\"echo\\""') + " with []"
    function_text = 'fn () with [] { return eval (' + inner + ') with_signal "echo" }'
    original, restored = harness.compare(function_text, [()])
    assert harness.outcome(restored) == ("VALUE", b"7", [7])


def test_evaluated_signal_name(harness):
    function_text = 'fn () with [] { return eval ' + quote(b'emit 7 as "echo"') + ' with_signal (eval ' + quote(b'"echo"') + ') }'
    original, restored = harness.compare(function_text, [()])
    assert harness.outcome(restored) == ("VALUE", b"7", [7])


def test_function_representation_example(harness):
    original = harness.evaluate('do {\noffset := 10\nreturn fn (value) { return value + offset }\n}')
    assert representation(original) == b'fn (value) with [][\n  "offset" := 10\n] {\n  return value + offset\n}'


CLOSURE_CASES = [
    pytest.param('fn (value) with [] { return fn () { return type value } }', (None,), 1, ("VALUE", b'"null"', []), id="null-argument"),
    pytest.param('fn (value) with [] { return fn () { return type value } }', (23,), 1, ("VALUE", b'"int"', []), id="integer-argument"),
    pytest.param('fn () with [] {\nvalue := null\nreturn fn () { return type value }\n}', (), 1, ("VALUE", b'"null"', []), id="null-local"),
    pytest.param('fn () with [] { return try { raise null } except error then { return fn () { return type error } } }', (), 1, ("VALUE", b'"null"', []), id="exception-binding"),
    pytest.param('fn (env) with [] { return fn () with env { return fn () { return value } } }', (Dictionary().with_item(b"value", 23),), 2, ("VALUE", b"23", []), id="dynamic-environment"),
    pytest.param('fn () with [] { return fn () with (emit [] ["value" := 23] as "environment") { return fn () { return value } } }', (), 2, ("VALUE", b"23", []), id="environment-once"),
    pytest.param('fn (env) with [] { return fn () with env { return fn () { return value + _sfl_repr_env_1 } } }', (Dictionary().with_item(b"value", 23).with_item(b"_sfl_repr_env_1", 5),), 2, ("VALUE", b"28", []), id="helper-collision"),
    pytest.param('fn () with [] { return fn () with [] { return fn () { return missing } } }', (), 2, ("ERROR", "UNDEFINED_NAME", []), id="lazy-missing-capture"),
]


@pytest.mark.parametrize(("function_text", "arguments", "depth", "expected"), CLOSURE_CASES)
def test_nested_closure_reconstruction(harness, function_text, arguments, depth, expected):
    original = harness.evaluate(function_text)
    restored = harness.rebuild(original)
    harness.events.clear()
    left = harness.call(original, arguments)
    left_events = list(harness.events)
    harness.events.clear()
    right = harness.call(restored, arguments)
    assert left_events == harness.events
    if 'as "environment"' in function_text:
        assert len(harness.events) == 1
    for position in range(depth - 1):
        left, right = harness.call(left), harness.call(right)
    assert harness.outcome(left) == harness.outcome(right) == expected
    repeated = harness.rebuild(right)
    assert harness.outcome(repeated) == expected


def test_dynamic_environment_in_call_position(harness):
    function_text = "fn (env) with [] { return (fn () with env { return fn () { return value } })()() }"
    original, restored = harness.compare(function_text, [(Dictionary().with_item(b"value", 17),)])
    assert harness.call(restored, (Dictionary().with_item(b"value", 17),)) == 17


def test_enclosing_args_in_nested_environment(harness):
    function_text = 'fn (...) with [] { return fn () with [] ["copy" := args] { return copy } }'
    original = harness.evaluate(function_text)
    restored = harness.rebuild(original)
    left, right = harness.call(original, (2, None, 4)), harness.call(restored, (2, None, 4))
    assert representation(harness.call(left)) == representation(harness.call(right)) == b"[][0 := 2, 2 := 4]"


def test_captured_imported_module(harness):
    original = harness.module('import "stdio"\nexport fn (value) { return stdio.print(value) } as function').find(b"function")
    restored = harness.rebuild(original)
    assert harness.outcome(original, (b"hello",)) == harness.outcome(restored, (b"hello",)) == ("VALUE", b'"hello"', [b"hello"])


def test_functions_in_captured_dictionaries(harness):
    inner = harness.evaluate("fn (value) with [] { return value + 1 }")
    captures = Dictionary().with_item(b"inner", inner).with_item(b"text", b'([{}])"\\')
    original = harness.evaluate("fn (value) with env { return inner(value) }", {b"env": captures})
    restored = harness.rebuild(original)
    assert harness.call(original, (5,)) == harness.call(restored, (5,)) == 6
    dictionary = Dictionary().with_item(b"nested", original)
    text = representation(dictionary).decode("ascii")
    assert_indentation(text)
    reconstructed = harness.evaluate(text).find(b"nested")
    assert isinstance(reconstructed, Function)
    assert harness.call(reconstructed, (5,)) == 6


@pytest.mark.parametrize("value", [None, 12, -12, 1.5, -1.5, float("inf"), -float("inf"), float("nan"), b'([{}])"\\', Dictionary().with_item(2, b"value")])
def test_scalar_representation_roundtrip(harness, value):
    restored = harness.evaluate(representation(value).decode("ascii"))
    if type(value) is float and math.isnan(value):
        assert math.isnan(restored)
        assert representation(value) == b"inf - inf"
    else:
        assert representation(value) == representation(restored)


@pytest.mark.parametrize("value", [float("nan"), -float("inf")])
def test_nonfinite_capture(harness, value):
    original = harness.evaluate("fn () { return type value }", {b"value": value})
    restored = harness.rebuild(original)
    assert harness.call(restored) == b"float"
