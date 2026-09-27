"""Compatibilidade para iniciar o gerador de cenas pelo caminho historico."""
import importlib
import sys

_implementation = importlib.import_module("contracenador.scenarios.generator")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation