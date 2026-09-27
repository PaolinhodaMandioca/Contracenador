"""
simular_lote.py - roda muitas cenas de investigação "de cabeça" (sem terminal, sem LLM de
verdade) e imprime estatísticas agregadas. Serve para calibrar os pesos de escolher_acao()/
escolher_tatica() por número, em vez de só no olho rodando uma cena de cada vez.

ATENÇÃO - não é um teste automatizado (não faz parte do `unittest discover`): é uma ferramenta
de calibração. Reimplementa uma versão simplificada e NÃO-interativa do loop de `/cena`
(main.cmd_cena) porque cmd_cena foi escrito para o terminal (usa input()/print() a cada
rodada). Se a lógica de decisão do jogo mudar (escolher_acao, crenças do investigador,
condição de vitória), mantenha esta simulação em mente - ela pode ficar desatualizada.

Uso:
    python tests/simular_lote.py --n 200
    python tests/simular_lote.py --n 1000 --rodadas 10
"""
import argparse
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Ator import Ator, TRACOS_PADRAO, criar_ator
from mundo import (abrir_mundo, evidencias_por_origem, posicionar, registrar_evento,
                    registrar_evidencia, registrar_local)


class LLMSurdoMudo:
    """LLM falso: a estatística só depende das decisões em código, o texto não importa."""

    def gerar(self, *args, **kwargs):
        return "..."


def _criar_ator(pasta, nome, tracos):
    caminho = os.path.join(pasta, f"{nome.lower()}.db")
    criar_ator(caminho, nome, "personagem de teste", ["..."], {**TRACOS_PADRAO, **tracos})
    ator = Ator(caminho, slot=0)
    ator.mostrar = False  # 1000 cenas com log de decisão linha a linha seria ilegível
    return ator


def _tracos_ao_redor_de(base):
    """Variação aleatoria em torno de um perfil-base, usando o `random` global (já semeado
    pelo chamador) - garante que cada cena simulada seja reproduzível pela sua semente."""
    return {k: min(1.0, max(0.0, v + random.uniform(-0.2, 0.2))) for k, v in base.items()}


def montar_cena(tmp, semente):
    """Cria um cenário sintético de 5 personagens (1 culpado, 1 investigador, 3 testemunhas)
    com traços aleatórios (mas plausíveis) em torno de perfis fixos."""
    pasta = os.path.join(tmp, f"cena_{semente}")
    pasta_atores = os.path.join(pasta, "atores")
    os.makedirs(pasta_atores, exist_ok=True)
    mundo = abrir_mundo(os.path.join(pasta, "mundo.db"))
    registrar_local(mundo, "cena", publico=False)

    verdade = "Joao pegou a joia as 21:30."
    tracos_culpado = _tracos_ao_redor_de({"honestidade": 0.2, "dissimulacao": 0.8, "empatia": 0.3,
                                          "coragem": 0.6, "agressividade": 0.5, "ganancia": 0.7})
    joao = _criar_ator(pasta_atores, "Joao", tracos_culpado)
    id_mem = joao.lembrar(verdade, sensibilidade=0.9, compartilhavel=1)
    joao.definir_versao_falsa(id_mem, "Joao estava no jardim o tempo todo.")
    joao.formar_objetivo("Não ser descoberto", prioridade=0.9, risco=0.9)
    posicionar(mundo, "Joao", "cena", papel="culpado")
    id_evento_crime, _ = registrar_evento(mundo, "crime", ator="Joao", local="cena",
                                          dados={"proposicao": verdade}, publico=False)

    tracos_investigador = _tracos_ao_redor_de({"honestidade": 0.7, "dissimulacao": 0.2,
                                               "empatia": 0.5, "coragem": 0.6,
                                               "agressividade": 0.4, "ganancia": 0.3})
    ana = _criar_ator(pasta_atores, "Ana", tracos_investigador)
    ana.formar_objetivo("Descobrir quem pegou a joia", prioridade=0.9)
    posicionar(mundo, "Ana", "cena", papel="investigador")

    testemunhas = []
    for nome, viu in zip(("Bia", "Caio", "Dora"),
                        ("Vi Joao perto da vitrine da joia.", None, "Vi Joao saindo as pressas.")):
        tracos = _tracos_ao_redor_de({"honestidade": 0.6, "dissimulacao": 0.2, "empatia": 0.5,
                                      "coragem": 0.5, "agressividade": 0.2, "ganancia": 0.2})
        ator = _criar_ator(pasta_atores, nome, tracos)
        posicionar(mundo, nome, "cena", papel="testemunha")
        if viu:
            ator.lembrar(viu, origem="observacao", sensibilidade=0.6, compartilhavel=1, sobre="Joao")
            registrar_evidencia(mundo, id_evento_crime, viu, origem=nome, assunto="Joao")
        testemunhas.append(ator)

    # Registro de presença (ver cmd_cena em main.py): todo mundo "conhece" todo mundo, o que
    # dá candidatos a bode expiatório pra ação DESVIAR.
    presentes = [joao, ana] + testemunhas
    for a in presentes:
        for b in presentes:
            if a is not b:
                a.relacao(b.nome)

    return joao, ana, testemunhas, mundo, verdade


