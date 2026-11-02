from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from .imports import resolve_import
from .lexer import FLOAT, IDENTIFIER, INTEGER, KEYWORDS, PUNCTUATION, TOKEN_KINDS, decode_bytes_literal
from .model import ASSOCIATIONS, EXPRESSION_KINDS, SCHEMAS, STATEMENT_KINDS, AstDocument, AstUnit, Diagnostic, FormatError, LexDocument, LexUnit, Node, Position, SourceInfo, Span, Token, children, number_nodes


def quote(value: bytes) -> str:
    escapes = {34: '\\"', 92: '\\\\', 10: '\\n', 13: '\\r', 9: '\\t'}
    return '"' + "".join(escapes.get(byte, chr(byte) if 32 <= byte <= 126 else f"\\x{byte:02X}") for byte in value) + '"'


def unquote(text: str) -> bytes:
    if any(ord(character) < 32 for character in text):
        raise FormatError("Unescaped control character in string")
    try:
        return decode_bytes_literal(text.encode("utf-8"))
    except ValueError as error:
        raise FormatError(str(error)) from error


def split_record(line: str) -> list[str]:
    result = []
    position = 0
    while position < len(line):
        if line[position] in " \t":
            position += 1
            continue
        if line[position] == "#":
            break
        start = position
        quoted = False
        while position < len(line):
            character = line[position]
            if quoted and character == "\\":
                position += 2
                continue
            if character == '"':
                quoted = not quoted
            elif not quoted and character in " \t#":
                break
            position += 1
        if quoted:
            raise FormatError("Unclosed quoted field")
        result.append(line[start:position])
    return result


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def header(version: str, source: SourceInfo, generator: str, body: str, input_hash: str = "") -> str:
    lines = [version, "SOURCE_FILE " + quote(source.filename.encode("utf-8")), "SOURCE_SHA256 " + quote(source.sha256.encode("ascii")), "SOURCE_MTIME_UTC " + (quote(source.mtime_utc.encode("ascii")) if source.mtime_utc is not None else "-")]
    if generator:
        lines.append("# SFL_GENERATOR " + generator)
        lines.append("# SFL_CONTENT_SHA256 " + digest(body))
    if input_hash:
        lines.append("# SFL_INPUT_SHA256 " + input_hash)
    return "\n".join(lines) + "\n\n" + body


def lex_body(document: LexDocument) -> str:
    lines = []
    for number, unit in enumerate(document.units, 1):
        lines.append(f"UNIT {number} {unit.span.start} {unit.span.end}")
        for token in unit.tokens:
            lines.append(f"{token.kind} {token.span.start} {token.span.end} {quote(token.raw)} " + (quote(token.payload) if token.payload is not None else "-"))
        lines.extend(["END", ""])
    return "\n".join(lines)


def write_lex(document: LexDocument) -> str:
    body = lex_body(document)
    return header("SFLEX 1", document.source, document.generator, body)


@dataclass(frozen=True)
class Reference:
    number: int


def parse_value(text: str):
    if text == "-":
        return None
    if text.startswith('"'):
        return unquote(text)
    if re.fullmatch(r"@[1-9][0-9]*", text):
        return Reference(int(text[1:]))
    if text.startswith("[") and text.endswith("]"):
        items = [] if text == "[]" else [parse_value(item) for item in text[1:-1].split(",")]
        if not all(isinstance(item, Reference) for item in items):
            raise FormatError("Lists must contain node references")
        return items
    if re.fullmatch(r"[A-Z][A-Z_0-9]*", text):
        return text
    raise FormatError(f"Invalid field value: {text}")


def write_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, Node):
        return f"@{value.number}"
    if isinstance(value, bytes):
        return quote(value)
    if isinstance(value, list):
        return "[" + ",".join(write_value(item) for item in value) + "]"
    if isinstance(value, str):
        return value
    raise FormatError("Unsupported AST field value")


def ast_body(document: AstDocument) -> str:
    lines = []
    for number, unit in enumerate(document.units, 1):
        nodes = number_nodes(unit.root) if unit.root else []
        lines.extend([f"UNIT {number} {unit.span.start} {unit.span.end}", "STATUS " + unit.status, "ROOT " + (f"@{unit.root.number}" if unit.root else "-")])
        for diagnostic in unit.diagnostics:
            lines.append(f"DIAG {diagnostic.severity} {quote(diagnostic.code.encode('utf-8'))} {diagnostic.span.start} {diagnostic.span.end} {quote(diagnostic.message.encode('utf-8'))}")
        for node in nodes:
            fields = " ".join(name + "=" + write_value(node.fields[name]) for name in SCHEMAS[node.kind])
            lines.append(f"NODE {node.number} {node.kind} {node.span.start} {node.span.end}" + (" " + fields if fields else ""))
        lines.extend(["END", ""])
    return "\n".join(lines)


