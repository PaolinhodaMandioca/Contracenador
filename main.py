"""
main.py - o terminal do projeto e o ORQUESTRADOR.

O orquestrador é o "carteiro": pega o envelope de fala de um agente e entrega ao outro.
Os agentes nunca abrem o arquivo .db um do outro, nunca veem o prompt um do outro e só
trocam o que está dentro do envelope. É isso que garante o isolamento.

Uso:
    1) llama-server -m SEU-MODELO-3B.gguf -c 4096 -np 2 -t 8 --port 8080
    2) python main.py --slots 2
"""
import argparse
import os
import random
import re
import sys

from agente import Agente, TRACOS_PADRAO, criar_agente, limitar, normalizar
from llm import LLM

AJUDA = """
Comandos (qualquer outro texto é uma mensagem para o agente atual):
  /agentes                    lista os agentes
  /falar <nome>               troca o agente com quem você conversa
  /lembrar <texto>            ensina um fato ao agente atual
  /memorias                   mostra o que o agente atual sabe
  /falsa <id> <texto>         escreve à mão a versão falsa da memória <id> (a que ele conta ao mentir)
  /sobre <id> <nome>          diz de quem é a memória <id> (ex.: /sobre 3 Bia); sem nome remove
  /estado                     mostra traços, emoções e relações do agente atual
  /tracos <traco> <0 a 1>     muda um traço do agente atual (ex.: /tracos honestidade 0.2)
  /novo <nome>                cria um agente novo (um arquivo .db novo)
  /conversar <A> <B> <tópico> faz A e B conversarem entre si sobre o tópico
  /turnos <N>                 quantas falas tem a conversa entre agentes (padrão 4)
  /ajuda   /sair
"""


# ============================================================================
# 1) AGENTES: carregar do disco e criar os de exemplo
# ============================================================================

def criar_exemplos(pasta):
    """Na primeira execução cria dois agentes opostos, para dar o que testar."""
    criar_agente(
        os.path.join(pasta, "bia.db"), "Bia",
        "Simpática, curiosa e muito falante. Fala com frases curtas e gírias leves.",
        ["Opa, essa eu não sabia! Me conta mais?", "Nossa, sério? Conta tudo!"],
        {"honestidade": 0.9, "dissimulacao": 0.1, "empatia": 0.8,
         "coragem": 0.4, "agressividade": 0.1, "ganancia": 0.3})
    criar_agente(
        os.path.join(pasta, "caio.db"), "Caio",
        "Reservado, desconfiado e calculista. Fala pouco, com frases secas e um tom irônico.",
        ["Hm. E por que eu te contaria isso?", "Depende. O que eu ganho com isso?"],
        {"honestidade": 0.3, "dissimulacao": 0.8, "empatia": 0.2,
         "coragem": 0.7, "agressividade": 0.6, "ganancia": 0.7})


