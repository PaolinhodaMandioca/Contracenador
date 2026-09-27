"""Testes determinísticos do banco de nomes (nomes.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nomes import PRIMEIROS_NOMES, SOBRENOMES, sortear_nomes


class TestSortearNomes(unittest.TestCase):

    def test_devolve_a_quantidade_pedida(self):
        self.assertEqual(len(sortear_nomes(5)), 5)
        self.assertEqual(len(sortear_nomes(3)), 3)

    def test_nomes_completos_sao_unicos(self):
        nomes = sortear_nomes(10)
        self.assertEqual(len(nomes), len(set(nomes)))

    def test_primeiro_nome_e_sobrenome_nao_se_repetem_entre_personagens(self):
        nomes = sortear_nomes(10)
        primeiros = [n.split()[0] for n in nomes]
        sobrenomes = [n.split()[1] for n in nomes]
        self.assertEqual(len(primeiros), len(set(primeiros)))
        self.assertEqual(len(sobrenomes), len(set(sobrenomes)))

    def test_formato_nome_sobrenome(self):
        for nome in sortear_nomes(5):
            partes = nome.split()
            self.assertEqual(len(partes), 2)
            self.assertIn(partes[0], PRIMEIROS_NOMES)
            self.assertIn(partes[1], SOBRENOMES)


if __name__ == "__main__":
    unittest.main()
