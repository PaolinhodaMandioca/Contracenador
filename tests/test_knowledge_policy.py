"""Regressões de conhecimento privado e confissão, sem inferência externa."""
import os
import sqlite3
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from support import ActorTestCase, FakeLLM
from contracenador.agents.agent import Actor
from contracenador.cli.main import (
    _record_revelation_evidence, _update_beliefs_from_evidence, cmd_scene, load_actors,
)
from contracenador.scenarios import materialize_scene
from contracenador.simulation.config import RuntimeConfig
from contracenador.world import open_world, register_event, register_evidence


class TestDeliveredKnowledge(ActorTestCase):
    def setUp(self):
        super().setUp()
        self.world = open_world(os.path.join(self.tmp, "world.db"))
        self.investigator = self.create_actor("Ana")

    def tearDown(self):
        self.investigator.db.close()
        self.world.close()
        super().tearDown()

    def deliver(self, text, effect="neutral", target="Ana", recorded=None):
        return _record_revelation_evidence(self.world, {
            "from": "Bia", "target": target,
            "facts": [{"text": text, "source_id": 1, "subject": "Joao",
                       "origin": "observation", "effect": effect}],
        }, ["Ana", "Bia", "Joao", "Caio"], recorded if recorded is not None else set())

    def test_only_the_delivered_testimony_affects_beliefs(self):
        event, _ = register_event(self.world, "crime", actor="Joao", public=False)
        hidden = register_evidence(self.world, event, "Bia viu o roubo.", "Bia",
                                   subject="Joao", effect="supports")
        ids = self.deliver("Joao estava junto da cadeira.", "supports")
        # Mesmo passar um ID global explicitamente não autoriza a leitura pelo ator.
        _update_beliefs_from_evidence(self.investigator, self.world, [hidden] + ids, set())
        belief = self.investigator.belief("Joao é o culpado")
        self.assertAlmostEqual(belief["confidence"], 0.64)
        self.assertEqual(len(belief["evidence"]), 1)
        self.assertNotIn("Bia viu o roubo", str(belief))

    def test_private_testimony_requires_delivery_to_each_recipient(self):
        recorded = set()
        private_ids = self.deliver("Joao saiu com o celular.", "supports", "Caio", recorded)
        _update_beliefs_from_evidence(self.investigator, self.world, private_ids, set())
        self.assertIsNone(self.investigator.belief("Joao é o culpado"))
        own_ids = self.deliver("Joao saiu com o celular.", "supports", "Ana", recorded)
        self.assertEqual(len(own_ids), 1)
        _update_beliefs_from_evidence(self.investigator, self.world, own_ids, set())
        self.assertAlmostEqual(self.investigator.belief("Joao é o culpado")["confidence"], 0.64)

    def test_neutral_testimony_does_not_incriminate_and_alibi_reduces_suspicion(self):
        ids = self.deliver("Joao tem um celular.")
        _update_beliefs_from_evidence(self.investigator, self.world, ids, set())
        self.assertIsNone(self.investigator.belief("Joao é o culpado"))
        self.investigator.form_belief("Joao é o culpado", confidence=0.7)
        ids = self.deliver("Joao estava comigo no jardim naquele horário.", "refutes")
        _update_beliefs_from_evidence(self.investigator, self.world, ids, set())
        self.assertAlmostEqual(self.investigator.belief("Joao é o culpado")["confidence"], 0.56)

    def test_repeating_testimony_in_another_scene_does_not_multiply_weight(self):
        for _ in range(3):
            ids = self.deliver("Joao saiu com o celular.", "supports")
            _update_beliefs_from_evidence(self.investigator, self.world, ids, set())
        self.assertAlmostEqual(self.investigator.belief("Joao é o culpado")["confidence"], 0.64)

    def test_actor_rejects_envelope_addressed_to_someone_else(self):
        self.investigator.receive({
            "from": "Bia", "target": "Caio", "tactic": "THREATEN",
            "facts": [{"text": "O celular está no depósito.", "effect": "supports"}],
            "accusations": [{"subject": "Joao", "weight": 0.9}],
        })
        self.assertEqual(self.investigator.list_memories(), [])
        self.assertEqual(self.investigator.list_beliefs(), [])
        self.assertEqual(self.investigator.relationship("Bia")["fear"], 0)

    def test_repeated_fact_does_not_keep_increasing_trust_or_favor(self):
        env = {"from": "Bia", "target": "Ana", "facts": [
            {"text": "A festa foi no salão.", "source_id": 1, "effect": "neutral"}]}
        self.investigator.receive(env)
        before = self.investigator.relationship("Bia")
        for _ in range(10):
            self.investigator.receive(env)
        after = self.investigator.relationship("Bia")
        self.assertAlmostEqual(after["trust"], before["trust"], places=4)
        self.assertAlmostEqual(after["favor_owed"], before["favor_owed"], places=4)

    def test_evidence_effect_survives_a_relay(self):
        env = {"from": "Bia", "target": "Ana", "facts": [
            {"text": "Joao estava no jardim comigo.", "source_id": 1,
             "subject": "Joao", "effect": "refutes"}]}
        self.investigator.receive(env)
        with patch("contracenador.agents.actions.random.random", return_value=0):
            reply = self.investigator.respond(
                {"from": "Caio", "target": "Ana", "text": "jardim", "facts": []},
                "jardim", FakeLLM(), last=True)
        self.assertEqual(reply["facts"][0]["effect"], "refutes")


