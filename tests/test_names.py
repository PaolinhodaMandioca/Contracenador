"""Testes determinísticos do banco de nomes (names.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracenador.agents.personality import FIRST_NAMES, LAST_NAMES, draw_names, generate_personality_profile
from contracenador.scenarios.generator import generate_world_seed


class TestDrawNames(unittest.TestCase):

    def test_returns_the_requested_amount(self):
        self.assertEqual(len(draw_names(5)), 5)
        self.assertEqual(len(draw_names(3)), 3)

    def test_full_names_are_unique(self):
        names = draw_names(10)
        self.assertEqual(len(names), len(set(names)))

    def test_first_and_last_names_do_not_repeat_among_characters(self):
        names = draw_names(10)
        first = [n.split()[0] for n in names]
        last = [n.split()[1] for n in names]
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(last), len(set(last)))

    def test_first_last_name_format(self):
        for name in draw_names(5):
            parts = name.split()
            self.assertEqual(len(parts), 2)
            self.assertIn(parts[0], FIRST_NAMES)
            self.assertIn(parts[1], LAST_NAMES)


class TestGeneratedRoleProfiles(unittest.TestCase):

    def test_generate_personality_profile_uses_code_defaults(self):
        profile = generate_personality_profile("guilty", name="Ana Silva", theme="joia roubada")
        self.assertEqual(profile["name"], "Ana Silva")
        self.assertIn("job", profile)
        self.assertIn("traits", profile)
        self.assertIn("speech", profile)
        self.assertGreater(profile["traits"]["deceit"], 0.55)
        self.assertIn(profile["job"], {"Joalheiro", "Caixeiro", "Contador", "Chaveiro", "Empresário"})

    def test_generate_world_seed_creates_structured_story_layout(self):
        seed = generate_world_seed("joia roubada em um hotel antigo")
        self.assertIn("location", seed)
        self.assertIn("conflict", seed)
        self.assertIn("jobs", seed)
        self.assertTrue(seed["jobs"])
        self.assertIn("tension", seed)


if __name__ == "__main__":
    unittest.main()
