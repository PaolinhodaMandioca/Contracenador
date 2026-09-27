"""Testes determinísticos de utilidades de main.py (o orquestrador). Não testa os comandos
interativos inteiros (dependem de input()/terminal) - só a lógica pura que vale a pena isolar."""
import os
import json
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import ActorTestCase, FakeLLM

from contracenador.cli.main import (
    ServerManager,
    _consume_actor_name,
    cmd_scene,
    load_actors,
    parse_runtime_config,
    _run_private_dialogue,
    _record_revelation_evidence,
    _start_fresh_game,
    _suspect_candidates,
    _update_beliefs_from_evidence,
    check_local_environment,
)
from contracenador.simulation import (
    ConversationLane, TurnScheduler, run_parallel_pair_rounds, run_private_pair_round,
)
from contracenador.world import (
    evidence_for_event, find_event_by_type, open_world, register_evidence,
    register_event, register_location,
)
from contracenador.scenarios import materialize_scene


class TestConsumeActorName(ActorTestCase):
    """Bug real: /conversar só reconhecia nomes de uma palavra, mas o Roteirista sempre gera
    'Nome Sobrenome'. _consume_actor_name precisa casar nomes de várias palavras."""

    def setUp(self):
        super().setUp()
        self.actors = {
            "ana carvalho": self.create_actor("Ana Carvalho"),
            "joão silva": self.create_actor("João Silva"),
            "bia": self.create_actor("Bia"),
        }

    def test_recognizes_a_two_word_name(self):
        actor, rest = _consume_actor_name("Ana Carvalho João Silva o roubo", self.actors)
        self.assertEqual(actor.name, "Ana Carvalho")
        self.assertEqual(rest, "João Silva o roubo")

    def test_recognizes_a_one_word_name(self):
        actor, rest = _consume_actor_name("Bia o roubo", self.actors)
        self.assertEqual(actor.name, "Bia")
        self.assertEqual(rest, "o roubo")

    def test_chains_two_names_and_leaves_the_topic(self):
        a, rest = _consume_actor_name("Ana Carvalho João Silva o roubo da joia", self.actors)
        b, rest = _consume_actor_name(rest, self.actors)
        self.assertEqual(a.name, "Ana Carvalho")
        self.assertEqual(b.name, "João Silva")
        self.assertEqual(rest, "o roubo da joia")

    def test_ignores_accents_and_case(self):
        actor, rest = _consume_actor_name("joao silva o roubo", self.actors)
        self.assertEqual(actor.name, "João Silva")

    def test_does_not_match_an_unknown_name(self):
        actor, rest = _consume_actor_name("Fulano de Tal algo", self.actors)
        self.assertIsNone(actor)
        self.assertEqual(rest, "Fulano de Tal algo")


class TestRuntimeConfig(unittest.TestCase):

    def test_defaults_match_existing_run_behavior(self):
        config = parse_runtime_config([])

        self.assertEqual(config.investigation_rounds, 10)
        self.assertEqual(config.dialogue_rounds, 10)
        self.assertEqual(config.conversation_turns, 4)
        self.assertTrue(config.influence_enabled)
        self.assertEqual(config.context_actors, 4096)
        self.assertEqual(config.context_screenwriter, 8192)
        self.assertIsNone(config.theme)

    def test_cli_overrides_json_experiment_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "experiment.json")
            with open(path, "w", encoding="utf-8") as config_file:
                json.dump({
                    "investigation_rounds": 6,
                    "dialogue_rounds": 4,
                    "influence_enabled": False,
                    "influence_weight": 0.4,
                }, config_file)

            config = parse_runtime_config([
                "--config-json", path,
                "--rodadas-investigacao", "3",
                "--influencia-culpado",
            ])

        self.assertEqual(config.investigation_rounds, 3)
        self.assertEqual(config.dialogue_rounds, 4)
        self.assertTrue(config.influence_enabled)
        self.assertEqual(config.influence_weight, 0.4)

    def test_cli_can_set_theme_and_show_effective_config(self):
        config = parse_runtime_config([
            "--tema", "Uma joia desapareceu",
            "--mostrar-config",
            "--conversas-simultaneas", "1",
        ])

        self.assertEqual(config.theme, "Uma joia desapareceu")
        self.assertTrue(config.show_config)
        self.assertEqual(config.simultaneous_conversations, 1)

    def test_invalid_experiment_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "invalid.json")
            with open(path, "w", encoding="utf-8") as config_file:
                json.dump({"deduction_threshold": 1.5}, config_file)

            with self.assertRaises(SystemExit):
                parse_runtime_config(["--config-json", path])


