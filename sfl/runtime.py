from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .formats import read_ast, write_ast
from .imports import resolve_import
from .lexer import IDENTIFIER, KEYWORDS, lex
from .model import AstDocument, Node, SflError, SourceInfo
from .parser import parse
from .render import representation
from .signals import CompositeSignalHandler, SignalHandler, StandardSignalHandler
from .values import Dictionary, Function, canonical_key, equal, parse_integer


class RuntimeError(SflError):
    @property
    def value(self) -> Dictionary:
        result = Dictionary().with_item(b"code", self.code.encode("utf-8")).with_item(b"message", self.message.encode("utf-8"))
        if self.source:
            result = result.with_item(b"source", self.source.encode("utf-8"))
        if self.span:
            result = result.with_item(b"line", self.span.start.line).with_item(b"column", self.span.start.column)
        return result


class RaisedValue(RuntimeError):
    def __init__(self, value, span=None, source=None):
        super().__init__("RAISED_VALUE", "Uncaught raised value", span, source)
        self.payload = value

    @property
    def value(self) -> Any:
        return self.payload

    def __str__(self) -> str:
        return super().__str__() + ": " + representation(self.value).decode("ascii")


class ReturnSignal(BaseException):
    def __init__(self, target: Node, value):
        self.target = target
        self.value = value


@dataclass
class Context:
    document: AstDocument
    origin: Path | None
    environment: dict[bytes, Any]
    function: Function | None = None
    allowed: frozenset[bytes] | None = None
    exports: Dictionary = field(default_factory=Dictionary)
    arguments: Dictionary = field(default_factory=Dictionary)


def valid_value(value) -> bool:
    return value is None or type(value) in {int, float, bytes} or isinstance(value, (Dictionary, Function))


