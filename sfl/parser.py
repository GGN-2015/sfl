from __future__ import annotations

from contextlib import contextmanager

from .formats import validate_ast
from .imports import resolve_import
from .model import AstDocument, AstUnit, Diagnostic, LexDocument, Node, SflError, Span, Token


OPERATORS = {
    "STAR": (30, "MUL"), "SLASH": (30, "DIV"), "FLOOR_DIV": (30, "FLOOR_DIV"), "PERCENT": (30, "MOD"),
    "PLUS": (20, "ADD"), "MINUS": (20, "SUB"),
    "LT": (10, "LT"), "GT": (10, "GT"), "LE": (10, "LE"), "GE": (10, "GE"), "EQ": (10, "EQ"), "NE": (10, "NE"),
}


class Parser:
    def __init__(self, tokens: list[Token], filename: str):
        self.tokens = [token for token in tokens if token.kind != "COMMENT"]
        self.filename = filename
        self.position = 0
        self.soft = False
        self.last = self.tokens[0]
        self.blocks: list[Node] = []
        self.functions: list[Node] = []
        self.module_scope = True
        for token in self.tokens:
            if token.kind == "ERROR":
                raise SflError(token.payload.decode("ascii"), "Invalid source token", token.span, filename)

    def error(self, message: str, token: Token | None = None, code: str = "SYNTAX_ERROR"):
        raise SflError(code, message, (token or self.peek()).span, self.filename)

    def peek(self) -> Token:
        if self.soft:
            self.skip_lines()
        return self.tokens[self.position]

    def take(self) -> Token:
        token = self.peek()
        if token.kind != "EOF":
            self.position += 1
        if token.kind not in {"NEWLINE", "EOF"}:
            self.last = token
        return token

    def match(self, kind: str) -> Token | None:
        return self.take() if self.peek().kind == kind else None

    def expect(self, kind: str) -> Token:
        token = self.peek()
        if kind == "IDENT" and token.kind.startswith("KW_"):
            self.error("Reserved keyword cannot be used as a name: " + token.raw.decode("ascii"), token, "RESERVED_NAME")
        if self.peek().kind != kind:
            self.error(f"Expected {kind}, got {self.peek().kind}")
        return self.take()

    def skip_lines(self):
        while self.tokens[self.position].kind == "NEWLINE":
            self.position += 1

    @contextmanager
    def newlines(self, soft: bool):
        previous = self.soft
        self.soft = soft
        try:
            yield
        finally:
            self.soft = previous

    def node(self, kind: str, start, **fields) -> Node:
        return Node(kind, Span(start, self.last.span.end), fields)

    def module(self, span: Span) -> Node:
        body = self.statements("EOF")
        self.expect("EOF")
        return Node("MODULE", span, {"body": body})

    def statements(self, terminator: str) -> list[Node]:
        body = []
        self.skip_lines()
        while self.peek().kind != terminator:
            if self.peek().kind == "EOF":
                self.error(f"Expected {terminator}")
            body.append(self.statement())
            if self.peek().kind not in {"NEWLINE", terminator}:
                self.error("Expected newline between statements")
            self.skip_lines()
        return body

    def statement(self) -> Node:
        first = self.peek()
        start = first.span.start
        if (first.kind == "IDENT" or first.kind.startswith("KW_")) and self.tokens[self.position + 1].kind == "BIND":
            name = self.expect("IDENT").raw
            self.expect("BIND")
            value = self.expression()
            return self.node("BINDING", start, name=name, value=value)
        if first.kind in {"KW_IMPORT", "KW_LOCAL"}:
            self.take()
            path = self.expect("STRING")
            alias = self.expect("IDENT").raw if self.match("KW_AS") else None
            try:
                resolve_import(path.payload, alias)
            except ValueError as error:
                self.error(str(error), path, "IMPORT_ERROR")
            return self.node("IMPORT", start, mode="LIBRARY" if first.kind == "KW_IMPORT" else "RELATIVE", path=path.payload, alias=alias)
        if first.kind == "KW_RETURN":
            self.take()
            if not self.blocks:
                self.error("return requires an enclosing block", first, "INVALID_RETURN")
            value = self.expression()
            return self.node("RETURN", start, value=value, target=self.blocks[-1])
        if first.kind == "KW_EXPORT":
            self.take()
            if not self.module_scope:
                self.error("export is only allowed directly in a module", first, "INVALID_EXPORT")
            value = self.expression()
            alias_kind, alias = "IMPLICIT", None
            if self.match("KW_AS"):
                token = self.take()
                kinds = {"IDENT": "NAME", "INT": "INT", "FLOAT": "FLOAT", "STRING": "STRING"}
                if token.kind not in kinds:
                    self.error("Export alias must be an identifier, number or string literal", token)
                alias_kind = kinds[token.kind]
                alias = token.payload if token.kind == "STRING" else token.raw
            elif value.kind != "NAME":
                self.error("An expression export needs an explicit alias", first)
            return self.node("EXPORT", start, value=value, alias_kind=alias_kind, alias=alias)
        value = self.expression()
        return self.node("EXPR_STMT", start, value=value)

    def block(self) -> Node:
        first = self.expect("LBRACE")
        node = Node("BLOCK", first.span, {"body": []})
        previous_scope = self.module_scope
        self.module_scope = False
        self.blocks.append(node)
        try:
            with self.newlines(False):
                node.fields["body"] = self.statements("RBRACE")
                self.expect("RBRACE")
        finally:
            self.blocks.pop()
            self.module_scope = previous_scope
        node.span = Span(first.span.start, self.last.span.end)
        return node

    def expression(self, minimum: int = 0) -> Node:
        first = self.take()
        start = first.span.start
        if first.kind == "IDENT":
            left = Node("NAME", first.span, {"name": first.raw})
        elif first.kind in {"INT", "FLOAT"}:
            left = Node(first.kind, first.span, {"text": first.raw})
        elif first.kind == "STRING":
            left = Node("STRING", first.span, {"bytes": first.payload})
        elif first.kind in {"KW_INF", "KW_NULL", "EMPTY_DICT"}:
            left = Node({"KW_INF": "INF", "KW_NULL": "NULL", "EMPTY_DICT": "EMPTY_DICT"}[first.kind], first.span)
        elif first.kind == "KW_SELF":
            if not self.functions:
                self.error("self requires an enclosing function body", first, "INVALID_SELF")
            left = Node("SELF", first.span, {"function": self.functions[-1]})
        elif first.kind == "LPAREN":
            with self.newlines(True):
                left = self.expression()
                self.expect("RPAREN")
        elif first.kind == "LBRACE":
            self.position -= 1
            left = self.block()
        elif first.kind in {"MINUS", "KW_LEN", "KW_REPR", "KW_TYPE"}:
            operand = self.expression(40)
            if first.kind == "MINUS":
                left = self.node("UNARY", start, op="NEG", operand=operand)
            else:
                left = self.node({"KW_LEN": "LENGTH", "KW_REPR": "REPR", "KW_TYPE": "TYPE"}[first.kind], start, value=operand)
        elif first.kind == "KW_FN":
            left = self.function(first)
        elif first.kind == "KW_IF":
            condition = self.expression()
            self.skip_lines()
            self.expect("KW_THEN")
            self.skip_lines()
            then_body = self.block()
            self.skip_lines()
            self.expect("KW_ELSE")
            self.skip_lines()
            else_body = self.block()
            left = self.node("IF", start, condition=condition, **{"then": then_body, "else": else_body})
        elif first.kind == "KW_DO":
            self.skip_lines()
            left = self.node("DO", start, body=self.block())
        elif first.kind == "KW_TRY":
            self.skip_lines()
            body = self.block()
            self.skip_lines()
            self.expect("KW_EXCEPT")
            error_name = self.expect("IDENT").raw
            self.expect("KW_THEN")
            self.skip_lines()
            left = self.node("TRY", start, body=body, error_name=error_name, handler=self.block())
        elif first.kind == "KW_EVAL":
            code = self.expression()
            environment = self.expression() if self.match("KW_WITH") else None
            signals = []
            while self.match("KW_WITH_SIGNAL"):
                signals.append(self.expression())
            left = self.node("EVAL", start, code=code, environment=environment, signals=signals)
        elif first.kind == "KW_EMIT":
            value = self.expression()
            self.expect("KW_AS")
            left = self.node("EMIT", start, value=value, signal=self.expression())
        else:
            self.error(f"Expected expression, got {first.kind}", first)

        comparison_seen = False
        while True:
            token = self.peek()
            if token.kind in {"LPAREN", "LBRACKET", "DOT"} and 50 >= minimum:
                if token.kind == "LPAREN":
                    self.take()
                    args = []
                    with self.newlines(True):
                        if not self.match("RPAREN"):
                            while True:
                                args.append(self.expression())
                                if not self.match("COMMA"):
                                    self.expect("RPAREN")
                                    break
                                if self.match("RPAREN"):
                                    break
                    left = self.node("CALL", left.span.start, callee=left, args=args)
                elif token.kind == "DOT":
                    self.take()
                    key = self.take()
                    if key.kind not in {"INT", "IDENT"}:
                        self.error("Expected integer or identifier member name", key)
                    left = self.node("MEMBER", left.span.start, base=left, key_kind="INT" if key.kind == "INT" else "NAME", key=key.raw)
                else:
                    left = self.brackets(left)
                continue
            operator = OPERATORS.get(token.kind)
            if operator is None or operator[0] < minimum:
                break
            if operator[0] == 10 and comparison_seen:
                self.error("Comparison chains need explicit parentheses")
            comparison_seen |= operator[0] == 10
            self.take()
            right = self.expression(operator[0] + 1)
            left = self.node("BINARY", left.span.start, op=operator[1], left=left, right=right)
        return left

    def function(self, first: Token) -> Node:
        self.expect("LPAREN")
        params = []
        with self.newlines(True):
            if not self.match("RPAREN"):
                while True:
                    token = self.expect("IDENT")
                    if any(parameter.fields["name"] == token.raw for parameter in params):
                        self.error("Duplicate function parameter", token, "DUPLICATE_PARAMETER")
                    params.append(Node("PARAM", token.span, {"name": token.raw}))
                    if not self.match("COMMA"):
                        self.expect("RPAREN")
                        break
                    if self.match("RPAREN"):
                        break
        self.skip_lines()
        environment = self.expression() if self.match("KW_WITH") else None
        self.skip_lines()
        function = Node("FUNCTION", first.span, {"params": params, "environment": environment, "body": None})
        previous_blocks = self.blocks
        self.blocks = []
        self.functions.append(function)
        try:
            function.fields["body"] = self.block()
        finally:
            self.functions.pop()
            self.blocks = previous_blocks
        function.span = Span(first.span.start, self.last.span.end)
        return function

    def brackets(self, base: Node) -> Node:
        self.expect("LBRACKET")
        entries = []
        with self.newlines(True):
            key = self.expression()
            is_update = self.match("BIND") is not None
        if not is_update:
            with self.newlines(True):
                self.expect("RBRACKET")
            return self.node("INDEX", base.span.start, base=base, key=key)
        while True:
            with self.newlines(False):
                value = self.expression()
                entries.append(self.node("ENTRY", key.span.start, key=key, value=value))
                if self.match("RBRACKET"):
                    break
                if not self.match("COMMA") and not self.match("NEWLINE"):
                    self.error("Expected newline or comma between dictionary entries")
                self.skip_lines()
                if self.match("RBRACKET"):
                    break
            with self.newlines(True):
                key = self.expression()
                self.expect("BIND")
        return self.node("DICT_UPDATE", base.span.start, base=base, entries=entries)


def parse(document: LexDocument, expression: bool = False) -> AstDocument:
    units = []
    for unit in document.units:
        try:
            parser = Parser(unit.tokens, document.source.filename)
            if expression:
                with parser.newlines(True):
                    root = parser.expression()
                    parser.expect("EOF")
            else:
                root = parser.module(unit.span)
            parsed = AstUnit(unit.span, root)
            validate_ast(parsed)
        except SflError as error:
            parsed = AstUnit(unit.span, None, "INVALID", [Diagnostic("ERROR", error.code, error.message, error.span or unit.span)])
        units.append(parsed)
    return AstDocument(document.source, units)
