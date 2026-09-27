"""
batch_simulation.py - roda muitas cenas de investigação "de cabeça" (sem terminal, sem LLM de
verdade) e imprime estatísticas agregadas. Serve para calibrar os pesos de choose_action()/
choose_tactic() por número, em vez de só no olho rodando uma cena de cada vez.

ATENÇÃO - não é um teste automatizado (não faz parte do `unittest discover`): é uma ferramenta
de calibração. Reimplementa uma versão simplificada e NÃO-interativa do loop de `/cena`
(main.cmd_scene) porque cmd_scene foi escrito para o terminal (usa input()/print() a cada
rodada). Se a lógica de decisão do jogo mudar (choose_action, crenças do investigador,
condição de vitória), mantenha esta simulação em mente - ela pode ficar desatualizada.

Uso:
    python tests/batch_simulation.py --n 200
    python tests/batch_simulation.py --n 1000 --rodadas 10
"""
import argparse
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actor import Actor, DEFAULT_TRAITS, create_actor
from world import (evidence_by_origin, open_world, position, register_evidence,
                   register_event, register_location)


class DeafMuteLLM:
    """LLM falso: a estatística só depende das decisões em código, o texto não importa."""

    def generate(self, *args, **kwargs):
        return "..."

    def embedding(self, text):
        # Sem --embeddings: os Atores caem sozinhos pra busca lexical (ver Actor._recall_best).
        raise RuntimeError("DeafMuteLLM não suporta embeddings")


def _create_actor(folder, name, traits):
    path = os.path.join(folder, f"{name.lower()}.db")
    create_actor(path, name, "personagem de teste", ["..."], {**DEFAULT_TRAITS, **traits})
    actor = Actor(path, slot=0)
    actor.verbose = False  # 1000 cenas com log de decisão linha a linha seria ilegível
    return actor


def _traits_around(base):
    """Variação aleatoria em torno de um perfil-base, usando o `random` global (já semeado
    pelo chamador) - garante que cada cena simulada seja reproduzível pela sua semente."""
    return {k: min(1.0, max(0.0, v + random.uniform(-0.2, 0.2))) for k, v in base.items()}


def build_scene(tmp, seed):
    """Cria um cenário sintético de 5 personagens (1 culpado, 1 investigador, 3 testemunhas)
    com traços aleatórios (mas plausíveis) em torno de perfis fixos."""
    folder = os.path.join(tmp, f"scene_{seed}")
    actors_folder = os.path.join(folder, "atores")
    os.makedirs(actors_folder, exist_ok=True)
    world = open_world(os.path.join(folder, "world.db"))
    register_location(world, "cena", public=False)

    truth = "Joao pegou a joia as 21:30."
    guilty_traits = _traits_around({"honesty": 0.2, "deceit": 0.8, "empathy": 0.3,
                                    "courage": 0.6, "aggressiveness": 0.5, "greed": 0.7})
    joao = _create_actor(actors_folder, "Joao", guilty_traits)
    memory_id = joao.remember(truth, sensitivity=0.9, shareable=1)
    joao.set_false_version(memory_id, "Joao estava no jardim o tempo todo.")
    joao.form_goal("Não ser descoberto", priority=0.9, risk=0.9)
    position(world, "Joao", "cena", role="guilty")
    crime_event_id, _ = register_event(world, "crime", actor="Joao", location="cena",
                                       data={"proposition": truth}, public=False)

    investigator_traits = _traits_around({"honesty": 0.7, "deceit": 0.2,
                                          "empathy": 0.5, "courage": 0.6,
                                          "aggressiveness": 0.4, "greed": 0.3})
    ana = _create_actor(actors_folder, "Ana", investigator_traits)
    ana.form_goal("Descobrir quem pegou a joia", priority=0.9)
    position(world, "Ana", "cena", role="investigator")

    witnesses = []
    for name, saw in zip(("Bia", "Caio", "Dora"),
                         ("Vi Joao perto da vitrine da joia.", None, "Vi Joao saindo as pressas.")):
        traits = _traits_around({"honesty": 0.6, "deceit": 0.2, "empathy": 0.5,
                                 "courage": 0.5, "aggressiveness": 0.2, "greed": 0.2})
        actor = _create_actor(actors_folder, name, traits)
        position(world, name, "cena", role="witness")
        if saw:
            actor.remember(saw, origin="observation", sensitivity=0.6, shareable=1, about="Joao")
            register_evidence(world, crime_event_id, saw, origin=name, subject="Joao")
        witnesses.append(actor)

    # Registro de presença (ver cmd_scene em main.py): todo mundo "conhece" todo mundo, o que
    # dá candidatos a bode expiatório pra ação DEFLECT.
    present = [joao, ana] + witnesses
    for a in present:
        for b in present:
            if a is not b:
                a.relationship(b.name)

    return joao, ana, witnesses, world, truth