class Interpreter:
    def __init__(self, compiler=None, signals: SignalHandler | Iterable[SignalHandler] | Mapping[bytes, Callable[[Any], Any]] | None = None, library_paths=()):
        if compiler is None:
            from .cache import Compiler
            compiler = Compiler()
        self.compiler = compiler
        if signals is None:
            self.signals: SignalHandler = StandardSignalHandler()
        elif isinstance(signals, SignalHandler):
            self.signals = signals
        elif isinstance(signals, Mapping):
            self.signals = SignalHandler(signals)
        else:
            self.signals = CompositeSignalHandler(signals)
        self.library_paths = [Path(path).resolve() for path in library_paths]
        self.library_paths.extend(Path(path).resolve() for path in os.environ.get("SFL_PATH", "").split(os.pathsep) if path)
        self.library_paths.append(Path(__file__).parent / "stdlib")
        self.modules: dict[Path, Dictionary] = {}
        self.loading: set[Path] = set()
        self.dynamic: dict[bytes, AstDocument] = {}

    def fail(self, code: str, message: str, node: Node, context: Context):
        source = context.document.source.filename
        if context.origin is not None and not source.startswith("<"):
            source = str(context.origin)
        raise RuntimeError(code, message, node.span, source)

    def check_document(self, document: AstDocument, origin: Path | None = None):
        if len(document.units) != 1:
            raise SflError("INVALID_UNIT_COUNT", "Execution requires a single input unit", source=str(origin))
        unit = document.units[0]
        if unit.status != "VALID" or unit.root is None:
            diagnostic = unit.diagnostics[0] if unit.diagnostics else None
            raise SflError(diagnostic.code if diagnostic else "INVALID_AST", diagnostic.message if diagnostic else "Cannot execute invalid AST", diagnostic.span if diagnostic else unit.span, str(origin) if origin else document.source.filename)

    def execute(self, document: AstDocument, origin: Path | None = None, environment: dict[bytes, Any] | None = None):
        self.check_document(document, origin)
        return self.evaluate(document.units[0].root, Context(document, origin, dict(environment or {})))

    def run(self, path: str | Path):
        self.modules.clear()
        return self.load_module(Path(path).resolve())

    def load_module(self, path: Path, allowed: frozenset[bytes] | None = None):
        path = path.resolve()
        if allowed is None and path in self.modules:
            return self.modules[path]
        if path in self.loading:
            raise RuntimeError("IMPORT_CYCLE", f"Circular module import: {path}", source=str(path))
        self.loading.add(path)
        try:
            document = self.compiler.ast(path)
            self.check_document(document, path)
            result = self.evaluate(document.units[0].root, Context(document, path, {}, allowed=allowed))
            if allowed is None:
                self.modules[path] = result
            return result
        finally:
            self.loading.remove(path)

    def evaluate(self, node: Node, context: Context):
        try:
            return self._evaluate(node, context)
        except (ZeroDivisionError, OverflowError) as error:
            self.fail("ARITHMETIC_ERROR", str(error), node, context)

    def _evaluate(self, node: Node, context: Context):
        kind, fields = node.kind, node.fields
        if kind == "MODULE":
            for statement in fields["body"]:
                self.evaluate(statement, context)
            return context.exports
        if kind == "BLOCK":
            inner = Context(context.document, context.origin, dict(context.environment), context.function, context.allowed, arguments=context.arguments)
            try:
                for statement in fields["body"]:
                    self.evaluate(statement, inner)
            except ReturnSignal as signal:
                if signal.target is not node:
                    raise
                return signal.value
            return None
        if kind == "BINDING":
            value = self.evaluate(fields["value"], context)
            context.environment[fields["name"]] = value
            return None
        if kind == "EXPR_STMT":
            self.evaluate(fields["value"], context)
            return None
        if kind == "NAME":
            if fields["name"] not in context.environment:
                self.fail("UNDEFINED_NAME", "Undefined name: " + fields["name"].decode("ascii"), node, context)
            return context.environment[fields["name"]]
        if kind == "INT":
            return parse_integer(fields["text"])
        if kind == "FLOAT":
            return float(fields["text"])
        if kind == "STRING":
            return fields["bytes"]
        if kind == "INF":
            return float("inf")
        if kind == "NULL":
            return None
        if kind == "EMPTY_DICT":
            return Dictionary()
        if kind == "UNARY":
            value = self.evaluate(fields["operand"], context)
            if type(value) not in {int, float}:
                self.fail("TYPE_ERROR", "Unary minus requires a number", node, context)
            return -value
        if kind == "BINARY":
            left = self.evaluate(fields["left"], context)
            right = self.evaluate(fields["right"], context)
            return self.binary(fields["op"], left, right, node, context)
        if kind == "DICT_UPDATE":
            result = self.evaluate(fields["base"], context)
            if not isinstance(result, (Dictionary, bytes)):
                self.fail("TYPE_ERROR", "Updates require a dictionary or byte string", node, context)
            for entry in fields["entries"]:
                key = self.evaluate(entry.fields["key"], context)
                value = self.evaluate(entry.fields["value"], context)
                if isinstance(result, bytes):
                    result = self.update_string(result, key, value, entry, context)
                else:
                    try:
                        result = result.with_item(key, value)
                    except ValueError as error:
                        self.fail("INVALID_KEY", str(error), entry, context)
            return result
        if kind in {"INDEX", "MEMBER"}:
            base = self.evaluate(fields["base"], context)
            key = self.evaluate(fields["key"], context) if kind == "INDEX" else parse_integer(fields["key"]) if fields["key_kind"] == "INT" else fields["key"]
            try:
                key = canonical_key(key)
            except ValueError as error:
                self.fail("INVALID_KEY", str(error), node, context)
            if isinstance(base, Dictionary):
                value = base.find(key)
            elif isinstance(base, bytes):
                value = base[key:key + 1] if type(key) is int and 0 <= key < len(base) else None
            else:
                self.fail("TYPE_ERROR", "Indexing requires a dictionary or byte string", node, context)
            if value is None and kind == "MEMBER":
                self.fail("MISSING_MEMBER", "Member does not exist: " + representation(key).decode("ascii"), node, context)
            return value
        if kind == "FUNCTION":
            captured = dict(context.environment)
            if fields["environment"] is not None:
                captured = self.environment(self.evaluate(fields["environment"], context), node, context)
            return Function(node, captured, context.document, context.origin)
        if kind in {"SELF", "ARGS"}:
            if context.function is None or context.function.node is not fields["function"]:
                self.fail("INVALID_" + kind, kind.lower() + " does not belong to this function", node, context)
            return context.function if kind == "SELF" else context.arguments
        if kind == "CALL":
            function = self.evaluate(fields["callee"], context)
            args = [self.evaluate(argument, context) for argument in fields["args"]]
            if not isinstance(function, Function):
                self.fail("TYPE_ERROR", "Call target is not a function", node, context)
            params = function.node.fields["params"]
            variadic = function.node.fields["variadic"] == "YES"
            if len(args) < len(params) or (not variadic and len(args) > len(params)):
                expected = "at least " + str(len(params)) if variadic else str(len(params))
                self.fail("ARGUMENT_COUNT", f"Expected {expected} arguments, got {len(args)}", node, context)
            environment = dict(function.environment)
            environment.update((param.fields["name"], value) for param, value in zip(params, args))
            arguments = Dictionary()
            for position, value in enumerate(args):
                arguments = arguments.with_item(position, value)
            return self.evaluate(function.node.fields["body"], Context(function.document, function.origin, environment, function, context.allowed, arguments=arguments))
        if kind == "IF":
            condition = self.evaluate(fields["condition"], context)
            if type(condition) is not int:
                self.fail("TYPE_ERROR", "if condition must be an integer", node, context)
            return self.evaluate(fields["then"] if condition != 0 else fields["else"], context)
        if kind == "DO":
            return self.evaluate(fields["body"], context)
        if kind == "RETURN":
            raise ReturnSignal(fields["target"], self.evaluate(fields["value"], context))
        if kind == "RAISE":
            value = self.evaluate(fields["value"], context)
            source = context.document.source.filename
            if context.origin is not None and not source.startswith("<"):
                source = str(context.origin)
            raise RaisedValue(value, node.span, source)
        if kind == "TRY":
            try:
                return self.evaluate(fields["body"], context)
            except RuntimeError as error:
                environment = dict(context.environment)
                environment[fields["error_name"]] = error.value
                return self.evaluate(fields["handler"], Context(context.document, context.origin, environment, context.function, context.allowed, arguments=context.arguments))
        if kind == "LENGTH":
            value = self.evaluate(fields["value"], context)
            if not isinstance(value, (Dictionary, bytes)):
                self.fail("TYPE_ERROR", "len requires a dictionary or byte string", node, context)
            return len(value)
        if kind == "REPR":
            return representation(self.evaluate(fields["value"], context))
        if kind == "TYPE":
            value = self.evaluate(fields["value"], context)
            if isinstance(value, Dictionary):
                return b"dict"
            if isinstance(value, Function):
                return b"fn"
            return {type(None): b"null", int: b"int", float: b"float", bytes: b"str"}[type(value)]
        if kind == "EVAL":
            code = self.evaluate(fields["code"], context)
            if not isinstance(code, bytes):
                self.fail("TYPE_ERROR", "eval code must be a byte string", node, context)
            environment = self.environment(self.evaluate(fields["environment"], context), node, context) if fields["environment"] is not None else {}
            signals = [self.evaluate(signal, context) for signal in fields["signals"]]
            if any(not isinstance(signal, bytes) for signal in signals):
                self.fail("TYPE_ERROR", "with_signal requires byte strings", node, context)
            allowed = frozenset(signals)
            if context.allowed is not None:
                allowed &= context.allowed
            document = self.dynamic.get(code)
            if document is None:
                info = SourceInfo("<eval>", hashlib.sha256(code).hexdigest().upper(), None)
                document = read_ast(write_ast(parse(lex(code, info), expression=True)))
                self.dynamic[code] = document
            try:
                self.check_document(document)
            except SflError as error:
                raise RuntimeError(error.code, error.message, error.span, "<eval>") from error
            return self.evaluate(document.units[0].root, Context(document, context.origin, environment, allowed=allowed))
        if kind == "EMIT":
            value = self.evaluate(fields["value"], context)
            signal = self.evaluate(fields["signal"], context)
            if not isinstance(signal, bytes):
                self.fail("TYPE_ERROR", "Signal name must be a byte string", node, context)
            if context.allowed is not None and signal not in context.allowed:
                return None
            try:
                result = self.signals.handle(signal, value)
            except SflError as error:
                if isinstance(error, RuntimeError):
                    raise
                self.fail(error.code, error.message, node, context)
            if not valid_value(result):
                self.fail("SIGNAL_RESULT", "Signal handler returned a non-SFL value", node, context)
            return result
        if kind == "IMPORT":
            return self.import_module(node, context)
        if kind == "EXPORT":
            value = self.evaluate(fields["value"], context)
            alias_kind = fields["alias_kind"]
            alias = fields["value"].fields["name"] if alias_kind == "IMPLICIT" else parse_integer(fields["alias"]) if alias_kind == "INT" else float(fields["alias"]) if alias_kind == "FLOAT" else fields["alias"]
            context.exports = context.exports.with_item(alias, value)
            return None
        self.fail("INVALID_AST", f"Cannot execute node {kind}", node, context)

    def update_string(self, base: bytes, key, value, node: Node, context: Context) -> bytes:
        if type(key) is not int:
            self.fail("INVALID_KEY", "Byte-string update indices must be integers", node, context)
        if value is not None and (type(value) is not int or not 0 <= value <= 255):
            self.fail("INVALID_BYTE", "Byte-string update values must be integers from 0 through 255 or null", node, context)
        if key < 0 or (value is not None and key > len(base)):
            self.fail("INDEX_OUT_OF_RANGE", "Byte-string updates require a non-negative index and cannot create gaps", node, context)
        if value is None:
            if key >= len(base):
                return base
            return base[:key] + base[key + 1:]
        return base[:key] + bytes((value,)) + base[key + 1:]

    def environment(self, value, node, context):
        if not isinstance(value, Dictionary):
            self.fail("TYPE_ERROR", "Explicit environment must be a dictionary", node, context)
        return {key: item for key, item in value.items() if isinstance(key, bytes) and IDENTIFIER.fullmatch(key) and key.decode("ascii") not in KEYWORDS}

    def binary(self, op, left, right, node, context):
        if op in {"EQ", "NE"}:
            result = equal(left, right)
            return int(result if op == "EQ" else not result)
        numeric = type(left) in {int, float} and type(right) in {int, float}
        strings = isinstance(left, bytes) and isinstance(right, bytes)
        if op in {"LT", "GT", "LE", "GE"}:
            if not (numeric or strings):
                self.fail("TYPE_ERROR", "Ordered comparisons require two numbers or two byte strings", node, context)
            return int({"LT": lambda: left < right, "GT": lambda: left > right, "LE": lambda: left <= right, "GE": lambda: left >= right}[op]())
        if op == "ADD" and strings:
            return left + right
        if not numeric:
            self.fail("TYPE_ERROR", "Arithmetic operands must be numbers", node, context)
        return {"ADD": lambda: left + right, "SUB": lambda: left - right, "MUL": lambda: left * right, "DIV": lambda: left / right, "FLOOR_DIV": lambda: left // right, "MOD": lambda: left % right}[op]()

    def import_module(self, node, context):
        fields = node.fields
        try:
            relative, alias = resolve_import(fields["path"], fields["alias"])
        except ValueError as error:
            self.fail("IMPORT_ERROR", str(error), node, context)
        if fields["mode"] == "RELATIVE":
            bases = [context.origin.parent if context.origin else Path.cwd()]
        else:
            if relative.is_absolute():
                self.fail("IMPORT_ERROR", "Library imports require a relative module name", node, context)
            bases = self.library_paths
        try:
            path = next((base / relative for base in bases if (base / relative).is_file()), None)
            if path is None:
                self.fail("MODULE_NOT_FOUND", f"Module not found: {relative}", node, context)
            module = self.load_module(path, context.allowed)
        except OSError as error:
            self.fail("IMPORT_ERROR", str(error), node, context)
        except SflError as error:
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError(error.code, error.message, error.span, error.source or str(path)) from error
        context.environment[alias] = module
        return None
