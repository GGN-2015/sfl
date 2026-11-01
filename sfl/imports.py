from __future__ import annotations

from pathlib import Path

from .lexer import IDENTIFIER, KEYWORDS


def resolve_import(path: bytes, alias: bytes | None) -> tuple[Path, bytes]:
    try:
        name = path.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("Module path must be valid UTF-8") from error
    relative = Path(name if name.endswith(".sfl") else name + ".sfl")
    if alias is None:
        alias = relative.name[:-4].encode("utf-8")
        if not IDENTIFIER.fullmatch(alias) or alias.decode("ascii") in KEYWORDS:
            raise ValueError("Module filename requires an explicit 'as' alias: " + repr(relative.name))
    elif not IDENTIFIER.fullmatch(alias) or alias.decode("ascii") in KEYWORDS:
        raise ValueError("Import aliases must be non-keyword identifiers")
    return relative, alias