def simular_uma_cena(semente, rodadas_max):
    """Reproduz o núcleo de decisão de `/cena` sem terminal. Devolve um dict com o desfecho."""
    random.seed(semente)
    tmp = tempfile.mkdtemp(prefix="contracenador_lote_")
    try:
        joao, ana, testemunhas, mundo, verdade = montar_cena(tmp, semente)
        suspeitos = testemunhas + [joao]
        for s in suspeitos:
            ana.nova_conversa(s.nome)
            s.nova_conversa(ana.nome)

        llm = LLMSurdoMudo()
        mentiras, vencedor, rodadas_usadas = 0, "culpado_escapou", rodadas_max

        for rodada in range(1, rodadas_max + 1):
            alvo = max(suspeitos, key=lambda s: (
                0.6 * (ana.crenca(f"{s.nome} é o culpado") or {}).get("confianca", 0.0)
                + 0.4 * ana.relacao(s.nome)["desconfianca"]
                + (0.4 if s.nome not in ana.satisfeitos else 0.0)
                + random.uniform(0.0, 0.2)
            ))

            env_pergunta = ana.abrir_conversa(alvo.nome, verdade, llm)
            env_resposta = alvo.responder(env_pergunta, verdade, llm)
            ana.receber(env_resposta)

            if alvo is joao:
                for f in env_resposta.get("fatos", []):
                    if f["texto"] != verdade:
                        mentiras += 1

            for ev in evidencias_por_origem(mundo, alvo.nome):
                if ev["assunto"] and ev["assunto"] != ana.nome:
                    ana.atualizar_crenca(f"{ev['assunto']} é o culpado", delta=0.2,
                                        origem=alvo.nome, assunto=ev["assunto"],
                                        evidencia=f"evidencia:{ev['id']}")

            for outro in suspeitos:
                if outro is not alvo:
                    outro.receber(env_resposta)

            memorias = [m["texto"].strip() for m in ana.listar_memorias()]
            if verdade in memorias:
                vencedor, rodadas_usadas = "confissao", rodada
                break
            crenca = ana.crenca(f"{joao.nome} é o culpado")
            if crenca and crenca["confianca"] >= 0.75:
                vencedor, rodadas_usadas = "deducao", rodada
                break

        return {"vencedor": vencedor, "rodadas": rodadas_usadas, "mentiras": mentiras,
                "contradicoes": len(ana.contradicoes())}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200, help="número de cenas simuladas (padrão: 200)")
    ap.add_argument("--rodadas", type=int, default=15, help="teto de rodadas por cena (padrão: 15)")
    args = ap.parse_args()

    resultados = [simular_uma_cena(semente=i, rodadas_max=args.rodadas) for i in range(args.n)]

    n = len(resultados)
    confissoes = sum(1 for r in resultados if r["vencedor"] == "confissao")
    deducoes = sum(1 for r in resultados if r["vencedor"] == "deducao")
    escapes = sum(1 for r in resultados if r["vencedor"] == "culpado_escapou")
    media_rodadas = sum(r["rodadas"] for r in resultados) / n
    media_mentiras = sum(r["mentiras"] for r in resultados) / n
    media_contradicoes = sum(r["contradicoes"] for r in resultados) / n

    print(f"\n=== Estatísticas de {n} cenas simuladas ===")
    print(f"Investigador venceu por confissão: {confissoes} ({confissoes/n:.1%})")
    print(f"Investigador venceu por dedução:   {deducoes} ({deducoes/n:.1%})")
    print(f"Culpado escapou por exaustão:      {escapes} ({escapes/n:.1%})")
    print(f"Média de rodadas até o fim:        {media_rodadas:.1f}")
    print(f"Média de mentiras do culpado:      {media_mentiras:.1f}")
    print(f"Média de contradições pegas:       {media_contradicoes:.1f}")


if __name__ == "__main__":
    main()
