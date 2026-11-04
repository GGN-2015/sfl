import io
import sys
from types import SimpleNamespace

import pytest
import sfl
from sfl.formats import quote
from sfl.model import SflError
from sfl.runtime import Interpreter, RuntimeError
from sfl.signals import CompositeSignalHandler, SignalHandler, StandardSignalHandler, StdioSignalHandler, StrSignalHandler

from .support import Harness, compile_text, copy_standard_library


class RecordingHandler(SignalHandler):
    def __init__(self, name=b"custom", result=None):
        super().__init__()
        self.calls = []
        self.result = result
        self.register(name, self.record)

    def record(self, value):
        self.calls.append(value)
        return self.result


def test_registration_and_unregistration():
    handler = SignalHandler()
    assert not handler.supports(b"custom")
    assert handler.handle(b"custom", 1) is None
    handler.register(b"custom", lambda value: value + 1)
    assert handler.supports(b"custom")
    assert handler.handle(b"custom", 2) == 3
    handler.register(b"custom", lambda value: value + 2)
    assert handler.handle(b"custom", 2) == 4
    handler.unregister(b"custom")
    handler.unregister(b"custom")
    assert not handler.supports(b"custom")


@pytest.mark.parametrize(("name", "handler"), [("custom", lambda value: value), (b"custom", None)])
def test_invalid_registrations(name, handler):
    with pytest.raises(TypeError):
        SignalHandler().register(name, handler)


@pytest.mark.parametrize("container", [list, tuple, iter], ids=["list", "tuple", "generator"])
def test_interpreter_accepts_multiple_handlers(container):
    first = RecordingHandler(result=1)
    last = RecordingHandler(result=2)
    interpreter = Interpreter(signals=container([first, last]))
    document = compile_text('emit 7 as "custom"', expression=True)
    assert interpreter.execute(document) == interpreter.execute(document) == 2
    assert first.calls == []
    assert last.calls == [7, 7]


def test_null_result_does_not_fall_back():
    first, last = RecordingHandler(result=1), RecordingHandler()
    composite = CompositeSignalHandler([first, last])
    assert composite.handle(b"custom", 7) is None
    assert first.calls == []
    assert last.calls == [7]


def test_composite_override_and_member_updates():
    first = SignalHandler({b"custom": lambda value: 1})
    last = SignalHandler({b"custom": lambda value: 2})
    composite = CompositeSignalHandler([first, last])
    assert composite.handle(b"custom", 0) == 2
    last.unregister(b"custom")
    assert composite.handle(b"custom", 0) == 1
    last.register(b"custom", lambda value: 3)
    assert composite.handle(b"custom", 0) == 3
    composite.register(b"custom", lambda value: 4)
    assert composite.handle(b"custom", 0) == 4
    composite.unregister(b"custom")
    assert composite.handle(b"custom", 0) == 3


def test_nested_composition_with_dynamic_handler():
    class DynamicHandler(SignalHandler):
        def supports(self, name):
            return name == b"dynamic"

        def handle(self, name, value):
            return value + 1 if self.supports(name) else None

    composite = CompositeSignalHandler([CompositeSignalHandler([DynamicHandler()])])
    assert composite.supports(b"dynamic")
    assert composite.handle(b"dynamic", 7) == 8
    assert not composite.supports(b"other")


@pytest.mark.parametrize("handlers", [[None], [lambda value: value], [{b"custom": lambda value: value}]])
def test_invalid_handler_members(handlers):
    with pytest.raises(TypeError):
        CompositeSignalHandler(handlers)


def test_mapping_is_copied():
    mapping = {b"custom": lambda value: 1}
    interpreter = Interpreter(signals=mapping)
    mapping[b"custom"] = lambda value: 2
    assert interpreter.execute(compile_text('emit 0 as "custom"', expression=True)) == 1


def test_defaults_and_explicitly_empty_handlers():
    assert isinstance(Interpreter().signals, StandardSignalHandler)
    document = compile_text('emit "A" as "str.ord"', expression=True)
    for empty in [[], {}, SignalHandler()]:
        assert Interpreter(signals=empty).execute(document) is None
    assert Interpreter().execute(document) == 65


def test_run_source_accepts_handler_iterable(tmp_path):
    source = tmp_path / "signals.sfl"
    source.write_text('export emit 7 as "custom" as value\n', encoding="ascii")
    first, last = RecordingHandler(result=1), RecordingHandler(result=2)
    assert sfl.run_source(source, signals=(first, last)).find(b"value") == 2
    assert last.calls == [7]


