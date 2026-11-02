from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .model import SflError
from .render import representation


class SignalHandler:
    def __init__(self, handlers: Mapping[bytes, Callable[[Any], Any]] | None = None) -> None:
        self._handlers: dict[bytes, Callable[[Any], Any]] = {}
        if handlers is not None:
            for name, handler in handlers.items():
                self.register(name, handler)

    def register(self, name: bytes, handler: Callable[[Any], Any]) -> None:
        if not isinstance(name, bytes):
            raise TypeError("Signal name must be bytes")
        if not callable(handler):
            raise TypeError("Signal handler must be callable")
        self._handlers[name] = handler

    def unregister(self, name: bytes) -> None:
        self._handlers.pop(name, None)

    def supports(self, name: bytes) -> bool:
        return name in self._handlers

    def handle(self, name: bytes, value: Any) -> Any:
        handler = self._handlers.get(name)
        return handler(value) if handler is not None else None


class CompositeSignalHandler(SignalHandler):
    def __init__(self, handlers: Iterable[SignalHandler]) -> None:
        super().__init__()
        self.handlers = tuple(handlers)
        if not all(isinstance(handler, SignalHandler) for handler in self.handlers):
            raise TypeError("Composite signal handlers must be SignalHandler instances")

    def supports(self, name: bytes) -> bool:
        return super().supports(name) or any(handler.supports(name) for handler in self.handlers)

    def handle(self, name: bytes, value: Any) -> Any:
        if super().supports(name):
            return super().handle(name, value)
        for handler in reversed(self.handlers):
            if handler.supports(name):
                return handler.handle(name, value)
        return None


class StdioSignalHandler(SignalHandler):
    def __init__(self) -> None:
        super().__init__({
            b"stdio.print": self.print,
            b"stdio.write": self.write,
            b"stdio.readline": self.readline,
        })

    def _output(self, value: Any, newline: bool) -> None:
        data = value if isinstance(value, bytes) else representation(value)
        data += b"\n" if newline else b""
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        else:
            sys.stdout.write(data.decode("utf-8", errors="backslashreplace"))
            sys.stdout.flush()

    def print(self, value: Any) -> None:
        self._output(value, True)

    def write(self, value: Any) -> None:
        self._output(value, False)

    def readline(self, value: Any) -> bytes | None:
        data = sys.stdin.buffer.readline() if hasattr(sys.stdin, "buffer") else sys.stdin.readline().encode("utf-8")
        if data.endswith(b"\r\n"):
            data = data[:-2] + b"\n"
        return data if data else None


class StrSignalHandler(SignalHandler):
    def __init__(self) -> None:
        super().__init__({b"str.ord": self.ord})

    def ord(self, value: Any) -> int:
        if not isinstance(value, bytes):
            raise SflError("TYPE_ERROR", "str.ord requires a byte string")
        if len(value) != 1:
            raise SflError("INVALID_LENGTH", "str.ord requires exactly one byte")
        return value[0]


class StandardSignalHandler(CompositeSignalHandler):
    def __init__(self) -> None:
        self.stdio = StdioSignalHandler()
        self.strings = StrSignalHandler()
        super().__init__([self.stdio, self.strings])
