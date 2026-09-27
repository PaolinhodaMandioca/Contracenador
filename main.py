"""
main.py - o terminal do projeto e o ORQUESTRADOR.

O orquestrador é o "carteiro": pega o envelope de fala de um Ator e entrega ao outro.
Os atores nunca abrem o arquivo .db um do outro, nunca veem o prompt um do outro e só
trocam o que está dentro do envelope. É isso que garante o isolamento.

Uso:
    python main.py

    Ao iniciar, sobe automaticamente o llama-server com o modelo 7B (atores).
    Quando /roteiro é chamado, derruba o 7B, sobe o 14B para gerar a cena, e depois
    volta ao 7B — tudo transparente para o usuário.

    Para personalizar os modelos ou portas use as flags abaixo:
    python main.py --modelo-atores Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M
                   --modelo-roteirista Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M
"""
import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time

from actor import Actor, DEFAULT_TRAITS, clamp, create_actor, detect_subject, normalize
from llm import LLM
from screenwriter import generate_scene_llm, materialize_scene
from world import evidence_by_origin, find_event_by_type, open_world, register_event


# ============================================================================
# 0) GERENCIADOR DE SERVIDOR llama-server
# ============================================================================

class ServerManager:
    """
    Sobe e derruba um processo llama-server automaticamente.

    Dois modos de carregamento de modelo:
      - Hugging Face: model = "Org/Repo:arquivo.gguf"  (flag -hf)
      - Local:        model = "/caminho/para/modelo.gguf" (flag -m)

    Detecta qual usar pelo conteúdo de `model`:
      - se termina em .gguf → local (-m)
      - se contém "/" ou ":" → Hugging Face (-hf)
    """

    # Modelos padrão sugeridos (pode substituir via --modelo-atores / --modelo-roteirista)
    ACTORS_MODEL       = "Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M"
    SCREENWRITER_MODEL = "Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M"

    def __init__(self, model, port, slots=2, context=4096, threads=None, embedding=False):
        self.model     = model
        self.port      = port
        self.slots     = slots
        self.context   = context
        self.threads   = threads    # None = llama-server decide sozinho
        self.embedding = embedding  # liga o endpoint de embedding (memória semântica, actor.py)
        self._proc     = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def start(self):
        """Inicia o llama-server e aguarda o servidor ficar pronto."""
        if self._proc and self._proc.poll() is None:
            return  # já está rodando

        cmd = self._build_cmd()
        print(f"\n[servidor] Subindo: {' '.join(cmd)}")
        # creationflags=CREATE_NEW_PROCESS_GROUP permite matar o processo
        # filho sem matar o Python (Windows).
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self._wait_ready()

    def stop(self):
        """Para o processo llama-server, se estiver em execução."""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            print(f"[servidor] Encerrando servidor na porta {self.port}...")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _build_cmd(self):
        model = self.model.strip()
        # Decide -hf (Hugging Face) ou -m (arquivo local)
        if model.endswith(".gguf"):
            model_flag = ["-m", model]
        else:
            model_flag = ["-hf", model]

        cmd = (
            ["llama-server"]
            + model_flag
            + ["-c", str(self.context),
               "-np", str(self.slots),
               "--port", str(self.port)]
        )
        if self.threads:
            cmd += ["-t", str(self.threads)]
        if self.embedding:
            cmd += ["--embeddings"]
        return cmd

    def _wait_ready(self, attempts=120, interval=2.0):
        """
        Faz polling no endpoint /health do llama-server.
        Aguarda até `attempts * interval` segundos (padrão: 4 minutos).
        O 14B pode demorar para carregar em CPU — não reduza muito.
        """
        import urllib.request, urllib.error
        health_url = f"{self.url()}/health"
        print(f"[servidor] Aguardando o modelo carregar na porta {self.port}", end="", flush=True)
        for _ in range(attempts):
            time.sleep(interval)
            print(".", end="", flush=True)
            if self._proc.poll() is not None:
                print()
                raise RuntimeError("O llama-server encerrou antes de ficar pronto. "
                                   "Verifique se o modelo existe e se há RAM suficiente.")
            try:
                with urllib.request.urlopen(health_url, timeout=3) as r:
                    data = json.load(r)
                    if data.get("status") in ("ok", "loading model"):
                        # "loading model" = servidor no ar, ainda carregando;
                        # continuamos esperando até status == "ok"
                        if data.get("status") == "ok":
                            print(f"\n[servidor] Pronto! ({self.model})")
                            return
            except Exception:
                pass  # porta ainda não abriu — tenta de novo
        print()
        raise RuntimeError(f"Tempo esgotado aguardando o llama-server na porta {self.port}.")


