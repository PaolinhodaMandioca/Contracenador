"""Testes determinísticos de utilidades de main.py (o orquestrador). Não testa os comandos
interativos inteiros (dependem de input()/terminal) - só a lógica pura que vale a pena isolar."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import ActorTestCase

from main import _consume_actor_name


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


if __name__ == "__main__":
    unittest.main()
