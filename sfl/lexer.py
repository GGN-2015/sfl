from __future__ import annotations

import re

from .model import LexDocument, LexUnit, Position, SourceInfo, Span, Token


KEYWORDS = {word: "KW_" + word.upper() for word in "import local as fn with with_signal if then else do return len repr type eval emit try except self null inf export".split()}
PUNCTUATION = {
    b":=": "BIND", b"//": "FLOOR_DIV", b"<=": "LE", b">=": "GE", b"==": "EQ", b"!=": "NE",
    b"+": "PLUS", b"-": "MINUS", b"*": "STAR", b"/": "SLASH", b"%": "PERCENT",
    b"<": "LT", b">": "GT", b"(": "LPAREN", b")": "RPAREN", b"[": "LBRACKET", b"]": "RBRACKET",
    b"{": "LBRACE", b"}": "RBRACE", b",": "COMMA", b".": "DOT", b"[]": "EMPTY_DICT",
}
IDENTIFIER = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
INTEGER = re.compile(rb"[0-9]+")
FLOAT = re.compile(rb"(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+[eE][+-]?[0-9]+)")
TOKEN_KINDS = frozenset(KEYWORDS.values()) | frozenset(PUNCTUATION.values()) | {"IDENT", "INT", "FLOAT", "STRING", "COMMENT", "NEWLINE", "ERROR", "EOF"}


def decode_bytes_literal(raw: bytes) -> bytes:
    if len(raw) < 2 or raw[:1] != b'"' or raw[-1:] != b'"':
        raise ValueError("Missing string delimiter")
    result = bytearray()
    position = 1
    escapes = {34: 34, 92: 92, 110: 10, 114: 13, 116: 9}
    while position < len(raw) - 1:
        byte = raw[position]
        if byte != 92:
            if byte < 32 or byte == 34:
                raise ValueError("Unescaped string delimiter or newline")
            result.append(byte)
            position += 1
            continue
        position += 1
        if position >= len(raw) - 1:
            raise ValueError("Incomplete escape")
        escape = raw[position]
        if escape == 120:
            digits = raw[position + 1:position + 3]
            if len(digits) != 2 or any(byte not in b"0123456789abcdefABCDEF" for byte in digits):
                raise ValueError("Invalid hexadecimal escape")
            result.append(int(digits, 16))
            position += 3
        elif escape in escapes:
            result.append(escapes[escape])
            position += 1
        else:
            raise ValueError("Unknown escape")
    return bytes(result)


def lex(data: bytes, source: SourceInfo) -> LexDocument:
    tokens: list[Token] = []
    offset = 0
    line = 1
    column = 1
    after_dot = False

    def emit(kind: str, length: int, payload: bytes | None = None):
        nonlocal offset, line, column, after_dot
        start = Position(line, column)
        raw = data[offset:offset + length]
        offset += length
        if kind == "NEWLINE":
            line += 1
            column = 1
        else:
            column += length
        tokens.append(Token(kind, Span(start, Position(line, column)), raw, payload))
        if kind not in ("COMMENT", "NEWLINE"):
            after_dot = kind == "DOT"

    while offset < len(data):
        byte = data[offset]
        if byte in (32, 9):
            offset += 1
            column += 1
        elif data.startswith(b"\r\n", offset):
            emit("NEWLINE", 2)
        elif byte == 10:
            emit("NEWLINE", 1)
        elif byte == 35:
            end = offset
            while end < len(data) and data[end] not in (10, 13):
                end += 1
            emit("COMMENT", end - offset)
        elif byte == 34:
            end = offset + 1
            closed = False
            while end < len(data) and data[end] not in (10, 13):
                if data[end] == 34:
                    end += 1
                    closed = True
                    break
                if data[end] == 92 and end + 1 < len(data) and data[end + 1] not in (10, 13):
                    end += 2
                else:
                    end += 1
            if not closed:
                emit("ERROR", end - offset, b"UNTERMINATED_STRING")
            else:
                try:
                    decoded = decode_bytes_literal(data[offset:end])
                except ValueError:
                    emit("ERROR", end - offset, b"INVALID_ESCAPE")
                else:
                    emit("STRING", end - offset, decoded)
        elif 48 <= byte <= 57:
            end = INTEGER.match(data, offset).end()
            kind = "INT"
            if not after_dot:
                if end + 1 < len(data) and data[end] == 46 and 48 <= data[end + 1] <= 57:
                    end = INTEGER.match(data, end + 1).end()
                    kind = "FLOAT"
                if end < len(data) and data[end] in (69, 101):
                    end += 1
                    if end < len(data) and data[end] in (43, 45):
                        end += 1
                    exponent = INTEGER.match(data, end)
                    if exponent is None:
                        emit("ERROR", end - offset, b"INVALID_NUMBER")
                        continue
                    end = exponent.end()
                    kind = "FLOAT"
            emit(kind, end - offset)
        elif IDENTIFIER.match(data, offset):
            match = IDENTIFIER.match(data, offset)
            raw = match.group()
            emit(KEYWORDS.get(raw.decode("ascii"), "IDENT"), len(raw))
        else:
            pair = data[offset:offset + 2]
            single = data[offset:offset + 1]
            if len(pair) == 2 and pair in PUNCTUATION:
                emit(PUNCTUATION[pair], 2)
            elif single in PUNCTUATION:
                emit(PUNCTUATION[single], 1)
            else:
                emit("ERROR", 1, b"INVALID_BYTE")
    end = Position(line, column)
    tokens.append(Token("EOF", Span(end, end), b""))
    return LexDocument(source, [LexUnit(Span(Position(1, 1), end), tokens)])
