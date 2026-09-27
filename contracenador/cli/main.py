"""
main.py - o terminal do projeto e o ORQUESTRADOR.

O orquestrador é o "carteiro": pega o envelope de fala de um Ator e entrega ao outro.
Os atores nunca abrem o arquivo .db um do outro, nunca veem o prompt um do outro e só
trocam o que está dentro do envelope. É isso que garante o isolamento.

Uso:
    python main.py

    Em um novo save, a ordem correta é: 14B (roteirista) gera a cena e só depois o 7B
    (atores) é iniciado. Quando /roteiro é chamado, o 7B é desligado antes do 14B e
    reativado somente após a cena estar materializada.

    Para personalizar os modelos ou portas use as flags abaixo:
    python main.py --modelo-atores Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M
                   --modelo-roteirista Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M
"""
import argparse
import json
import os
import random
import shutil
import sys

from .. import colors
from ..agents.agent import Actor, DEFAULT_TRAITS, clamp, create_actor, detect_subject, normalize
from ..llm.client import LLM
from ..llm.model_manager import ServerManager
from ..scenarios.generator import generate_scene_llm
from ..scenarios.loader import clear_scene_storage, load_scenario, materialize_scene
from ..simulation import (
    DEFAULT_PAIR_ROUNDS,
    ConversationLane,
    SimulationEngine,
    run_private_pair_round,
)
from ..simulation.config import RuntimeConfig
from ..world import (
    find_event_by_type,
    open_world,
    register_event,
    register_evidence,
)
from ..world.evidence import EVIDENCE_EFFECTS, delivered_evidence

DIALOGUE_ROUNDS = DEFAULT_PAIR_ROUNDS


def check_local_environment(actors_server, screenwriter_server, actors_folder, scenario_folder):
    """Valida requisitos locais sem iniciar modelos nem alterar os saves."""
    errors = []
    print("\n[ambiente] Verificando pré-requisitos locais...")

    if sys.version_info < (3, 8):
        errors.append(f"Python {sys.version.split()[0]} detectado; é necessário Python 3.8 ou superior.")
    else:
        print(f"[ambiente] Python {sys.version.split()[0]}: OK")

    executable = shutil.which("llama-server")
    if executable:
        print(f"[ambiente] llama-server: {executable}")
    else:
        errors.append(
            "llama-server não encontrado no PATH. Instale uma build do llama.cpp compatível "
            "e adicione a pasta do executável ao PATH."
        )

    shared_port = actors_server.port == screenwriter_server.port
    if shared_port:
        errors.append("Os servidores 7B e 14B não podem usar a mesma porta.")

    for label, server in (("Atores (7B)", actors_server), ("Roteirista (14B)", screenwriter_server)):
        try:
            server._validate_startup()
            model_source = "arquivo local" if server.model.strip().lower().endswith(".gguf") else "Hugging Face"
            port_status = f"porta {server.port}" if not shared_port else f"porta {server.port} (duplicada)"
            print(f"[ambiente] {label}: modelo {model_source}; {port_status}")
        except RuntimeError as error:
            errors.append(f"{label}: {error}")

        if server.gpu_layers == 0:
            print(f"[ambiente] {label}: execução em CPU solicitada")
        elif server.gpu_layers is None:
            print(f"[ambiente] {label}: offload automático de GPU (--fit on)")
        else:
            print(f"[ambiente] {label}: limite de {server.gpu_layers} camadas na GPU")

    actors_path = os.path.realpath(actors_folder)
    scenario_path = os.path.realpath(scenario_folder)
    try:
        common_path = os.path.commonpath((actors_path, scenario_path))
    except ValueError:
        common_path = None
    if common_path in (actors_path, scenario_path):
        errors.append("As pastas de atores e cenário devem ser diferentes e não podem conter uma à outra.")

    for label, folder in (("atores", actors_path), ("cenário", scenario_path)):
        if os.path.exists(folder) and not os.path.isdir(folder):
            errors.append(f"O caminho da pasta de {label} existe, mas não é uma pasta: '{folder}'.")
            continue
        writable_path = folder
        while not os.path.exists(writable_path):
            parent = os.path.dirname(writable_path)
            if parent == writable_path:
                break
            writable_path = parent
        if not os.access(writable_path, os.W_OK):
            errors.append(f"Sem permissão de escrita para criar a pasta de {label}: '{folder}'.")
        else:
            print(f"[ambiente] Pasta de {label}: gravável ({folder})")

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise RuntimeError(f"Pré-verificação falhou; nenhum save foi apagado.\n{details}")

    print("[ambiente] Pré-verificação concluída. Modelos do Hugging Face podem precisar ser baixados na primeira execução.")


HELP = """
Comandos (qualquer outro texto é uma mensagem para o ator atual):
  /cenario                    mostra o incidente e os personagens do cenário atual
    /cena [rodadas]             interroga um suspeito e pode intercalar uma conversa privada de influência do culpado
  /roteiro [tema]             cria uma nova cena com 5 atores usando a IA (LLM)
  /atores                     lista os atores carregados
  /falar <nome>               troca o ator com quem você conversa
  /lembrar <texto>            ensina um fato ao ator atual
  /memorias                   mostra o que o ator atual sabe
  /falsa <id> <texto>         escreve à mão a versão falsa da memória <id> (a que ele conta ao mentir)
  /sobre <id> <nome>          diz de quem é a memória <id> (ex.: /sobre 3 Marcos); sem nome remove
  /estado                     mostra traços, emoções e relações do ator atual
  /painel [outro]             mostra o painel de debug do ator atual (emoções, traços, probabilidades)
  /debug                      liga/desliga o painel de debug automático após cada resposta
  /tracos <traco> <0 a 1>     muda um traço do ator atual (ex.: /tracos honesty 0.2)
  /novo <nome>                cria um ator novo avulso (um arquivo .db novo)
  /conversar <A> <B> <tópico> faz A e B conversarem entre si sobre o tópico (1 para 1)
  /turnos <N>                 quantas falas tem a conversa entre 2 atores (padrão 4)
  /ajuda   /sair
"""


