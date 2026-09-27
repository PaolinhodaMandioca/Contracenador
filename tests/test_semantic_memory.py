"""Testes determinísticos da memória semântica (Actor.recall_semantic / _recall_best).
Usa um FakeLLM com embeddings fixos e conhecidos - sem LLM de verdade, mas exercitando a
matemática real de similaridade de cosseno."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import ActorTestCase, FakeLLM

from contracenador.agents.agent import _cosine_similarity, _deserialize_vector, _serialize_vector


class FakeLLMWithEmbeddings(FakeLLM):
    """Devolve embeddings de um dicionário fixo texto -> vetor, pra controlar exatamente a
    similaridade nos testes. Conta as chamadas pra verificar o cache preguiçoso."""

    def __init__(self, vectors, response="..."):
        super().__init__(response)
        self.vectors = vectors
        self.embedding_calls = 0

    def embedding(self, text):
        self.embedding_calls += 1
        if text not in self.vectors:
            raise KeyError(f"sem vetor de teste para: {text!r}")
        return self.vectors[text]


class TestVectorSerialization(unittest.TestCase):

    def test_serialize_and_deserialize_preserves_values(self):
        vector = [0.1, -0.5, 2.0, 0.0]
        restored = _deserialize_vector(_serialize_vector(vector))
        for original, restored_value in zip(vector, restored):
            self.assertAlmostEqual(original, restored_value, places=5)


class TestCosineSimilarity(unittest.TestCase):

    def test_equal_vectors_have_similarity_1(self):
        self.assertAlmostEqual(_cosine_similarity([1, 0], [1, 0]), 1.0)

    def test_orthogonal_vectors_have_similarity_0(self):
        self.assertAlmostEqual(_cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_null_vector_does_not_break(self):
        self.assertEqual(_cosine_similarity([0, 0], [1, 1]), 0.0)


class TestRecallSemantic(ActorTestCase):

    def test_prioritizes_the_fact_closest_in_meaning(self):
        joao = self.create_actor("Joao")
        joao.remember("Vi Carlos saindo do escritorio.", shareable=1)
        joao.remember("O jantar estava delicioso.", shareable=1)

        vectors = {
            "quem estava perto do cofre?": [1.0, 0.0],
            "Vi Carlos saindo do escritorio.": [0.9, 0.1],  # próximo da consulta
            "O jantar estava delicioso.": [0.0, 1.0],       # nada a ver com a consulta
        }
        llm = FakeLLMWithEmbeddings(vectors)

        result = joao.recall_semantic("quem estava perto do cofre?", llm, k=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Vi Carlos saindo do escritorio.")

    def test_each_memorys_embedding_is_computed_only_once(self):
        joao = self.create_actor("Joao")
        joao.remember("Fato A", shareable=1)
        vectors = {"consulta": [1.0, 0.0], "Fato A": [1.0, 0.0]}
        llm = FakeLLMWithEmbeddings(vectors)

        joao.recall_semantic("consulta", llm, k=1)
        self.assertEqual(llm.embedding_calls, 2)  # 1 da consulta + 1 do fato (1ª vez)

        joao.recall_semantic("consulta", llm, k=1)
        self.assertEqual(llm.embedding_calls, 3)  # +1 só da consulta - fato já tem embedding

    def test_respects_the_shareable_only_filter(self):
        joao = self.create_actor("Joao")
        joao.remember("Segredo privado.", shareable=0)
        vectors = {"consulta": [1.0, 0.0], "Segredo privado.": [1.0, 0.0]}
        llm = FakeLLMWithEmbeddings(vectors)

        result = joao.recall_semantic("consulta", llm, k=5, shareable_only=True)
        self.assertEqual(result, [])


class TestRecallBestCombines(ActorTestCase):
    """_recall_best combina lexical + semântica em vez de a semântica substituir a
    lexical - o embedding de um LLM genérico é um sinal ruidoso (testado ao vivo contra um
    llama-server real: às vezes rankeia uma memória aleatória acima da relevante), então a
    lexical roda sempre primeiro e a semântica só ACRESCENTA o que a lexical não achou."""

    def test_combines_without_discarding_the_lexical_match(self):
        joao = self.create_actor("Joao")
        joao.remember("Vi Carlos saindo do escritorio.", shareable=1)  # bate por palavra
        joao.remember("Falava-se muito sobre o assunto na festa.", shareable=1)  # só semântico

        vectors = {
            "escritorio": [1.0, 0.0],
            "Vi Carlos saindo do escritorio.": [0.0, 1.0],       # longe no espaço semântico
            "Falava-se muito sobre o assunto na festa.": [0.9, 0.1],  # perto da consulta
        }
        llm = FakeLLMWithEmbeddings(vectors)

        result = joao._recall_best("escritorio", llm, k=5)
        texts = [m["text"] for m in result]
        # O achado lexical vem primeiro mesmo o embedding dele sendo "distante" - a lexical
        # nunca é sobrescrita pelo ranking semântico.
        self.assertEqual(texts[0], "Vi Carlos saindo do escritorio.")
        self.assertIn("Falava-se muito sobre o assunto na festa.", texts)

    def test_respects_the_k_limit_after_combining(self):
        joao = self.create_actor("Joao")
        joao.remember("Fato lexical.", shareable=1)
        joao.remember("Algo relevante A ocorreu.", shareable=1)
        joao.remember("Algo relevante B ocorreu.", shareable=1)

        vectors = {
            "fato": [1.0, 0.0, 0.0],
            "Fato lexical.": [0.0, 1.0, 0.0],
            "Algo relevante A ocorreu.": [0.9, 0.0, 0.1],
            "Algo relevante B ocorreu.": [0.8, 0.0, 0.2],
        }
        llm = FakeLLMWithEmbeddings(vectors)

        result = joao._recall_best("fato", llm, k=2)
        self.assertEqual(len(result), 2)


class TestRecallBestFallback(ActorTestCase):
    """_recall_best nunca pode quebrar uma conversa por causa de um servidor sem
    --embeddings - precisa cair para a busca lexical de sempre."""

    def test_falls_back_to_lexical_search_without_embedding_support(self):
        joao = self.create_actor("Joao")
        joao.remember("Vi Carlos saindo do escritorio.", shareable=1)
        llm = FakeLLM()  # não suporta embeddings (levanta RuntimeError)

        result = joao._recall_best("escritorio", llm, k=1)
        self.assertEqual(len(result), 1)
        self.assertTrue(joao._no_embedding)

    def test_does_not_retry_embedding_after_failing_once(self):
        joao = self.create_actor("Joao")
        joao.remember("Vi Carlos saindo do escritorio.", shareable=1)
        llm = FakeLLM()
        joao._recall_best("escritorio", llm, k=1)  # aqui já desiste (_no_embedding=True)

        calls = []
        joao.recall_semantic = lambda *a, **k: calls.append(1)
        joao._recall_best("escritorio", llm, k=1)
        self.assertEqual(calls, [], "não deveria tentar recall_semantic de novo")


if __name__ == "__main__":
    unittest.main()
