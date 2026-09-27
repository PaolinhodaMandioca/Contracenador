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
import re
import socket
import subprocess
import sys
import time

from Ator import Ator, TRACOS_PADRAO, criar_ator, limitar, normalizar
from llm import LLM
from mundo import abrir_mundo, buscar_evento_tipo, evidencias_por_origem, registrar_evento
from roteirista import gerar_cena_llm, materializar_cena


# ============================================================================
# 0) GERENCIADOR DE SERVIDOR llama-server
# ============================================================================

class GerenciadorServidor:
    """
    Sobe e derruba um processo llama-server automaticamente.

    Dois modos de carregamento de modelo:
      - Hugging Face: modelo = "Org/Repo:arquivo.gguf"  (flag -hf)
      - Local:        modelo = "/caminho/para/modelo.gguf" (flag -m)

    Detecta qual usar pelo conteúdo de `modelo`:
      - se termina em .gguf → local (-m)
      - se contém "/" ou ":" → Hugging Face (-hf)
    """

    # Modelos padrão sugeridos (pode substituir via --modelo-atores / --modelo-roteirista)
    MODELO_ATORES      = "Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M"
    MODELO_ROTEIRISTA  = "Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M"

    def __init__(self, modelo, porta, slots=2, contexto=4096, threads=None):
        self.modelo   = modelo
        self.porta    = porta
        self.slots    = slots
        self.contexto = contexto
        self.threads  = threads   # None = llama-server decide sozinho
        self._proc    = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def subir(self):
        """Inicia o llama-server e aguarda o servidor ficar pronto."""
        if self._proc and self._proc.poll() is None:
            return  # já está rodando

        cmd = self._montar_cmd()
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
        self._aguardar_pronto()

    def derrubar(self):
        """Para o processo llama-server, se estiver em execução."""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            print(f"[servidor] Encerrando servidor na porta {self.porta}...")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def url(self):
        return f"http://127.0.0.1:{self.porta}"

    def em_execucao(self):
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _montar_cmd(self):
        modelo = self.modelo.strip()
        # Decide -hf (Hugging Face) ou -m (arquivo local)
        if modelo.endswith(".gguf"):
            flag_modelo = ["-m", modelo]
        else:
            flag_modelo = ["-hf", modelo]

        cmd = (
            ["llama-server"]
            + flag_modelo
            + ["-c", str(self.contexto),
               "-np", str(self.slots),
               "--port", str(self.porta)]
        )
        if self.threads:
            cmd += ["-t", str(self.threads)]
        return cmd

    def _aguardar_pronto(self, tentativas=120, intervalo=2.0):
        """
        Faz polling no endpoint /health do llama-server.
        Aguarda até `tentativas * intervalo` segundos (padrão: 4 minutos).
        O 14B pode demorar para carregar em CPU — não reduza muito.
        """
        import urllib.request, urllib.error
        url_health = f"{self.url()}/health"
        print(f"[servidor] Aguardando o modelo carregar na porta {self.porta}", end="", flush=True)
        for _ in range(tentativas):
            time.sleep(intervalo)
            print(".", end="", flush=True)
            if self._proc.poll() is not None:
                print()
                raise RuntimeError("O llama-server encerrou antes de ficar pronto. "
                                   "Verifique se o modelo existe e se há RAM suficiente.")
            try:
                with urllib.request.urlopen(url_health, timeout=3) as r:
                    dados = json.load(r)
                    if dados.get("status") in ("ok", "loading model"):
                        # "loading model" = servidor no ar, ainda carregando;
                        # continuamos esperando até status == "ok"
                        if dados.get("status") == "ok":
                            print(f"\n[servidor] Pronto! ({self.modelo})")
                            return
            except Exception:
                pass  # porta ainda não abriu — tenta de novo
        print()
        raise RuntimeError(f"Tempo esgotado aguardando o llama-server na porta {self.porta}.")


AJUDA = """
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
  /tracos <traco> <0 a 1>     muda um traço do ator atual (ex.: /tracos honestidade 0.2)
  /novo <nome>                cria um ator novo avulso (um arquivo .db novo)
  /conversar <A> <B> <tópico> faz A e B conversarem entre si sobre o tópico (1 para 1)
  /turnos <N>                 quantas falas tem a conversa entre 2 atores (padrão 4)
  /ajuda   /sair
"""