class TestConfessionPolicy(ActorTestCase):
    def make_suspect(self, **traits):
        actor = self.create_actor("Tiago", traits={
            "honesty": 0.8, "deceit": 1.0, "empathy": 0.6, "courage": 0.9, **traits})
        goal = actor.form_goal("Não ser descoberto", priority=0.9, risk=0.9)
        memory = actor.remember("Fui eu que peguei o celular.", origin="system", sensitivity=0.9,
                                about="Tiago", effect="supports", protected_by_goal=goal["id"])
        actor.set_false_version(memory, "Eu estava no jardim.")
        return actor, actor.list_memories()[0]

    def test_original_profile_does_not_confess_under_repeated_questions(self):
        actor, fact = self.make_suspect()
        with patch("contracenador.agents.actions.random.random", return_value=0):
            for _ in range(30):
                decision, _, chance = actor.choose_action(fact, "Ana")
                self.assertNotEqual(decision, "REVEAL")
                self.assertEqual(chance, 0)
        actor.db.close()

    def test_new_conversation_and_reload_do_not_remove_goal_protection(self):
        actor, fact = self.make_suspect()
        path = actor.path
        for _ in range(3):
            actor.new_conversation("Ana")
            with patch("contracenador.agents.actions.random.random", return_value=0):
                self.assertNotEqual(actor.choose_action(fact, "Ana")[0], "REVEAL")
            actor.db.close()
            actor = Actor(path)
        actor.db.close()

    def test_repeated_alibi_does_not_create_unbounded_guilt_or_leak_truth(self):
        actor, fact = self.make_suspect()
        question = {"from": "Ana", "target": "Tiago", "text": "celular", "facts": []}
        with patch("contracenador.agents.actions.random.random", return_value=0):
            for _ in range(12):
                answer = actor.respond(question, "celular", FakeLLM(), last=True)
                self.assertNotIn(fact["text"], answer["text"])
                self.assertEqual(answer["facts"][0]["effect"], "refutes")
        self.assertAlmostEqual(actor.state("guilt"), 0.34, places=3)
        actor.db.close()

    def test_meaningful_emotional_change_can_enable_confession(self):
        actor, fact = self.make_suspect(honesty=1.0, deceit=0.0, courage=0.0)
        self.assertNotEqual(actor.choose_action(fact, "Ana")[0], "REVEAL")
        actor.change_relationship("Ana", trust=0.5, fear=1.0)
        actor.change_state("guilt", 1.0)
        self.assertEqual(actor.choose_action(fact, "Ana")[0], "REVEAL")
        # A memória própria protegida também precisa entrar na resposta, apesar de about=self.
        answer = actor.respond({"from": "Ana", "target": "Tiago", "text": "celular", "facts": []},
                               "celular", FakeLLM(), last=True)
        self.assertEqual(answer["facts"][0]["text"], fact["text"])
        actor.db.close()

    def test_completed_goal_no_longer_imposes_disclosure_cost(self):
        actor, fact = self.make_suspect()
        actor.choose_action(fact, "Ana")
        actor.update_goal("Não ser descoberto", status="done")
        with patch("contracenador.agents.actions.random.random", return_value=0):
            self.assertEqual(actor.choose_action(fact, "Ana")[0], "REVEAL")
        actor.db.close()

    def test_unprotected_stance_does_not_expire_after_four_questions(self):
        actor = self.create_actor("Bia")
        actor.remember("Vi algo no jardim.")
        fact = actor.list_memories()[0]
        with patch("contracenador.agents.actions.random.random", return_value=1):
            self.assertEqual(actor.choose_action(fact, "Ana")[0], "HIDE")
        with patch("contracenador.agents.actions.random.random", return_value=0) as draw:
            for _ in range(20):
                self.assertEqual(actor.choose_action(fact, "Ana")[0], "HIDE")
            draw.assert_not_called()
        actor.db.close()

    def test_legacy_objective_and_secret_are_migrated_without_losing_memories(self):
        actor = self.create_actor("Tiago")
        goal = actor.form_goal("Não ser descoberto", priority=0.9, risk=0.9)
        truth = actor.remember("Peguei o celular.", origin="system", sensitivity=0.9)
        actor.set_false_version(truth, "Fiquei no jardim.")
        actor.form_goal("Descobrir o caso", priority=0.5)
        actor.remember("Objetivo da investigação: Descobrir o caso", origin="system")
        actor.db.executescript("""
            CREATE TABLE legacy_memories AS SELECT id, text, timestamp, origin, shareable,
                sensitivity, false_version, about, source_id, contradictory, embedding FROM memories;
            DROP TABLE memories;
            ALTER TABLE legacy_memories RENAME TO memories;
        """)
        actor.db.execute("PRAGMA user_version=2")
        actor.db.close()
        migrated = Actor(actor.path)
        memories = migrated.list_memories()
        self.assertEqual(len(memories), 2)
        self.assertEqual(memories[0]["protected_by_goal"], goal["id"])
        self.assertEqual(memories[1]["kind"], "goal")
        self.assertEqual(memories[1]["shareable"], 0)
        self.assertNotIn(memories[1], migrated.recall("objetivo", shareable_only=True))
        migrated.db.close()


