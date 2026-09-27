"""Testes determinísticos do WorldState (mundo.py): eventos, evidências e percepção por local."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mundo import (abrir_mundo, buscar_evento_tipo, evidencias_do_evento,
                    evidencias_por_origem, posicionar, registrar_evento, registrar_evidencia,
                    registrar_local, verdade_evento)


class TestMundo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contracenador_mundo_")
        self.mundo = abrir_mundo(os.path.join(self.tmp, "mundo.db"))

    def tearDown(self):
        self.mundo.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_evento_privado_nao_gera_testemunhas_automaticas(self):
        registrar_local(self.mundo, "cena", publico=False)
        posicionar(self.mundo, "Joao", "cena", papel="culpado")
        posicionar(self.mundo, "Bia", "cena", papel="testemunha")

        id_evento, testemunhas = registrar_evento(
            self.mundo, "crime", ator="Joao", local="cena",
            dados={"proposicao": "Joao roubou o dinheiro."}, publico=False)

        self.assertEqual(testemunhas, [])
        self.assertEqual(verdade_evento(self.mundo, id_evento), "Joao roubou o dinheiro.")

    def test_evento_publico_gera_testemunhas_por_local(self):
        posicionar(self.mundo, "Joao", "sala")
        posicionar(self.mundo, "Maria", "sala")
        posicionar(self.mundo, "Carlos", "cozinha")  # em outro local: não percebe

        _, testemunhas = registrar_evento(
            self.mundo, "movimento", ator="Joao", local="sala",
            dados={"proposicao": "Joao andou pela sala."})

        self.assertEqual(sorted(testemunhas), ["Maria"])

    def test_evidencia_liga_a_um_evento_e_a_um_assunto(self):
        id_evento, _ = registrar_evento(
            self.mundo, "crime", ator="Joao", local="cena",
            dados={"proposicao": "Joao roubou."}, publico=False)
        id_ev = registrar_evidencia(self.mundo, id_evento, "Vi Joao perto do cofre.",
                                    origem="Bia", assunto="Joao")

        evidencias = evidencias_do_evento(self.mundo, id_evento)
        self.assertEqual(len(evidencias), 1)
        self.assertEqual(evidencias[0]["id"], id_ev)
        self.assertEqual(evidencias[0]["assunto"], "Joao")

        por_origem = evidencias_por_origem(self.mundo, "Bia")
        self.assertEqual(len(por_origem), 1)
        self.assertEqual(por_origem[0]["origem"], "Bia")

    def test_buscar_evento_tipo(self):
        registrar_evento(self.mundo, "crime", ator="Joao", dados={"proposicao": "x"}, publico=False)
        self.assertEqual(buscar_evento_tipo(self.mundo, "crime")["tipo"], "crime")
        self.assertIsNone(buscar_evento_tipo(self.mundo, "inexistente"))


if __name__ == "__main__":
    unittest.main()