HELP = """
Comandos (qualquer outro texto é uma mensagem para o ator atual):
  /cenario                    mostra o incidente e os personagens do cenário atual
  /cena [rodadas]             inicia a encenação na sala (investigador interroga até descobrir)
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

def load_actors(folder, slots):
    """Cada arquivo .db da pasta é um Ator. Cada um recebe um slot fixo do servidor
    (os slots se repetem se houver mais atores que slots)."""
    actors = {}
    if not os.path.exists(folder):
        return actors
    for i, file_ in enumerate(sorted(f for f in os.listdir(folder) if f.endswith(".db"))):
        actor = Actor(os.path.join(folder, file_), slot=i % slots)
        actors[actor.name.lower()] = actor
    return actors


def load_scenario(scenario_folder):
    path = os.path.join(scenario_folder, "cena.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


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


def cmd_screenplay(actors_server, screenwriter_server, rest, actors_folder, scenario_folder, slots, current_actors):
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
        actors_server.stop()
        screenwriter_server.start()

        screenwriter_llm = LLM(screenwriter_server.url())
        data = generate_scene_llm(screenwriter_llm, theme)
        materialize_scene(data, actors_folder=actors_folder, scenario_folder=scenario_folder, slots=slots)

        print("\n[roteiro] Voltando ao modelo dos atores (7B)...")
        screenwriter_server.stop()
        actors_server.start()

        new_actors = load_actors(actors_folder, slots)
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
        return load_actors(actors_folder, slots)


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
    print(f'\n=== {a.name} e {b.name} conversam sobre "{topic}" ({turns} falas) ===')

    speaker, listener = a, b
    envelope = speaker.open_conversation(listener.name, topic, llm)      # fala 1
    for turn in range(2, turns + 1):
        speaker, listener = listener, speaker                            # troca a vez
        envelope = speaker.respond(envelope, topic, llm, last=(turn == turns))
    listener.receive(envelope)  # quem ouviu a última fala também precisa processá-la

    print("\n=== Como ficaram os atores ===")
    print(a.summary())
    print(b.summary())


def cmd_scene(actors, rest, scenario_folder, llm):
    """
    Orquestra a dinâmica da 'Sala' (Contracenador com 5 atores), do início ao fim, sem pausa
    interativa a cada rodada - o usuário só acompanha:
    - A cada rodada, o Investigador decide SOZINHO quem interrogar (choose_investigation_target,
      com o LLM como planejador e o código validando a resposta - nunca um menu para o usuário
      escolher, nunca um sorteio puro).
    - O par troca falas (open_conversation / respond).
    - Os outros presentes na sala escutam e processam o que ouviram via .receive(envelope).
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

    suspects = [a for a in actors.values() if a is not investigator]

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

    max_rounds = 15
    if rest.strip().isdigit():
        max_rounds = max(1, int(rest.strip()))

    print(f"\n{'=' * 65}")
    print(f"[CENA] A SALA DE INVESTIGAÇÃO")
    print(f"Incidente: \"{scenario_data.get('scene')}\"")
    print(f"Investigador(a): {investigator.name} | Objetivo: {topic}")
    print(f"Presentes na sala: {', '.join(a.name for a in actors.values())}")
    print(f"Teto de rodadas: {max_rounds}")
    print(f"{'=' * 65}")

    # Inicializa o contexto de conversa entre o investigador e os outros
    for s in suspects:
        investigator.new_conversation(s.name)
        s.new_conversation(investigator.name)

    # Registro de presença: todo mundo na sala "conhece" (relação neutra) todo mundo, mesmo
    # antes de conversarem - é o que permite ao culpado escolher um bode expiatório em DEFLECT
    # (Actor.choose_action só considera quem já tem relação registrada).
    present = list(actors.values())
    for a in present:
        for b in present:
            if a is not b:
                a.relationship(b.name)

    victory = False

    for round_ in range(1, max_rounds + 1):
        print(f"\n--- [Rodada {round_}/{max_rounds}] ---")

        # Quem interrogar é SEMPRE decisão do investigador, nunca do usuário nem de um sorteio
        # solto: o código monta as opções e o contexto (suspeita já reunida), o LLM escolhe um
        # nome da lista, e o código valida antes de seguir (ver choose_investigation_target em
        # actor.py).
        candidate_names = [s.name for s in suspects]
        target_name = investigator.choose_investigation_target(candidate_names, topic, llm)
        target = actors[target_name.lower()]
        print(f"-> {investigator.name} decide focar em {target.name}.")

        print(f"\n[Interrogatório] {investigator.name} aborda {target.name}...")

        # 1) Investigador fala com o alvo
        question_envelope = investigator.open_conversation(target.name, topic, llm)

        # 2) Alvo responde
        answer_envelope = target.respond(question_envelope, topic, llm)

        # 3) Investigador processa a resposta
        investigator.receive(answer_envelope)

        # 3b) WorldState: registra o que de fato aconteceu nesta rodada (não o que cada um
        # ACREDITA - isso é o evento objetivo, separado da memória de cada Ator).
        for env in (question_envelope, answer_envelope):
            if env.get("tactic") == "THREATEN":
                register_event(world, "threat", actor=env["from"], target=env.get("target"),
                               location="cena", data={"proposition": f'{env["from"]} ameaçou {env.get("target")}'})
        if answer_envelope.get("facts"):
            revealed_texts = [f["text"] for f in answer_envelope["facts"]]
            register_event(world, "revelation", actor=target.name, target=investigator.name,
                           location="cena", data={"proposition": "; ".join(revealed_texts)})

            # 3c) Crenças: o que o Roteirista ligou como evidência de {target} (world.db) vira
            # reforço na hipótese "<assunto> é o culpado" do investigador. Investigador não
            # sabe automaticamente quem é culpado - ele só reforça a hipótese na medida em
            # que suspeitos concretos vão sendo apontados por quem ele interroga.
            for ev in evidence_by_origin(world, target.name):
                if ev["subject"] and ev["subject"] != investigator.name:
                    investigator.update_belief(
                        f"{ev['subject']} é o culpado", delta=0.2, origin=target.name,
                        subject=ev["subject"], evidence=f"evidence:{ev['id']}")

            guilty_belief = investigator.belief(f"{guilty.name} é o culpado")
            if guilty_belief and guilty_belief["confidence"] >= 0.75 and not victory:
                print(f"\n{'*' * 65}")
                print(f"*** VITÓRIA DA INVESTIGAÇÃO POR DEDUÇÃO! (Rodada {round_}) ***")
                print(f"{investigator.name} tem {guilty_belief['confidence']:.0%} de certeza de que "
                      f"{guilty.name} é o culpado, com base nas evidências reunidas.")
                print(f"{'*' * 65}")
                victory = True
                break

        # 4) Plateia: os outros presentes na sala escutam tudo. receive() já grava a memória
        # (e já detecta contradição, se houver); aqui só marcamos de quem é o assunto, já que
        # receive() não sabe que este fato específico é sobre o caso do culpado.
        listeners = [a for a in suspects if a is not target]
        for listener in listeners:
            listener.receive(question_envelope)
            listener.receive(answer_envelope)
            for fact in answer_envelope.get("facts", []):
                memory_id = listener.memory_id_by_text(fact["text"])
                if memory_id:
                    listener.set_about(memory_id, guilty.name)

        # 5) Fim Dinâmico: checa se a verdade entrou na memória do investigador
        investigator_memories = [m["text"].strip() for m in investigator.list_memories()]
        if any(exact_truth == m or exact_truth in m for m in investigator_memories):
            print(f"\n{'*' * 65}")
            print(f"*** VITÓRIA DA INVESTIGAÇÃO! (Descoberto na rodada {round_}) ***")
            print(f"{investigator.name} conseguiu a confissão da verdade:")
            print(f"\"{exact_truth}\"")
            print(f"{'*' * 65}")
            victory = True
            break

    if not victory and round_ == max_rounds:
        print(f"\n{'*' * 65}")
        print(f"*** VITÓRIA DO CULPADO POR EXAUSTÃO! ***")
        print(f"{guilty.name} conseguiu despistar {investigator.name} após {max_rounds} rodadas.")
        print(f"A verdade que ficou oculta foi:")
        print(f"\"{exact_truth}\"")
        print(f"{'*' * 65}")

    # Fecha os objetivos (roadmap, seção 17): dá desfecho explícito, não deixa "ativo" pra sempre.
    investigator.update_goal(topic, status="done" if victory else "failed")
    guilty.update_goal("Não ser descoberto", status="failed" if victory else "done")

    world.close()
    print("\n=== Resumo final dos personagens ===")
    print(investigator.summary())
    print()
    print(guilty.summary())


