"""
support.py - utilidades compartilhadas pelos testes (não é um módulo de teste em si, não roda
nada sozinho). Mantém os arquivos .db de cada teste isolados num diretório temporário.
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actor import Actor, DEFAULT_TRAITS, create_actor


class FakeLLM:
    """LLM falso: não faz rede nem inferência. As estatísticas e comportamentos testados aqui
    dependem das decisões em CÓDIGO (Actor.choose_action e afins), não do texto gerado - por
    isso um LLM de verdade nunca é necessário nestes testes.

    "Cooperativo" por padrão: ecoa de volta os trechos entre aspas da instrução recebida (é
    onde respond() coloca "conte a fulano: '...'"), em vez de sempre devolver um texto fixo que
    não menciona nada do que foi pedido. Isso é necessário porque respond() agora VALIDA se a
    fala realmente verbalizou o que o código decidiu revelar/mentir (ver actor.verbalized) -
    um fake sempre-mudo faria esse descarte acontecer em todo teste, mesmo quando não é isso
    que o teste quer exercitar. Passe uma `response` explícita para voltar ao comportamento
    fixo antigo (ex.: para testar justamente o caso de um LLM que ignora a instrução)."""

    def __init__(self, response=None):
        self.response = response
        self.url = "fake"

    def generate(self, messages, max_tokens=150, temperature=0.7, slot=None, live=False,
                 response_format=None):
        if self.response is not None:
            return self.response
        content = messages[-1]["content"] if messages else ""
        quotes = re.findall(r'"([^"]+)"', content)
        return " ".join(quotes) if quotes else "..."

    def embedding(self, text):
        # Simula um llama-server iniciado SEM --embeddings: é exatamente o caso que
        # Actor._recall_best precisa detectar e cair para a busca lexical (recall()).
        raise RuntimeError("FakeLLM não suporta embeddings (servidor de teste sem --embeddings)")


class ActorTestCase(unittest.TestCase):
    """Base para testes que precisam de Atores de verdade (arquivos .db temporários, apagados
    ao final de cada teste)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contracenador_teste_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create_actor(self, name, traits=None, description="personagem de teste"):
        path = os.path.join(self.tmp, f"{name.lower()}.db")
        create_actor(path, name, description, ["..."], {**DEFAULT_TRAITS, **(traits or {})})
        actor = Actor(path, slot=0)
        actor.verbose = False  # testes não precisam do log de decisões no terminal
        return actor