# ============================================================================
# 1) ATORES E CENÁRIO: carregar do disco
# ============================================================================

def carregar_atores(pasta, slots):
    """Cada arquivo .db da pasta é um Ator. Cada um recebe um slot fixo do servidor
    (os slots se repetem se houver mais atores que slots)."""
    atores = {}
    if not os.path.exists(pasta):
        return atores
    for i, arquivo in enumerate(sorted(f for f in os.listdir(pasta) if f.endswith(".db"))):
        ator = Ator(os.path.join(pasta, arquivo), slot=i % slots)
        atores[ator.nome.lower()] = ator
    return atores


def carregar_cenario(pasta_cenario):
    caminho = os.path.join(pasta_cenario, "cena.json")
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


# ============================================================================
# 2) COMANDOS
# ============================================================================

def pedir_numero(pergunta, padrao):
    """Pergunta um número de 0 a 1 (Enter = valor padrão)."""
    while True:
        resposta = input(pergunta).strip().replace(",", ".")
        if not resposta:
            return padrao
        try:
            valor = float(resposta)
            if 0 <= valor <= 1:
                return valor
        except ValueError:
            pass
        print("   Digite um número entre 0 e 1 (ou Enter para o padrão).")


def detectar_sujeito(texto, nomes):
    """
    Acha, dentro do texto, o nome de um ator conhecido - ignora acento/maiúscula e
    respeita fronteira de palavra (então 'Bianca' não casa com 'Bia'). Devolve o nome
    (na grafia original) se achar exatamente um; None se não achar ou achar mais de um.
    """
    alvo = normalizar(texto)
    achados = [nome for nome in nomes if re.search(rf"\b{re.escape(normalizar(nome))}\b", alvo)]
    return achados[0] if len(achados) == 1 else None


def cmd_cenario(pasta_cenario):
    dados = carregar_cenario(pasta_cenario)
    if not dados:
        print(f"   Nenhum cenário salvo em '{pasta_cenario}/cena.json'. Use /roteiro para criar um.")
        return
    print("\n=== Cenário Atual ===")
    print(f"Incidente: {dados.get('cena', 'Sem descrição')}")
    atores = dados.get("atores", [])
    investigador = next((a for a in atores if a.get("papel") == "investigador"), None)
    if investigador:
        print(f"Investigador(a): {investigador['nome']} (Objetivo: {investigador.get('objetivo', 'Descobrir a verdade')})")
    nomes_atores = ", ".join(a["nome"] for a in atores)
    print(f"Personagens na cena: {nomes_atores}")
    print(f"(Configurações completas salvas em '{pasta_cenario}/cena.json')")


def cmd_roteiro(srv_atores, srv_roteirista, resto, pasta_atores, pasta_cenario, slots, atores_atuais):
    """
    Gera uma nova cena com o Roteirista (14B, 100% via LLM) e recarrega os atores (7B).
    Fluxo com RAM apertada:
      1) Derruba o servidor 7B (atores)
      2) Sobe o servidor 14B (roteirista)
      3) Gera a cena
      4) Derruba o 14B
      5) Sobe novamente o 7B
    """
    tema = resto.strip()
    if not tema:
        print("\n=== Novo Roteiro com IA (modelo 14B) ===")
        tema = input("Digite o tema ou incidente da cena a ser criada pelo modelo:\n> ").strip()
        if not tema:
            print("Operação cancelada: informe um tema para gerar a cena.")
            return atores_atuais

    for ator in atores_atuais.values():
        ator.db.close()

    try:
        # Troca de modelos
        print("\n[roteiro] Carregando modelo do Roteirista (14B)...")
        srv_atores.derrubar()
        srv_roteirista.subir()

        llm_roteirista = LLM(srv_roteirista.url())
        dados = gerar_cena_llm(llm_roteirista, tema)
        materializar_cena(dados, pasta_atores=pasta_atores, pasta_cenario=pasta_cenario, slots=slots)

        print("\n[roteiro] Voltando ao modelo dos atores (7B)...")
        srv_roteirista.derrubar()
        srv_atores.subir()

        novos_atores = carregar_atores(pasta_atores, slots)
        print(f"\nCena pronta! Atores carregados: {', '.join(a.nome for a in novos_atores.values())}")
        return novos_atores
    except Exception as e:
        print(f"\n[Erro ao gerar roteiro via LLM] {e}")
        # Garante que o servidor dos atores volte mesmo com erro
        if not srv_atores.em_execucao():
            try:
                srv_atores.subir()
            except Exception as e2:
                print(f"[Erro ao reativar servidor de atores] {e2}")
        return carregar_atores(pasta_atores, slots)


