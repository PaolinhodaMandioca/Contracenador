"""
names.py - banco de nomes prontos para os personagens.

IDEIA CENTRAL (a mesma do resto do projeto): escolher um nome não exige criatividade nem
compreensão de contexto - é o tipo de coisa que o CÓDIGO decide, sorteando de uma lista, em vez
de pedir para o LLM inventar. O Roteirista ainda decide personalidade, papel e trama; só não
inventa mais os nomes - ele recebe os nomes já sorteados e os USA na história (screenwriter.py).

Vantagens sobre deixar o LLM inventar:
  * nomes sempre bem formados ("Nome Sobrenome"), nunca algo estranho ou incompleto;
  * nunca repete o mesmo nome duas vezes na mesma cena;
  * zero tokens gastos pelo LLM "pensando" em nomes.
"""
import random

FIRST_NAMES = [
    "Ana", "Bruno", "Carla", "Daniel", "Elisa", "Fábio", "Gabriela", "Hugo",
    "Isabela", "João", "Karina", "Lucas", "Mariana", "Nuno", "Otávio", "Paula",
    "Rafael", "Sofia", "Tiago", "Vitória", "Wagner", "Yasmin", "Alice", "Bernardo",
    "Camila", "Diego", "Eduarda", "Felipe", "Giovanna", "Henrique",
]

LAST_NAMES = [
    "Almeida", "Barros", "Carvalho", "Dias", "Esteves", "Ferreira", "Gomes",
    "Henriques", "Ibrahim", "Junqueira", "Lopes", "Martins", "Nogueira", "Oliveira",
    "Pereira", "Queiroz", "Ribeiro", "Silva", "Teixeira", "Uchoa", "Vasconcelos",
    "Xavier", "Zanetti", "Andrade", "Bezerra", "Correia", "Duarte", "Espinoza",
    "Farias", "Guimarães",
]


def draw_names(k=5):
    """
    Sorteia `k` nomes completos únicos ("Nome Sobrenome"), sem repetir primeiro nome nem
    sobrenome entre si (deixa os personagens mais fáceis de distinguir numa cena).
    """
    first = random.sample(FIRST_NAMES, k)
    last = random.sample(LAST_NAMES, k)
    return [f"{f} {s}" for f, s in zip(first, last)]
