"""Engine de cena que conecta uma política ao scheduler genérico."""
from .scheduler import TurnScheduler
from .turn import DEFAULT_PAIR_ROUNDS, run_private_pair_round


class SimulationEngine:
    def __init__(self, agents, select_pair):
        self.scheduler = TurnScheduler(agents, select_pair)

    def run(self, topic, llm, max_turns, rounds_per_pair=DEFAULT_PAIR_ROUNDS,
            on_exchange=None, on_turn_complete=None):
        return self.scheduler.run(
            topic,
            llm,
            max_turns=max_turns,
            rounds_per_pair=rounds_per_pair,
            on_exchange=on_exchange,
            on_turn_complete=on_turn_complete,
        )