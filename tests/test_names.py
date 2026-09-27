"""Testes determinísticos do banco de nomes (names.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from names import FIRST_NAMES, LAST_NAMES, draw_names


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


if __name__ == "__main__":
    unittest.main()
