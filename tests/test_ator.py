"""Testes determinísticos do "cérebro" do Ator (Ator.py): memória, crenças, objetivos,
decisões e detecção de contradição. Tudo em código - nenhum destes testes chama um LLM de
verdade (ver FakeLLM em apoio.py)."""
import random
import time
import unittest

from apoio import FakeLLM, TesteComAtores

from Ator import MEIA_VIDA, barra


class TestMemoria(TesteComAtores):

    def test_recordar_respeita_compartilhavel(self):
        ator = self.criar_ator("Joao")
        ator.lembrar("segredo privado sobre a chave", compartilhavel=0, sensibilidade=0.9)
        ator.lembrar("fato publico sobre o roubo", compartilhavel=1, sensibilidade=0.3)

        todas = ator.recordar("segredo roubo", k=5)
        publicas = ator.recordar("segredo roubo", k=5, so_compartilhaveis=True)

        self.assertEqual(len(todas), 2)
        self.assertEqual(len(publicas), 1)
        self.assertEqual(publicas[0]["texto"], "fato publico sobre o roubo")

    def test_lembrar_nao_duplica_mesmo_texto(self):
        ator = self.criar_ator("Joao")
        id1 = ator.lembrar("O cofre fica no escritorio.")
        id2 = ator.lembrar("O cofre fica no escritorio.")
        self.assertEqual(id1, id2)
        self.assertEqual(len(ator.listar_memorias()), 1)


class TestDecisoes(TesteComAtores):

    def test_mentir_nao_vaza_a_verdade(self):
        """MENTIR precisa mandar SÓ a versão falsa pro LLM - a verdade nunca deve aparecer
        entre os fatos que saem no envelope."""
        joao = self.criar_ator("Joao", tracos={"honestidade": 0.0, "dissimulacao": 1.0,
                                               "empatia": 0.0, "coragem": 1.0})
        # Sem objetivo ativo, DESVIAR nunca é oferecido (ver escolher_acao): só MENTIR/ESCONDER
        # entram em jogo, que é o que este teste quer exercitar.
        id_mem = joao.lembrar("Joao pegou a joia.", sensibilidade=0.95, compartilhavel=1)
        joao.definir_versao_falsa(id_mem, "Joao estava no jardim.")
        joao.nova_conversa("Ana")

        llm = FakeLLM()
        env_pergunta = {"de": "Ana", "alvo": "Joao", "tatica": "PEDIR", "texto": "...",
                        "fatos": [], "acusacoes": []}

        random.seed(1)
        resposta = None
        for _ in range(300):
            # nova_conversa reseta posturas E contados: sem isso, uma REVELAR por sorteio numa
            # tentativa marcaria o fato como "já contado" e ele nunca mais apareceria depois.
            joao.nova_conversa("Ana")
            resposta = joao.responder(env_pergunta, "a joia", llm, ultima=True)
            textos = [f["texto"] for f in resposta["fatos"]]
            if textos and "Joao pegou a joia." not in textos:
                break

        textos = [f["texto"] for f in resposta["fatos"]]
        self.assertTrue(textos, "o teste precisa de pelo menos uma tentativa com fato revelado")
        self.assertNotIn("Joao pegou a joia.", textos)

    def test_fofoca_sobre_si_mesmo_nao_gera_decisao_duplicada(self):
        """Bug real reportado pelo usuário: se Joao ouviu de outra pessoa uma fofoca sobre ELE
        MESMO ser o culpado, essa memória não pode entrar no pipeline de REVELAR/ESCONDER/
        MENTIR/DESVIAR como se fosse fofoca de terceiro - senão ele decide duas vezes sobre o
        "mesmo" assunto (uma pela verdade dele, outra pela fofoca ecoada) e a fala final sai
        confessando e mentindo ao mesmo tempo."""
        joao = self.criar_ator("Joao", tracos={"honestidade": 0.0, "dissimulacao": 1.0,
                                               "empatia": 0.0, "coragem": 1.0})
        joao.lembrar("Joao pegou a joia.", origem="sistema", sensibilidade=0.9, compartilhavel=1)
        # Joao ouviu de Pedro, em algum momento, uma fofoca sobre SI MESMO ser o culpado.
        joao.lembrar("Foi o Joao quem pegou a joia.", origem="Pedro", sensibilidade=0.5,
                    compartilhavel=1, sobre="Joao")

        llm = FakeLLM()
        env_pergunta = {"de": "Ana", "alvo": "Joao", "tatica": "PEDIR", "texto": "...",
                        "fatos": [], "acusacoes": []}

        # Roda muitas tentativas (nova_conversa reseta a postura a cada uma): com o bug, cedo
        # ou tarde as DUAS memórias saem decididas (revelar/mentir/desviar) na mesma fala - o
        # que produziria uma resposta que confessa e mente ao mesmo tempo.
        random.seed(3)
        for _ in range(50):
            joao.nova_conversa("Ana")
            resposta = joao.responder(env_pergunta, "a joia", llm, ultima=True)
            self.assertLessEqual(
                len(resposta["fatos"]), 1,
                "fofoca sobre o próprio Joao não pode virar uma segunda decisão além da dele mesmo")

    def test_desviar_exige_objetivo_e_candidato(self):
        """DESVIAR não pode ser só personalidade: precisa de um objetivo ativo de alta
        prioridade E de alguém conhecido pra culpar (ver escolher_acao em Ator.py)."""
        joao = self.criar_ator("Joao", tracos={"honestidade": 0.0, "dissimulacao": 0.95,
                                               "empatia": 0.1, "coragem": 1.0})
        id_mem = joao.lembrar("segredo grave", sensibilidade=0.9, compartilhavel=1)
        fato = joao.listar_memorias()[0]

        random.seed(0)

        # Sem objetivo formado ainda: nunca desvia, mesmo com personalidade compatível.
        for _ in range(100):
            joao.posturas.clear()
            decisao, extra, p = joao.escolher_acao(fato, "Ana")
            self.assertNotEqual(decisao, "DESVIAR")

        # Com objetivo, mas sem ninguém conhecido pra culpar: ainda não desvia.
        joao.formar_objetivo("Não ser descoberto", prioridade=0.9)
        for _ in range(100):
            joao.posturas.clear()
            decisao, extra, p = joao.escolher_acao(fato, "Ana")
            self.assertNotEqual(decisao, "DESVIAR")

        # Com um candidato conhecido, DESVIAR passa a ser possível.
        joao.relacao("Caio")
        desviou = False
        for _ in range(300):
            joao.posturas.clear()
            decisao, extra, p = joao.escolher_acao(fato, "Ana")
            if decisao == "DESVIAR":
                desviou = True
                self.assertEqual(extra["alvo_falso"], "Caio")
                break
        self.assertTrue(desviou, "DESVIAR nunca ocorreu com objetivo e candidato disponíveis")


