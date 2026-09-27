"""Testes determinísticos da memória semântica (Ator.recordar_semantico / _recordar_melhor).
Usa um FakeLLM com embeddings fixos e conhecidos - sem LLM de verdade, mas exercitando a
matemática real de similaridade de cosseno."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apoio import FakeLLM, TesteComAtores

from Ator import _cosseno, _desserializar_vetor, _serializar_vetor


class FakeLLMComEmbeddings(FakeLLM):
    """Devolve embeddings de um dicionário fixo texto -> vetor, pra controlar exatamente a
    similaridade nos testes. Conta as chamadas pra verificar o cache preguiçoso."""

    def __init__(self, vetores, resposta="..."):
        super().__init__(resposta)
        self.vetores = vetores
        self.chamadas_embedding = 0

    def embedding(self, texto):
        self.chamadas_embedding += 1
        if texto not in self.vetores:
            raise KeyError(f"sem vetor de teste para: {texto!r}")
        return self.vetores[texto]


class TestSerializacaoDeVetor(unittest.TestCase):

    def test_serializar_e_desserializar_preserva_os_valores(self):
        vetor = [0.1, -0.5, 2.0, 0.0]
        recuperado = _desserializar_vetor(_serializar_vetor(vetor))
        for original, restaurado in zip(vetor, recuperado):
            self.assertAlmostEqual(original, restaurado, places=5)


class TestCosseno(unittest.TestCase):

    def test_vetores_iguais_tem_similaridade_1(self):
        self.assertAlmostEqual(_cosseno([1, 0], [1, 0]), 1.0)

    def test_vetores_ortogonais_tem_similaridade_0(self):
        self.assertAlmostEqual(_cosseno([1, 0], [0, 1]), 0.0)

    def test_vetor_nulo_nao_quebra(self):
        self.assertEqual(_cosseno([0, 0], [1, 1]), 0.0)


class TestRecordarSemantico(TesteComAtores):

    def test_prioriza_o_fato_mais_proximo_por_significado(self):
        joao = self.criar_ator("Joao")
        joao.lembrar("Vi Carlos saindo do escritorio.", compartilhavel=1)
        joao.lembrar("O jantar estava delicioso.", compartilhavel=1)

        vetores = {
            "quem estava perto do cofre?": [1.0, 0.0],
            "Vi Carlos saindo do escritorio.": [0.9, 0.1],  # próximo da consulta
            "O jantar estava delicioso.": [0.0, 1.0],       # nada a ver com a consulta
        }
        llm = FakeLLMComEmbeddings(vetores)

        resultado = joao.recordar_semantico("quem estava perto do cofre?", llm, k=1)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["texto"], "Vi Carlos saindo do escritorio.")

    def test_embedding_de_cada_memoria_e_calculado_uma_unica_vez(self):
        joao = self.criar_ator("Joao")
        joao.lembrar("Fato A", compartilhavel=1)
        vetores = {"consulta": [1.0, 0.0], "Fato A": [1.0, 0.0]}
        llm = FakeLLMComEmbeddings(vetores)

        joao.recordar_semantico("consulta", llm, k=1)
        self.assertEqual(llm.chamadas_embedding, 2)  # 1 da consulta + 1 do fato (1ª vez)

        joao.recordar_semantico("consulta", llm, k=1)
        self.assertEqual(llm.chamadas_embedding, 3)  # +1 só da consulta - fato já tem embedding

    def test_respeita_filtro_de_compartilhavel(self):
        joao = self.criar_ator("Joao")
        joao.lembrar("Segredo privado.", compartilhavel=0)
        vetores = {"consulta": [1.0, 0.0], "Segredo privado.": [1.0, 0.0]}
        llm = FakeLLMComEmbeddings(vetores)

        resultado = joao.recordar_semantico("consulta", llm, k=5, so_compartilhaveis=True)
        self.assertEqual(resultado, [])


class TestRecordarMelhorComFallback(TesteComAtores):
    """_recordar_melhor nunca pode quebrar uma conversa por causa de um servidor sem
    --embeddings - precisa cair para a busca lexical de sempre."""

    def test_cai_para_busca_lexical_sem_suporte_a_embedding(self):
        joao = self.criar_ator("Joao")
        joao.lembrar("Vi Carlos saindo do escritorio.", compartilhavel=1)
        llm = FakeLLM()  # não suporta embeddings (levanta RuntimeError)

        resultado = joao._recordar_melhor("escritorio", llm, k=1)
        self.assertEqual(len(resultado), 1)
        self.assertTrue(joao._sem_embedding)

    def test_nao_tenta_embedding_de_novo_apos_falhar_uma_vez(self):
        joao = self.criar_ator("Joao")
        joao.lembrar("Vi Carlos saindo do escritorio.", compartilhavel=1)
        llm = FakeLLM()
        joao._recordar_melhor("escritorio", llm, k=1)  # aqui já desiste (_sem_embedding=True)

        chamadas = []
        joao.recordar_semantico = lambda *a, **k: chamadas.append(1)
        joao._recordar_melhor("escritorio", llm, k=1)
        self.assertEqual(chamadas, [], "não deveria tentar recordar_semantico de novo")


if __name__ == "__main__":
    unittest.main()
