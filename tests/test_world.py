"""Testes determinísticos do WorldState (world.py): eventos, evidências e percepção por local."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world import (evidence_by_origin, evidence_for_event, event_truth, find_event_by_type,
                   open_world, position, register_evidence, register_event, register_location)


class TestWorld(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contracenador_world_")
        self.world = open_world(os.path.join(self.tmp, "world.db"))

    def tearDown(self):
        self.world.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_private_event_does_not_generate_automatic_witnesses(self):
        register_location(self.world, "cena", public=False)
        position(self.world, "Joao", "cena", role="guilty")
        position(self.world, "Bia", "cena", role="witness")

        event_id, witnesses = register_event(
            self.world, "crime", actor="Joao", location="cena",
            data={"proposition": "Joao roubou o dinheiro."}, public=False)

        self.assertEqual(witnesses, [])
        self.assertEqual(event_truth(self.world, event_id), "Joao roubou o dinheiro.")

    def test_public_event_generates_witnesses_by_location(self):
        position(self.world, "Joao", "sala")
        position(self.world, "Maria", "sala")
        position(self.world, "Carlos", "cozinha")  # em outro local: não percebe

        _, witnesses = register_event(
            self.world, "movement", actor="Joao", location="sala",
            data={"proposition": "Joao andou pela sala."})

        self.assertEqual(sorted(witnesses), ["Maria"])

    def test_evidence_links_to_an_event_and_a_subject(self):
        event_id, _ = register_event(
            self.world, "crime", actor="Joao", location="cena",
            data={"proposition": "Joao roubou."}, public=False)
        evidence_id = register_evidence(self.world, event_id, "Vi Joao perto do cofre.",
                                        origin="Bia", subject="Joao")

        evidence = evidence_for_event(self.world, event_id)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["id"], evidence_id)
        self.assertEqual(evidence[0]["subject"], "Joao")

        by_origin = evidence_by_origin(self.world, "Bia")
        self.assertEqual(len(by_origin), 1)
        self.assertEqual(by_origin[0]["origin"], "Bia")

    def test_find_event_by_type(self):
        register_event(self.world, "crime", actor="Joao", data={"proposition": "x"}, public=False)
        self.assertEqual(find_event_by_type(self.world, "crime")["type"], "crime")
        self.assertIsNone(find_event_by_type(self.world, "inexistente"))


if __name__ == "__main__":
    unittest.main()