def cmd_lembrar(ator, texto, llm, atores):
    """Salvar é explícito (/lembrar): confiável e não gasta CPU com o modelo."""
    if not texto:
        print("Uso: /lembrar <texto>")
        return
    sens = pedir_numero("   Sensibilidade, de 0 (qualquer um pode saber) a 1 (segredo) [0.3]: ", 0.3)
    pode = input("   Pode ser contado a outros atores? (s/n) [s]: ").strip().lower() != "n"

    # RECONHECIMENTO: se o texto menciona outro ator pelo nome, sugere marcar essa
    # memória como sendo sobre ele (ver sabe_sobre em Ator.py).
    outros = [ag.nome for ag in atores.values() if ag is not ator]
    sugestao = detectar_sujeito(texto, outros)
    pergunta = (f"   É sobre outro ator? [Enter = {sugestao}, 'n' = nenhum, ou digite outro nome]: "
                if sugestao else "   É sobre outro ator? (nome, ou Enter para nenhum): ")
    resposta = input(pergunta).strip()
    if resposta.lower() in ("n", "nao", "não"):
        sobre = None
    elif resposta:
        sobre = resposta.capitalize()
    else:
        sobre = sugestao

    id_memoria = ator.lembrar(texto, sensibilidade=sens, compartilhavel=int(pode), sobre=sobre)
    print(f"   Salvo (memória #{id_memoria})." + (f" Sobre: {sobre}." if sobre else ""))

    alvo = atores.get(sobre.lower()) if sobre else None
    if alvo is not None and alvo is not ator:
        if input(f"   A/O {alvo.nome} também sabe disso, porque estava lá? (s/n) [n]: ").strip().lower() == "s":
            alvo.lembrar(texto, sensibilidade=sens, compartilhavel=int(pode))
            print(f"   Também salvo para {alvo.nome}.")

    if pode and sens >= 0.5:
        print("   Gerando uma versão falsa para o caso de ele mentir...")
        falsa = ator.gerar_versao_falsa(id_memoria, llm)
        if falsa:
            print(f"   Versão falsa: {falsa}")
        else:
            print("   Não consegui gerar uma versão falsa: sem ela o ator só consegue ESCONDER "
                  "esse fato, não mentir. Escreva uma com /falsa.")


def cmd_memorias(ator):
    memorias = ator.listar_memorias()
    if not memorias:
        print("   (sem memórias)")
    for m in memorias:
        privada = "" if m["compartilhavel"] else " | PRIVADA"
        sobre = f" | sobre: {m['sobre']}" if m["sobre"] else ""
        falsa = f"\n      versão falsa: {m['versao_falsa']}" if m["versao_falsa"] else ""
        print(f"   #{m['id']} [sens {m['sensibilidade']:.1f} | origem {m['origem']}{privada}{sobre}] "
              f"{m['texto']}{falsa}")


def cmd_falsa(ator, resto):
    id_texto = resto.split(maxsplit=1)
    if len(id_texto) == 2 and id_texto[0].isdigit() and ator.definir_versao_falsa(int(id_texto[0]), id_texto[1]):
        print("   Versão falsa gravada.")
    else:
        print("Uso: /falsa <id da memória> <texto da versão falsa>  (veja os ids em /memorias)")


