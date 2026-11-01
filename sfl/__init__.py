from .signals import SignalHandler, StdioSignalHandler


__version__ = "0.1.0"


def compile_source(path, *, force=False):
    from .cache import Compiler

    return Compiler(force=force).ast(path)


def run_source(path, *, force=False, signals=None, library_paths=()):
    from .cache import Compiler
    from .runtime import Interpreter

    return Interpreter(Compiler(force=force), signals=signals, library_paths=library_paths).run(path)