# ============================================================================
# 1) ATORES E CENÁRIO: carregar do disco
# ============================================================================

def configure_actors(actors, config):
    for actor in actors.values():
        actor.llm_temperature = config.actor_temperature
        actor.llm_max_tokens = config.actor_max_tokens
    return actors


def load_actors(folder, slots, config=None):
    """Cada arquivo .db da pasta é um Ator. Cada um recebe um slot fixo do servidor
    (os slots se repetem se houver mais atores que slots)."""
    actors = {}
    if not os.path.exists(folder):
        return actors
    for i, file_ in enumerate(sorted(f for f in os.listdir(folder) if f.endswith(".db"))):
        actor = Actor(os.path.join(folder, file_), slot=i % slots)
        actors[actor.name.lower()] = actor
    return configure_actors(actors, config) if config is not None else actors


def _start_fresh_game(actors_server, screenwriter_server, actors_folder, scenario_folder, slots,
                      screenwriter_max_tokens=1800, screenwriter_temperature=0.7,
                      initial_theme=None):
    clear_scene_storage(actors_folder, scenario_folder)

    print("\n[Novo save] Pastas de atores e cenário limpas.")
    if actors_server.is_running():
        print("[roteiro] Encerrando servidor dos atores antes do Roteirista (14B)...")
        actors_server.stop()
    print("[roteiro] Carregando primeiro o modelo do Roteirista (14B)...")
    screenwriter_server.start()
    try:
        theme = (initial_theme or "").strip()
        while not theme:
            theme = input("Digite o tema ou incidente da nova cena:\n> ").strip()
            if not theme:
                print("Por favor, digite um tema para a IA criar o mistério.")

        screenwriter_llm = LLM(screenwriter_server.url())
        data = generate_scene_llm(
            screenwriter_llm, theme,
            max_tokens=screenwriter_max_tokens,
            temperature=screenwriter_temperature,
        )
        materialize_scene(data, actors_folder=actors_folder,
                          scenario_folder=scenario_folder, slots=slots)
    finally:
        screenwriter_server.stop()

    print("\n[roteiro] Cena salva. Carregando o modelo dos atores (7B)...")
    actors_server.start()
    return LLM(actors_server.url())


# ============================================================================
# 2) COMANDOS
# ============================================================================

def ask_number(question, default):
    """Pergunta um número de 0 a 1 (Enter = valor padrão)."""
    while True:
        answer = input(question).strip().replace(",", ".")
        if not answer:
            return default
        try:
            value = float(answer)
            if 0 <= value <= 1:
                return value
        except ValueError:
            pass
        print("   Digite um número entre 0 e 1 (ou Enter para o padrão).")


def cmd_scenario(scenario_folder):
    data = load_scenario(scenario_folder)
    if not data:
        print(f"   Nenhum cenário salvo em '{scenario_folder}/cena.json'. Use /roteiro para criar um.")
        return
    print("\n=== Cenário Atual ===")
    print(f"Incidente: {data.get('scene', 'Sem descrição')}")
    characters = data.get("characters", [])
    investigator = next((c for c in characters if c.get("role") == "investigator"), None)
    if investigator:
        print(f"Investigador(a): {investigator['name']} (Objetivo: {investigator.get('goal', 'Descobrir a verdade')})")
    character_names = ", ".join(c["name"] for c in characters)
    print(f"Personagens na cena: {character_names}")
    print(f"(Configurações completas salvas em '{scenario_folder}/cena.json')")


def cmd_screenplay(actors_server, screenwriter_server, rest, actors_folder, scenario_folder,
                  slots, current_actors, config=None):
    """
    Gera uma nova cena com o Roteirista (14B, 100% via LLM) e recarrega os atores (7B).
    Fluxo com RAM apertada:
      1) Derruba o servidor 7B (atores)
      2) Sobe o servidor 14B (roteirista)
      3) Gera a cena
      4) Derruba o 14B
      5) Sobe novamente o 7B
    """
    theme = rest.strip()
    if not theme:
        print("\n=== Novo Roteiro com IA (modelo 14B) ===")
        theme = input("Digite o tema ou incidente da cena a ser criada pelo modelo:\n> ").strip()
        if not theme:
            print("Operação cancelada: informe um tema para gerar a cena.")
            return current_actors

    for actor in current_actors.values():
        actor.db.close()

    try:
        # Troca de modelos
        print("\n[roteiro] Carregando modelo do Roteirista (14B)...")
        if actors_server.is_running():
            actors_server.stop()
        screenwriter_server.start()

        screenwriter_llm = LLM(screenwriter_server.url())
        data = generate_scene_llm(
            screenwriter_llm, theme,
            max_tokens=config.screenwriter_max_tokens if config else 1800,
            temperature=config.screenwriter_temperature if config else 0.7,
        )
        materialize_scene(data, actors_folder=actors_folder, scenario_folder=scenario_folder, slots=slots)

        print("\n[roteiro] Voltando ao modelo dos atores (7B)...")
        screenwriter_server.stop()
        actors_server.start()

        new_actors = load_actors(actors_folder, slots, config=config)
        print(f"\nCena pronta! Atores carregados: {', '.join(a.name for a in new_actors.values())}")
        return new_actors
    except Exception as e:
        print(f"\n[Erro ao gerar roteiro via LLM] {e}")
        # Garante que o servidor dos atores volte mesmo com erro
        if not actors_server.is_running():
            try:
                actors_server.start()
            except Exception as e2:
                print(f"[Erro ao reativar servidor de atores] {e2}")
        return load_actors(actors_folder, slots, config=config)


