from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .formats import digest, lex_body, read_ast, read_lex, write_ast, write_lex
from .lexer import lex
from .model import AstDocument, FormatError, LexDocument, Position, SflError, SourceInfo
from .parser import parse


@dataclass(frozen=True)
class Snapshot:
    path: Path
    data: bytes
    source: SourceInfo
    identity: tuple


def identity(metadata) -> tuple:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def take_snapshot(path: Path) -> Snapshot:
    for attempt in range(3):
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read()
            after = os.fstat(stream.fileno())
        if identity(before) == identity(after) == identity(path.stat()) and len(data) == after.st_size:
            seconds, fraction = divmod(after.st_mtime_ns, 1_000_000_000)
            mtime = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:09d}Z"
            filename = os.path.relpath(path, path.parent / "sflcache").replace(os.sep, "/")
            return Snapshot(path, data, SourceInfo(filename, hashlib.sha256(data).hexdigest().upper(), mtime), identity(after))
    raise SflError("SOURCE_CHANGED", "Source changed while being read", source=str(path))


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def implementation_hash(stage: str) -> str:
    names = ["model.py", "lexer.py", "formats.py", "cache.py"]
    if stage == "ast":
        names.extend(["parser.py", "imports.py"])
    result = hashlib.sha256(b"SFL-0.1-cache-v1\0" + stage.encode("ascii"))
    for name in names:
        result.update(name.encode("ascii") + b"\0")
        result.update((Path(__file__).parent / name).read_bytes())
    return result.hexdigest().upper()


class Compiler:
    def __init__(self, force: bool = False, reporter: Callable[[str], None] | None = None):
        self.force = force
        self.reporter = reporter
        self.events: list[tuple[str, str, Path]] = []
        self.keys = {stage: implementation_hash(stage) for stage in ("lex", "ast")}

    def cache_paths(self, path: str | Path) -> tuple[Path, Path]:
        source = Path(path).resolve()
        directory = source.parent / "sflcache"
        return directory / (source.stem + ".sflex"), directory / (source.stem + ".sflast")

    def event(self, stage: str, action: str, path: Path):
        self.events.append((stage, action, path))
        if self.reporter:
            self.reporter(f"{action.upper():7} {stage:3} {path}")

    def unchanged(self, snapshot: Snapshot):
        if identity(snapshot.path.stat()) != snapshot.identity or snapshot.path.read_bytes() != snapshot.data:
            raise SflError("SOURCE_CHANGED", "Source changed during compilation", source=str(snapshot.path))

    def matches(self, document, snapshot: Snapshot, stage: str, cache_path: Path) -> bool:
        try:
            same_path = (cache_path.parent / document.source.filename).resolve() == snapshot.path
        except (OSError, ValueError):
            return False
        end = Position(snapshot.data.count(b"\n") + 1, len(snapshot.data.rsplit(b"\n", 1)[-1]) + 1)
        return same_path and document.source.sha256 == snapshot.source.sha256 and document.generator == self.keys[stage] and bool(document.content_hash) and len(document.units) == 1 and document.units[0].span.start == Position(1, 1) and document.units[0].span.end == end

    def lex(self, path: str | Path) -> LexDocument:
        return self.prepare(path, False)

    def ast(self, path: str | Path) -> AstDocument:
        return self.prepare(path, True)

    def prepare(self, path: str | Path, ast_required: bool):
        path = Path(path).resolve()
        for attempt in range(3):
            snapshot = take_snapshot(path)
            try:
                lexical = self.prepare_lex(snapshot)
                result = self.prepare_ast(snapshot, lexical) if ast_required else lexical
                self.unchanged(snapshot)
                return result
            except SflError as error:
                if error.code != "SOURCE_CHANGED" or attempt == 2:
                    raise
        raise SflError("SOURCE_CHANGED", "Source changed repeatedly", source=str(path))

    def prepare_lex(self, snapshot: Snapshot) -> LexDocument:
        cache_path, _ = self.cache_paths(snapshot.path)
        if not self.force:
            try:
                cached = read_lex(cache_path.read_text(encoding="utf-8"), snapshot.data)
                if self.matches(cached, snapshot, "lex", cache_path):
                    if cached.source != snapshot.source:
                        cached.source = snapshot.source
                        self.unchanged(snapshot)
                        atomic_write(cache_path, write_lex(cached))
                        self.event("lex", "refresh", cache_path)
                    else:
                        self.event("lex", "hit", cache_path)
                    return read_lex(cache_path.read_text(encoding="utf-8"), snapshot.data)
            except (OSError, FormatError, UnicodeError, ValueError, RecursionError):
                pass
        document = lex(snapshot.data, snapshot.source)
        document.generator = self.keys["lex"]
        self.unchanged(snapshot)
        atomic_write(cache_path, write_lex(document))
        self.event("lex", "build", cache_path)
        return read_lex(cache_path.read_text(encoding="utf-8"), snapshot.data)

    def prepare_ast(self, snapshot: Snapshot, lexical: LexDocument) -> AstDocument:
        _, cache_path = self.cache_paths(snapshot.path)
        input_hash = digest(lex_body(lexical))
        if not self.force:
            try:
                cached = read_ast(cache_path.read_text(encoding="utf-8"))
                if self.matches(cached, snapshot, "ast", cache_path) and cached.input_hash == input_hash and cached.units[0].span == lexical.units[0].span:
                    if cached.source != snapshot.source:
                        cached.source = snapshot.source
                        self.unchanged(snapshot)
                        atomic_write(cache_path, write_ast(cached))
                        self.event("ast", "refresh", cache_path)
                    else:
                        self.event("ast", "hit", cache_path)
                    return read_ast(cache_path.read_text(encoding="utf-8"))
            except (OSError, FormatError, UnicodeError, ValueError, RecursionError):
                pass
        document = parse(lexical)
        document.generator = self.keys["ast"]
        document.input_hash = input_hash
        self.unchanged(snapshot)
        atomic_write(cache_path, write_ast(document))
        self.event("ast", "build", cache_path)
        return read_ast(cache_path.read_text(encoding="utf-8"))
