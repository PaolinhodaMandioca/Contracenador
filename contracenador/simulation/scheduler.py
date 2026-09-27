"""Política independente para selecionar pares e avançar turnos."""
from .turn import (
    DEFAULT_PAIR_ROUNDS,
    ConversationLane,
    run_parallel_pair_rounds,
    run_private_pair_round,
)


class TurnScheduler:
    """Seleciona pares e executa turnos sem impor uma regra específica de cenário.

    `select_pair(agents, turn_number, history)` devolve dois agentes ou None para
    encerrar. `on_exchange` recebe `(initiator, respondent, question, answer,
    turn_number, exchange_number)`; `on_turn_complete` pode parar após um turno.
    """

    def __init__(self, agents, select_pair):
        self.agents = tuple(agents)
        self.select_pair = select_pair

    def run(self, topic, llm, max_turns, rounds_per_pair=DEFAULT_PAIR_ROUNDS,
            on_exchange=None, on_turn_complete=None):
        if max_turns < 1:
            raise ValueError("max_turns precisa ser pelo menos 1")
        if rounds_per_pair < 1:
            raise ValueError("rounds_per_pair precisa ser pelo menos 1")

        history = []
        for turn_number in range(1, max_turns + 1):
            selection = self.select_pair(self.agents, turn_number, tuple(history))
            if selection is None:
                break

            if isinstance(selection, ConversationLane):
                lanes = (selection,)
            elif isinstance(selection, list) and selection and all(
                    isinstance(item, ConversationLane) for item in selection):
                lanes = tuple(selection)
            else:
                lanes = None

            if lanes is not None:
                for lane in lanes:
                    if not any(lane.initiator is agent for agent in self.agents):
                        raise ValueError("O iniciador escolhido não pertence à simulação")
                    if not any(lane.respondent is agent for agent in self.agents):
                        raise ValueError("O respondente escolhido não pertence à simulação")

                def observe_lane(lane, question, answer, exchange_number):
                    if on_exchange is None:
                        return False
                    return on_exchange(
                        lane.initiator, lane.respondent, question, answer,
                        turn_number, exchange_number,
                    )

                conversations = run_parallel_pair_rounds(
                    lanes, llm, rounds=rounds_per_pair, on_exchange=observe_lane,
                )
                result = {
                    "turn": turn_number,
                    "pairs": [conversation["pair"] for conversation in conversations],
                    "conversations": conversations,
                    "private_only": True,
                    "stopped": any(conversation["stopped"] for conversation in conversations),
                }
                history.append(result)
                if result["stopped"]:
                    break
                if on_turn_complete is not None and on_turn_complete(result):
                    break
                continue

            if not isinstance(selection, (tuple, list)) or len(selection) != 2:
                raise ValueError("A política deve devolver um par de agentes ou None")

            initiator, respondent = selection
            if initiator is respondent:
                raise ValueError("Um agente não pode interagir consigo mesmo")
            if not any(initiator is agent for agent in self.agents):
                raise ValueError("O iniciador escolhido não pertence à simulação")
            if not any(respondent is agent for agent in self.agents):
                raise ValueError("O respondente escolhido não pertence à simulação")

            def observe_exchange(question, answer, exchange_number):
                if on_exchange is None:
                    return False
                return on_exchange(
                    initiator, respondent, question, answer,
                    turn_number, exchange_number,
                )

            result = run_private_pair_round(
                initiator, respondent, topic, llm,
                on_exchange=observe_exchange,
                rounds=rounds_per_pair,
            )
            result["turn"] = turn_number
            history.append(result)

            if result["stopped"]:
                break
            if on_turn_complete is not None and on_turn_complete(result):
                break

        return history