def cmd_remember(actor, text, llm, actors):
    """Salvar é explícito (/lembrar): confiável e não gasta CPU com o modelo."""
    if not text:
        print("Uso: /lembrar <texto>")
        return
    sensitivity = ask_number("   Sensibilidade, de 0 (qualquer um pode saber) a 1 (segredo) [0.3]: ", 0.3)
    shareable = input("   Pode ser contado a outros atores? (s/n) [s]: ").strip().lower() != "n"

    # RECONHECIMENTO: se o texto menciona outro ator pelo nome, sugere marcar essa
    # memória como sendo sobre ele (ver knows_about em actor.py).
    others = [ag.name for ag in actors.values() if ag is not actor]
    suggestion = detect_subject(text, others)
    question = (f"   É sobre outro ator? [Enter = {suggestion}, 'n' = nenhum, ou digite outro nome]: "
                if suggestion else "   É sobre outro ator? (nome, ou Enter para nenhum): ")
    answer = input(question).strip()
    if answer.lower() in ("n", "nao", "não"):
        about = None
    elif answer:
        about = answer.capitalize()
    else:
        about = suggestion

    memory_id = actor.remember(text, sensitivity=sensitivity, shareable=int(shareable), about=about)
    print(f"   Salvo (memória #{memory_id})." + (f" Sobre: {about}." if about else ""))

    target = actors.get(about.lower()) if about else None
    if target is not None and target is not actor:
        if input(f"   A/O {target.name} também sabe disso, porque estava lá? (s/n) [n]: ").strip().lower() == "s":
            target.remember(text, sensitivity=sensitivity, shareable=int(shareable))
            print(f"   Também salvo para {target.name}.")

    if shareable and sensitivity >= 0.5:
        print("   Gerando uma versão falsa para o caso de ele mentir...")
        false_version = actor.generate_false_version(memory_id, llm)
        if false_version:
            print(f"   Versão falsa: {false_version}")
        else:
            print("   Não consegui gerar uma versão falsa: sem ela o ator só consegue ESCONDER "
                  "esse fato, não mentir. Escreva uma com /falsa.")


def cmd_memories(actor):
    memories = actor.list_memories()
    if not memories:
        print("   (sem memórias)")
    for m in memories:
        private = "" if m["shareable"] else " | PRIVADA"
        about = f" | sobre: {m['about']}" if m["about"] else ""
        false_version = f"\n      versão falsa: {m['false_version']}" if m["false_version"] else ""
        print(f"   #{m['id']} [sens {m['sensitivity']:.1f} | origem {m['origin']}{private}{about}] "
              f"{m['text']}{false_version}")


def cmd_false(actor, rest):
    id_text = rest.split(maxsplit=1)
    if len(id_text) == 2 and id_text[0].isdigit() and actor.set_false_version(int(id_text[0]), id_text[1]):
        print("   Versão falsa gravada.")
    else:
        print("Uso: /falsa <id da memória> <texto da versão falsa>  (veja os ids em /memorias)")


def cmd_about(actor, rest):
    parts = rest.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        print("Uso: /sobre <id da memória> <nome>  (sem nome remove; veja os ids em /memorias)")
        return
    memory_id = int(parts[0])
    name = parts[1].strip() if len(parts) > 1 else None
    if not actor.set_about(memory_id, name):
        print("   Memória não encontrada (veja os ids em /memorias).")
    elif name:
        print(f"   Memória #{memory_id} agora é sobre: {name}.")
    else:
        print(f"   Assunto removido da memória #{memory_id}.")


def cmd_traits(actor, rest):
    parts = rest.split()
    if len(parts) == 2 and parts[0] in DEFAULT_TRAITS:
        try:
            actor.personality["traits"][parts[0]] = clamp(float(parts[1].replace(",", ".")))
            actor.save_personality()
            print(actor.summary())
            return
        except ValueError:
            pass
    print("Uso: /tracos <" + " | ".join(DEFAULT_TRAITS) + "> <0 a 1>")


def cmd_new(folder, name, actors, slots):
    if not name.isalnum() or name.lower() in actors:
        print("Use um nome novo, só com letras e números.")
        return
    description = input("   Como ele(a) fala e é? (uma frase): ").strip()
    example = input("   Uma frase de exemplo de como ele(a) fala: ").strip()
    traits = {t: ask_number(f"   {t} (0 a 1) [0.5]: ", 0.5) for t in DEFAULT_TRAITS}
    path = os.path.join(folder, f"{name.lower()}.db")
    create_actor(path, name.capitalize(), description, [example] if example else [], traits)
    actors[name.lower()] = Actor(path, slot=len(actors) % slots)
    print(f"   Ator {name.capitalize()} criado em {path}.")


def _consume_actor_name(text, actors):
    """
    Tenta casar, no INÍCIO de `text`, o nome de um ator conhecido - funciona tanto com nomes
    de uma palavra ('Bia') quanto de várias ('Ana Carvalho', como o Roteirista sempre gera).
    Testa do nome mais longo pro mais curto, pra 'Ana Carvalho' não parar em 'Ana' por engano.
    Devolve (ator, resto do texto) ou (None, texto) se nada bateu.
    """
    text = text.strip()
    target = normalize(text)
    for actor in sorted(actors.values(), key=lambda a: -len(a.name)):
        prefix = normalize(actor.name)
        if target == prefix or target.startswith(prefix + " "):
            return actor, text[len(actor.name):].strip()
    return None, text


