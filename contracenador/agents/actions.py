"""Políticas numéricas de ação e seleção de alvos para agentes."""
import math
import random

STANCE_PERSISTENCE = 3
INTERROGATION_FATIGUE = 0.12


def sigmoid(value):
    value = max(-30.0, min(30.0, value))
    return 1 / (1 + math.exp(-value))


def choose_scapegoat(agent, other):
    candidates = [row["other"] for row in
                  agent.db.execute("SELECT other FROM relationships WHERE other != ?", (other,))]
    return random.choice(candidates) if candidates else None


def choose_action(agent, fact, other):
    traits = agent.personality["traits"]
    relation = agent.relationship(other)
    guilt = agent.state("guilt")
    wants_to_reveal = (
        2.0 * relation["trust"]
        + 1.5 * traits["honesty"]
        + guilt
        + 3.0 * relation["fear"] * (1 - traits["courage"])
        + 0.8 * relation["favor_owed"]
        - 2.5 * fact["sensitivity"]
        - relation["distrust"]
    )
    reveal_chance = sigmoid(wants_to_reveal)

    key = (other, fact["id"])
    previous = agent.stances.get(key)
    if previous:
        previous_decision, previous_score, previous_extra, attempts = previous
        if abs(wants_to_reveal - previous_score) < 0.5 and attempts < STANCE_PERSISTENCE:
            agent.stances[key] = (previous_decision, previous_score, previous_extra, attempts + 1)
            return previous_decision, previous_extra, reveal_chance

    if random.random() < reveal_chance:
        decision, extra = "REVEAL", None
    else:
        goal = agent.main_goal()
        risks_deflecting = (
            goal is not None and goal["priority"] >= 0.7
            and traits["deceit"] >= 0.6 and traits["empathy"] < 0.5
            and fact["sensitivity"] >= 0.7
        )
        fake_target = choose_scapegoat(agent, other) if risks_deflecting else None
        if fake_target:
            decision, extra = "DEFLECT", {"fake_target": fake_target}
        else:
            lie_chance = (traits["deceit"] * (1 - traits["honesty"])
                          * (1 - 0.7 * guilt) * fact["sensitivity"])
            if fact["false_version"] and random.random() < lie_chance:
                decision, extra = "LIE", None
            else:
                decision, extra = "HIDE", None
    agent.stances[key] = (decision, wants_to_reveal, extra, 0)
    return decision, extra, reveal_chance


def choose_tactic(agent, other):
    traits = agent.personality["traits"]
    disposition = (2.0 * traits["aggressiveness"] + traits["greed"]
                   + 1.5 * (1 - traits["empathy"]))
    threat_chance = sigmoid(
        disposition * (1 + 1.4 * agent.state("frustration"))
        - 2.0 * agent.relationship(other)["trust"]
        - 5.7
    )
    tactic = "THREATEN" if random.random() < threat_chance else "ASK"
    agent._log(f"{agent.name} escolheu a tatica {tactic} (chance de ameacar: {threat_chance:.0%})")
    return tactic


def tactic_instruction(tactic, other, topic):
    if tactic == "THREATEN":
        return (f'Ameace {other} (dentro do jogo: parar de confiar, cortar a troca de '
                f'informações, contar aos outros que esconde coisas) para que conte o que '
                f'sabe sobre "{topic}".')
    return f'Pergunte a {other} o que sabe sobre "{topic}".'


def suspicion_score(agent, candidate):
    belief = agent.belief(f"{candidate} é o culpado")
    suspicion = belief["confidence"] if belief else 0.0
    fatigue = agent.interrogation_counts.get(candidate, 0)
    return (
        0.6 * suspicion
        + 0.4 * agent.relationship(candidate)["distrust"]
        + (0.3 if candidate not in agent.satisfied else 0.0)
        - INTERROGATION_FATIGUE * fatigue
    )


def choose_investigation_target(agent, candidates, topic, llm, detect_subject):
    scores = {candidate: suspicion_score(agent, candidate) for candidate in candidates}
    ranking = "\n".join(
        f"- {name}: suspeita {scores[name]:.2f}"
        + (" (já interrogado)" if name in agent.satisfied else "")
        for name in sorted(candidates, key=lambda candidate: -scores[candidate])
    )
    request = [
        {"role": "system", "content": (
            "Você é um investigador decidindo quem interrogar a seguir numa investigação. "
            "Responda SOMENTE com o nome exato de uma pessoa da lista, sem mais nada.")},
        {"role": "user", "content": (
            f"Objetivo da investigação: {topic}\n\n"
            f"Suspeitos e o quanto você já suspeita de cada um (0 a 1):\n{ranking}\n\n"
            "Quem você vai interrogar agora? Responda só com o nome.")},
    ]
    answer = llm.generate(request, max_tokens=20, temperature=0.3) or ""
    chosen = detect_subject(answer, candidates)
    if not chosen:
        chosen = max(candidates, key=lambda candidate: scores[candidate] + random.uniform(0.0, 0.01))
        agent._log(f"{agent.name} (fallback do código) decide interrogar {chosen}.")
    else:
        agent._log(f"{agent.name} (LLM) decide interrogar {chosen}.")
    agent.interrogation_counts[chosen] = agent.interrogation_counts.get(chosen, 0) + 1
    return chosen