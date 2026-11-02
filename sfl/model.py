from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, order=True)
class Position:
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"

    @classmethod
    def parse(cls, text: str) -> Position:
        parts = text.split(":")
        if len(parts) != 2 or any(not part.isascii() or not part.isdecimal() or part.startswith("0") for part in parts):
            raise ValueError(f"Invalid position: {text}")
        return cls(*(int(part) for part in parts))


@dataclass(frozen=True)
class Span:
    start: Position
    end: Position


class SflError(Exception):
    def __init__(self, code: str, message: str, span: Span | None = None, source: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span
        self.source = source

    def __str__(self) -> str:
        location = self.source or "<source>"
        if self.span:
            location += f":{self.span.start.line}:{self.span.start.column}"
        return f"{location}: {self.code}: {self.message}"


class FormatError(SflError):
    def __init__(self, message: str):
        super().__init__("CACHE_FORMAT", message)


@dataclass(frozen=True)
class SourceInfo:
    filename: str
    sha256: str
    mtime_utc: str | None


@dataclass(frozen=True)
class Token:
    kind: str
    span: Span
    raw: bytes
    payload: bytes | None = None


@dataclass(eq=False)
class Node:
    kind: str
    span: Span
    fields: dict[str, Any] = field(default_factory=dict)
    number: int = 0


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    span: Span


@dataclass
class LexUnit:
    span: Span
    tokens: list[Token]


@dataclass
class LexDocument:
    source: SourceInfo
    units: list[LexUnit]
    generator: str = ""
    content_hash: str = ""


@dataclass
class AstUnit:
    span: Span
    root: Node | None
    status: str = "VALID"
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class AstDocument:
    source: SourceInfo
    units: list[AstUnit]
    generator: str = ""
    input_hash: str = ""
    content_hash: str = ""


EXPRESSION_KINDS = frozenset("NAME INT FLOAT STRING INF NULL EMPTY_DICT BLOCK UNARY BINARY DICT_UPDATE INDEX MEMBER CALL FUNCTION SELF IF DO TRY LENGTH REPR TYPE EVAL EMIT ERROR_EXPR".split())
STATEMENT_KINDS = frozenset("IMPORT BINDING EXPR_STMT RETURN EXPORT ERROR_STMT".split())
SCHEMAS = {
    "MODULE": {"body": "statements"},
    "BINDING": {"name": "identifier", "value": "expression"},
    "EXPR_STMT": {"value": "expression"},
    "NAME": {"name": "identifier"},
    "INT": {"text": "integer"},
    "FLOAT": {"text": "float"},
    "STRING": {"bytes": "bytes"},
    "INF": {}, "NULL": {}, "EMPTY_DICT": {},
    "BLOCK": {"body": "statements"},
    "UNARY": {"op": {"NEG"}, "operand": "expression"},
    "BINARY": {"op": set("ADD SUB MUL DIV FLOOR_DIV MOD LT GT LE GE EQ NE".split()), "left": "expression", "right": "expression"},
    "DICT_UPDATE": {"base": "expression", "entries": "entries"},
    "ENTRY": {"key": "expression", "value": "expression"},
    "INDEX": {"base": "expression", "key": "expression"},
    "MEMBER": {"base": "expression", "key_kind": {"INT", "NAME"}, "key": "bytes"},
    "CALL": {"callee": "expression", "args": "expressions"},
    "PARAM": {"name": "identifier"},
    "FUNCTION": {"params": "params", "environment": "optional_expression", "body": "block"},
    "SELF": {"function": "function"},
    "IF": {"condition": "expression", "then": "block", "else": "block"},
    "DO": {"body": "block"},
    "RETURN": {"value": "expression", "target": "block"},
    "TRY": {"body": "block", "error_name": "identifier", "handler": "block"},
    "LENGTH": {"value": "expression"}, "REPR": {"value": "expression"}, "TYPE": {"value": "expression"},
    "EVAL": {"code": "expression", "environment": "optional_expression", "signals": "expressions"},
    "EMIT": {"value": "expression", "signal": "expression"},
    "IMPORT": {"mode": {"LIBRARY", "RELATIVE"}, "path": "bytes", "alias": "optional_identifier"},
    "EXPORT": {"value": "expression", "alias_kind": set("IMPLICIT NAME INT FLOAT STRING".split()), "alias": "optional_bytes"},
    "ERROR_EXPR": {"code": "bytes", "message": "bytes"},
    "ERROR_STMT": {"code": "bytes", "message": "bytes"},
}
ASSOCIATIONS = {("RETURN", "target"), ("SELF", "function")}


def children(node: Node):
    for name in SCHEMAS[node.kind]:
        if (node.kind, name) in ASSOCIATIONS:
            continue
        value = node.fields[name]
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, Node):
                yield name, child


def number_nodes(root: Node) -> list[Node]:
    nodes: list[Node] = []
    seen: set[Node] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if node in seen:
            raise FormatError("Structural cycle or shared child")
        seen.add(node)
        node.number = len(nodes) + 1
        nodes.append(node)
        pending.extend(child for _, child in reversed(list(children(node))))
    return nodes