def write_ast(document: AstDocument) -> str:
    return header("SFLAST 1", document.source, document.generator, ast_body(document), document.input_hash)


def read_header(text: str, version: str):
    records = []
    metadata = {}
    for line in text.splitlines():
        match = re.fullmatch(r"# SFL_(GENERATOR|CONTENT_SHA256|INPUT_SHA256) ([A-F0-9]{64})", line)
        if match:
            if match[1] in metadata:
                raise FormatError("Duplicate compiler metadata")
            metadata[match[1]] = match[2]
        record = split_record(line)
        if record:
            records.append(record)
    if len(records) < 4 or records[0] != version.split():
        raise FormatError("Unsupported or missing format version")
    values = []
    for record, name in zip(records[1:4], ["SOURCE_FILE", "SOURCE_SHA256", "SOURCE_MTIME_UTC"]):
        if len(record) != 2 or record[0] != name:
            raise FormatError("Expected exactly one ordered file-level source header")
        values.append(None if record[1] == "-" else unquote(record[1]).decode("utf-8"))
    filename, sha256, mtime = values
    if not filename or not sha256 or not re.fullmatch(r"[A-F0-9]{64}", sha256):
        raise FormatError("Invalid source filename or SHA256")
    if mtime is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z", mtime):
            raise FormatError("Invalid UTC modification time")
        datetime.fromisoformat(mtime)
    elif not (filename.startswith("<") and filename.endswith(">")):
        raise FormatError("Disk sources require a modification time")
    if any(record[0].startswith("SOURCE_") for record in records[4:]):
        raise FormatError("Duplicate or misplaced source header")
    return SourceInfo(filename, sha256, mtime), records[4:], metadata


def unit_start(record: list[str], expected: int) -> Span:
    if len(record) != 4 or record[:2] != ["UNIT", str(expected)]:
        raise FormatError("Invalid UNIT record or numbering")
    span = Span(Position.parse(record[2]), Position.parse(record[3]))
    if span.end < span.start:
        raise FormatError("Reversed source interval")
    return span


def validate_token(token: Token):
    if token.kind not in TOKEN_KINDS or token.span.end < token.span.start:
        raise FormatError("Invalid token kind or interval")
    if token.kind == "STRING":
        if token.payload != decode_bytes_literal(token.raw):
            raise FormatError("String payload differs from raw string")
    elif token.kind == "ERROR":
        if token.payload not in {b"UNTERMINATED_STRING", b"INVALID_ESCAPE", b"INVALID_NUMBER", b"INVALID_BYTE"}:
            raise FormatError("Unknown lexer diagnostic")
    elif token.payload is not None:
        raise FormatError("Unexpected token payload")
    if token.kind in {"INT", "FLOAT", "IDENT"}:
        pattern = {"INT": INTEGER, "FLOAT": FLOAT, "IDENT": IDENTIFIER}[token.kind]
        if not pattern.fullmatch(token.raw) or (token.kind == "IDENT" and token.raw.decode("ascii") in KEYWORDS):
            raise FormatError("Invalid token lexeme")
    reverse = {kind: raw for raw, kind in PUNCTUATION.items()} | {kind: word.encode("ascii") for word, kind in KEYWORDS.items()}
    if token.kind in reverse and token.raw != reverse[token.kind]:
        raise FormatError("Token lexeme does not match its kind")
    if token.kind == "NEWLINE" and token.raw not in (b"\n", b"\r\n"):
        raise FormatError("Invalid newline")
    if token.kind == "COMMENT" and (not token.raw.startswith(b"#") or b"\n" in token.raw or b"\r" in token.raw):
        raise FormatError("Invalid comment")
    if token.kind == "EOF" and (token.raw or token.span.start != token.span.end):
        raise FormatError("Invalid EOF")


