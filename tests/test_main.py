"""Testes determinísticos de utilidades de main.py (o orquestrador). Não testa os comandos
interativos inteiros (dependem de input()/terminal) - só a lógica pura que vale a pena isolar."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apoio import TesteComAtores

from main import _consumir_nome_ator


class TestConsumirNomeAtor(TesteComAtores):
    """Bug real: /conversar só reconhecia nomes de uma palavra, mas o Roteirista sempre gera
    'Nome Sobrenome'. _consumir_nome_ator precisa casar nomes de várias palavras."""

    def setUp(self):
        super().setUp()
        self.atores = {
            "ana carvalho": self.criar_ator("Ana Carvalho"),
            "joão silva": self.criar_ator("João Silva"),
            "bia": self.criar_ator("Bia"),
        }

    def test_reconhece_nome_de_duas_palavras(self):
        ator, resto = _consumir_nome_ator("Ana Carvalho João Silva o roubo", self.atores)
        self.assertEqual(ator.nome, "Ana Carvalho")
        self.assertEqual(resto, "João Silva o roubo")

    def test_reconhece_nome_de_uma_palavra(self):
        ator, resto = _consumir_nome_ator("Bia o roubo", self.atores)
        self.assertEqual(ator.nome, "Bia")
        self.assertEqual(resto, "o roubo")

    def test_encadeia_dois_nomes_e_sobra_o_topico(self):
        a, resto = _consumir_nome_ator("Ana Carvalho João Silva o roubo da joia", self.atores)
        b, resto = _consumir_nome_ator(resto, self.atores)
        self.assertEqual(a.nome, "Ana Carvalho")
        self.assertEqual(b.nome, "João Silva")
        self.assertEqual(resto, "o roubo da joia")

    def test_ignora_acentos_e_maiuscula(self):
        ator, resto = _consumir_nome_ator("joao silva o roubo", self.atores)
        self.assertEqual(ator.nome, "João Silva")

    def test_nao_casa_nome_desconhecido(self):
        ator, resto = _consumir_nome_ator("Fulano de Tal algo", self.atores)
        self.assertIsNone(ator)
        self.assertEqual(resto, "Fulano de Tal algo")


if __name__ == "__main__":
    unittest.main()
