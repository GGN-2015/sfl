from __future__ import annotations

import math
from dataclasses import dataclass, field

from .formats import quote
from .imports import resolve_import
from .lexer import IDENTIFIER
from .model import Node, children
from .parser import OPERATORS
from .values import Dictionary, Function, integer_text


SYMBOLS = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/", "FLOOR_DIV": "//", "MOD": "%", "LT": "<", "GT": ">", "LE": "<=", "GE": ">=", "EQ": "==", "NE": "!="}
BINARY_PRECEDENCE = {operator: power for power, operator in OPERATORS.values()}


def indent(text: str) -> str:
    lines = []
    depth = 0
    for line in text.splitlines():
        content = line.strip()
        if not content:
            continue
        closing = len(content) - len(content.lstrip(")]}"))
        lines.append("  " * max(0, depth - closing) + content)
        quoted = False
        escaped = False
        for character in content:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character == "#":
                break
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
    return "\n".join(lines)


def parameters(node: Node) -> str:
    names = [parameter.fields["name"].decode("ascii") for parameter in node.fields["params"]]
    if node.fields["variadic"] == "YES":
        names.append("...")
    return ", ".join(names)


def environment_update(base: str, entries: list[str]) -> str:
    return base + "[\n" + "\n".join(entries) + "\n]" if entries else base


@dataclass
class Scope:
    names: set[bytes] = field(default_factory=set)
    environment: str = "[]"

    def copy(self) -> Scope:
        return Scope(set(self.names), self.environment)