def carregar_agentes(pasta, slots):
    """Cada arquivo .db da pasta é um agente. Cada um recebe um slot fixo do servidor
    (os slots se repetem se houver mais agentes que slots)."""
    agentes = {}
    for i, arquivo in enumerate(sorted(f for f in os.listdir(pasta) if f.endswith(".db"))):
        agente = Agente(os.path.join(pasta, arquivo), slot=i % slots)
        agentes[agente.nome.lower()] = agente
    return agentes


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
    Acha, dentro do texto, o nome de um agente conhecido - ignora acento/maiúscula e
    respeita fronteira de palavra (então 'Bianca' não casa com 'Bia'). Devolve o nome
    (na grafia original) se achar exatamente um; None se não achar ou achar mais de um
    (nesse caso é mais seguro perguntar do que adivinhar errado).
    """
    alvo = normalizar(texto)
    achados = [nome for nome in nomes if re.search(rf"\b{re.escape(normalizar(nome))}\b", alvo)]
    return achados[0] if len(achados) == 1 else None


def cmd_lembrar(agente, texto, llm, agentes):
    """Salvar é explícito (/lembrar): confiável e não gasta CPU com o modelo."""
    if not texto:
        print("Uso: /lembrar <texto>")
        return
    sens = pedir_numero("   Sensibilidade, de 0 (qualquer um pode saber) a 1 (segredo) [0.3]: ", 0.3)
    pode = input("   Pode ser contado a outros agentes? (s/n) [s]: ").strip().lower() != "n"

    # RECONHECIMENTO: se o texto menciona outro agente pelo nome, sugere marcar essa
    # memória como sendo sobre ele - é isso que permite ao agente atual reconhecer
    # esse outro agente quando ele aparecer numa conversa (ver sabe_sobre em agente.py).
    outros = [ag.nome for ag in agentes.values() if ag is not agente]
    sugestao = detectar_sujeito(texto, outros)
    pergunta = (f"   É sobre outro agente? [Enter = {sugestao}, 'n' = nenhum, ou digite outro nome]: "
                if sugestao else "   É sobre outro agente? (nome, ou Enter para nenhum): ")
    resposta = input(pergunta).strip()
    if resposta.lower() in ("n", "nao", "não"):
        sobre = None
    elif resposta:
        sobre = resposta.capitalize()
    else:
        sobre = sugestao

    id_memoria = agente.lembrar(texto, sensibilidade=sens, compartilhavel=int(pode), sobre=sobre)
    print(f"   Salvo (memória #{id_memoria})." + (f" Sobre: {sobre}." if sobre else ""))

    # Se o assunto é outro agente que existe de verdade, ele pode ter estado lá e já
    # saber disso por conta própria - nesse caso o mesmo fato entra na memória DELE,
    # como algo que o próprio usuário lhe contou (não como algo que "agente" revelou).
    alvo = agentes.get(sobre.lower()) if sobre else None
    if alvo is not None and alvo is not agente:
        if input(f"   A {alvo.nome} também sabe disso, porque estava lá? (s/n) [n]: ").strip().lower() == "s":
            alvo.lembrar(texto, sensibilidade=sens, compartilhavel=int(pode))
            print(f"   Também salvo para a {alvo.nome}.")

    # Só vale gastar uma inferência com a versão falsa se o fato é sensível e pode sair do agente.
    if pode and sens >= 0.5:
        print("   Gerando uma versão falsa para o caso de ele mentir...")
        falsa = agente.gerar_versao_falsa(id_memoria, llm)
        if falsa:
            print(f"   Versão falsa: {falsa}")
        else:
            print("   Não consegui gerar uma versão falsa: sem ela o agente só consegue ESCONDER "
                  "esse fato, não mentir. Escreva uma com /falsa.")


def cmd_memorias(agente):
    memorias = agente.listar_memorias()
    if not memorias:
        print("   (sem memórias)")
    for m in memorias:
        privada = "" if m["compartilhavel"] else " | PRIVADA"
        sobre = f" | sobre: {m['sobre']}" if m["sobre"] else ""
        falsa = f"\n      versão falsa: {m['versao_falsa']}" if m["versao_falsa"] else ""
        print(f"   #{m['id']} [sens {m['sensibilidade']:.1f} | origem {m['origem']}{privada}{sobre}] "
              f"{m['texto']}{falsa}")


def cmd_falsa(agente, resto):
    id_texto = resto.split(maxsplit=1)
    if len(id_texto) == 2 and id_texto[0].isdigit() and agente.definir_versao_falsa(int(id_texto[0]), id_texto[1]):
        print("   Versão falsa gravada.")
    else:
        print("Uso: /falsa <id da memória> <texto da versão falsa>  (veja os ids em /memorias)")


def cmd_sobre(agente, resto):
    partes = resto.split(maxsplit=1)
    if not partes or not partes[0].isdigit():
        print("Uso: /sobre <id da memória> <nome>  (sem nome remove; veja os ids em /memorias)")
        return
    id_memoria = int(partes[0])
    nome = partes[1].strip() if len(partes) > 1 else None
    if not agente.definir_sobre(id_memoria, nome):
        print("   Memória não encontrada (veja os ids em /memorias).")
    elif nome:
        print(f"   Memória #{id_memoria} agora é sobre: {nome}.")
    else:
        print(f"   Assunto removido da memória #{id_memoria}.")


def cmd_tracos(agente, resto):
    partes = resto.split()
    if len(partes) == 2 and partes[0] in TRACOS_PADRAO:
        try:
            agente.pers["tracos"][partes[0]] = limitar(float(partes[1].replace(",", ".")))
            agente.salvar_personalidade()
            print(agente.resumo())
            return
        except ValueError:
            pass
    print("Uso: /tracos <" + " | ".join(TRACOS_PADRAO) + "> <0 a 1>")


def cmd_novo(pasta, nome, agentes, slots):
    if not nome.isalnum() or nome.lower() in agentes:
        print("Use um nome novo, só com letras e números.")
        return
    descricao = input("   Como ele(a) fala e é? (uma frase): ").strip()
    exemplo = input("   Uma frase de exemplo de como ele(a) fala: ").strip()
    tracos = {t: pedir_numero(f"   {t} (0 a 1) [0.5]: ", 0.5) for t in TRACOS_PADRAO}
    caminho = os.path.join(pasta, f"{nome.lower()}.db")
    criar_agente(caminho, nome.capitalize(), descricao, [exemplo] if exemplo else [], tracos)
    agentes[nome.lower()] = Agente(caminho, slot=len(agentes) % slots)
    print(f"   Agente {nome.capitalize()} criado em {caminho}.")


def cmd_conversar(agentes, resto, turnos, llm):
    """Faz dois agentes conversarem. O orquestrador só leva o envelope de um para o outro."""
    partes = resto.split(maxsplit=2)
    if len(partes) < 3:
        print("Uso: /conversar <agente1> <agente2> <tópico>")
        return
    a, b = agentes.get(partes[0].lower()), agentes.get(partes[1].lower())
    if a is None or b is None or a is b:
        print("Escolha dois agentes diferentes (veja /agentes).")
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

    print("\n=== Como ficaram os agentes ===")
    print(a.resumo())
    print(b.resumo())


# ============================================================================
# 3) PROGRAMA PRINCIPAL
# ============================================================================

def main():
    analisador = argparse.ArgumentParser(description="Agentes de IA isolados (versão simples)")
    analisador.add_argument("--url", default="http://127.0.0.1:8080", help="endereço do llama-server")
    analisador.add_argument("--pasta", default="agentes", help="pasta dos arquivos .db dos agentes")
    analisador.add_argument("--slots", type=int, default=2,
                            help="mesmo valor do -np do llama-server (nº de slots)")
    analisador.add_argument("--semente", type=int, help="fixa o sorteio das decisões (para testes)")
    args = analisador.parse_args()

    if args.semente is not None:
        random.seed(args.semente)
    if hasattr(sys.stdout, "reconfigure"):  # evita erro de acento/emoji em terminais antigos
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    os.makedirs(args.pasta, exist_ok=True)
    if not any(f.endswith(".db") for f in os.listdir(args.pasta)):
        criar_exemplos(args.pasta)
        print(f"Primeira execução: criei bia.db e caio.db em '{args.pasta}/'.")

    llm = LLM(args.url)
    agentes = carregar_agentes(args.pasta, args.slots)
    atual = next(iter(agentes.values()))
    turnos = 4
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
            elif comando == "/agentes":
                for nome, ag in agentes.items():
                    print(f"   {'*' if ag is atual else ' '} {ag.nome} (slot {ag.slot})")
            elif comando == "/falar":
                if resto.lower() in agentes:
                    atual = agentes[resto.lower()]
                else:
                    print("Agente não encontrado (veja /agentes).")
            elif comando == "/lembrar":
                cmd_lembrar(atual, resto, llm, agentes)
            elif comando == "/memorias":
                cmd_memorias(atual)
            elif comando == "/falsa":
                cmd_falsa(atual, resto)
            elif comando == "/sobre":
                cmd_sobre(atual, resto)
            elif comando == "/estado":
                print(atual.resumo())
            elif comando == "/tracos":
                cmd_tracos(atual, resto)
            elif comando == "/novo":
                cmd_novo(args.pasta, resto, agentes, args.slots)
            elif comando == "/turnos":
                turnos = max(1, int(resto)) if resto.isdigit() else turnos
                print(f"   Conversas entre agentes terão {turnos} falas.")
            elif comando == "/conversar":
                cmd_conversar(agentes, resto, turnos, llm)
            else:
                print("Comando desconhecido. Digite /ajuda.")
        except RuntimeError as erro:  # ex.: llama-server desligado
            print(f"\n[erro] {erro}")
        except (EOFError, KeyboardInterrupt):
            break

    for agente in agentes.values():
        agente.db.close()  # fecha limpo: os arquivos auxiliares do WAL (-wal/-shm) somem
    print("\nAté mais!")


if __name__ == "__main__":
    main()
