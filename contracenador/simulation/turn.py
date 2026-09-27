"""Execução de uma interação privada entre dois agentes."""
from dataclasses import dataclass

DEFAULT_PAIR_ROUNDS = 10


@dataclass
class ConversationLane:
    initiator: object
    respondent: object
    topic: str
    opener: object = None


def run_parallel_pair_rounds(lanes, llm, rounds=DEFAULT_PAIR_ROUNDS, on_exchange=None):
    """Avança conversas privadas independentes em rodadas intercaladas.

    As chamadas ao LLM continuam sequenciais; nenhum agente pode estar em duas linhas
    ativas no mesmo turno, evitando concorrência no estado SQLite e no histórico do ator.
    """
    lanes = tuple(lanes)
    if rounds < 1:
        raise ValueError("rounds precisa ser pelo menos 1")

    active_agents = set()
    sessions = []
    for lane in lanes:
        if lane.initiator is lane.respondent:
            raise ValueError("Um agente não pode interagir consigo mesmo")
        for agent in (lane.initiator, lane.respondent):
            if id(agent) in active_agents:
                raise ValueError("Um agente não pode participar de duas conversas simultâneas")
            active_agents.add(id(agent))
        if lane.opener is None:
            question = lane.initiator.open_conversation(lane.respondent.name, lane.topic, llm)
        else:
            question = lane.opener(lane.initiator, lane.respondent, lane.topic, llm)
        sessions.append({
            "lane": lane, "question": question, "rounds": 0,
            "done": False, "stopped": False,
        })

    for dialogue_round in range(1, rounds + 1):
        answers = []
        for session in sessions:
            if session["done"]:
                continue
            lane = session["lane"]
            answer = lane.respondent.respond(
                session["question"], lane.topic, llm, last=(dialogue_round == rounds),
            )
            session["rounds"] = dialogue_round
            answers.append((session, answer))

        stop_all = False
        if on_exchange is not None:
            for session, answer in answers:
                lane = session["lane"]
                stop_all = on_exchange(lane, session["question"], answer, dialogue_round) or stop_all

        for session, answer in answers:
            lane = session["lane"]
            if stop_all:
                lane.initiator.receive(answer)
                session["done"] = True
                session["stopped"] = True
            elif dialogue_round == rounds:
                lane.initiator.receive(answer)
                session["done"] = True
            else:
                next_question = lane.initiator.respond(answer, lane.topic, llm, last=False)
                if next_question is None:
                    session["done"] = True
                    session["stopped"] = True
                else:
                    session["question"] = next_question

        if stop_all or all(session["done"] for session in sessions):
            break

    return [
        {
            "pair": (session["lane"].initiator.name, session["lane"].respondent.name),
            "rounds": session["rounds"],
            "private_only": True,
            "stopped": session["stopped"],
        }
        for session in sessions
    ]


def run_private_pair_round(initiator, respondent, topic, llm, on_exchange,
                           rounds=DEFAULT_PAIR_ROUNDS):
    """Executa até `rounds` respostas privadas e devolve os metadados da interação."""
    if rounds < 1:
        raise ValueError("rounds precisa ser pelo menos 1")

    question = initiator.open_conversation(respondent.name, topic, llm)
    completed_rounds = 0

    for dialogue_round in range(1, rounds + 1):
        answer = respondent.respond(question, topic, llm, last=(dialogue_round == rounds))
        completed_rounds = dialogue_round

        if on_exchange(question, answer, dialogue_round):
            return {
                "pair": (initiator.name, respondent.name),
                "rounds": completed_rounds,
                "private_only": True,
                "stopped": True,
            }

        if dialogue_round == rounds:
            initiator.receive(answer)
            break

        question = initiator.respond(answer, topic, llm, last=False)
        if question is None:
            break

    return {
        "pair": (initiator.name, respondent.name),
        "rounds": completed_rounds,
        "private_only": True,
        "stopped": False,
    }