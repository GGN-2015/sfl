from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from typing import Any

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

    def handle(self, name: bytes, value: Any) -> Any:
        handler = self._handlers.get(name)
        return handler(value) if handler is not None else None


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
