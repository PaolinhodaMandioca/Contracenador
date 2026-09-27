"""Testes determinísticos do Roteirista (roteirista.py): validação de JSON e materialização
da cena (WorldState + Atores), sem nenhuma chamada a LLM."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Ator import Ator
from mundo import abrir_mundo, buscar_evento_tipo, evidencias_do_evento
from roteirista import extrair_json, materializar_cena, validar_dados_cena


class TestExtrairJson(unittest.TestCase):

    def test_extrai_de_cercas_markdown(self):
        texto = '```json\n{"a": 1}\n```'
        self.assertEqual(extrair_json(texto), {"a": 1})

    def test_recorta_texto_ao_redor(self):
        texto = 'Aqui está: {"a": 1} obrigado!'
        self.assertEqual(extrair_json(texto), {"a": 1})

    def test_levanta_erro_sem_json(self):
        with self.assertRaises(ValueError):
            extrair_json("nada de json aqui")


class TestValidarDadosCena(unittest.TestCase):

    def test_exige_culpado_e_investigador(self):
        with self.assertRaises(ValueError):
            validar_dados_cena({"cena": "x", "atores": [{"nome": "A", "papel": "testemunha"}]})

    def test_preenche_padroes_de_objetivo_e_tracos(self):
        dados = {"cena": "x", "atores": [
            {"nome": "Joao", "papel": "culpado", "verdade": "v", "alibi": "a"},
            {"nome": "Ana", "papel": "investigador"},
        ]}
        validar_dados_cena(dados)
        self.assertIn("objetivo", dados["atores"][1])
        self.assertEqual(dados["atores"][0]["tracos"]["honestidade"], 0.5)


class TestMaterializarCena(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contracenador_roteirista_")
        self.pasta_atores = os.path.join(self.tmp, "atores")
        self.pasta_cenario = os.path.join(self.tmp, "cenario")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_grava_crime_e_evidencias_no_worldstate(self):
        dados = {
            "cena": "Uma joia sumiu",
            "atores": [
                {"nome": "Joao", "papel": "culpado", "verdade": "Joao pegou a joia.",
                 "alibi": "Estava no jardim.", "tracos": {}, "exemplos": []},
                {"nome": "Ana", "papel": "investigador", "objetivo": "Achar a joia",
                 "tracos": {}, "exemplos": []},
                {"nome": "Bia", "papel": "testemunha", "viu": "Vi Joao perto da vitrine.",
                 "tracos": {}, "exemplos": []},
            ],
        }
        validar_dados_cena(dados)
        materializar_cena(dados, pasta_atores=self.pasta_atores,
                          pasta_cenario=self.pasta_cenario, slots=2)

        mundo = abrir_mundo(os.path.join(self.pasta_cenario, "mundo.db"))
        try:
            evento = buscar_evento_tipo(mundo, "crime")
            self.assertIsNotNone(evento)
            self.assertEqual(evento["dados"]["proposicao"], "Joao pegou a joia.")

            evidencias = evidencias_do_evento(mundo, evento["id"])
            self.assertEqual(len(evidencias), 1)
            self.assertEqual(evidencias[0]["assunto"], "Joao")
            self.assertEqual(evidencias[0]["origem"], "Bia")
        finally:
            mundo.close()

    def test_culpado_e_investigador_ganham_objetivos_estruturados(self):
        dados = {
            "cena": "Uma joia sumiu",
            "atores": [
                {"nome": "Joao", "papel": "culpado", "verdade": "Joao pegou a joia.",
                 "alibi": "Estava no jardim.", "tracos": {}, "exemplos": []},
                {"nome": "Ana", "papel": "investigador", "objetivo": "Achar a joia",
                 "tracos": {}, "exemplos": []},
            ],
        }
        validar_dados_cena(dados)
        materializar_cena(dados, pasta_atores=self.pasta_atores,
                          pasta_cenario=self.pasta_cenario, slots=2)

        joao = Ator(os.path.join(self.pasta_atores, "joao.db"), slot=0)
        self.assertIsNotNone(joao.objetivo("Não ser descoberto"))

        ana = Ator(os.path.join(self.pasta_atores, "ana.db"), slot=0)
        self.assertIsNotNone(ana.objetivo("Achar a joia"))


if __name__ == "__main__":
    unittest.main()