def read_lex(text: str, source_bytes: bytes | None = None) -> LexDocument:
    try:
        source, records, metadata = read_header(text, "SFLEX 1")
        units = []
        position = 0
        while position < len(records):
            span = unit_start(records[position], len(units) + 1)
            position += 1
            tokens = []
            previous = span.start
            while position < len(records) and records[position] != ["END"]:
                record = records[position]
                position += 1
                if len(record) != 5:
                    raise FormatError("Tokens must have five fields")
                kind, start, end, raw, payload = record
                token = Token(kind, Span(Position.parse(start), Position.parse(end)), unquote(raw), None if payload == "-" else unquote(payload))
                validate_token(token)
                if not previous <= token.span.start <= token.span.end <= span.end or (tokens and tokens[-1].kind == "EOF"):
                    raise FormatError("Unordered tokens or records after EOF")
                tokens.append(token)
                previous = token.span.end
            if position >= len(records) or not tokens or tokens[-1].kind != "EOF" or tokens[-1].span.end != span.end:
                raise FormatError("Missing END or EOF")
            position += 1
            units.append(LexUnit(span, tokens))
        if not units:
            raise FormatError("Missing UNIT")
        document = LexDocument(source, units, metadata.get("GENERATOR", ""), metadata.get("CONTENT_SHA256", ""))
        if document.content_hash and document.content_hash != digest(lex_body(document)):
            raise FormatError("Token content checksum mismatch")
        if source_bytes is not None:
            validate_source_tokens(document, source_bytes)
        return document
    except (ValueError, UnicodeError, IndexError) as error:
        raise FormatError(str(error)) from error


def validate_source_tokens(document: LexDocument, data: bytes):
    starts = [0] + [match.end() for match in re.finditer(b"\n", data)]
    lines = data.split(b"\n")

    def offset(position: Position) -> int:
        if not 1 <= position.line <= len(starts):
            raise FormatError("Source line outside input")
        raw_line = lines[position.line - 1]
        length = len(raw_line.removesuffix(b"\r") if position.line < len(lines) else raw_line)
        if not 1 <= position.column <= length + 1:
            raise FormatError("Source column outside input")
        return starts[position.line - 1] + position.column - 1

    if hashlib.sha256(data).hexdigest().upper() != document.source.sha256:
        raise FormatError("Source hash mismatch")
    for unit in document.units:
        cursor = offset(unit.span.start)
        for token in unit.tokens:
            start, end = offset(token.span.start), offset(token.span.end)
            if data[cursor:start].strip(b" \t") or token.raw != data[start:end]:
                raise FormatError("Token stream does not cover source bytes")
            cursor = end
        if cursor != offset(unit.span.end):
            raise FormatError("Token stream does not reach UNIT end")


def validate_ast(unit: AstUnit):
    if unit.status not in {"VALID", "INVALID"}:
        raise FormatError("Invalid AST status")
    for diagnostic in unit.diagnostics:
        if diagnostic.severity not in {"ERROR", "WARNING"} or not unit.span.start <= diagnostic.span.start <= diagnostic.span.end <= unit.span.end:
            raise FormatError("Invalid diagnostic")
        if unit.status == "VALID" and diagnostic.severity == "ERROR":
            raise FormatError("ERROR diagnostic in VALID AST")
    if unit.root is None:
        if unit.status == "VALID":
            raise FormatError("VALID AST has no root")
        return
    seen = set()

    def check_value(value, expected):
        if isinstance(expected, set):
            return isinstance(value, str) and value in expected
        if expected.startswith("optional_"):
            return value is None or check_value(value, expected[9:])
        if expected in {"bytes", "identifier", "integer", "float"}:
            pattern = {"identifier": IDENTIFIER, "integer": INTEGER, "float": FLOAT}.get(expected)
            if not isinstance(value, bytes) or (pattern is not None and pattern.fullmatch(value) is None):
                return False
            return expected != "identifier" or value.decode("ascii") not in KEYWORDS
        if expected in {"statements", "expressions", "entries", "params"}:
            singular = {"statements": "statement", "expressions": "expression", "entries": "entry", "params": "param"}[expected]
            return isinstance(value, list) and all(check_value(item, singular) for item in value)
        return isinstance(value, Node) and (value.kind in EXPRESSION_KINDS if expected == "expression" else value.kind in STATEMENT_KINDS if expected == "statement" else value.kind == expected.upper())

    def visit(node, enclosing, block, function, parent):
        if node in seen or node.kind not in SCHEMAS:
            raise FormatError("Unknown, cyclic or shared AST node")
        seen.add(node)
        if not enclosing.start <= node.span.start <= node.span.end <= enclosing.end:
            raise FormatError("AST node outside parent interval")
        schema = SCHEMAS[node.kind]
        if node.fields.keys() != schema.keys() or any(not check_value(node.fields[name], expected) for name, expected in schema.items()):
            raise FormatError(f"Invalid fields on {node.kind}")
        if unit.status == "VALID" and node.kind.startswith("ERROR_"):
            raise FormatError("Error node in VALID AST")
        if node.kind == "RETURN" and (block is None or node.fields["target"] is not block):
            raise FormatError("Invalid return target")
        if node.kind in {"SELF", "ARGS"} and (function is None or node.fields["function"] is not function):
            raise FormatError("Invalid " + node.kind.lower() + " target")
        if node.kind == "FUNCTION":
            names = [parameter.fields["name"] for parameter in node.fields["params"]]
            if len(names) != len(set(names)):
                raise FormatError("Duplicate function parameter")
        if node.kind == "IMPORT":
            try:
                resolve_import(node.fields["path"], node.fields["alias"])
            except ValueError as error:
                raise FormatError(str(error)) from error
        if node.kind == "EXPORT":
            fields = node.fields
            if parent is None or parent.kind != "MODULE" or ((fields["alias_kind"] == "IMPLICIT") != (fields["alias"] is None)):
                raise FormatError("Invalid export scope or alias")
            if fields["alias_kind"] == "IMPLICIT" and fields["value"].kind != "NAME":
                raise FormatError("Implicit export requires a name")
            if fields["alias_kind"] in {"INT", "FLOAT", "NAME"} and not check_value(fields["alias"], {"INT": "integer", "FLOAT": "float", "NAME": "identifier"}[fields["alias_kind"]]):
                raise FormatError("Invalid export alias")
        if node.kind == "MEMBER" and not check_value(node.fields["key"], "integer" if node.fields["key_kind"] == "INT" else "identifier"):
            raise FormatError("Invalid member key")
        for name, child in children(node):
            child_block = node if node.kind == "BLOCK" else block
            child_function = function
            if node.kind == "FUNCTION" and name == "body":
                child_block, child_function = None, node
            visit(child, node.span, child_block, child_function, node)

    visit(unit.root, unit.span, None, None, None)
    if unit.root.kind not in EXPRESSION_KINDS | {"MODULE"}:
        raise FormatError("Invalid AST root category")