class TestContradicao(TesteComAtores):

    def test_contradicao_e_detectada_e_penaliza_confianca(self):
        joao = self.criar_ator("Joao")
        ana = self.criar_ator("Ana")
        id_mem = joao.lembrar("Joao pegou a joia as 21:30.", sensibilidade=0.9, compartilhavel=1)

        mentira = {"de": "Joao", "alvo": "Ana", "tatica": "NENHUMA", "texto": "...",
                   "fatos": [{"texto": "Joao estava no jardim.", "origem_id": id_mem}],
                   "acusacoes": []}
        verdade = {"de": "Joao", "alvo": "Ana", "tatica": "NENHUMA", "texto": "...",
                   "fatos": [{"texto": "Joao pegou a joia as 21:30.", "origem_id": id_mem}],
                   "acusacoes": []}

        ana.receber(mentira)
        self.assertEqual(ana.relacao("Joao")["desconfianca"], 0.0)
        self.assertEqual(len(ana.contradicoes()), 0)

        ana.receber(verdade)
        self.assertAlmostEqual(ana.relacao("Joao")["desconfianca"], 0.3)
        self.assertEqual(len(ana.contradicoes()), 2)

    def test_mesma_origem_repetindo_o_mesmo_fato_nao_e_contradicao(self):
        joao = self.criar_ator("Joao")
        ana = self.criar_ator("Ana")
        id_mem = joao.lembrar("Joao estava no jardim.", sensibilidade=0.5, compartilhavel=1)
        env = {"de": "Joao", "alvo": "Ana", "tatica": "NENHUMA", "texto": "...",
               "fatos": [{"texto": "Joao estava no jardim.", "origem_id": id_mem}],
               "acusacoes": []}
        ana.receber(env)
        ana.receber(env)
        self.assertEqual(len(ana.contradicoes()), 0)