def simulate_one_scene(seed, max_rounds):
    """Reproduz o núcleo de decisão de `/cena` sem terminal. Devolve um dict com o desfecho."""
    random.seed(seed)
    tmp = tempfile.mkdtemp(prefix="contracenador_lote_")
    try:
        joao, ana, witnesses, world, truth = build_scene(tmp, seed)
        suspects = witnesses + [joao]
        for s in suspects:
            ana.new_conversation(s.name)
            s.new_conversation(ana.name)

        llm = DeafMuteLLM()
        lies, winner, rounds_used = 0, "guilty_escaped", max_rounds

        for round_ in range(1, max_rounds + 1):
            # Mesmo mecanismo do jogo de verdade (main.cmd_scene): quem interrogar é decisão do
            # investigador via choose_investigation_target(). Como o LLM aqui é surdo-mudo, a
            # resposta nunca casa com um nome e o código sempre cai no fallback por pontuação -
            # o que é exatamente o comportamento que queremos calibrar.
            target_name = ana.choose_investigation_target([s.name for s in suspects], truth, llm)
            target = next(s for s in suspects if s.name == target_name)

            question_env = ana.open_conversation(target.name, truth, llm)
            answer_env = target.respond(question_env, truth, llm)
            ana.receive(answer_env)

            if target is joao:
                for f in answer_env.get("facts", []):
                    if f["text"] != truth:
                        lies += 1

            for ev in evidence_by_origin(world, target.name):
                if ev["subject"] and ev["subject"] != ana.name:
                    ana.update_belief(f"{ev['subject']} é o culpado", delta=0.2,
                                      origin=target.name, subject=ev["subject"],
                                      evidence=f"evidence:{ev['id']}")

            for other in suspects:
                if other is not target:
                    other.receive(answer_env)

            memories = [m["text"].strip() for m in ana.list_memories()]
            if truth in memories:
                winner, rounds_used = "confession", round_
                break
            belief = ana.belief(f"{joao.name} é o culpado")
            if belief and belief["confidence"] >= 0.75:
                winner, rounds_used = "deduction", round_
                break

        return {"winner": winner, "rounds": rounds_used, "lies": lies,
                "contradictions": len(ana.contradictions())}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200, help="número de cenas simuladas (padrão: 200)")
    ap.add_argument("--rodadas", type=int, default=15, help="teto de rodadas por cena (padrão: 15)")
    args = ap.parse_args()

    results = [simulate_one_scene(seed=i, max_rounds=args.rodadas) for i in range(args.n)]

    n = len(results)
    confessions = sum(1 for r in results if r["winner"] == "confession")
    deductions = sum(1 for r in results if r["winner"] == "deduction")
    escapes = sum(1 for r in results if r["winner"] == "guilty_escaped")
    avg_rounds = sum(r["rounds"] for r in results) / n
    avg_lies = sum(r["lies"] for r in results) / n
    avg_contradictions = sum(r["contradictions"] for r in results) / n

    print(f"\n=== Estatísticas de {n} cenas simuladas ===")
    print(f"Investigador venceu por confissão: {confessions} ({confessions/n:.1%})")
    print(f"Investigador venceu por dedução:   {deductions} ({deductions/n:.1%})")
    print(f"Culpado escapou por exaustão:      {escapes} ({escapes/n:.1%})")
    print(f"Média de rodadas até o fim:        {avg_rounds:.1f}")
    print(f"Média de mentiras do culpado:      {avg_lies:.1f}")
    print(f"Média de contradições pegas:       {avg_contradictions:.1f}")


if __name__ == "__main__":
    main()