def cmd_talk(actors, rest, turns, llm):
    """Faz dois atores conversarem. O orquestrador só leva o envelope de um para o outro."""
    a, rest = _consume_actor_name(rest, actors)
    b, rest = _consume_actor_name(rest, actors) if a else (None, rest)
    topic = rest.strip()

    if a is None or b is None or not topic:
        print("Uso: /conversar <ator1> <ator2> <tópico>  (nomes com espaço são aceitos, ex.: Ana Carvalho)")
        return
    if a is b:
        print("Escolha dois atores diferentes (veja /atores).")
        return

    a.new_conversation(b.name)
    b.new_conversation(a.name)
    print(colors.heading(f'\n=== {a.name} e {b.name} conversam sobre "{topic}" ({turns} falas) ==='))

    speaker, listener = a, b
    envelope = speaker.open_conversation(listener.name, topic, llm)      # fala 1
    for turn in range(2, turns + 1):
        speaker, listener = listener, speaker                            # troca a vez
        envelope = speaker.respond(envelope, topic, llm, last=(turn == turns))
    listener.receive(envelope)  # quem ouviu a última fala também precisa processá-la

    print("\n=== Como ficaram os atores ===")
    print(a.summary())
    print(b.summary())


def _run_private_dialogue(investigator, target, topic, llm, on_exchange, rounds=DIALOGUE_ROUNDS):
    """Alterna falas somente entre investigador e alvo; cada rodada contém uma resposta do alvo."""
    result = run_private_pair_round(investigator, target, topic, llm, on_exchange, rounds=rounds)
    return result["rounds"], result["stopped"]


def _suspect_candidates(suspects, interrogated):
    candidates = [suspect.name for suspect in suspects if suspect.name not in interrogated]
    if not candidates:
        interrogated.clear()
        candidates = [suspect.name for suspect in suspects]
    return candidates


def _record_revelation_evidence(world, envelope, character_names, recorded_revelations,
                               direct_reliability=0.7, relayed_reliability=0.35):
    """Registra fatos declarados em eventos privados e evidências com sua proveniência."""
    evidence_ids = []
    speaker = envelope["from"]
    for fact in envelope.get("facts", []):
        text = fact.get("text", "").strip()
        if not text:
            continue
        effect = fact.get("effect", "neutral")
        if effect not in EVIDENCE_EFFECTS:
            effect = "neutral"
        key = (speaker, envelope.get("target"), fact.get("source_id"), text, effect)
        if key in recorded_revelations:
            continue

        subject = fact.get("subject")
        if subject not in character_names:
            subject = detect_subject(text, character_names)
        if subject is None and fact.get("origin") == "system":
            subject = speaker

        default_reliability = (
            direct_reliability if fact.get("origin") in {"system", "observation", "user"}
            else relayed_reliability
        )
        reliability = fact.get("reliability", default_reliability)
        event_id, _ = register_event(
            world, "revelation", actor=speaker, target=envelope.get("target"),
            location="cena", data={"proposition": text}, public=False,
        )
        evidence_ids.append(register_evidence(
            world, event_id, text, origin=speaker, subject=subject,
            type_="testimony", reliability=reliability, effect=effect,
        ))
        recorded_revelations.add(key)
    return evidence_ids


def _update_beliefs_from_evidence(investigator, world, evidence_ids, processed_evidence, delta=0.2):
    """Usa só os relatos entregues; mencionar alguém não implica acusá-lo."""
    for evidence_id in evidence_ids:
        evidence = delivered_evidence(world, evidence_id, investigator.name)
        if evidence is None:
            continue
        # Referência estável também evita reforço repetido entre chamadas de /cena.
        key = "testimony:" + json.dumps(
            [evidence["origin"], evidence["subject"], evidence["content"], evidence["effect"]],
            ensure_ascii=False,
        )
        direction = EVIDENCE_EFFECTS.get(evidence["effect"], 0)
        if (key in processed_evidence or not direction or not evidence["subject"]
            or evidence["subject"] == investigator.name
            or evidence["type"] != "testimony"):
            continue
        processed_evidence.add(key)
        reliability = clamp(float(evidence.get("reliability", 0.7)))
        investigator.update_belief(
            f"{evidence['subject']} é o culpado", delta=direction * delta * reliability,
            origin=evidence["origin"], subject=evidence["subject"], evidence=key,
        )