def cmd_sobre(ator, resto):
    partes = resto.split(maxsplit=1)
    if not partes or not partes[0].isdigit():
        print("Uso: /sobre <id da memória> <nome>  (sem nome remove; veja os ids em /memorias)")
        return
    id_memoria = int(partes[0])
    nome = partes[1].strip() if len(partes) > 1 else None
    if not ator.definir_sobre(id_memoria, nome):
        print("   Memória não encontrada (veja os ids em /memorias).")
    elif nome:
        print(f"   Memória #{id_memoria} agora é sobre: {nome}.")
    else:
        print(f"   Assunto removido da memória #{id_memoria}.")


def cmd_tracos(ator, resto):
    partes = resto.split()
    if len(partes) == 2 and partes[0] in TRACOS_PADRAO:
        try:
            ator.pers["tracos"][partes[0]] = limitar(float(partes[1].replace(",", ".")))
            ator.salvar_personalidade()
            print(ator.resumo())
            return
        except ValueError:
            pass
    print("Uso: /tracos <" + " | ".join(TRACOS_PADRAO) + "> <0 a 1>")


def cmd_novo(pasta, nome, atores, slots):
    if not nome.isalnum() or nome.lower() in atores:
        print("Use um nome novo, só com letras e números.")
        return
    descricao = input("   Como ele(a) fala e é? (uma frase): ").strip()
    exemplo = input("   Uma frase de exemplo de como ele(a) fala: ").strip()
    tracos = {t: pedir_numero(f"   {t} (0 a 1) [0.5]: ", 0.5) for t in TRACOS_PADRAO}
    caminho = os.path.join(pasta, f"{nome.lower()}.db")
    criar_ator(caminho, nome.capitalize(), descricao, [exemplo] if exemplo else [], tracos)
    atores[nome.lower()] = Ator(caminho, slot=len(atores) % slots)
    print(f"   Ator {nome.capitalize()} criado em {caminho}.")


def cmd_conversar(atores, resto, turnos, llm):
    """Faz dois atores conversarem. O orquestrador só leva o envelope de um para o outro."""
    partes = resto.split(maxsplit=2)
    if len(partes) < 3:
        print("Uso: /conversar <ator1> <ator2> <tópico>")
        return
    a, b = atores.get(partes[0].lower()), atores.get(partes[1].lower())
    if a is None or b is None or a is b:
        print("Escolha dois atores diferentes (veja /atores).")
        return
    topico = partes[2]

    a.nova_conversa(b.nome)
    b.nova_conversa(a.nome)
    print(f'\n=== {a.nome} e {b.nome} conversam sobre "{topico}" ({turnos} falas) ===')

    falante, ouvinte = a, b
    envelope = falante.abrir_conversa(ouvinte.nome, topico, llm)      # fala 1
    for fala in range(2, turnos + 1):
        falante, ouvinte = ouvinte, falante                            # troca a vez
        envelope = falante.responder(envelope, topico, llm, ultima=(fala == turnos))
    ouvinte.receber(envelope)  # quem ouviu a última fala também precisa processá-la

    print("\n=== Como ficaram os atores ===")
    print(a.resumo())
    print(b.resumo())


