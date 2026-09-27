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
import os
import re
import sys

from actor import Actor, DEFAULT_TRAITS, clamp, create_actor
from llm import LLM
from names import draw_names
from world import open_world, position, register_evidence, register_event, register_location

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {
            "type": "string",
            "description": "Descrição sucinta do incidente/crime que ocorreu na cena."
        },
        "characters": {
            "type": "array",
            "description": "Lista de exatamente 5 personagens que participam da cena.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "examples": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "traits": {
                        "type": "object",
                        "properties": {
                            "honesty": {"type": "number"},
                            "deceit": {"type": "number"},
                            "empathy": {"type": "number"},
                            "courage": {"type": "number"},
                            "aggressiveness": {"type": "number"},
                            "greed": {"type": "number"}
                        },
                        "required": ["honesty", "deceit", "empathy", "courage", "aggressiveness", "greed"]
                    },
                    "role": {
                        "type": "string",
                        "enum": ["guilty", "investigator", "witness"]
                    },
                    "truth": {
                        "type": ["string", "null"],
                        "description": "Preenchido apenas para o culpado (role='guilty'): o que ele realmente fez."
                    },
                    "alibi": {
                        "type": ["string", "null"],
                        "description": "Preenchido apenas para o culpado: a versão mentirosa/álibi que ele usará."
                    },
                    "goal": {
                        "type": ["string", "null"],
                        "description": "Preenchido apenas para o investigador: o que ele deve descobrir."
                    },
                    "saw": {
                        "type": ["string", "null"],
                        "description": "Para testemunhas: fato observado sobre o culpado, ou null se não souber de nada."
                    }
                },
                "required": ["name", "description", "examples", "traits", "role"]
            }
        }
    },
    "required": ["scene", "characters"]
}

SCREENWRITER_SYSTEM_PROMPT = """Você é um roteirista especializado em simulações teatrais e jogos de investigação psicológica.
Sua missão é gerar um mistério completo com exatamente 5 personagens, pronto para ser executado por modelos de linguagem.

Regras estruturais obrigatórias:
1. Deve haver exatamente 5 personagens, cada um com um campo "role":
   - 1 "guilty": cometeu o ato. Deve ter 'truth' (confissão detalhada do ato) e 'alibi' (versão falsa e plausível que ele contará).
   - 1 "investigator": encarregado de desvendar o mistério. Deve ter 'goal' claro relacionado ao tema (ex.: descobrir quem roubou a joia, quem causou o acidente, etc.).
   - 3 "witness": pessoas que estavam no local. O campo 'saw' deve conter um fato observado sobre o culpado (mencionando o nome dele), OU ser null caso a testemunha genuinamente não tenha visto nada. Pelo menos uma testemunha deve ter visto algo suspeito.
2. Coerência lógica absoluta:
   - O 'alibi' do culpado não pode entrar em contradição boba com o que as testemunhas viram, mas deve permitir brechas para dedução e pressão psicológica.
3. Traços numéricos no campo "traits" (valores de 0.0 a 1.0 para honesty, deceit, empathy, courage, aggressiveness, greed):
   - Ajuste os traços de acordo com o papel. Exemplo: um culpado frio deve ter alta deceit e baixa honesty; uma testemunha assustada deve ter baixa courage; um investigador determinado deve ter courage e aggressiveness moderadas.
4. Exemplos de fala:
   - Cada personagem deve ter 2 frases de exemplo (campo "examples") demonstrando seu estilo, vocabulário e temperamento.

A sua resposta deve ser EXCLUSIVAMENTE um objeto JSON válido, sem texto ou explicações antes ou depois.
"""


# ============================================================================
# 1) EXTRAÇÃO E VALIDAÇÃO DE JSON
# ============================================================================

