from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .cache import Compiler
from .model import SflError
from .render import representation
from .runtime import Interpreter


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sfl", description="SFL lexer, AST compiler and interpreter")
    parser.add_argument("--version", action="version", version="sfl " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("lex", "generate or reuse .sflex"), ("ast", "generate or reuse .sflex and .sflast"), ("run", "compile and execute the serialized AST")]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("source", type=Path, help="path to a .sfl source file")
        command.add_argument("--force", action="store_true", help="rebuild caches even when valid")
        command.add_argument("-v", "--verbose", action="store_true", help="report cache hits, builds and metadata refreshes")
        if name == "run":
            command.add_argument("-I", "--library", action="append", default=[], type=Path, help="add a library search directory")
            command.add_argument("--print-result", action="store_true", help="print the module export dictionary after execution")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = argument_parser().parse_args(argv)
    compiler = Compiler(arguments.force, (lambda message: print(message, file=sys.stderr)) if arguments.verbose else None)
    path = arguments.source.resolve()
    try:
        if arguments.command == "lex":
            document = compiler.lex(path)
            errors = [token for unit in document.units for token in unit.tokens if token.kind == "ERROR"]
            if errors:
                token = errors[0]
                raise SflError(token.payload.decode("ascii"), "Invalid source token", token.span, str(path))
            print(compiler.cache_paths(path)[0])
        elif arguments.command == "ast":
            document = compiler.ast(path)
            Interpreter(compiler).check_document(document, path)
            for cache_path in compiler.cache_paths(path):
                print(cache_path)
        else:
            result = Interpreter(compiler, library_paths=arguments.library).run(path)
            if arguments.print_result:
                print(representation(result).decode("ascii"))
        return 0
    except SflError as error:
        print(str(error), file=sys.stderr)
    except OSError as error:
        print(f"{path}: IO_ERROR: {error}", file=sys.stderr)
    except RecursionError:
        print(f"{path}: RESOURCE_LIMIT: Python recursion limit reached", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
