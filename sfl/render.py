from __future__ import annotations

import math

from .formats import quote
from .model import Node
from .values import Dictionary, Function, integer_text


SYMBOLS = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/", "FLOOR_DIV": "//", "MOD": "%", "LT": "<", "GT": ">", "LE": "<=", "GE": ">=", "EQ": "==", "NE": "!="}


def source(node: Node) -> str:
    kind, fields = node.kind, node.fields
    if kind in {"NAME", "PARAM"}:
        return fields["name"].decode("ascii")
    if kind in {"INT", "FLOAT"}:
        return fields["text"].decode("ascii")
    if kind == "STRING":
        return quote(fields["bytes"])
    if kind in {"NULL", "INF", "EMPTY_DICT", "SELF"}:
        return {"NULL": "null", "INF": "inf", "EMPTY_DICT": "[]", "SELF": "self"}[kind]
    if kind == "MODULE":
        return "\n".join(source(statement) for statement in fields["body"])
    if kind == "BLOCK":
        return "{\n" + "\n".join(source(statement) for statement in fields["body"]) + "\n}"
    if kind == "BINDING":
        return fields["name"].decode("ascii") + " := " + source(fields["value"])
    if kind == "EXPR_STMT":
        return source(fields["value"])
    if kind == "RETURN":
        return "return " + source(fields["value"])
    if kind == "UNARY":
        return "(-(" + source(fields["operand"]) + "))"
    if kind == "BINARY":
        return "(" + source(fields["left"]) + " " + SYMBOLS[fields["op"]] + " " + source(fields["right"]) + ")"
    if kind == "ENTRY":
        return source(fields["key"]) + " := " + source(fields["value"])
    if kind == "DICT_UPDATE":
        return "(" + source(fields["base"]) + ")[" + ", ".join(source(entry) for entry in fields["entries"]) + "]"
    if kind == "INDEX":
        return "(" + source(fields["base"]) + ")[" + source(fields["key"]) + "]"
    if kind == "MEMBER":
        return "(" + source(fields["base"]) + ")." + fields["key"].decode("ascii")
    if kind == "CALL":
        return "(" + source(fields["callee"]) + ")(" + ", ".join(source(argument) for argument in fields["args"]) + ")"
    if kind == "FUNCTION":
        environment = " with (" + source(fields["environment"]) + ")" if fields["environment"] else ""
        return "fn (" + ", ".join(source(parameter) for parameter in fields["params"]) + ")" + environment + " " + source(fields["body"])
    if kind == "IF":
        return "(if (" + source(fields["condition"]) + ") then " + source(fields["then"]) + " else " + source(fields["else"]) + ")"
    if kind == "DO":
        return "(do " + source(fields["body"]) + ")"
    if kind == "TRY":
        return "(try " + source(fields["body"]) + " except " + fields["error_name"].decode("ascii") + " then " + source(fields["handler"]) + ")"
    if kind in {"LENGTH", "REPR"}:
        return "(" + ("len" if kind == "LENGTH" else "repr") + " (" + source(fields["value"]) + "))"
    if kind == "EVAL":
        environment = " with (" + source(fields["environment"]) + ")" if fields["environment"] else ""
        signals = "".join(" with_signal (" + source(signal) + ")" for signal in fields["signals"])
        return "(eval (" + source(fields["code"]) + ")" + environment + signals + ")"
    if kind == "EMIT":
        return "(emit (" + source(fields["value"]) + ") as (" + source(fields["signal"]) + "))"
    if kind == "IMPORT":
        alias = " as " + fields["alias"].decode("ascii") if fields["alias"] is not None else ""
        return ("import " if fields["mode"] == "LIBRARY" else "local ") + quote(fields["path"]) + alias
    if kind == "EXPORT":
        alias = ""
        if fields["alias_kind"] != "IMPLICIT":
            alias = " as " + (quote(fields["alias"]) if fields["alias_kind"] == "STRING" else fields["alias"].decode("ascii"))
        return "export " + source(fields["value"]) + alias
    raise ValueError(f"Cannot render {kind}")


def representation(value) -> bytes:
    if value is None:
        return b"null"
    if type(value) is int:
        return integer_text(value)
    if type(value) is float:
        if math.isnan(value):
            return b"(inf - inf)"
        return repr(value).encode("ascii")
    if isinstance(value, bytes):
        return quote(value).encode("ascii")
    if isinstance(value, Dictionary):
        return b"[][" + b", ".join(representation(key) + b" := " + representation(item) for key, item in value.items()) + b"]" if len(value) else b"[]"
    if isinstance(value, Function):
        params = b", ".join(parameter.fields["name"] for parameter in value.node.fields["params"])
        bindings = b"\n".join(name + b" := " + representation(item) for name, item in sorted(value.environment.items()))
        return b"(do {\n" + bindings + b"\nreturn fn (" + params + b") " + source(value.node.fields["body"]).encode("ascii") + b"\n})"
    raise ValueError("Value is not an SFL value")
