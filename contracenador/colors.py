"""colors.py - cores ANSI simples para o terminal, só para deixar mais fácil separar visualmente
a FALA de um personagem dos logs internos de decisão (aquelas linhas "   . fulano fez tal coisa").

Sem dependência externa: funciona em qualquer terminal que suporte ANSI (Linux/macOS de boa;
no Windows moderno também, desde que não seja o cmd.exe antigo sem VT100 habilitado).
"""

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Paleta para nomes de personagens: evita vermelho/verde puros, que ficam reservados para
# derrota/vitória.
_PALETTE = [
    "\033[36m",  # ciano
    "\033[33m",  # amarelo
    "\033[35m",  # magenta
    "\033[34m",  # azul
    "\033[96m",  # ciano claro
    "\033[93m",  # amarelo claro
    "\033[95m",  # magenta claro
    "\033[94m",  # azul claro
]

GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"


def color_for_name(name):
    """Cor determinística por nome: o mesmo personagem sempre sai com a mesma cor na sessão."""
    return _PALETTE[sum(ord(c) for c in name) % len(_PALETTE)]


def dim(text):
    """Log interno (decisões, eventos) - discreto, para não competir com a fala."""
    return f"{DIM}{text}{RESET}"


def heading(text):
    """Cabeçalho de seção (rodada, interrogatório) - chama atenção sem ser fala nem vitória."""
    return f"{BOLD}{CYAN}{text}{RESET}"


def victory(text):
    return f"{BOLD}{GREEN}{text}{RESET}"


def defeat(text):
    return f"{BOLD}{RED}{text}{RESET}"