class TestAcusacao(TesteComAtores):

    def test_acusado_nao_acredita_na_propria_acusacao(self):
        caio = self.criar_ator("Caio")
        acusacao = {"de": "Joao", "alvo": "Ana", "tatica": "NENHUMA", "texto": "...",
                    "fatos": [], "acusacoes": [
                        {"assunto": "Caio", "proposicao": "Caio pode estar envolvido.", "peso": 0.15}
                    ]}
        caio.receber(acusacao)
        self.assertIsNone(caio.crenca("Caio é o culpado"))

    def test_terceiro_absorve_a_acusacao_como_crenca_fraca(self):
        ana = self.criar_ator("Ana")
        acusacao = {"de": "Joao", "alvo": "Ana", "tatica": "NENHUMA", "texto": "...",
                    "fatos": [], "acusacoes": [
                        {"assunto": "Caio", "proposicao": "Caio pode estar envolvido.", "peso": 0.15}
                    ]}
        ana.receber(acusacao)
        crenca = ana.crenca("Caio é o culpado")
        self.assertIsNotNone(crenca)
        self.assertAlmostEqual(crenca["confianca"], 0.65)


class TestCrencas(TesteComAtores):

    def test_formar_e_atualizar_crenca(self):
        ana = self.criar_ator("Ana")
        self.assertIsNone(ana.crenca("Joao é o culpado"))

        ana.atualizar_crenca("Joao é o culpado", delta=0.2, origem="Bia", assunto="Joao",
                             evidencia="evidencia:1")
        c = ana.crenca("Joao é o culpado")
        self.assertAlmostEqual(c["confianca"], 0.7)

        ana.atualizar_crenca("Joao é o culpado", delta=0.5, origem="Dora", evidencia="evidencia:2")
        c = ana.crenca("Joao é o culpado")
        self.assertAlmostEqual(c["confianca"], 1.0)  # limitado em 1.0
        self.assertEqual(c["evidencias"], ["evidencia:1", "evidencia:2"])

    def test_crencas_sobre_filtra_por_assunto(self):
        ana = self.criar_ator("Ana")
        ana.atualizar_crenca("Joao é o culpado", delta=0.3, assunto="Joao")
        ana.atualizar_crenca("Caio sabe de algo", delta=0.1, assunto="Caio")
        sobre_joao = ana.crencas_sobre("Joao")
        self.assertEqual(len(sobre_joao), 1)
        self.assertEqual(sobre_joao[0]["proposicao"], "Joao é o culpado")


class TestObjetivos(TesteComAtores):

    def test_ciclo_de_vida_do_objetivo(self):
        joao = self.criar_ator("Joao")
        joao.formar_objetivo("Não ser descoberto", prioridade=0.9, risco=0.8)

        self.assertEqual(len(joao.objetivos_ativos()), 1)
        self.assertEqual(joao.objetivo_principal()["descricao"], "Não ser descoberto")

        joao.atualizar_objetivo("Não ser descoberto", status="falhou")
        self.assertEqual(joao.objetivos_ativos(), [])
        self.assertEqual(joao.objetivo("Não ser descoberto")["status"], "falhou")

    def test_objetivo_principal_prioriza_maior_prioridade(self):
        joao = self.criar_ator("Joao")
        joao.formar_objetivo("Escapar da cidade", prioridade=0.4)
        joao.formar_objetivo("Não ser descoberto", prioridade=0.9)
        self.assertEqual(joao.objetivo_principal()["descricao"], "Não ser descoberto")


class TestEstadoERelacoes(TesteComAtores):

    def test_medo_decai_com_o_tempo(self):
        joao = self.criar_ator("Joao")
        joao.mudar_relacao("Maria", medo=0.8)

        # Simula que a última atualização aconteceu há uma meia-vida inteira.
        passado = time.time() - MEIA_VIDA["medo"]
        joao.db.execute("UPDATE relacoes SET atualizado_em=? WHERE outro=?", (passado, "Maria"))
        joao.db.commit()

        self.assertAlmostEqual(joao.relacao("Maria")["medo"], 0.4, delta=0.01)

    def test_ameaca_aumenta_medo_de_quem_e_alvo(self):
        joao = self.criar_ator("Joao")
        ameaca = {"de": "Carlos", "alvo": "Joao", "tatica": "AMEACAR", "texto": "...",
                  "fatos": [], "acusacoes": []}
        antes = joao.relacao("Carlos")["medo"]
        joao.receber(ameaca)
        self.assertGreater(joao.relacao("Carlos")["medo"], antes)


class TestPainel(unittest.TestCase):

    def test_barra_respeita_limites(self):
        self.assertEqual(barra(0.0, largura=10), "░" * 10 + " 0.00")
        self.assertEqual(barra(1.0, largura=10), "█" * 10 + " 1.00")
        self.assertEqual(barra(1.5, largura=10), "█" * 10 + " 1.00")  # limitado em 1.0
        self.assertEqual(barra(-0.5, largura=10), "░" * 10 + " 0.00")  # limitado em 0.0


if __name__ == "__main__":
    unittest.main()
