from __future__ import annotations

import pytest
from sfl.runtime import Interpreter
from sfl.signals import SignalHandler, StandardSignalHandler

from .support import Harness, copy_standard_library


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.delenv("SFL_PATH", raising=False)
    return copy_standard_library(tmp_path / "stdlib")


@pytest.fixture
def harness(library):
    events = []

    def echo(value):
        events.append(value)
        return value

    signals = SignalHandler({b"echo": echo, b"environment": echo, b"stdio.print": echo})
    interpreter = Interpreter(signals=[StandardSignalHandler(), signals], library_paths=[library])
    return Harness(interpreter, events, library)