def cmd_scene(actors, rest, scenario_folder, llm, config=None):
    """
    Orquestra a dinâmica da 'Sala' (Contracenador com 5 atores), do início ao fim, sem pausa
    interativa a cada rodada - o usuário só acompanha:
        - Cada interrogatório é privado: só investigador e alvo recebem os envelopes.
        - O culpado pode conversar em paralelo com uma testemunha para plantar uma suspeita.
        - Cada alvo conversa por até 10 trocas, depois o investigador escolhe outro suspeito.
        - O teto padrão é de 10 interrogatórios para descobrir o culpado.
    - Fim dinâmico: a investigação vence se a 'verdade' entrar na memória do investigador (ou
      se a crença "<suspeito> é o culpado" passar de 75% de confiança - vitória por dedução);
      o culpado vence por exaustão se atingir o teto de rodadas (padrão 15).
    """
    scenario_data = load_scenario(scenario_folder)
    if not scenario_data:
        print(f"Nenhum cenário encontrado em '{scenario_folder}/cena.json'.")
        print("Crie um cenário primeiro usando: /roteiro")
        return

    characters_data = scenario_data.get("characters", [])
    investigator_info = next((c for c in characters_data if c.get("role") == "investigator"), None)
    guilty_info = next((c for c in characters_data if c.get("role") == "guilty"), None)

    if not investigator_info or not guilty_info:
        print("Erro: O cenário precisa ter pelo menos 1 investigador e 1 culpado.")
        return

    investigator = actors.get(investigator_info["name"].lower())
    guilty = actors.get(guilty_info["name"].lower())

    if not investigator or not guilty:
        print("Erro: Os atores do cenário não estão todos carregados. Use /roteiro para recarregar.")
        return

    suspects = [actor for actor in actors.values() if actor is not investigator]
    witnesses = [
        actors[character["name"].lower()]
        for character in characters_data
        if character.get("role") == "witness" and character["name"].lower() in actors
    ]

    # A verdade vem do WorldState (world.db), não mais direto do cena.json: é o evento
    # 'crime' gravado por materialize_scene(). Se por algum motivo o mundo não existir
    # (cena antiga, gerada antes do WorldState), cai de volta no campo do cena.json.
    world_path = os.path.join(scenario_folder, "world.db")
    world = open_world(world_path)
    crime_event = find_event_by_type(world, "crime")
    if crime_event:
        exact_truth = (crime_event["data"].get("proposition") or "").strip()
    else:
        exact_truth = (guilty_info.get("truth") or "").strip()

    topic = investigator_info.get("goal") or scenario_data.get("scene", "O mistério")

    max_rounds = config.investigation_rounds if config else 10
    if rest.strip().isdigit():
        max_rounds = max(1, int(rest.strip()))
    dialogue_rounds = config.dialogue_rounds if config else DIALOGUE_ROUNDS
    influence_enabled = config.influence_enabled if config else True
    influence_weight = config.influence_weight if config else 0.25
    simultaneous_conversations = config.simultaneous_conversations if config else 2
    deduction_threshold = config.deduction_threshold if config else 0.75
    evidence_delta = config.evidence_delta if config else 0.2
    direct_reliability = config.direct_reliability if config else 0.7
    relayed_reliability = config.relayed_reliability if config else 0.35

    print(colors.heading(f"\n{'=' * 65}"))
    print(colors.heading("[CENA] A SALA DE INVESTIGAÇÃO"))
    print(f"Incidente: \"{scenario_data.get('scene')}\"")
    print(f"Investigador(a): {investigator.name} | Objetivo: {topic}")
    print(f"Elenco: {', '.join(a.name for a in actors.values())}")
    print(f"Teto de interrogatórios: {max_rounds} | Diálogo por linha: {dialogue_rounds} trocas")
    print(colors.heading(f"{'=' * 65}"))

    # Inicializa o contexto de conversa entre o investigador e os outros
    for s in suspects:
        investigator.new_conversation(s.name)
        s.new_conversation(investigator.name)
    for witness in witnesses:
        guilty.new_conversation(witness.name)
        witness.new_conversation(guilty.name)

    # Registro de presença: todo mundo na sala "conhece" (relação neutra) todo mundo, mesmo
    # antes de conversarem - é o que permite ao culpado escolher um bode expiatório em DEFLECT
    # (Actor.choose_action só considera quem já tem relação registrada).
    present = list(actors.values())
    for a in present:
        for b in present:
            if a is not b:
                a.relationship(b.name)

    victory = False
    interrogated = set()
    processed_evidence = set()
    recorded_revelations = set()
    influence_counts = {}

    def select_investigation_pair(available_agents, investigation_round, history):
        print(colors.heading(f"\n--- [Rodada de investigação {investigation_round}/{max_rounds}] ---"))
        print(colors.dim(f"Bloco ativo: {dialogue_rounds} trocas privadas antes de seguir."))

        candidate_names = _suspect_candidates(suspects, interrogated)
        if not candidate_names:
            print(colors.dim("Não há mais suspeitos disponíveis; reiniciando a lista para continuar."))
            interrogated.clear()
            candidate_names = _suspect_candidates(suspects, interrogated)

        target_name = investigator.choose_investigation_target(
            candidate_names, topic, llm,
            temperature=config.investigator_temperature if config else 0.3,
            max_tokens=config.investigator_max_tokens if config else 20,
            fatigue_weight=config.interrogation_fatigue if config else 0.12,
        )
        target = actors[target_name.lower()]
        interrogated.add(target.name)
        print(colors.dim(f"-> {investigator.name} decide focar em {target.name}."))
        print(colors.dim(f"\n[Interrogatório privado] {investigator.name} conversa com {target.name} por {dialogue_rounds} rounds."))
        lanes = [ConversationLane(investigator, target, topic)]

        available_influence_targets = (
            [witness for witness in witnesses if witness is not target]
            if influence_enabled and simultaneous_conversations > 1 and target is not guilty else []
        )
        if available_influence_targets:
            influence_target = min(
                available_influence_targets,
                key=lambda witness: influence_counts.get(witness.name, 0),
            )
            false_subjects = [
                witness for witness in witnesses
                if witness is not target and witness is not influence_target
            ]
            if false_subjects:
                false_subject = random.choice(false_subjects)
                influence_counts[influence_target.name] = influence_counts.get(influence_target.name, 0) + 1

                def open_influence(initiator, respondent, lane_topic, lane_llm):
                    return initiator.open_influence_conversation(
                        respondent.name, lane_topic, false_subject.name, lane_llm,
                        weight=influence_weight,
                    )

                lanes.append(ConversationLane(
                    guilty, influence_target, topic, opener=open_influence,
                ))
                print(colors.dim(
                    f"[Diálogo privado paralelo] {guilty.name} tenta convencer "
                    f"{influence_target.name} de que {false_subject.name} está envolvido."
                ))
        return lanes

    def process_exchange(initiator, respondent, question_envelope, answer_envelope,
                         investigation_round, dialogue_round):
        nonlocal victory
        print(colors.dim(
            f"  [{initiator.name} -> {respondent.name}] diálogo "
            f"{dialogue_round}/{dialogue_rounds}"
        ))

        delivered_ids = []
        for env in (question_envelope, answer_envelope):
            if env.get("tactic") == "THREATEN":
                register_event(world, "threat", actor=env["from"], target=env.get("target"),
                               location="cena", data={"proposition": f'{env["from"]} ameaçou {env.get("target")}'} )
            for accusation in env.get("accusations", []):
                accusation_event, _ = register_event(
                    world, "accusation", actor=env["from"], target=env.get("target"),
                    location="cena", data={"proposition": accusation["proposition"]}, public=False,
                )
                register_evidence(
                    world, accusation_event, accusation["proposition"],
                    origin=env["from"], subject=accusation["subject"],
                    type_="accusation", reliability=accusation.get("weight", 0.15),
                )
            if env.get("facts"):
                delivered_ids.extend(_record_revelation_evidence(
                    world, env, [actor.name for actor in actors.values()], recorded_revelations,
                    direct_reliability=direct_reliability,
                    relayed_reliability=relayed_reliability,
                ))

        _update_beliefs_from_evidence(
            investigator, world, delivered_ids, processed_evidence, delta=evidence_delta,
        )

        if victory:
            return True

        guilty_belief = investigator.belief(f"{guilty.name} é o culpado")
        if guilty_belief and guilty_belief["confidence"] >= deduction_threshold:
            print(colors.victory(f"\n{'*' * 65}"))
            print(colors.victory(f"*** VITÓRIA DA INVESTIGAÇÃO POR DEDUÇÃO! (Rodada {investigation_round}) ***"))
            print(colors.victory(
                f"{investigator.name} tem {guilty_belief['confidence']:.0%} de certeza de que "
                f"{guilty.name} é o culpado, com base nas evidências reunidas."))
            print(colors.victory(f"{'*' * 65}"))
            victory = True
            return True

        revealed_truth = initiator is investigator and any(
            exact_truth == fact.get("text", "").strip()
            or exact_truth in fact.get("text", "")
            for fact in answer_envelope.get("facts", [])
        )
        investigator_memories = [m["text"].strip() for m in investigator.list_memories()]
        if revealed_truth or any(exact_truth == memory or exact_truth in memory
                                 for memory in investigator_memories):
            print(colors.victory(f"\n{'*' * 65}"))
            print(colors.victory(f"*** VITÓRIA DA INVESTIGAÇÃO! (Rodada {investigation_round}) ***"))
            print(colors.victory(f"{investigator.name} conseguiu a confissão da verdade:"))
            print(colors.victory(f"\"{exact_truth}\""))
            print(colors.victory(f"{'*' * 65}"))
            victory = True
            return True
        return False

    engine = SimulationEngine(actors.values(), select_investigation_pair)
    try:
        turn_history = engine.run(
            topic,
            llm,
            max_turns=max_rounds,
            rounds_per_pair=dialogue_rounds,
            on_exchange=process_exchange,
        )
    finally:
        world.close()

    if not victory and len(turn_history) == max_rounds:
        print(colors.defeat(f"\n{'*' * 65}"))
        print(colors.defeat(f"*** VITÓRIA DO CULPADO POR EXAUSTÃO! ***"))
        print(colors.defeat(f"{guilty.name} conseguiu despistar {investigator.name} após {max_rounds} rodadas de investigação."))
        print(colors.defeat(f"A verdade que ficou oculta foi:"))
        print(colors.defeat(f"\"{exact_truth}\""))
        print(colors.defeat(f"{'*' * 65}"))

    # Fecha os objetivos (roadmap, seção 17): dá desfecho explícito, não deixa "ativo" pra sempre.
    investigator.update_goal(topic, status="done" if victory else "failed")
    guilty.update_goal("Não ser descoberto", status="failed" if victory else "done")

    print("\n=== Resumo final dos personagens ===")
    print(investigator.summary())
    print()
    print(guilty.summary())