def cmd_cena(atores, resto, pasta_cenario, llm):
    """
    Orquestra a dinâmica da 'Sala' (Contracenador com 5 atores):
    - O Investigador interroga um dos outros atores a cada rodada.
    - O par troca falas (abrir_conversa / responder).
    - Os outros presentes na sala escutam e processam o que ouviram via .receber(envelope).
    - Fim dinâmico: a investigação vence se a 'verdade' entrar na memória do investigador;
      o culpado vence por exaustão se atingir o teto de rodadas (padrão 15).
    """
    cenario_dados = carregar_cenario(pasta_cenario)
    if not cenario_dados:
        print(f"Nenhum cenário encontrado em '{pasta_cenario}/cena.json'.")
        print("Crie um cenário primeiro usando: /roteiro")
        return

    atores_dados = cenario_dados.get("atores", [])
    investigador_info = next((a for a in atores_dados if a.get("papel") == "investigador"), None)
    culpado_info = next((a for a in atores_dados if a.get("papel") == "culpado"), None)

    if not investigador_info or not culpado_info:
        print("Erro: O cenário precisa ter pelo menos 1 investigador e 1 culpado.")
        return

    investigador = atores.get(investigador_info["nome"].lower())
    culpado = atores.get(culpado_info["nome"].lower())

    if not investigador or not culpado:
        print("Erro: Os atores do cenário não estão todos carregados. Use /roteiro para recarregar.")
        return

    suspeitos = [a for a in atores.values() if a is not investigador]

    # A verdade vem do WorldState (mundo.db), não mais direto do cena.json: é o evento
    # 'crime' gravado por materializar_cena(). Se por algum motivo o mundo não existir
    # (cena antiga, gerada antes do WorldState), cai de volta no campo do cena.json.
    caminho_mundo = os.path.join(pasta_cenario, "mundo.db")
    mundo = abrir_mundo(caminho_mundo)
    evento_crime = buscar_evento_tipo(mundo, "crime")
    if evento_crime:
        verdade_exata = (evento_crime["dados"].get("proposicao") or "").strip()
    else:
        verdade_exata = (culpado_info.get("verdade") or "").strip()

    topico = investigador_info.get("objetivo") or cenario_dados.get("cena", "O mistério")

    turnos_max = 15
    if resto.strip().isdigit():
        turnos_max = max(1, int(resto.strip()))

    print(f"\n{'=' * 65}")
    print(f"[CENA] A SALA DE INVESTIGAÇÃO")
    print(f"Incidente: \"{cenario_dados.get('cena')}\"")
    print(f"Investigador(a): {investigador.nome} | Objetivo: {topico}")
    print(f"Presentes na sala: {', '.join(a.nome for a in atores.values())}")
    print(f"Teto de rodadas: {turnos_max}")
    print(f"{'=' * 65}")

    # Inicializa o contexto de conversa entre o investigador e os outros
    for s in suspeitos:
        investigador.nova_conversa(s.nome)
        s.nova_conversa(investigador.nome)

    # Registro de presença: todo mundo na sala "conhece" (relação neutra) todo mundo, mesmo
    # antes de conversarem - é o que permite ao culpado escolher um bode expiatório em DESVIAR
    # (Ator.escolher_acao só considera quem já tem relação registrada).
    presentes = list(atores.values())
    for a in presentes:
        for b in presentes:
            if a is not b:
                a.relacao(b.nome)

    vitoria = False

    for rodada in range(1, turnos_max + 1):
        print(f"\n--- [Rodada {rodada}/{turnos_max}] ---")
        print(f"Quem {investigador.nome} deve interrogar?")
        for idx, s in enumerate(suspeitos, 1):
            desconf = investigador.relacao(s.nome)["desconfianca"]
            conhece = " (já interrogado)" if s.nome in investigador.satisfeitos else ""
            print(f"   [{idx}] {s.nome} (desconfiança de {investigador.nome}: {desconf:.2f}){conhece}")
        print("   [A] Automático (o investigador escolhe sozinho)")
        print("   [S] Encerrar a cena agora")

        escolha = input("Opção [A]: ").strip().lower()

        if escolha in ("s", "sair", "exit", "q"):
            print("\nCena encerrada pelo usuário.")
            break

        alvo = None
        if escolha.isdigit() and 1 <= int(escolha) <= len(suspeitos):
            alvo = suspeitos[int(escolha) - 1]
        elif escolha in ("", "a", "auto", "automatico", "automático"):
            # Heurística: prioriza quem ainda não foi interrogado, quem tem maior desconfiança
            # e o principal suspeito segundo as crenças já formadas (ver atualizar_crenca acima).
            def pontuacao(s):
                crenca = investigador.crenca(f"{s.nome} é o culpado")
                suspeita = crenca["confianca"] if crenca else 0.0
                return (
                    0.6 * suspeita
                    + 0.4 * investigador.relacao(s.nome)["desconfianca"]
                    + (0.4 if s.nome not in investigador.satisfeitos else 0.0)
                    + random.uniform(0.0, 0.2)
                )
            alvo = max(suspeitos, key=pontuacao)
            print(f"-> {investigador.nome} decide focar em {alvo.nome}.")
        else:
            achados = [s for s in suspeitos if normalizar(s.nome) == normalizar(escolha)]
            if achados:
                alvo = achados[0]
            else:
                alvo = suspeitos[0]
                print(f"-> Opção não reconhecida. Focando em {alvo.nome}.")

        print(f"\n[Interrogatório] {investigador.nome} aborda {alvo.nome}...")

        # 1) Investigador fala com o alvo
        envelope_pergunta = investigador.abrir_conversa(alvo.nome, topico, llm)

        # 2) Alvo responde
        envelope_resposta = alvo.responder(envelope_pergunta, topico, llm)

        # 3) Investigador processa a resposta
        investigador.receber(envelope_resposta)

        # 3b) WorldState: registra o que de fato aconteceu nesta rodada (não o que cada um
        # ACREDITA - isso é o evento objetivo, separado da memória de cada Ator).
        for env in (envelope_pergunta, envelope_resposta):
            if env.get("tatica") == "AMEACAR":
                registrar_evento(mundo, "ameaca", ator=env["de"], alvo=env.get("alvo"),
                                  local="cena", dados={"proposicao": f'{env["de"]} ameaçou {env.get("alvo")}'})
        if envelope_resposta.get("fatos"):
            textos_revelados = [f["texto"] for f in envelope_resposta["fatos"]]
            registrar_evento(mundo, "revelacao", ator=alvo.nome, alvo=investigador.nome,
                              local="cena", dados={"proposicao": "; ".join(textos_revelados)})

            # 3c) Crenças: o que o Roteirista ligou como evidência de {alvo} (mundo.db) vira
            # reforço na hipótese "<assunto> é o culpado" do investigador. Investigador não
            # sabe automaticamente quem é culpado - ele só reforça a hipótese na medida em
            # que suspeitos concretos vão sendo apontados por quem ele interroga.
            for ev in evidencias_por_origem(mundo, alvo.nome):
                if ev["assunto"] and ev["assunto"] != investigador.nome:
                    investigador.atualizar_crenca(
                        f"{ev['assunto']} é o culpado", delta=0.2, origem=alvo.nome,
                        assunto=ev["assunto"], evidencia=f"evidencia:{ev['id']}")

            crenca_culpado = investigador.crenca(f"{culpado.nome} é o culpado")
            if crenca_culpado and crenca_culpado["confianca"] >= 0.75 and not vitoria:
                print(f"\n{'*' * 65}")
                print(f"*** VITÓRIA DA INVESTIGAÇÃO POR DEDUÇÃO! (Rodada {rodada}) ***")
                print(f"{investigador.nome} tem {crenca_culpado['confianca']:.0%} de certeza de que "
                      f"{culpado.nome} é o culpado, com base nas evidências reunidas.")
                print(f"{'*' * 65}")
                vitoria = True
                break

        # 4) Plateia: os outros presentes na sala escutam tudo. receber() já grava a memória
        # (e já detecta contradição, se houver); aqui só marcamos de quem é o assunto, já que
        # receber() não sabe que este fato específico é sobre o caso do culpado.
        ouvintes = [a for a in suspeitos if a is not alvo]
        for ouvinte in ouvintes:
            ouvinte.receber(envelope_pergunta)
            ouvinte.receber(envelope_resposta)
            for fato in envelope_resposta.get("fatos", []):
                id_mem = ouvinte.memoria_id_por_texto(fato["texto"])
                if id_mem:
                    ouvinte.definir_sobre(id_mem, culpado.nome)

        # 5) Fim Dinâmico: checa se a verdade entrou na memória do investigador
        memorias_inv = [m["texto"].strip() for m in investigador.listar_memorias()]
        if any(verdade_exata == m or verdade_exata in m for m in memorias_inv):
            print(f"\n{'*' * 65}")
            print(f"*** VITÓRIA DA INVESTIGAÇÃO! (Descoberto na rodada {rodada}) ***")
            print(f"{investigador.nome} conseguiu a confissão da verdade:")
            print(f"\"{verdade_exata}\"")
            print(f"{'*' * 65}")
            vitoria = True
            break

    if not vitoria and rodada == turnos_max:
        print(f"\n{'*' * 65}")
        print(f"*** VITÓRIA DO CULPADO POR EXAUSTÃO! ***")
        print(f"{culpado.nome} conseguiu despistar {investigador.nome} após {turnos_max} rodadas.")
        print(f"A verdade que ficou oculta foi:")
        print(f"\"{verdade_exata}\"")
        print(f"{'*' * 65}")

    # Fecha os objetivos (roadmap, seção 17): dá desfecho explícito, não deixa "ativo" pra sempre.
    investigador.atualizar_objetivo(topico, status="concluido" if vitoria else "falhou")
    culpado.atualizar_objetivo("Não ser descoberto", status="falhou" if vitoria else "concluido")

    mundo.close()
    print("\n=== Resumo final dos personagens ===")
    print(investigador.resumo())
    print()
    print(culpado.resumo())