def read_ast(text: str) -> AstDocument:
    try:
        source, records, metadata = read_header(text, "SFLAST 1")
        units = []
        position = 0
        while position < len(records):
            span = unit_start(records[position], len(units) + 1)
            status, root_record = records[position + 1:position + 3]
            if len(status) != 2 or status[0] != "STATUS" or len(root_record) != 2 or root_record[0] != "ROOT":
                raise FormatError("Expected STATUS and ROOT")
            root_ref = parse_value(root_record[1])
            if root_ref is not None and not isinstance(root_ref, Reference):
                raise FormatError("Invalid root reference")
            position += 3
            nodes = {}
            diagnostics = []
            while position < len(records) and records[position] != ["END"]:
                record = records[position]
                position += 1
                if record[0] == "DIAG" and len(record) == 6 and not nodes:
                    diagnostics.append(Diagnostic(record[1], unquote(record[2]).decode("utf-8"), unquote(record[5]).decode("utf-8"), Span(Position.parse(record[3]), Position.parse(record[4]))))
                    continue
                if len(record) < 5 or record[:2] != ["NODE", str(len(nodes) + 1)] or record[2] not in SCHEMAS:
                    raise FormatError("Invalid node record")
                fields = {}
                for item in record[5:]:
                    name, separator, value = item.partition("=")
                    if not separator or name in fields:
                        raise FormatError("Duplicate or malformed node field")
                    fields[name] = parse_value(value)
                nodes[len(nodes) + 1] = Node(record[2], Span(Position.parse(record[3]), Position.parse(record[4])), fields, len(nodes) + 1)
            if position >= len(records):
                raise FormatError("Missing END")
            position += 1

            def resolve(value):
                if isinstance(value, Reference):
                    if value.number not in nodes:
                        raise FormatError("Dangling node reference")
                    return nodes[value.number]
                if isinstance(value, list):
                    return [resolve(item) for item in value]
                return value

            for node in nodes.values():
                node.fields = {name: resolve(value) for name, value in node.fields.items()}
            root = resolve(root_ref)
            unit = AstUnit(span, root, status[1], diagnostics)
            validate_ast(unit)
            reached = number_nodes(root) if root else []
            if reached != list(nodes.values()):
                raise FormatError("Unreachable or non-preorder AST nodes")
            units.append(unit)
        if not units:
            raise FormatError("Missing UNIT")
        document = AstDocument(source, units, metadata.get("GENERATOR", ""), metadata.get("INPUT_SHA256", ""), metadata.get("CONTENT_SHA256", ""))
        if document.content_hash and document.content_hash != digest(ast_body(document)):
            raise FormatError("AST content checksum mismatch")
        return document
    except (ValueError, UnicodeError, IndexError, KeyError) as error:
        raise FormatError(str(error)) from error