# ============================================================================
# 3) PROGRAMA PRINCIPAL
# ============================================================================

def build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Atores de IA isolados com Roteirista (dois modelos, gerenciados automaticamente)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config-json", help="Arquivo JSON com parâmetros de experimento")
    parser.add_argument(
        "--modelo-atores",
        dest="model_actors",
        metavar="MODELO",
        help=(
            "Modelo para os atores (7B).\n"
            "  Hugging Face: Org/Repo:arquivo.gguf  (baixa automaticamente)\n"
            "  Local:        /caminho/modelo.gguf\n"
            f"  Padrão: {ServerManager.ACTORS_MODEL}"
        ),
    )
    parser.add_argument(
        "--modelo-roteirista",
        dest="model_screenwriter",
        metavar="MODELO",
        help=(
            "Modelo para o Roteirista (14B).\n"
            f"  Padrão: {ServerManager.SCREENWRITER_MODEL}"
        ),
    )
    parser.add_argument("--porta-atores", dest="port_actors", type=int, help="Porta do servidor 7B")
    parser.add_argument("--porta-roteirista", dest="port_screenwriter", type=int,
                        help="Porta do servidor 14B")
    parser.add_argument("--slots", dest="slots_actors", type=int,
                        help="Slots KV do servidor dos atores")
    parser.add_argument("--slots-roteirista", dest="slots_screenwriter", type=int,
                        help="Slots KV do servidor do roteirista")
    parser.add_argument("--threads", type=int, help="Threads de CPU de ambos os servidores")
    parser.add_argument("--contexto-atores", dest="context_actors", type=int,
                        help="Tamanho do contexto do modelo dos atores")
    parser.add_argument("--contexto-roteirista", dest="context_screenwriter", type=int,
                        help="Tamanho do contexto do modelo do roteirista")
    parser.add_argument("--camadas-gpu-atores", dest="gpu_layers_actors", type=int,
                        help="Limite manual de camadas dos atores na GPU (padrão: ajuste automático à VRAM)")
    parser.add_argument("--camadas-gpu-roteirista", dest="gpu_layers_screenwriter", type=int,
                        help="Limite manual de camadas do roteirista na GPU (padrão: ajuste automático à VRAM)")
    embeddings_group = parser.add_mutually_exclusive_group()
    embeddings_group.add_argument("--embeddings", dest="embeddings", action="store_true",
                                  help="Ativa embeddings no servidor dos atores")
    embeddings_group.add_argument("--sem-embeddings", dest="embeddings", action="store_false",
                                  help="Desativa embeddings no servidor dos atores")

    parser.add_argument("--temperatura-atores", dest="actor_temperature", type=float,
                        help="Temperatura das falas dos atores (0 a 2)")
    parser.add_argument("--tokens-atores", dest="actor_max_tokens", type=int,
                        help="Limite de tokens por fala do ator")
    parser.add_argument("--temperatura-investigador", dest="investigator_temperature", type=float,
                        help="Temperatura ao selecionar o suspeito (0 a 2)")
    parser.add_argument("--tokens-investigador", dest="investigator_max_tokens", type=int,
                        help="Limite de tokens ao selecionar o suspeito")
    parser.add_argument("--temperatura-roteirista", dest="screenwriter_temperature", type=float,
                        help="Temperatura de geração da cena (0 a 2)")
    parser.add_argument("--tokens-roteirista", dest="screenwriter_max_tokens", type=int,
                        help="Limite de tokens da geração da cena")

    parser.add_argument("--rodadas-investigacao", dest="investigation_rounds", type=int,
                        help="Número máximo de interrogatórios")
    parser.add_argument("--rodadas-dialogo", dest="dialogue_rounds", type=int,
                        help="Trocas de fala em cada conversa privada")
    parser.add_argument("--turnos-conversa", dest="conversation_turns", type=int,
                        help="Número padrão de falas do comando /conversar")
    parser.add_argument("--conversas-simultaneas", dest="simultaneous_conversations", type=int,
                        choices=(1, 2), help="Linhas privadas ativas por turno: 1 ou 2")
    influence_group = parser.add_mutually_exclusive_group()
    influence_group.add_argument("--influencia-culpado", dest="influence_enabled", action="store_true",
                                 help="Permite ao culpado plantar suspeitas (padrão)")
    influence_group.add_argument("--sem-influencia", dest="influence_enabled", action="store_false",
                                 help="Desativa a conversa de influência do culpado")
    parser.add_argument("--peso-influencia", dest="influence_weight", type=float,
                        help="Peso da suspeita plantada pelo culpado (0 a 1)")
    parser.add_argument("--delta-evidencia", dest="evidence_delta", type=float,
                        help="Reforço base por evidência antes da confiabilidade (0 a 1)")
    parser.add_argument("--confiabilidade-direta", dest="direct_reliability", type=float,
                        help="Confiabilidade padrão para fatos de origem direta (0 a 1)")
    parser.add_argument("--confiabilidade-repassada", dest="relayed_reliability", type=float,
                        help="Confiabilidade padrão para fatos repassados (0 a 1)")
    parser.add_argument("--limiar-deducao", dest="deduction_threshold", type=float,
                        help="Confiança necessária para vitória por dedução (0 a 1)")
    parser.add_argument("--fadiga-interrogatorio", dest="interrogation_fatigue", type=float,
                        help="Penalidade por suspeito já interrogado")

    parser.add_argument("--pasta", dest="actors_folder", help="Pasta dos arquivos .db dos atores")
    parser.add_argument("--cenario", dest="scenario_folder", help="Pasta de cena.json e world.db")
    parser.add_argument("--tema", dest="theme",
                        help="Tema inicial; sem esta opção o programa pergunta no terminal")
    parser.add_argument("--semente", dest="seed", type=int,
                        help="Fixa decisões aleatórias para testes reproduzíveis")
    parser.add_argument("--verificar-ambiente", dest="verify_environment", action="store_true",
                        help="Verifica requisitos sem iniciar modelos nem alterar saves")
    parser.add_argument("--mostrar-config", dest="show_config", action="store_true",
                        help="Mostra configuração efetiva sem iniciar modelos ou alterar saves")
    parser.set_defaults(
        embeddings=None, influence_enabled=None, verify_environment=None, show_config=None,
    )
    return parser