class TestKnowledgeIntegration(ActorTestCase):
    def test_scene_uses_delivered_knowledge_and_respects_secret_policy(self):
        scene = {"scene": "Um celular sumiu na festa", "characters": [
            {"name": "Tiago", "role": "guilty", "truth": "Peguei o celular e escondi na caixa.",
             "alibi": "Fiquei no jardim.", "traits": {"honesty": 0.8, "deceit": 1.0,
                                                       "empathy": 0.6, "courage": 0.9}},
            {"name": "Ana", "role": "investigator", "goal": "Encontrar o celular"},
            {"name": "Bia", "role": "witness", "saw": "Vi Tiago esconder o celular na caixa.",
             "saw_effect": "supports"},
        ]}
        for target in ("Bia", "Tiago"):
            with self.subTest(target=target):
                folder = os.path.join(self.tmp, target)
                actors_folder, scenario_folder = os.path.join(folder, "atores"), os.path.join(folder, "cenario")
                with redirect_stdout(StringIO()):
                    materialize_scene(scene, actors_folder, scenario_folder)
                actors = load_actors(actors_folder, slots=2)
                actors["ana"].choose_investigation_target = lambda *args, **kwargs: target
                actors["bia"].respond = lambda *args, **kwargs: {
                    "from": "Bia", "target": "Ana", "text": "Tiago foi à festa.", "tactic": "NONE",
                    "facts": [{"text": "Tiago foi à festa.", "subject": "Tiago",
                               "source_id": 2, "origin": "observation", "effect": "neutral"}]}
                for actor in actors.values():
                    actor.choose_tactic = lambda other: "ASK"
                config = RuntimeConfig(model_actors="fake", model_screenwriter="fake", influence_enabled=False)
                output = StringIO()
                try:
                    with redirect_stdout(output), patch("contracenador.agents.actions.random.random", return_value=0):
                        cmd_scene(actors, "1", scenario_folder, FakeLLM(), config=config)
                    self.assertNotIn("VITÓRIA DA INVESTIGAÇÃO", output.getvalue())
                    belief = actors["ana"].belief("Tiago é o culpado")
                    self.assertTrue(belief is None or belief["confidence"] < 0.5)
                    memories = str(actors["ana"].list_memories())
                    self.assertNotIn("esconder o celular na caixa", memories)
                    self.assertNotIn("Peguei o celular", memories)
                finally:
                    for actor in actors.values():
                        actor.db.close()

    def test_old_world_migration_keeps_unclassified_evidence_neutral(self):
        path = os.path.join(self.tmp, "old_world.db")
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE evidence (id INTEGER PRIMARY KEY, event_id INTEGER, type TEXT,
                content TEXT, origin TEXT, subject TEXT, reliability REAL, timestamp REAL);
            INSERT INTO evidence VALUES (1, 1, 'testimony', 'Joao estava no jardim.',
                'Bia', 'Joao', 0.7, 0);
            PRAGMA user_version=1;
        """)
        db.close()
        world = open_world(path)
        self.assertEqual(world.execute("SELECT effect FROM evidence WHERE id=1").fetchone()[0], "neutral")
        self.assertEqual(world.execute("PRAGMA user_version").fetchone()[0], 2)
        world.close()


if __name__ == "__main__":
    unittest.main()