def extract_json(text):
    """
    Extrai e decodifica um JSON a partir da resposta do modelo,
    mesmo que haja blocos ```json ... ``` ou texto ao redor.
    """
    text = text.strip()
    # Remove cercas de markdown
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Tenta recortar do primeiro '{' até o último '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        sub = text[start:end + 1]
        try:
            return json.loads(sub)
        except json.JSONDecodeError as err:
            raise ValueError(f"Não foi possível decodificar o JSON retornado: {err}\nTexto bruto:\n{text}")
    raise ValueError(f"Nenhum objeto JSON encontrado na resposta:\n{text}")


def validate_scene_data(data):
    """Garante que o dicionário possui os campos obrigatórios e a estrutura esperada."""
    if not isinstance(data, dict):
        raise ValueError("A raiz do JSON deve ser um objeto.")
    if "scene" not in data or not isinstance(data["scene"], str):
        raise ValueError("O campo 'scene' é obrigatório e deve ser uma string descritiva.")
    if "characters" not in data or not isinstance(data["characters"], list):
        raise ValueError("O campo 'characters' é obrigatório e deve ser uma lista.")

    characters = data["characters"]
    if len(characters) != 5:
        print(f"   [aviso] A cena tem {len(characters)} personagens (o padrão recomendado é 5).")

    roles = [c.get("role") for c in characters]
    if "guilty" not in roles:
        raise ValueError("A cena precisa ter pelo menos 1 personagem com role 'guilty'.")
    if "investigator" not in roles:
        raise ValueError("A cena precisa ter pelo menos 1 personagem com role 'investigator'.")

    for c in characters:
        name = c.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("Cada personagem deve ter um 'name' válido.")
        role = c.get("role")
        if role == "guilty":
            if not c.get("truth"):
                raise ValueError(f"O culpado ({name}) precisa ter o campo 'truth' preenchido.")
            if not c.get("alibi"):
                raise ValueError(f"O culpado ({name}) precisa ter o campo 'alibi' preenchido.")
        elif role == "investigator":
            if not c.get("goal"):
                c["goal"] = f"Descobrir quem cometeu o ato em: {data['scene']}"

        # Assegura que traços existem com valores numéricos limitados
        traits = c.setdefault("traits", {})
        for t in DEFAULT_TRAITS:
            val = traits.get(t, DEFAULT_TRAITS[t])
            try:
                traits[t] = clamp(float(val))
            except (ValueError, TypeError):
                traits[t] = DEFAULT_TRAITS[t]

        c.setdefault("examples", [])
        c.setdefault("description", f"Personagem {name}")


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

    data = extract_json(response_text)
    validate_scene_data(data)

    used = {c.get("name") for c in data.get("characters", [])}
    if not set(drawn_names) <= used:
        ignored = set(drawn_names) - used
        print(f"   [aviso] O modelo não usou todos os nomes sorteados (ignorou: "
              f"{', '.join(sorted(ignored))}). Seguindo com os nomes que ele escreveu.")

    return data


# ============================================================================
# 3) MATERIALIZAÇÃO DOS ATORES NO DISCO (.db)
# ============================================================================