def parse_runtime_config(argv=None):
    parser = build_argument_parser()
    parsed = vars(parser.parse_args(argv))
    config_path = parsed.pop("config_json")
    defaults = RuntimeConfig(
        model_actors=ServerManager.ACTORS_MODEL,
        model_screenwriter=ServerManager.SCREENWRITER_MODEL,
    )
    settings = defaults.__dict__.copy()

    if config_path:
        try:
            with open(config_path, "r", encoding="utf-8") as config_file:
                file_settings = json.load(config_file)
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"Não foi possível carregar --config-json: {error}")
        if not isinstance(file_settings, dict):
            parser.error("--config-json precisa conter um objeto JSON")
        unknown = sorted(set(file_settings) - set(settings))
        if unknown:
            parser.error("Chaves desconhecidas em --config-json: " + ", ".join(unknown))
        settings.update(file_settings)

    settings.update({key: value for key, value in parsed.items() if value is not None})
    try:
        return RuntimeConfig(**settings).validate()
    except (TypeError, ValueError, AttributeError) as error:
        parser.error(f"Configuração inválida: {error}")


def main():
    config = parse_runtime_config()

    if config.show_config:
        print(json.dumps(config.__dict__, ensure_ascii=False, indent=2))
        return

    if config.seed is not None:
        random.seed(config.seed)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Cria os dois gerenciadores (ainda não sobem o processo agora)
    actors_server = ServerManager(
        model=config.model_actors,
        port=config.port_actors,
        slots=config.slots_actors,
        context=config.context_actors,
        threads=config.threads,
        gpu_layers=config.gpu_layers_actors,
        embedding=config.embeddings,
    )
    screenwriter_server = ServerManager(
        model=config.model_screenwriter,
        port=config.port_screenwriter,
        slots=config.slots_screenwriter,
        context=config.context_screenwriter,
        threads=config.threads,
        gpu_layers=config.gpu_layers_screenwriter,
    )

    try:
        check_local_environment(
            actors_server, screenwriter_server, config.actors_folder, config.scenario_folder,
        )
    except RuntimeError as error:
        print(f"\n[ambiente] {error}")
        raise SystemExit(1) from error

    if config.verify_environment:
        print("[ambiente] Verificação concluída; nenhum modelo foi iniciado e nenhum save foi alterado.")
        return

    try:
        try:
            llm = _start_fresh_game(
                actors_server, screenwriter_server,
                config.actors_folder, config.scenario_folder, config.slots_actors,
                screenwriter_max_tokens=config.screenwriter_max_tokens,
                screenwriter_temperature=config.screenwriter_temperature,
                initial_theme=config.theme,
            )
        except Exception as error:
            print(f"\n[Erro ao iniciar novo save] {error}")
            raise SystemExit(1) from error

        actors = load_actors(config.actors_folder, config.slots_actors, config=config)
        current = next(iter(actors.values()))
        turns = config.conversation_turns
        debug_on = False
        print(HELP)

        while True:
            try:
                line = input(f"\n[{current.name}] você> ").strip()
                if not line:
                    continue
                if not line.startswith("/"):
                    current.speak_with_user(line, llm)
                    continue

                command, _, rest = line.partition(" ")
                rest = rest.strip()
                command = command.lower()

                if command == "/sair":
                    break
                elif command == "/ajuda":
                    print(HELP)
                elif command in ("/atores", "/agentes"):
                    for name, ag in actors.items():
                        print(f"   {'*' if ag is current else ' '} {ag.name} (slot {ag.slot})")
                elif command == "/cenario":
                    cmd_scenario(config.scenario_folder)
                elif command in ("/cena", "/sala"):
                    cmd_scene(actors, rest, config.scenario_folder, llm, config=config)
                elif command in ("/roteiro", "/roteirista"):
                    actors = cmd_screenplay(
                        actors_server, screenwriter_server,
                        rest, config.actors_folder, config.scenario_folder,
                        config.slots_actors, actors, config=config,
                    )
                    # Após /roteiro o 7B voltou: recria o cliente LLM apontando para ele
                    llm = LLM(actors_server.url())
                    current = next(iter(actors.values()))
                    for ag in actors.values():
                        ag.debug = debug_on
                elif command == "/falar":
                    if rest.lower() in actors:
                        current = actors[rest.lower()]
                    else:
                        print("Ator não encontrado (veja /atores).")
                elif command == "/lembrar":
                    cmd_remember(current, rest, llm, actors)
                elif command == "/memorias":
                    cmd_memories(current)
                elif command == "/falsa":
                    cmd_false(current, rest)
                elif command == "/sobre":
                    cmd_about(current, rest)
                elif command == "/estado":
                    print(current.summary())
                elif command == "/painel":
                    other = rest.strip() or None
                    if other and other.lower() not in actors:
                        other = None
                    print(current.panel(other))
                elif command == "/debug":
                    debug_on = not debug_on
                    for ag in actors.values():
                        ag.debug = debug_on
                    print(f"   Modo debug: {'ligado' if debug_on else 'desligado'}.")
                elif command == "/tracos":
                    cmd_traits(current, rest)
                elif command == "/novo":
                    cmd_new(config.actors_folder, rest, actors, config.slots_actors)
                    for ag in actors.values():
                        ag.debug = debug_on
                        ag.llm_temperature = config.actor_temperature
                        ag.llm_max_tokens = config.actor_max_tokens
                elif command == "/turnos":
                    turns = max(1, int(rest)) if rest.isdigit() else turns
                    print(f"   Conversas entre atores terão {turns} falas.")
                elif command == "/conversar":
                    cmd_talk(actors, rest, turns, llm)
                else:
                    print("Comando desconhecido. Digite /ajuda.")
            except RuntimeError as error:
                print(f"\n[erro] {error}")
            except (EOFError, KeyboardInterrupt):
                break

    finally:
        # Garante que os processos filhos sempre encerrem com o Python
        _final_actors = locals().get("actors", {})
        for actor in _final_actors.values():
            try:
                actor.db.close()
            except Exception:
                pass
        actors_server.stop()
        screenwriter_server.stop()
        print("\nAté mais!")



if __name__ == "__main__":
    main()