class SourceRenderer:
    def __init__(self, node: Node, names=(), explicit_environments: bool = False):
        self.explicit_environments = explicit_environments
        self.reserved = set(names)
        pending = [node]
        while pending:
            current = pending.pop()
            for value in current.fields.values():
                if isinstance(value, bytes):
                    self.reserved.update(IDENTIFIER.findall(value))
            pending.extend(child for _, child in children(current))

    def fresh_name(self) -> bytes:
        position = 1
        while (name := f"_sfl_repr_env_{position}".encode("ascii")) in self.reserved:
            position += 1
        self.reserved.add(name)
        return name

    def needs_environment(self, node: Node) -> bool:
        if node.kind == "FUNCTION":
            environment = node.fields["environment"]
            return environment is None or self.needs_environment(environment)
        return any(self.needs_environment(child) for _, child in children(node))

    def block(self, node: Node, scope: Scope, prefix=()) -> str:
        inner = scope.copy()
        statements = list(prefix)
        statements.extend(self.render(statement, inner) for statement in node.fields["body"])
        return "{\n" + "\n".join(statements) + "\n}" if statements else "{}"

    def function(self, node: Node, scope: Scope) -> str:
        fields = node.fields
        signature = "fn (" + parameters(node) + ")"
        param_names = {parameter.fields["name"] for parameter in fields["params"]}
        if not self.explicit_environments:
            environment = " with " + self.render(fields["environment"], scope) if fields["environment"] else ""
            return signature + environment + " " + self.block(fields["body"], Scope(scope.names | param_names))
        if fields["environment"] is None:
            captures = sorted(scope.names - param_names)
            entries = [quote(name) + " := [][0 := " + name.decode("ascii") + "]" for name in captures]
            prefix = [name.decode("ascii") + " := " + name.decode("ascii") + "[0]" for name in captures]
            environment = environment_update(scope.environment, entries)
            body = self.block(fields["body"], Scope(scope.names | param_names, scope.environment), prefix)
            return signature + " with " + environment + " " + body
        environment = self.render(fields["environment"], scope)
        body_scope = Scope(param_names)
        if self.needs_environment(fields["body"]):
            name = self.fresh_name()
            identifier = name.decode("ascii")
            forwarded = identifier + "[" + quote(name) + " := " + identifier + "]"
            body_scope.environment = forwarded
            body = self.block(fields["body"], body_scope)
            return "do {\n" + identifier + " := " + environment + "\nreturn " + signature + " with " + forwarded + " " + body + "\n}"
        return signature + " with " + environment + " " + self.block(fields["body"], body_scope)

    def render(self, node: Node, scope: Scope, minimum: int = 0, following: str = "") -> str:
        kind, fields = node.kind, node.fields
        if kind in {"RAISE", "EVAL", "EMIT"}:
            grouped = following in {"OPERATOR", "POSTFIX"}
            if kind == "EVAL":
                grouped |= following == "WITH_SIGNAL" or (following == "WITH" and fields["environment"] is None and not fields["signals"])
        else:
            power = 60
            if kind == "BINARY":
                power = BINARY_PRECEDENCE[fields["op"]]
            elif kind in {"UNARY", "LENGTH", "REPR", "TYPE"}:
                power = 40
            elif kind in {"CALL", "INDEX", "MEMBER", "DICT_UPDATE"}:
                power = 50
            grouped = power < minimum
        if grouped:
            return "(" + self.render(node, scope) + ")"
        if kind in {"NAME", "PARAM"}:
            return fields["name"].decode("ascii")
        if kind in {"INT", "FLOAT"}:
            return fields["text"].decode("ascii")
        if kind == "STRING":
            return quote(fields["bytes"])
        if kind in {"NULL", "INF", "EMPTY_DICT", "SELF", "ARGS"}:
            return {"NULL": "null", "INF": "inf", "EMPTY_DICT": "[]", "SELF": "self", "ARGS": "args"}[kind]
        if kind == "MODULE":
            return "\n".join(self.render(statement, scope) for statement in fields["body"])
        if kind == "BLOCK":
            return self.block(node, scope)
        if kind == "BINDING":
            value = self.render(fields["value"], scope)
            scope.names.add(fields["name"])
            return fields["name"].decode("ascii") + " := " + value
        if kind == "EXPR_STMT":
            return self.render(fields["value"], scope)
        if kind == "RETURN":
            return "return " + self.render(fields["value"], scope)
        if kind == "RAISE":
            return "raise " + self.render(fields["value"], scope, following=following)
        if kind == "UNARY":
            return "-" + self.render(fields["operand"], scope, 40, following)
        if kind == "BINARY":
            power = BINARY_PRECEDENCE[fields["op"]]
            left = self.render(fields["left"], scope, power + (power == 10), "OPERATOR")
            right = self.render(fields["right"], scope, power + 1, following)
            return left + " " + SYMBOLS[fields["op"]] + " " + right
        if kind == "ENTRY":
            return self.render(fields["key"], scope) + " := " + self.render(fields["value"], scope)
        if kind == "DICT_UPDATE":
            return self.render(fields["base"], scope, 50, "POSTFIX") + "[" + ", ".join(self.render(entry, scope) for entry in fields["entries"]) + "]"
        if kind == "INDEX":
            return self.render(fields["base"], scope, 50, "POSTFIX") + "[" + self.render(fields["key"], scope) + "]"
        if kind == "MEMBER":
            base = self.render(fields["base"], scope, 50, "POSTFIX")
            if fields["base"].kind == "INT" and fields["key"].isdigit():
                base = "(" + base + ")"
            return base + "." + fields["key"].decode("ascii")
        if kind == "CALL":
            return self.render(fields["callee"], scope, 50, "POSTFIX") + "(" + ", ".join(self.render(argument, scope) for argument in fields["args"]) + ")"
        if kind == "FUNCTION":
            return self.function(node, scope)
        if kind == "IF":
            return "if " + self.render(fields["condition"], scope) + " then " + self.render(fields["then"], scope) + " else " + self.render(fields["else"], scope)
        if kind == "DO":
            return "do " + self.render(fields["body"], scope)
        if kind == "TRY":
            handler_scope = scope.copy()
            handler_scope.names.add(fields["error_name"])
            return "try " + self.render(fields["body"], scope) + " except " + fields["error_name"].decode("ascii") + " then " + self.render(fields["handler"], handler_scope)
        if kind in {"LENGTH", "REPR", "TYPE"}:
            return {"LENGTH": "len", "REPR": "repr", "TYPE": "type"}[kind] + " " + self.render(fields["value"], scope, 40, following)
        if kind == "EVAL":
            code_following = "WITH" if fields["environment"] else "WITH_SIGNAL" if fields["signals"] else following
            code = self.render(fields["code"], scope, following=code_following)
            environment = " with " + self.render(fields["environment"], scope, following="WITH_SIGNAL" if fields["signals"] else following) if fields["environment"] else ""
            signals = "".join(" with_signal " + self.render(signal, scope, following="WITH_SIGNAL" if position + 1 < len(fields["signals"]) else following) for position, signal in enumerate(fields["signals"]))
            return "eval " + code + environment + signals
        if kind == "EMIT":
            return "emit " + self.render(fields["value"], scope) + " as " + self.render(fields["signal"], scope, following=following)
        if kind == "IMPORT":
            _, name = resolve_import(fields["path"], fields["alias"])
            scope.names.add(name)
            alias = " as " + fields["alias"].decode("ascii") if fields["alias"] is not None else ""
            return ("import " if fields["mode"] == "LIBRARY" else "local ") + quote(fields["path"]) + alias
        if kind == "EXPORT":
            alias = ""
            if fields["alias_kind"] != "IMPLICIT":
                alias = " as " + (quote(fields["alias"]) if fields["alias_kind"] == "STRING" else fields["alias"].decode("ascii"))
            return "export " + self.render(fields["value"], scope) + alias
        raise ValueError(f"Cannot render {kind}")


def source(node: Node) -> str:
    return indent(SourceRenderer(node).render(node, Scope()))


def represent(value) -> str:
    if value is None:
        return "null"
    if type(value) is int:
        return integer_text(value).decode("ascii")
    if type(value) is float:
        return "inf - inf" if math.isnan(value) else repr(value)
    if isinstance(value, bytes):
        return quote(value)
    if isinstance(value, Dictionary):
        return "[][" + ", ".join(represent(key) + " := " + represent(item) for key, item in value.items()) + "]" if len(value) else "[]"
    if isinstance(value, Function):
        param_names = {parameter.fields["name"] for parameter in value.node.fields["params"]}
        captures = {name: item for name, item in value.environment.items() if name not in param_names}
        entries = [quote(name) + " := " + represent(item if item is not None else Dictionary()) for name, item in sorted(captures.items())]
        prefix = [name.decode("ascii") + " := " + name.decode("ascii") + "[0]" for name, item in sorted(captures.items()) if item is None]
        renderer = SourceRenderer(value.node, captures, explicit_environments=True)
        body = renderer.block(value.node.fields["body"], Scope(set(captures) | param_names), prefix)
        return "fn (" + parameters(value.node) + ") with " + environment_update("[]", entries) + " " + body
    raise ValueError("Value is not an SFL value")


def representation(value) -> bytes:
    return indent(represent(value)).encode("ascii")