# ============================================================================
# 3) PROGRAMA PRINCIPAL
# ============================================================================

def main():
    analisador = argparse.ArgumentParser(
        description="Atores de IA isolados com Roteirista (dois modelos, gerenciados automaticamente)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # Modelos
    analisador.add_argument(
        "--modelo-atores",
        default=GerenciadorServidor.MODELO_ATORES,
        metavar="MODELO",
        help=(
            "Modelo para os atores (7B).\n"
            "  Hugging Face: Org/Repo:arquivo.gguf  (baixa automaticamente)\n"
            "  Local:        /caminho/modelo.gguf\n"
            f"  Padrão: {GerenciadorServidor.MODELO_ATORES}"
        ),
    )
    analisador.add_argument(
        "--modelo-roteirista",
        default=GerenciadorServidor.MODELO_ROTEIRISTA,
        metavar="MODELO",
        help=(
            "Modelo para o Roteirista (14B).\n"
            f"  Padrão: {GerenciadorServidor.MODELO_ROTEIRISTA}"
        ),
    )
    # Portas
    analisador.add_argument("--porta-atores",      type=int, default=8080, help="Porta do servidor 7B (padrão: 8080)")
    analisador.add_argument("--porta-roteirista",  type=int, default=8081, help="Porta do servidor 14B (padrão: 8081)")
    # Outros
    analisador.add_argument("--slots",   type=int, default=2,  help="Número de slots KV do llama-server (padrão: 2)")
    analisador.add_argument("--threads", type=int, default=None, help="Número de threads de CPU do llama-server (padrão: automático)")
    analisador.add_argument("--pasta",   default="atores",  help="Pasta dos arquivos .db dos atores")
    analisador.add_argument("--cenario", default="cenario", help="Pasta onde o arquivo cena.json é salvo")
    analisador.add_argument("--semente", type=int,          help="Fixa o sorteio das decisões (para testes)")
    args = analisador.parse_args()

    if args.semente is not None:
        random.seed(args.semente)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    os.makedirs(args.pasta, exist_ok=True)
    os.makedirs(args.cenario, exist_ok=True)

    # Cria os dois gerenciadores (ainda não sobem o processo agora)
    srv_atores = GerenciadorServidor(
        modelo=args.modelo_atores,
        porta=args.porta_atores,
        slots=args.slots,
        contexto=4096,
        threads=args.threads,
    )
    srv_roteirista = GerenciadorServidor(
        modelo=args.modelo_roteirista,
        porta=args.porta_roteirista,
        slots=1,        # roteirista gera 1 cena de cada vez: 1 slot basta
        contexto=8192,  # 14B precisa de contexto maior para gerar JSON longo
        threads=args.threads,
    )

    try:
        # Sobe o servidor dos atores ao iniciar
        srv_atores.subir()
        llm = LLM(srv_atores.url())

        # Se a pasta de atores estiver vazia, aciona o roteirista imediatamente
        if not any(f.endswith(".db") for f in os.listdir(args.pasta)):
            print(f"\n[Aviso] Nenhum ator encontrado em '{args.pasta}/'.")
            print("Vamos gerar uma nova cena com o Roteirista (14B)!")
            tema = ""
            while not tema:
                tema = input("Digite o tema ou incidente da cena:\n> ").strip()
                if not tema:
                    print("Por favor, digite um tema para a IA criar o mistério.")
            try:
                print("\n[roteiro] Carregando modelo do Roteirista (14B)...")
                srv_atores.derrubar()
                srv_roteirista.subir()
                llm_roteirista = LLM(srv_roteirista.url())
                dados = gerar_cena_llm(llm_roteirista, tema)
                materializar_cena(dados, pasta_atores=args.pasta, pasta_cenario=args.cenario, slots=args.slots)
                print("\n[roteiro] Voltando ao modelo dos atores (7B)...")
                srv_roteirista.derrubar()
                srv_atores.subir()
                llm = LLM(srv_atores.url())
            except Exception as e:
                print(f"\n[Erro ao criar cena com LLM] {e}")
                if not srv_atores.em_execucao():
                    srv_atores.subir()
                    llm = LLM(srv_atores.url())
                sys.exit(1)

        atores = carregar_atores(args.pasta, args.slots)
        atual = next(iter(atores.values()))
        turnos = 4
        debug_ativo = False
        print(AJUDA)

        while True:
            try:
                entrada = input(f"\n[{atual.nome}] você> ").strip()
                if not entrada:
                    continue
                if not entrada.startswith("/"):
                    atual.falar_com_usuario(entrada, llm)
                    continue

                comando, _, resto = entrada.partition(" ")
                resto = resto.strip()
                comando = comando.lower()

                if comando == "/sair":
                    break
                elif comando == "/ajuda":
                    print(AJUDA)
                elif comando in ("/atores", "/agentes"):
                    for nome, ag in atores.items():
                        print(f"   {'*' if ag is atual else ' '} {ag.nome} (slot {ag.slot})")
                elif comando == "/cenario":
                    cmd_cenario(args.cenario)
                elif comando in ("/cena", "/sala"):
                    cmd_cena(atores, resto, args.cenario, llm)
                elif comando in ("/roteiro", "/roteirista"):
                    atores = cmd_roteiro(
                        srv_atores, srv_roteirista,
                        resto, args.pasta, args.cenario, args.slots, atores,
                    )
                    # Após /roteiro o 7B voltou: recria o cliente LLM apontando para ele
                    llm = LLM(srv_atores.url())
                    atual = next(iter(atores.values()))
                    for ag in atores.values():
                        ag.debug = debug_ativo
                elif comando == "/falar":
                    if resto.lower() in atores:
                        atual = atores[resto.lower()]
                    else:
                        print("Ator não encontrado (veja /atores).")
                elif comando == "/lembrar":
                    cmd_lembrar(atual, resto, llm, atores)
                elif comando == "/memorias":
                    cmd_memorias(atual)
                elif comando == "/falsa":
                    cmd_falsa(atual, resto)
                elif comando == "/sobre":
                    cmd_sobre(atual, resto)
                elif comando == "/estado":
                    print(atual.resumo())
                elif comando == "/painel":
                    outro = resto.strip() or None
                    if outro and outro.lower() not in atores:
                        outro = None
                    print(atual.painel(outro))
                elif comando == "/debug":
                    debug_ativo = not debug_ativo
                    for ag in atores.values():
                        ag.debug = debug_ativo
                    print(f"   Modo debug: {'ligado' if debug_ativo else 'desligado'}.")
                elif comando == "/tracos":
                    cmd_tracos(atual, resto)
                elif comando == "/novo":
                    cmd_novo(args.pasta, resto, atores, args.slots)
                    for ag in atores.values():
                        ag.debug = debug_ativo
                elif comando == "/turnos":
                    turnos = max(1, int(resto)) if resto.isdigit() else turnos
                    print(f"   Conversas entre atores terão {turnos} falas.")
                elif comando == "/conversar":
                    cmd_conversar(atores, resto, turnos, llm)
                else:
                    print("Comando desconhecido. Digite /ajuda.")
            except RuntimeError as erro:
                print(f"\n[erro] {erro}")
            except (EOFError, KeyboardInterrupt):
                break

    finally:
        # Garante que os processos filhos sempre encerrem com o Python
        _atores_final = locals().get("atores", {})
        for ator in _atores_final.values():
            try:
                ator.db.close()
            except Exception:
                pass
        srv_atores.derrubar()
        srv_roteirista.derrubar()
        print("\nAté mais!")



if __name__ == "__main__":
    main()
