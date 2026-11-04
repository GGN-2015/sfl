from __future__ import annotations

import hashlib
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sfl
from sfl.formats import read_ast, read_lex, write_ast, write_lex
from sfl.lexer import lex
from sfl.model import AstDocument, Node, SflError, SourceInfo, number_nodes
from sfl.parser import parse
from sfl.render import representation, source
from sfl.runtime import Interpreter, RaisedValue
from sfl.values import Function


def copy_standard_library(destination: Path) -> Path:
    destination.mkdir()
    for source in (Path(sfl.__file__).parent / "stdlib").glob("*.sfl"):
        shutil.copyfile(source, destination / source.name)
    return destination


def source_info(data: bytes, filename: str = "<test>") -> SourceInfo:
    return SourceInfo(filename, hashlib.sha256(data).hexdigest().upper(), None)


def compile_text(text: str | bytes, *, expression: bool = False, valid: bool = True) -> AstDocument:
    data = text.encode("ascii") if isinstance(text, str) else text
    lexical = read_lex(write_lex(lex(data, source_info(data))), data)
    document = read_ast(write_ast(parse(lexical, expression=expression)))
    if valid:
        assert len(document.units) == 1
        unit = document.units[0]
        assert unit.status == "VALID", (text, unit.diagnostics)
    return document


def fingerprint(root: Node) -> list:
    nodes = number_nodes(root)

    def field(value):
        if isinstance(value, Node):
            return value.number
        if isinstance(value, list):
            return tuple(field(item) for item in value)
        return value

    return [(node.kind, tuple((name, field(value)) for name, value in node.fields.items())) for node in nodes]


def expression_wrapper(expression: str) -> str:
    return "export fn (a, b, c, ...) with [] {\nreturn " + expression + "\n} as value"


def assert_source_roundtrip(expression: str) -> tuple[Node, str]:
    original = compile_text(expression_wrapper(expression)).units[0].root
    rendered = source(original)
    restored = compile_text(rendered).units[0].root
    assert fingerprint(original) == fingerprint(restored), (expression, rendered)
    return original, rendered


def assert_indentation(text: str) -> None:
    data = text.encode("ascii")
    tokens = lex(data, source_info(data)).units[0].tokens
    rows = {}
    for token in tokens:
        if token.kind not in {"NEWLINE", "EOF"}:
            rows.setdefault(token.span.start.line, []).append(token)
    depth = 0
    opening = {"LPAREN", "LBRACKET", "LBRACE"}
    closing = {"RPAREN", "RBRACKET", "RBRACE"}
    for line_number, line in enumerate(text.splitlines(), 1):
        row = rows.get(line_number, [])
        leading = 0
        for token in row:
            if token.kind not in closing:
                break
            leading += 1
        assert len(line) - len(line.lstrip()) == max(0, depth - leading) * 2, (line_number, text)
        for token in row:
            depth += (token.kind in opening) - (token.kind in closing)
        assert depth >= 0, text
    assert depth == 0, text


def assert_parentheses_necessary(original: Node, rendered: str) -> int:
    data = rendered.encode("ascii")
    tokens = lex(data, source_info(data)).units[0].tokens
    line_starts = [0]
    for position, character in enumerate(rendered):
        if character == "\n":
            line_starts.append(position + 1)
    parentheses = []
    checked = 0
    for token in tokens:
        offset = line_starts[token.span.start.line - 1] + token.span.start.column - 1
        if token.kind == "LPAREN":
            parentheses.append(offset)
        elif token.kind == "RPAREN":
            opening = parentheses.pop()
            shortened = rendered[:opening] + rendered[opening + 1:offset] + rendered[offset + 1:]
            shortened_data = shortened.encode("ascii")
            result = parse(lex(shortened_data, source_info(shortened_data)))
            assert len(result.units) != 1 or result.units[0].status != "VALID" or fingerprint(result.units[0].root) != fingerprint(original), (rendered, shortened)
            checked += 1
    return checked


