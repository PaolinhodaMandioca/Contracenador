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

JOB_POOL = [
    "Joalheiro",
    "Caixeiro",
    "Contador",
    "Chaveiro",
    "Empresário",
    "Porteiro",
    "Professor",
    "Advogado",
    "Mecânico",
    "Chef de Cozinha",
    "Enfermeira",
    "Motorista",
]

ROLE_TRAIT_BASE = {
    "guilty": {
        "honesty": 0.18,
        "deceit": 0.9,
        "empathy": 0.25,
        "courage": 0.45,
        "aggressiveness": 0.6,
        "greed": 0.8,
    },
    "investigator": {
        "honesty": 0.78,
        "deceit": 0.25,
        "empathy": 0.6,
        "courage": 0.8,
        "aggressiveness": 0.55,
        "greed": 0.35,
    },
    "witness": {
        "honesty": 0.7,
        "deceit": 0.3,
        "empathy": 0.7,
        "courage": 0.42,
        "aggressiveness": 0.35,
        "greed": 0.28,
    },
}

ROLE_BLUEPRINTS = {
    "guilty": {
        "jobs": ["Joalheiro", "Caixeiro", "Contador", "Chaveiro", "Empresário"],
        "speech": [
            "fala baixo, mede cada palavra e evita contato visual",
            "usa frases curtas e densas, sem revelar o que realmente sabe",
            "desvia o assunto com ironia quando se sente pressionado",
        ],
    },
    "investigator": {
        "jobs": ["Detetive", "Professor", "Advogado", "Porteiro", "Contador"],
        "speech": [
            "fala clara, direta e insistente",
            "faz perguntas rápidas para testar a consistência das versões",
            "mantém o tom firme e quase interrogatório em cada frase",
        ],
    },
    "witness": {
        "jobs": ["Cozinheiro", "Motorista", "Enfermeira", "Manobrista", "Porteiro"],
        "speech": [
            "fala nervoso, reparando nos detalhes mais pequenos",
            "mistura lembranças e hesitações ao relatar o que viu",
            "usa detalhes concretos para reforçar a memória",
        ],
    },
}

SPEECH_STYLES = {
    "guilty": [
        "fala baixo, mede cada palavra e evita contato visual",
        "usa frases curtas e densas, sem revelar o que realmente sabe",
        "desvia o assunto com ironia quando se sente pressionado",
    ],
    "investigator": [
        "fala clara, direta e insistente",
        "faz perguntas rápidas para testar a consistência das versões",
        "mantém o tom firme e quase interrogatório em cada frase",
    ],
    "witness": [
        "fala nervoso, reparando nos detalhes mais pequenos",
        "mistura lembranças e hesitações ao relatar o que viu",
        "usa detalhes concretos para reforçar a memória",
    ],
}

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


def generate_personality_profile(role, name, theme="", rng=random):
    """Gera um perfil de personalidade determinístico em código para reduzir tokens.

    A estrutura aceita a mesma convenção do resto do projeto: o código decide o que é
    estrutural e o LLM só interpreta o contexto narrativo.
    """
    normalized_role = (role or "witness").lower()
    base_traits = ROLE_TRAIT_BASE.get(normalized_role, ROLE_TRAIT_BASE["witness"]).copy()
    blueprint = ROLE_BLUEPRINTS.get(normalized_role, ROLE_BLUEPRINTS["witness"])
    job = rng.choice(blueprint["jobs"])
    speech = rng.choice(blueprint["speech"])

    if normalized_role == "guilty":
        description = (
            f"{name} trabalha como {job.lower()} e sabe como manipular a rotina da casa para "
            "pegar o que quer sem ser notado."
        )
        examples = [
            f"{name}: 'Eu só queria que ninguém visse a situação.'",
            f"{name}: 'Tudo aconteceu de forma natural; não foi nada demais.'",
        ]
    elif normalized_role == "investigator":
        description = (
            f"{name} é {job.lower()} e se move pela cena com uma lógica rígida: anota detalhes, "
            "testa versões e não abandona o caso até fechar a inconsistência."
        )
        examples = [
            f"{name}: 'Quero a versão correta, não a mais conveniente.'",
            f"{name}: 'Se houve contradição, então existe uma falha na narrativa.'",
        ]
    else:
        description = (
            f"{name} trabalha como {job.lower()} e se lembra do que viu com clareza, mesmo "
            "quando o medo o faz hesitar antes de falar."
        )
        examples = [
            f"{name}: 'Eu vi alguém agir de forma estranha perto do corredor.'",
            f"{name}: 'Não sei dizer por quê, mas o jeito dele me deixou desconfortável.'",
        ]

    traits = {
        key: round(max(0.0, min(1.0, value + rng.uniform(-0.08, 0.08))), 2)
        for key, value in base_traits.items()
    }
    return {
        "name": name,
        "role": normalized_role,
        "job": job,
        "description": description,
        "speech": speech,
        "traits": traits,
        "examples": examples,
        "theme": theme,
    }
