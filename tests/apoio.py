"""
apoio.py - utilidades compartilhadas pelos testes (não é um módulo de teste em si, não roda
nada sozinho). Mantém os arquivos .db de cada teste isolados num diretório temporário.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Ator import Ator, TRACOS_PADRAO, criar_ator


class FakeLLM:
    """LLM falso: não faz rede nem inferência, só devolve um texto fixo. As estatísticas e
    comportamentos testados aqui dependem das decisões em CÓDIGO (Ator.escolher_acao e afins),
    não do texto gerado - por isso um LLM de verdade nunca é necessário nestes testes."""

    def __init__(self, resposta="..."):
        self.resposta = resposta
        self.url = "fake"

    def gerar(self, mensagens, max_tokens=150, temperatura=0.7, slot=None, ao_vivo=False,
              response_format=None):
        return self.resposta

    def embedding(self, texto):
        # Simula um llama-server iniciado SEM --embeddings: é exatamente o caso que
        # Ator._recordar_melhor precisa detectar e cair para a busca lexical (recordar()).
        raise RuntimeError("FakeLLM não suporta embeddings (servidor de teste sem --embeddings)")


class TesteComAtores(unittest.TestCase):
    """Base para testes que precisam de Atores de verdade (arquivos .db temporários, apagados
    ao final de cada teste)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contracenador_teste_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def criar_ator(self, nome, tracos=None, descricao="personagem de teste"):
        caminho = os.path.join(self.tmp, f"{nome.lower()}.db")
        criar_ator(caminho, nome, descricao, ["..."], {**TRACOS_PADRAO, **(tracos or {})})
        ator = Ator(caminho, slot=0)
        ator.mostrar = False  # testes não precisam do log de decisões no terminal
        return ator