@dataclass
class Harness:
    interpreter: Interpreter
    events: list
    library: Path

    def evaluate(self, text: str, environment: dict[bytes, Any] | None = None):
        return self.interpreter.execute(compile_text(text, expression=True), environment=environment)

    def module(self, text: str):
        return self.interpreter.execute(compile_text(text))

    def call(self, function: Function, arguments=()):
        environment = {b"target": function}
        names = []
        for position, value in enumerate(arguments):
            name = "argument_" + str(position)
            names.append(name)
            environment[name.encode("ascii")] = value
        return self.evaluate("target(" + ", ".join(names) + ")", environment)

    def outcome(self, function: Function, arguments=()):
        self.events.clear()
        try:
            value = self.call(function, arguments)
            return "VALUE", representation(value), list(self.events)
        except RaisedValue as error:
            return "RAISE", representation(error.value), list(self.events)
        except SflError as error:
            return "ERROR", error.code, list(self.events)

    def rebuild(self, function: Function) -> Function:
        text = representation(function).decode("ascii")
        assert text.startswith("fn "), text
        assert_indentation(text)
        document = compile_text(text, expression=True)
        assert all(node.fields["environment"] is not None for node in number_nodes(document.units[0].root) if node.kind == "FUNCTION"), text
        restored = self.interpreter.execute(document)
        assert isinstance(restored, Function)
        return restored

    def compare(self, function_text: str, argument_sets):
        original = self.evaluate(function_text)
        restored = self.rebuild(original)
        for arguments in argument_sets:
            assert self.outcome(original, arguments) == self.outcome(restored, arguments), (function_text, arguments, representation(restored))
        return original, restored


OPERATORS = ["+", "-", "*", "/", "//", "%", "<", ">", "<=", ">=", "==", "!="]
ATOMS = ["a", "b", "c", "1", "1.5", "[]", '"([{}])"', "null", "inf", "self", "args"]


def generated_expression(randomizer: random.Random, depth: int) -> str:
    if depth == 0 or randomizer.randrange(7) == 0:
        return randomizer.choice(ATOMS)
    choice = randomizer.randrange(21)
    first = generated_expression(randomizer, depth - 1)
    if choice < 3:
        return "(" + first + " " + randomizer.choice(OPERATORS) + " " + generated_expression(randomizer, depth - 1) + ")"
    if choice < 7:
        return "(" + randomizer.choice(["-", "len ", "repr ", "type "]) + first + ")"
    if choice == 7:
        return "(" + first + "(" + generated_expression(randomizer, depth - 1) + "))"
    if choice == 8:
        return "(" + first + "[" + generated_expression(randomizer, depth - 1) + "])"
    if choice == 9:
        return "(" + first + "." + randomizer.choice(["key", "0", "1"]) + ")"
    if choice == 10:
        return "(" + first + "[" + generated_expression(randomizer, depth - 1) + " := " + generated_expression(randomizer, depth - 1) + "])"
    if choice == 11:
        return "(raise " + first + ")"
    if choice == 12:
        return "(emit " + first + " as " + generated_expression(randomizer, depth - 1) + ")"
    if choice == 13:
        return "(eval " + first + ")"
    if choice == 14:
        return "(eval " + first + " with " + generated_expression(randomizer, depth - 1) + ")"
    if choice == 15:
        return "(eval " + first + " with_signal " + generated_expression(randomizer, depth - 1) + " with_signal " + generated_expression(randomizer, depth - 1) + ")"
    if choice == 16:
        return "(eval " + first + " with " + generated_expression(randomizer, depth - 1) + " with_signal " + generated_expression(randomizer, depth - 1) + ")"
    if choice == 17:
        return "(if " + first + " then { return a } else { return b })"
    if choice == 18:
        return "(do { return " + first + " })"
    if choice == 19:
        return "(try { return " + first + " } except error then { return error })"
    return "(fn (...) with " + first + " { return args })"


def random_expressions(count: int, depth: int = 4, skip: int = 0) -> list[str]:
    randomizer = random.Random(20261102)
    for position in range(skip):
        generated_expression(randomizer, 4)
    return [generated_expression(randomizer, depth) for position in range(count)]
