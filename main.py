"""Compatibilidade para iniciar e importar a CLI pelo caminho historico."""
import importlib
import sys

_implementation = importlib.import_module("contracenador.cli.main")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation