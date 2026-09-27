"""
screenwriter.py - O Roteirista: IA que gera a trama e personalidades dos atores.

O Roteirista é responsável por:
1) Criar um mistério fechado e coerente (cenário, culpado, investigador e testemunhas).
2) Definir traços psicológicos e jeitos de falar compatíveis com a história.
3) Gerar a 'verdade' e o 'álibi' do culpado, sem contradições com o que as testemunhas viram.
4) Materializar esses dados em arquivos .db SQLite isolados (um para cada ator).

Uso:
    python screenwriter.py --tema "Uma xícara antiga foi quebrada durante o jantar"
    python screenwriter.py --carregar cena.json --pasta atores
"""
import argparse
import json
import sys

from ..agents.personality import draw_names
from ..llm.client import LLM
from ..llm.prompts import SCREENWRITER_SYSTEM_PROMPT
from .loader import clear_scene_storage, materialize_scene
from .validator import SCENE_SCHEMA, extract_json, validate_scene_data

def generate_world_seed(theme):
    """Cria uma estrutura mínima do mundo em código para reduzir tokens e padronizar a cena.

    A IA de roteiro ainda pode preencher a descrição textual principal, mas a estrutura base e
    os papéis importantes ficam estáveis e consistentes sem depender de muito contexto gerado
    por prompt.
    """
    location = "Casa antiga e pouco iluminada"
    conflict = f"Mistério em torno de {theme.lower()}"
    jobs = [
        "porteiro",
        "professor",
        "contadora",
        "empresário",
        "cozinheiro",
    ]
    tension = {
        "mood": "suspense",
        "pressure": 0.8,
        "stakes": "a reputação e a verdade precisam ser protegidas",
    }
    return {
        "location": location,
        "conflict": conflict,
        "jobs": jobs,
        "tension": tension,
        "theme": theme,
    }


def build_code_fallback_scene(theme, drawn_names):
    """Fallback determinístico que mantém a cena coerente sem depender do modelo.

    A lógica central fica em código para evitar ruído, custo de token e inconsistência.
    """
    world_seed = generate_world_seed(theme)
    names = drawn_names or draw_names(5)
    character_data = [
        {
            "name": names[0],
            "role": "guilty",
            "description": f"{names[0]} age como alguém que precisa controlar a narrativa da cena.",
            "examples": [
                f"{names[0]}: 'Não foi o que parece.'",
                f"{names[0]}: 'Eu só estava tentando evitar problemas.'",
            ],
            "traits": {
                "honesty": 0.18,
                "deceit": 0.9,
                "empathy": 0.3,
                "courage": 0.4,
                "aggressiveness": 0.6,
                "greed": 0.8,
            },
            "truth": f"{names[0]} causou o incidente em {theme.lower()}.",
            "alibi": "Estava ocupado em outra parte e não viu nada relevante.",
        },
        {
            "name": names[1],
            "role": "investigator",
            "description": f"{names[1]} age como quem exige coerência e provas antes de aceitar qualquer versão.",
            "examples": [
                f"{names[1]}: 'Precisamos da verdade, não da conveniência.'",
                f"{names[1]}: 'Há uma contradição. Vamos separar fatos de emoção.'",
            ],
            "traits": {
                "honesty": 0.8,
                "deceit": 0.2,
                "empathy": 0.6,
                "courage": 0.8,
                "aggressiveness": 0.55,
                "greed": 0.3,
            },
            "goal": f"Descobrir a verdade por trás de {theme.lower()}.",
        },
    ]
    for name in names[2:]:
        character_data.append({
            "name": name,
            "role": "witness",
            "description": f"{name} viu o suficiente para conservar detalhes, mas não quer ser arrastado para o centro do problema.",
            "examples": [
                f"{name}: 'Eu vi algo estranho, mas não tenho certeza.'",
                f"{name}: 'Foi só um detalhe, mas ficou na minha cabeça.'",
            ],
            "traits": {
                "honesty": 0.7,
                "deceit": 0.3,
                "empathy": 0.7,
                "courage": 0.43,
                "aggressiveness": 0.35,
                "greed": 0.28,
            },
            "saw": f"Vi {names[0]} agir de forma suspeita perto do evento central.",
            "saw_effect": "supports",
        })
    return {
        "scene": world_seed["conflict"],
        "characters": character_data,
    }


# ============================================================================
# 2) GERAÇÃO DA CENA VIA LLM
# ============================================================================

def generate_scene_llm(llm, theme, max_tokens=1800, temperature=0.7):
    """
    Solicita ao modelo a criação da cena e dos 5 personagens a partir do tema proposto.
    Tenta usar response_format para forçar JSON e trata possíveis incompatibilidades.

    Os NOMES dos personagens são sorteados pelo código (names.py), não inventados pelo LLM -
    escolher um nome não exige criatividade nem entendimento de contexto, é exatamente o tipo
    de decisão que cabe ao código (mesma filosofia do resto do projeto). O LLM só usa os nomes
    já sorteados ao escrever a trama (personalidade, papel, quem viu o quê).
    """
    drawn_names = draw_names(5)
    messages = [
        {"role": "system", "content": SCREENWRITER_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Crie um mistério completo com 5 personagens sobre o seguinte tema:\n\"{theme}\"\n\n"
            "Use OBRIGATORIAMENTE estes 5 nomes, um para cada personagem (você decide quem tem "
            f"qual papel): {', '.join(drawn_names)}."
        )}
    ]

    print(f"\n[Roteirista] Conectando a {llm.url} para criar a cena...")
    print(f"[Roteirista] Tema: \"{theme}\"")

    # Tentativa 1: com schema estruturado
    schema_format = {
        "type": "json_object",
        "schema": SCENE_SCHEMA
    }

    response_text = ""
    try:
        response_text = llm.generate(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            live=True,
            response_format=schema_format
        )
    except RuntimeError as err:
        # Se o servidor rejeitar o formato avançado de schema, tenta json_object genérico
        print(f"   [aviso] Falha com schema avançado ({err}). Tentando formato JSON simples...")
        try:
            response_text = llm.generate(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                live=True,
                response_format={"type": "json_object"}
            )
        except RuntimeError:
            print("   [aviso] Tentando chamada sem parâmetro response_format...")
            response_text = llm.generate(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                live=True
            )

    try:
        data = extract_json(response_text)
        validate_scene_data(data)
    except Exception:
        print(f"   [aviso] Falha na resposta do roteirista. Usando fallback determinístico baseado em código.")
        data = build_code_fallback_scene(theme, draw_names(5))
        validate_scene_data(data)
        return data

    used = {c.get("name") for c in data.get("characters", [])}
    if not set(drawn_names) <= used:
        ignored = set(drawn_names) - used
        print(f"   [aviso] O modelo não usou todos os nomes sorteados (ignorou: "
              f"{', '.join(sorted(ignored))}). Seguindo com os nomes que ele escreveu.")

    return data


# ============================================================================
# 4) INTERFACE CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="O Roteirista - Gerador de tramas e atores de IA (100% via LLM)")
    parser.add_argument("--tema", type=str, help="Tema ou incidente central para o Roteirista inventar a cena")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Endereço do llama-server (ex: 8080 ou 8081)")
    parser.add_argument("--pasta", default="atores", help="Pasta onde os arquivos .db dos atores serão criados")
    parser.add_argument("--cenario", default="cenario", help="Pasta onde o arquivo cena.json será salvo")
    parser.add_argument("--slots", type=int, default=2, help="Número de slots no llama-server")
    parser.add_argument("--carregar", type=str, help="Carrega e materializa uma cena a partir de um arquivo JSON")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.carregar:
        print(f"[Roteirista] Carregando cena de {args.carregar}...")
        with open(args.carregar, "r", encoding="utf-8") as f:
            data = json.load(f)
        materialize_scene(data, actors_folder=args.pasta, scenario_folder=args.cenario, slots=args.slots)
        return

    theme = args.tema
    while not theme:
        print("\n=== O Roteirista de Cenas (100% IA) ===")
        print("Digite o tema ou incidente da cena a ser criada pelo modelo:")
        theme = input("> ").strip()
        if not theme:
            print("Por favor, digite um tema para que a IA possa criar a história.")

    llm = LLM(url=args.url, timeout=300)
    try:
        scene_data = generate_scene_llm(llm, theme)
        materialize_scene(scene_data, actors_folder=args.pasta, scenario_folder=args.cenario, slots=args.slots)
    except Exception as e:
        print(f"\n[Erro do Roteirista] {e}")
        print("Verifique se o llama-server está em execução com o modelo carregado.")
        sys.exit(1)


if __name__ == "__main__":
    main()