def materialize_scene(scene_data, actors_folder="atores", scenario_folder="cenario", slots=2):
    """
    Transforma a cena JSON em arquivos .db SQLite reais na pasta de atores
    e salva os metadados cena.json na pasta do cenário.
    """
    validate_scene_data(scene_data)
    os.makedirs(actors_folder, exist_ok=True)
    os.makedirs(scenario_folder, exist_ok=True)

    characters_data = scene_data["characters"]
    guilty_name = next((c["name"] for c in characters_data if c["role"] == "guilty"), None)
    investigator_name = next((c["name"] for c in characters_data if c["role"] == "investigator"), None)

    print(f"\n=== Materializando cena ===")
    print(f"Cenário salvo em: '{scenario_folder}/cena.json'")
    print(f"Atores salvos em: '{actors_folder}/'")
    print(f"Cena: {scene_data['scene']}")
    print(f"Culpado: {guilty_name} | Investigador: {investigator_name}")

    meta_path = os.path.join(scenario_folder, "cena.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(scene_data, f, ensure_ascii=False, indent=2)
    print(f"Metadados do cenário gravados em: {meta_path}")

    # WorldState: a verdade objetiva do crime vira um EVENTO em world.db, não só uma string
    # solta no cena.json. Hoje só existe 1 local (o Roteirista ainda não gera múltiplos - ver
    # nota de escopo em world.py); por isso o evento é public=False e cada testemunha que
    # "viu" algo é ligada explicitamente como evidência, em vez de por percepção automática.
    world_path = os.path.join(scenario_folder, "world.db")
    world = open_world(world_path)
    register_location(world, "cena", description=scene_data["scene"], public=False)
    for c_info in characters_data:
        position(world, c_info["name"], "cena", role=c_info["role"])

    crime_event_id = None
    if guilty_name:
        guilty_info = next(c for c in characters_data if c["role"] == "guilty")
        crime_event_id, _ = register_event(
            world, "crime", actor=guilty_name, location="cena",
            data={"proposition": guilty_info["truth"]}, public=False)
    print(f"Mundo (verdade objetiva) gravado em: {world_path}")

    created_actors = {}
    for i, c_info in enumerate(characters_data):
        name = c_info["name"]
        role = c_info["role"]
        db_path = os.path.join(actors_folder, f"{name.lower()}.db")

        # Se já existia um .db com esse nome, remove para começar a cena limpa
        if os.path.exists(db_path):
            for suffix in ("", "-wal", "-shm"):
                file_ = db_path + suffix
                if os.path.exists(file_):
                    try:
                        os.remove(file_)
                    except OSError:
                        pass

        # 1) Cria o banco com a personalidade
        create_actor(
            db_path,
            name=name,
            description=c_info["description"],
            examples=c_info.get("examples", []),
            traits=c_info["traits"]
        )

        # 2) Abre o ator para gravar as memórias iniciais
        actor = Actor(db_path, slot=i % slots)

        if role == "guilty":
            truth = c_info["truth"]
            alibi = c_info["alibi"]
            # Sensibilidade alta (0.9): é o segredo do crime
            memory_id = actor.remember(truth, origin="system", sensitivity=0.9, shareable=1)
            actor.set_false_version(memory_id, alibi)
            # Objetivo estruturado (prioridade/risco altos): é o que libera a ação DEFLECT em
            # choose_action(), não só a personalidade dele - ver actor.py, seção 4.4.
            actor.form_goal("Não ser descoberto", priority=0.9, risk=0.9)
            print(f"   [Culpado] {name}: gravada a verdade (memória #{memory_id}) e o álibi pré-gerado.")

        elif role == "investigator":
            goal = c_info.get("goal")
            if goal:
                # O objetivo serve como memória inicial para guiar a atenção do investigador
                memory_id = actor.remember(f"Objetivo da investigação: {goal}",
                                           origin="system", sensitivity=0.2, shareable=1)
                actor.form_goal(goal, priority=0.9)
                print(f"   [Investigador] {name}: objetivo definido (memória #{memory_id}).")

        elif role == "witness":
            saw = c_info.get("saw")
            if saw:
                # Se viu algo, vincula ao culpado usando o campo 'about'
                memory_id = actor.remember(saw, origin="observation", sensitivity=0.6,
                                           shareable=1, about=guilty_name)
                print(f"   [Testemunha] {name}: gravado fato observado sobre {guilty_name} (memória #{memory_id}).")
                if crime_event_id is not None:
                    register_evidence(world, crime_event_id, saw, origin=name, subject=guilty_name)
            else:
                print(f"   [Testemunha] {name}: não presenciou nada relevante (sem memórias iniciais).")

        actor.db.close()
        created_actors[name.lower()] = db_path

    world.close()
    print(f"\nSucesso! {len(created_actors)} atores materializados prontos para a cena.")
    return created_actors


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