# ============================================================================
# 3) PROGRAMA PRINCIPAL
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Atores de IA isolados com Roteirista (dois modelos, gerenciados automaticamente)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # Modelos
    parser.add_argument(
        "--modelo-atores",
        default=ServerManager.ACTORS_MODEL,
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
        default=ServerManager.SCREENWRITER_MODEL,
        metavar="MODELO",
        help=(
            "Modelo para o Roteirista (14B).\n"
            f"  Padrão: {ServerManager.SCREENWRITER_MODEL}"
        ),
    )
    # Portas
    parser.add_argument("--porta-atores",      type=int, default=8080, help="Porta do servidor 7B (padrão: 8080)")
    parser.add_argument("--porta-roteirista",  type=int, default=8081, help="Porta do servidor 14B (padrão: 8081)")
    # Outros
    parser.add_argument("--slots",   type=int, default=2,  help="Número de slots KV do llama-server (padrão: 2)")
    parser.add_argument("--threads", type=int, default=None, help="Número de threads de CPU do llama-server (padrão: automático)")
    parser.add_argument("--pasta",   default="atores",  help="Pasta dos arquivos .db dos atores")
    parser.add_argument("--cenario", default="cenario", help="Pasta onde o arquivo cena.json é salvo")
    parser.add_argument("--semente", type=int,          help="Fixa o sorteio das decisões (para testes)")
    args = parser.parse_args()

    if args.semente is not None:
        random.seed(args.semente)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    os.makedirs(args.pasta, exist_ok=True)
    os.makedirs(args.cenario, exist_ok=True)

    # Cria os dois gerenciadores (ainda não sobem o processo agora)
    actors_server = ServerManager(
        model=args.modelo_atores,
        port=args.porta_atores,
        slots=args.slots,
        context=4096,
        threads=args.threads,
        embedding=True,  # memória semântica (Actor.recall_semantic); se a build do
                        # llama.cpp não suportar, o Ator cai sozinho pra busca lexical
    )
    screenwriter_server = ServerManager(
        model=args.modelo_roteirista,
        port=args.porta_roteirista,
        slots=1,        # roteirista gera 1 cena de cada vez: 1 slot basta
        context=8192,   # 14B precisa de contexto maior para gerar JSON longo
        threads=args.threads,
    )

    try:
        # Sobe o servidor dos atores ao iniciar
        actors_server.start()
        llm = LLM(actors_server.url())

        # Se a pasta de atores estiver vazia, aciona o roteirista imediatamente
        if not any(f.endswith(".db") for f in os.listdir(args.pasta)):
            print(f"\n[Aviso] Nenhum ator encontrado em '{args.pasta}/'.")
            print("Vamos gerar uma nova cena com o Roteirista (14B)!")
            theme = ""
            while not theme:
                theme = input("Digite o tema ou incidente da cena:\n> ").strip()
                if not theme:
                    print("Por favor, digite um tema para a IA criar o mistério.")
            try:
                print("\n[roteiro] Carregando modelo do Roteirista (14B)...")
                actors_server.stop()
                screenwriter_server.start()
                screenwriter_llm = LLM(screenwriter_server.url())
                data = generate_scene_llm(screenwriter_llm, theme)
                materialize_scene(data, actors_folder=args.pasta, scenario_folder=args.cenario, slots=args.slots)
                print("\n[roteiro] Voltando ao modelo dos atores (7B)...")
                screenwriter_server.stop()
                actors_server.start()
                llm = LLM(actors_server.url())
            except Exception as e:
                print(f"\n[Erro ao criar cena com LLM] {e}")
                if not actors_server.is_running():
                    actors_server.start()
                    llm = LLM(actors_server.url())
                sys.exit(1)

        actors = load_actors(args.pasta, args.slots)
        current = next(iter(actors.values()))
        turns = 4
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
                    cmd_scenario(args.cenario)
                elif command in ("/cena", "/sala"):
                    cmd_scene(actors, rest, args.cenario, llm)
                elif command in ("/roteiro", "/roteirista"):
                    actors = cmd_screenplay(
                        actors_server, screenwriter_server,
                        rest, args.pasta, args.cenario, args.slots, actors,
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
                    cmd_new(args.pasta, rest, actors, args.slots)
                    for ag in actors.values():
                        ag.debug = debug_on
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