class TestServerManagerStartup(unittest.TestCase):

    def test_gpu_fit_is_automatic_unless_layer_limit_is_set(self):
        default_server = ServerManager("org/model", 18080)
        custom_server = ServerManager("org/model", 18081, gpu_layers=12)

        default_command = default_server._build_cmd()
        custom_command = custom_server._build_cmd()

        self.assertEqual(default_command[default_command.index("--fit") + 1], "on")
        self.assertNotIn("-ngl", default_command)
        self.assertEqual(custom_command[custom_command.index("-ngl") + 1], "12")

    def test_negative_gpu_layer_count_is_rejected(self):
        server = ServerManager("org/model", 18080, gpu_layers=-1)
        with self.assertRaisesRegex(RuntimeError, "camadas GPU inválido"):
            server._validate_startup()

    def test_missing_executable_has_actionable_error(self):
        server = ServerManager("org/model.gguf", 18080)
        with patch("contracenador.cli.main.os.path.isfile", return_value=True), \
             patch("contracenador.cli.main.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "não encontrado no PATH"):
                server.start()

    def test_missing_local_model_fails_before_starting_process(self):
        server = ServerManager("missing-model.gguf", 18080)
        with patch("contracenador.cli.main.os.path.isfile", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Arquivo de modelo não encontrado"):
                server.start()

    def test_port_in_use_has_actionable_error(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = ServerManager("org/model", port)
            with patch("contracenador.cli.main.shutil.which", return_value="llama-server"):
                with self.assertRaisesRegex(RuntimeError, f"porta {port} já está em uso"):
                    server.start()

    def test_startup_failure_stops_child_process(self):
        server = ServerManager("org/model", 18080)
        process = Mock()
        process.poll.return_value = None
        with patch.object(server, "_validate_startup"), \
               patch("contracenador.llm.model_manager.subprocess.Popen", return_value=process), \
             patch.object(server, "_wait_ready", side_effect=RuntimeError("startup failed")), \
             patch.object(server, "_read_log_tail", return_value="model load failed"):
            with self.assertRaisesRegex(RuntimeError, "model load failed"):
                server.start()

        process.terminate.assert_called_once()
        self.assertIsNone(server._proc)

    def test_environment_failure_preserves_existing_save_files(self):
        with tempfile.TemporaryDirectory() as temp:
            actors_folder = os.path.join(temp, "atores")
            scenario_folder = os.path.join(temp, "cenario")
            os.makedirs(actors_folder)
            os.makedirs(scenario_folder)
            actor_file = os.path.join(actors_folder, "ana.db")
            scene_file = os.path.join(scenario_folder, "cena.json")
            with open(actor_file, "w", encoding="utf-8") as file:
                file.write("existing actor save")
            with open(scene_file, "w", encoding="utf-8") as file:
                file.write("existing scene save")

            actors_server = Mock(model="org/actors", port=8080, gpu_layers=None)
            actors_server._validate_startup.side_effect = RuntimeError("modelo inválido")
            screenwriter_server = Mock(model="org/writer", port=8081, gpu_layers=None)
            screenwriter_server._validate_startup.return_value = None

            with patch("contracenador.cli.main.shutil.which", return_value="llama-server.exe"), \
                 self.assertRaisesRegex(RuntimeError, "nenhum save foi apagado"):
                check_local_environment(
                    actors_server, screenwriter_server, actors_folder, scenario_folder,
                )

            with open(actor_file, "r", encoding="utf-8") as file:
                self.assertEqual(file.read(), "existing actor save")
            with open(scene_file, "r", encoding="utf-8") as file:
                self.assertEqual(file.read(), "existing scene save")

    def test_environment_check_rejects_shared_server_port(self):
        actors_server = Mock(model="org/actors", port=8080, gpu_layers=None)
        actors_server._validate_startup.return_value = None
        screenwriter_server = Mock(model="org/writer", port=8080, gpu_layers=None)
        screenwriter_server._validate_startup.return_value = None

        with tempfile.TemporaryDirectory() as temp, \
             patch("contracenador.cli.main.shutil.which", return_value="llama-server.exe"), \
             self.assertRaisesRegex(RuntimeError, "não podem usar a mesma porta"):
            check_local_environment(
                actors_server, screenwriter_server,
                os.path.join(temp, "atores"), os.path.join(temp, "cenario"),
            )


class TestPrivateDialogue(unittest.TestCase):

    def test_visits_each_suspect_before_starting_another_cycle(self):
        suspects = [SimpleNamespace(name=name) for name in ("Bia", "Caio", "Joao")]
        interrogated = {"Bia", "Caio"}

        self.assertEqual(_suspect_candidates(suspects, interrogated), ["Joao"])
        interrogated.add("Joao")
        self.assertEqual(_suspect_candidates(suspects, interrogated), ["Bia", "Caio", "Joao"])
        self.assertEqual(interrogated, set())

    def test_fresh_start_clears_storage_and_loads_14b_before_actors(self):
        with tempfile.TemporaryDirectory() as temp:
            actors_folder = os.path.join(temp, "atores")
            scenario_folder = os.path.join(temp, "cenario")
            os.makedirs(actors_folder)
            os.makedirs(scenario_folder)
            with open(os.path.join(actors_folder, "old.db"), "w") as old_db:
                old_db.write("old save")
            with open(os.path.join(scenario_folder, "cena.json"), "w") as old_scene:
                old_scene.write("old save")

            events = []
            actors_server = Mock()
            actors_server.stop.side_effect = lambda: events.append("actors_stop")
            actors_server.start.side_effect = lambda: events.append("actors_start")
            actors_server.url.return_value = "http://actors"
            screenwriter_server = Mock()
            screenwriter_server.start.side_effect = lambda: (
                self.assertEqual(os.listdir(actors_folder), []),
                self.assertEqual(os.listdir(scenario_folder), []),
                events.append("screenwriter_start"),
            )
            screenwriter_server.stop.side_effect = lambda: events.append("screenwriter_stop")
            screenwriter_server.url.return_value = "http://screenwriter"

            def generate_scene(llm, theme, **kwargs):
                self.assertEqual(theme, "tema novo")
                events.append("generate_scene")
                return {"scene": "nova"}

            def materialize(*args, **kwargs):
                events.append("materialize_scene")

            with patch("builtins.input", return_value="tema novo"), \
                 patch("contracenador.cli.main.generate_scene_llm", side_effect=generate_scene), \
                 patch("contracenador.cli.main.materialize_scene", side_effect=materialize), \
                 patch("contracenador.cli.main.LLM", return_value="actors_llm"):
                llm = _start_fresh_game(
                    actors_server, screenwriter_server,
                    actors_folder, scenario_folder, slots=2,
                )

            self.assertEqual(llm, "actors_llm")
            self.assertEqual(events, [
                "actors_stop", "screenwriter_start", "generate_scene", "materialize_scene",
                "screenwriter_stop", "actors_start",
            ])

    def test_interrogation_has_ten_private_exchanges(self):
        investigator = Mock()
        target = Mock()
        llm = Mock()
        investigator.name = "Ana"
        target.name = "Bia"
        investigator.open_conversation.return_value = {"from": "Ana", "text": "Pergunta inicial"}
        target.respond.side_effect = [
            {"from": "Bia", "target": "Ana", "text": f"Resposta {turn}"}
            for turn in range(1, 11)
        ]
        investigator.respond.side_effect = [
            {"from": "Ana", "target": "Bia", "text": f"Pergunta {turn}"}
            for turn in range(1, 10)
        ]
        exchanges = []

        rounds, stopped = _run_private_dialogue(
            investigator, target, "o incidente", llm,
            lambda question, answer, round_: exchanges.append((question, answer, round_)) or False,
        )

        self.assertEqual(rounds, 10)
        self.assertFalse(stopped)
        self.assertEqual(len(exchanges), 10)
        self.assertEqual(target.respond.call_count, 10)
        self.assertEqual(investigator.respond.call_count, 9)
        self.assertEqual(investigator.receive.call_count, 1)
        self.assertTrue(target.respond.call_args_list[-1].kwargs["last"])

    def test_private_pair_round_is_isolated_and_tracks_rounds(self):
        investigator = Mock()
        target = Mock()
        llm = Mock()
        investigator.name = "Ana"
        target.name = "Bia"
        investigator.open_conversation.return_value = {"from": "Ana", "text": "Pergunta inicial"}
        target.respond.side_effect = [
            {"from": "Bia", "target": "Ana", "text": f"Resposta {turn}"}
            for turn in range(1, 11)
        ]
        investigator.respond.side_effect = [
            {"from": "Ana", "target": "Bia", "text": f"Pergunta {turn}"}
            for turn in range(1, 10)
        ]

        result = run_private_pair_round(
            investigator,
            target,
            "o incidente",
            llm,
            on_exchange=lambda question, answer, round_number: False,
            rounds=10,
        )

        self.assertEqual(result["rounds"], 10)
        self.assertEqual(result["pair"], ("Ana", "Bia"))
        self.assertEqual(result["private_only"], True)
        self.assertEqual(target.respond.call_count, 10)
        self.assertEqual(investigator.respond.call_count, 9)

    def test_private_pair_round_reports_early_completion_count(self):
        initiator = Mock()
        respondent = Mock()
        initiator.name = "Ana"
        respondent.name = "Bia"
        initiator.open_conversation.return_value = {"from": "Ana", "text": "Pergunta inicial"}
        initiator.respond.return_value = None
        respondent.respond.return_value = {"from": "Bia", "target": "Ana", "text": "Resposta"}

        result = run_private_pair_round(
            initiator, respondent, "o incidente", Mock(),
            on_exchange=lambda question, answer, round_number: False,
            rounds=10,
        )

        self.assertEqual(result["rounds"], 1)
        self.assertEqual(respondent.respond.call_count, 1)
        self.assertEqual(initiator.respond.call_count, 1)

    def test_private_pair_round_rejects_nonpositive_round_count(self):
        with self.assertRaisesRegex(ValueError, "pelo menos 1"):
            run_private_pair_round(Mock(), Mock(), "tema", Mock(), lambda *_: False, rounds=0)


class TestRevelationEvidence(ActorTestCase):

    def setUp(self):
        super().setUp()
        self.world = open_world(os.path.join(self.tmp, "world.db"))
        register_location(self.world, "cena", public=False)

    def tearDown(self):
        self.world.close()
        super().tearDown()

    def test_revelation_creates_private_linked_evidence_once(self):
        envelope = {
            "from": "Bia",
            "target": "Ana",
            "facts": [{
                "text": "Joao estava perto da joia.",
                "source_id": 12,
                "subject": "Joao",
                "origin": "observation",
            }],
        }
        recorded = set()

        first_ids = _record_revelation_evidence(
            self.world, envelope, ["Ana", "Bia", "Joao"], recorded,
        )
        second_ids = _record_revelation_evidence(
            self.world, envelope, ["Ana", "Bia", "Joao"], recorded,
        )

        event = find_event_by_type(self.world, "revelation")
        evidence = evidence_for_event(self.world, event["id"])
        self.assertEqual(len(first_ids), 1)
        self.assertEqual(second_ids, [])
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["origin"], "Bia")
        self.assertEqual(evidence[0]["subject"], "Joao")
        self.assertAlmostEqual(evidence[0]["reliability"], 0.7)
        self.assertEqual(event["public"], 0)

    def test_belief_update_scales_with_evidence_reliability_and_deduplicates(self):
        investigator = self.create_actor("Ana")
        event_id, _ = register_event(
            self.world, "revelation", actor="Bia", target="Ana", location="cena", public=False,
        )
        evidence_id = register_evidence(
            self.world, event_id, "Joao estava perto da joia.",
            origin="Bia", subject="Joao", reliability=0.25, effect="supports",
        )
        processed = set()

        _update_beliefs_from_evidence(investigator, self.world, [evidence_id], processed)
        first_confidence = investigator.belief("Joao é o culpado")["confidence"]
        _update_beliefs_from_evidence(investigator, self.world, [evidence_id], processed)
        second_confidence = investigator.belief("Joao é o culpado")["confidence"]

        self.assertAlmostEqual(first_confidence, 0.55)
        self.assertEqual(second_confidence, first_confidence)


class TestTurnScheduler(unittest.TestCase):

    @staticmethod
    def make_agent(name):
        agent = Mock()
        agent.name = name
        agent.open_conversation.return_value = {"from": name, "text": "Vamos conversar."}
        agent.respond.return_value = {"from": name, "text": "Entendido."}
        return agent

    def test_generic_policy_schedules_pairs_and_stops_after_completed_turn(self):
        ana, bia, caio = [self.make_agent(name) for name in ("Ana", "Bia", "Caio")]
        agents = [ana, bia, caio]
        pairs = [(ana, bia), (bia, caio), (caio, ana)]
        selected = []
        exchanges = []

        def select_pair(available, turn, history):
            self.assertEqual(available, tuple(agents))
            self.assertEqual(len(history), turn - 1)
            selected.append(turn)
            return pairs[turn - 1]

        scheduler = TurnScheduler(agents, select_pair)
        history = scheduler.run(
            "planejar uma viagem", Mock(), max_turns=3, rounds_per_pair=1,
            on_exchange=lambda initiator, respondent, question, answer, turn, exchange: (
                exchanges.append((initiator.name, respondent.name, turn, exchange)) or False
            ),
            on_turn_complete=lambda result: result["turn"] == 2,
        )

        self.assertEqual(selected, [1, 2])
        self.assertEqual([(turn[0], turn[1]) for turn in exchanges], [("Ana", "Bia"), ("Bia", "Caio")])
        self.assertEqual([result["turn"] for result in history], [1, 2])
        self.assertTrue(all(result["private_only"] for result in history))

    def test_policy_can_schedule_multiple_private_pairs_in_one_turn(self):
        ana, bia, joao, caio = [
            self.make_agent(name) for name in ("Ana", "Bia", "Joao", "Caio")
        ]
        lanes = [
            ConversationLane(ana, bia, "investigação"),
            ConversationLane(joao, caio, "despistar"),
        ]
        scheduler = TurnScheduler([ana, bia, joao, caio], lambda *_: lanes)

        history = scheduler.run("cena", Mock(), max_turns=1, rounds_per_pair=1)

        self.assertEqual(history[0]["pairs"], [("Ana", "Bia"), ("Joao", "Caio")])
        self.assertEqual(len(history[0]["conversations"]), 2)
        self.assertTrue(history[0]["private_only"])

    def test_policy_cannot_schedule_agents_outside_the_simulation(self):
        first = self.make_agent("Ana")
        second = self.make_agent("Bia")
        outsider = self.make_agent("Caio")
        scheduler = TurnScheduler([first, second], lambda *_: (first, outsider))

        with self.assertRaisesRegex(ValueError, "não pertence à simulação"):
            scheduler.run("tema", Mock(), max_turns=1, rounds_per_pair=1)

    def test_parallel_pair_rounds_interleave_private_lanes(self):
        ana, bia, caio, dora = [
            self.make_agent(name) for name in ("Ana", "Bia", "Caio", "Dora")
        ]
        exchanges = []
        lanes = [
            ConversationLane(ana, bia, "o roubo"),
            ConversationLane(caio, dora, "o álibi"),
        ]

        results = run_parallel_pair_rounds(
            lanes,
            Mock(),
            rounds=2,
            on_exchange=lambda lane, question, answer, exchange: (
                exchanges.append((lane.initiator.name, lane.respondent.name, exchange)) or False
            ),
        )

        self.assertEqual(exchanges, [
            ("Ana", "Bia", 1), ("Caio", "Dora", 1),
            ("Ana", "Bia", 2), ("Caio", "Dora", 2),
        ])
        self.assertEqual([result["rounds"] for result in results], [2, 2])
        self.assertTrue(all(result["private_only"] for result in results))
        self.assertEqual(bia.respond.call_count, 2)
        self.assertEqual(dora.respond.call_count, 2)
        ana.receive.assert_called_once()
        caio.receive.assert_called_once()

    def test_parallel_pair_rounds_reject_shared_agent(self):
        ana, bia, caio = [self.make_agent(name) for name in ("Ana", "Bia", "Caio")]
        lanes = [
            ConversationLane(ana, bia, "tema 1"),
            ConversationLane(caio, ana, "tema 2"),
        ]

        with self.assertRaisesRegex(ValueError, "duas conversas simultâneas"):
            run_parallel_pair_rounds(lanes, Mock(), rounds=1)


class TestParallelInfluenceScene(unittest.TestCase):

    def test_guilty_can_seed_a_private_rumor_during_investigator_interrogation(self):
        scene = {
            "scene": "Uma joia desapareceu",
            "characters": [
                {"name": "Joao", "role": "guilty", "truth": "Joao pegou a joia.",
                 "alibi": "Joao ficou no jardim."},
                {"name": "Ana", "role": "investigator", "goal": "Descobrir quem pegou a joia."},
                {"name": "Bia", "role": "witness", "saw": "Vi Joao perto da joia."},
                {"name": "Caio", "role": "witness", "saw": None},
                {"name": "Dora", "role": "witness", "saw": None},
            ],
        }

        with tempfile.TemporaryDirectory() as temp:
            actors_folder = os.path.join(temp, "atores")
            scenario_folder = os.path.join(temp, "cenario")
            materialize_scene(scene, actors_folder, scenario_folder, slots=2)
            actors = load_actors(actors_folder, slots=2)
            actors["ana"].choose_investigation_target = lambda candidates, topic, llm, **kwargs: "Bia"
            try:
                with redirect_stdout(StringIO()):
                    cmd_scene(actors, "1", scenario_folder, FakeLLM())
                rumors = [
                    memory for memory in actors["caio"].list_memories()
                    if memory["origin"] == "Joao" and memory["about"] == "Dora"
                ]
                self.assertTrue(rumors, "Caio não guardou a suspeita privada plantada por Joao")
            finally:
                for actor in actors.values():
                    actor.db.close()


if __name__ == "__main__":
    unittest.main()