def test_eval_permissions_and_outer_restriction(harness):
    code = quote(b'emit 7 as "echo"')
    assert harness.evaluate("eval " + code) is None
    assert harness.events == []
    assert harness.evaluate("eval " + code + ' with_signal "echo"') == 7
    assert harness.events == [7]
    harness.events.clear()
    nested = quote(('eval ' + code + ' with_signal "echo"').encode("ascii"))
    assert harness.evaluate("eval " + nested) is None
    assert harness.events == []


def test_unregistered_signal_returns_null(harness):
    assert harness.evaluate('emit 7 as "unknown"') is None


def test_invalid_signal_result_is_a_language_error(harness):
    harness.interpreter.signals.register(b"bad", lambda value: object())
    with pytest.raises(RuntimeError) as caught:
        harness.evaluate('emit 7 as "bad"')
    assert caught.value.code == "SIGNAL_RESULT"


def test_handler_language_error_has_source_location(harness):
    def fail(value):
        raise SflError("CUSTOM_ERROR", "bad value")

    harness.interpreter.signals.register(b"bad", fail)
    with pytest.raises(RuntimeError) as caught:
        harness.evaluate('emit 7 as "bad"')
    assert caught.value.code == "CUSTOM_ERROR"
    assert caught.value.source == "<test>"
    assert caught.value.span.start.line == 1
    assert harness.evaluate('try { emit 7 as "bad" } except error then { return error.code }') == b"CUSTOM_ERROR"


def test_unexpected_host_error_is_not_caught(harness):
    def fail(value):
        raise ValueError("host failure")

    harness.interpreter.signals.register(b"bad", fail)
    with pytest.raises(ValueError, match="host failure"):
        harness.evaluate('try { emit 7 as "bad" } except error then { return 0 }')


def test_stdio_binary_output(monkeypatch):
    output = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    handler = StdioSignalHandler()
    assert handler.handle(b"stdio.print", b"\x00\xff") is None
    assert handler.handle(b"stdio.write", 42) is None
    assert output.getvalue() == b"\x00\xff\n42"


def test_stdio_text_output(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    handler = StdioSignalHandler()
    handler.print(b"hello")
    handler.write(42)
    assert output.getvalue() == "hello\n42"


@pytest.mark.parametrize("binary", [False, True])
def test_stdio_readline_preserves_bytes_and_normalizes_crlf(monkeypatch, binary):
    data = b"first\r\nsecond\nthird\rfourth\nfinal"
    stream = SimpleNamespace(buffer=io.BytesIO(data)) if binary else io.StringIO(data.decode("ascii"))
    monkeypatch.setattr(sys, "stdin", stream)
    handler = StdioSignalHandler()
    assert [handler.readline(None) for position in range(5)] == [b"first\n", b"second\n", b"third\rfourth\n", b"final", None]


@pytest.mark.parametrize("value", range(256))
def test_str_ord_signal(value):
    assert StrSignalHandler().handle(b"str.ord", bytes([value])) == value


@pytest.mark.parametrize(("value", "code"), [(42, "TYPE_ERROR"), (None, "TYPE_ERROR"), (b"", "INVALID_LENGTH"), (b"AB", "INVALID_LENGTH"), ("中".encode("utf-8"), "INVALID_LENGTH")])
def test_str_ord_errors(value, code):
    with pytest.raises(SflError) as caught:
        StrSignalHandler().ord(value)
    assert caught.value.code == code


@pytest.fixture(scope="module")
def strings(tmp_path_factory):
    library = copy_standard_library(tmp_path_factory.mktemp("strings") / "stdlib")
    interpreter = Interpreter(signals=StrSignalHandler(), library_paths=[library])
    harness = Harness(interpreter, [], library)
    return harness, harness.module('import "str"\nexport str').find(b"str")


@pytest.mark.parametrize("value", range(256))
def test_stdlib_chr_and_ord(strings, value):
    harness, functions = strings
    assert harness.call(functions.find(b"chr"), (value,)) == bytes([value])
    assert harness.call(functions.find(b"ord"), (bytes([value]),)) == value


@pytest.mark.parametrize("value", [0, 65, 127, 255])
def test_stdlib_function_representation(harness, value):
    original = harness.module('import "str"\nexport fn (value) { return str.chr(value) } as function').find(b"function")
    restored = harness.rebuild(original)
    assert harness.call(original, (value,)) == harness.call(restored, (value,)) == bytes([value])


def test_restricted_ord(harness):
    expression = b'do {\nimport "str"\nreturn str.ord("A")\n}'
    assert harness.evaluate("eval " + quote(expression)) is None
    assert harness.evaluate("eval " + quote(expression) + ' with_signal "str.ord"') == 65
