"""Testes determinísticos do "cérebro" do Ator (actor.py): memória, crenças, objetivos,
decisões e detecção de contradição. Tudo em código - nenhum destes testes chama um LLM de
verdade (ver FakeLLM em support.py)."""
import random
import time
import unittest

from support import ActorTestCase, FakeLLM

from actor import HALF_LIFE, bar


class LLMRecordsRequest(FakeLLM):
    """FakeLLM que grava o último pedido de mensagens recebido, pra inspecionar a instrução
    exata que o código mandou (sem precisar de um LLM de verdade só pra checar o texto)."""

    def __init__(self, response="..."):
        super().__init__(response)
        self.last_request = None

    def generate(self, messages, **kwargs):
        self.last_request = messages
        return super().generate(messages, **kwargs)


class TestMemory(ActorTestCase):

    def test_recall_respects_shareable(self):
        actor = self.create_actor("Joao")
        actor.remember("segredo privado sobre a chave", shareable=0, sensitivity=0.9)
        actor.remember("fato publico sobre o roubo", shareable=1, sensitivity=0.3)

        all_ = actor.recall("segredo roubo", k=5)
        public = actor.recall("segredo roubo", k=5, shareable_only=True)

        self.assertEqual(len(all_), 2)
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]["text"], "fato publico sobre o roubo")

    def test_remember_does_not_duplicate_same_text(self):
        actor = self.create_actor("Joao")
        id1 = actor.remember("O cofre fica no escritorio.")
        id2 = actor.remember("O cofre fica no escritorio.")
        self.assertEqual(id1, id2)
        self.assertEqual(len(actor.list_memories()), 1)


class TestDecisions(ActorTestCase):

    def test_lie_does_not_leak_the_truth(self):
        """LIE precisa mandar SÓ a versão falsa pro LLM - a verdade nunca deve aparecer
        entre os fatos que saem no envelope."""
        joao = self.create_actor("Joao", traits={"honesty": 0.0, "deceit": 1.0,
                                                  "empathy": 0.0, "courage": 1.0})
        # Sem objetivo ativo, DEFLECT nunca é oferecido (ver choose_action): só LIE/HIDE
        # entram em jogo, que é o que este teste quer exercitar.
        memory_id = joao.remember("Joao pegou a joia.", sensitivity=0.95, shareable=1)
        joao.set_false_version(memory_id, "Joao estava no jardim.")
        joao.new_conversation("Ana")

        llm = FakeLLM()
        question_env = {"from": "Ana", "target": "Joao", "tactic": "ASK", "text": "...",
                        "facts": [], "accusations": []}

        random.seed(1)
        response = None
        for _ in range(300):
            # new_conversation reseta postura E divulgados: sem isso, um REVEAL por sorteio
            # numa tentativa marcaria o fato como "já contado" e ele nunca mais apareceria depois.
            joao.new_conversation("Ana")
            response = joao.respond(question_env, "a joia", llm, last=True)
            texts = [f["text"] for f in response["facts"]]
            if texts and "Joao pegou a joia." not in texts:
                break

        texts = [f["text"] for f in response["facts"]]
        self.assertTrue(texts, "o teste precisa de pelo menos uma tentativa com fato revelado")
        self.assertNotIn("Joao pegou a joia.", texts)

    def test_gossip_about_self_does_not_cause_double_decision(self):
        """Bug real reportado pelo usuário: se Joao ouviu de outra pessoa uma fofoca sobre ELE
        MESMO ser o culpado, essa memória não pode entrar no pipeline de REVEAL/HIDE/
        LIE/DEFLECT como se fosse fofoca de terceiro - senão ele decide duas vezes sobre o
        "mesmo" assunto (uma pela verdade dele, outra pela fofoca ecoada) e a fala final sai
        confessando e mentindo ao mesmo tempo."""
        joao = self.create_actor("Joao", traits={"honesty": 0.0, "deceit": 1.0,
                                                  "empathy": 0.0, "courage": 1.0})
        joao.remember("Joao pegou a joia.", origin="system", sensitivity=0.9, shareable=1)
        # Joao ouviu de Pedro, em algum momento, uma fofoca sobre SI MESMO ser o culpado.
        joao.remember("Foi o Joao quem pegou a joia.", origin="Pedro", sensitivity=0.5,
                     shareable=1, about="Joao")

        llm = FakeLLM()
        question_env = {"from": "Ana", "target": "Joao", "tactic": "ASK", "text": "...",
                        "facts": [], "accusations": []}

        # Roda muitas tentativas (new_conversation reseta a postura a cada uma): com o bug,
        # cedo ou tarde as DUAS memórias saem decididas (revelar/mentir/desviar) na mesma fala -
        # o que produziria uma resposta que confessa e mente ao mesmo tempo.
        random.seed(3)
        for _ in range(50):
            joao.new_conversation("Ana")
            response = joao.respond(question_env, "a joia", llm, last=True)
            self.assertLessEqual(
                len(response["facts"]), 1,
                "fofoca sobre o próprio Joao não pode virar uma segunda decisão além da dele mesmo")

    def test_gossip_is_reported_in_third_person_not_first_person(self):
        """Bug real reportado pelo usuário (rodando com LLM de verdade): ao repassar a
        confissão de Carlos ("Fui eu quem roubou..."), João dizia a frase em primeira pessoa,
        soando como se ELE tivesse confessado. A instrução para o LLM precisa deixar claro que
        é um relato de terceiro quando `origin` da memória é outra pessoa (fofoca), não algo
        que o próprio Ator viu/viveu/sabe por si (OWN_ORIGINS)."""
        joao = self.create_actor("Joao", traits={"honesty": 1.0, "deceit": 0.0})
        joao.change_relationship("Fernanda", trust=0.5)
        joao.remember("Fui eu quem roubou o quadro.", origin="Carlos", sensitivity=0.1,
                     shareable=1)
        llm = LLMRecordsRequest()
        question_env = {"from": "Fernanda", "target": "Joao", "tactic": "ASK", "text": "quadro",
                        "facts": [], "accusations": []}

        random.seed(1)
        request_text = None
        for _ in range(50):
            joao.new_conversation("Fernanda")
            joao.respond(question_env, "o quadro", llm, last=True)
            if "Você soube por" in llm.last_request[-1]["content"]:
                request_text = llm.last_request[-1]["content"]
                break

        self.assertIsNotNone(request_text, "REVEAL nunca ocorreu em 50 tentativas")
        self.assertIn("Você soube por Carlos", request_text)
        self.assertIn("NUNCA como se fosse sobre você mesmo", request_text)

    def test_own_observation_stays_in_first_person(self):
        """O que o próprio Ator viu com os próprios olhos (origin='observation') não deve ser
        instruído como relato de terceiro - ele não "ouviu de alguém", ele mesmo presenciou."""
        joao = self.create_actor("Joao", traits={"honesty": 1.0, "deceit": 0.0})
        joao.change_relationship("Fernanda", trust=0.5)
        joao.remember("Vi Carlos saindo as pressas do escritorio.", origin="observation",
                     sensitivity=0.1, shareable=1, about="Carlos")
        llm = LLMRecordsRequest()
        question_env = {"from": "Fernanda", "target": "Joao", "tactic": "ASK", "text": "escritorio",
                        "facts": [], "accusations": []}

        expected = ('Conte a Fernanda, com suas palavras: '
                    '"Vi Carlos saindo as pressas do escritorio."')
        random.seed(1)
        request_text = None
        for _ in range(50):
            joao.new_conversation("Fernanda")
            joao.respond(question_env, "o quadro", llm, last=True)
            if expected in llm.last_request[-1]["content"]:
                request_text = llm.last_request[-1]["content"]
                break

        self.assertIsNotNone(request_text, "REVEAL nunca ocorreu em 50 tentativas")

    def test_deflect_requires_goal_and_candidate(self):
        """DEFLECT não pode ser só personalidade: precisa de um objetivo ativo de alta
        prioridade E de alguém conhecido pra culpar (ver choose_action em actor.py)."""
        joao = self.create_actor("Joao", traits={"honesty": 0.0, "deceit": 0.95,
                                                  "empathy": 0.1, "courage": 1.0})
        joao.remember("segredo grave", sensitivity=0.9, shareable=1)
        fact = joao.list_memories()[0]

        random.seed(0)

        # Sem objetivo formado ainda: nunca desvia, mesmo com personalidade compatível.
        for _ in range(100):
            joao.stances.clear()
            decision, extra, p = joao.choose_action(fact, "Ana")
            self.assertNotEqual(decision, "DEFLECT")

        # Com objetivo, mas sem ninguém conhecido pra culpar: ainda não desvia.
        joao.form_goal("Não ser descoberto", priority=0.9)
        for _ in range(100):
            joao.stances.clear()
            decision, extra, p = joao.choose_action(fact, "Ana")
            self.assertNotEqual(decision, "DEFLECT")

        # Com um candidato conhecido, DEFLECT passa a ser possível.
        joao.relationship("Caio")
        deflected = False
        for _ in range(300):
            joao.stances.clear()
            decision, extra, p = joao.choose_action(fact, "Ana")
            if decision == "DEFLECT":
                deflected = True
                self.assertEqual(extra["fake_target"], "Caio")
                break
        self.assertTrue(deflected, "DEFLECT nunca ocorreu com objetivo e candidato disponíveis")


class TestContradiction(ActorTestCase):

    def test_contradiction_is_detected_and_penalizes_trust(self):
        joao = self.create_actor("Joao")
        ana = self.create_actor("Ana")
        memory_id = joao.remember("Joao pegou a joia as 21:30.", sensitivity=0.9, shareable=1)

        lie = {"from": "Joao", "target": "Ana", "tactic": "NONE", "text": "...",
              "facts": [{"text": "Joao estava no jardim.", "source_id": memory_id}],
              "accusations": []}
        truth = {"from": "Joao", "target": "Ana", "tactic": "NONE", "text": "...",
                "facts": [{"text": "Joao pegou a joia as 21:30.", "source_id": memory_id}],
                "accusations": []}

        ana.receive(lie)
        self.assertEqual(ana.relationship("Joao")["distrust"], 0.0)
        self.assertEqual(len(ana.contradictions()), 0)

        ana.receive(truth)
        self.assertAlmostEqual(ana.relationship("Joao")["distrust"], 0.3)
        self.assertEqual(len(ana.contradictions()), 2)

    def test_same_origin_repeating_the_same_fact_is_not_a_contradiction(self):
        joao = self.create_actor("Joao")
        ana = self.create_actor("Ana")
        memory_id = joao.remember("Joao estava no jardim.", sensitivity=0.5, shareable=1)
        env = {"from": "Joao", "target": "Ana", "tactic": "NONE", "text": "...",
              "facts": [{"text": "Joao estava no jardim.", "source_id": memory_id}],
              "accusations": []}
        ana.receive(env)
        ana.receive(env)
        self.assertEqual(len(ana.contradictions()), 0)


class TestAccusation(ActorTestCase):

    def test_accused_does_not_believe_their_own_accusation(self):
        caio = self.create_actor("Caio")
        accusation = {"from": "Joao", "target": "Ana", "tactic": "NONE", "text": "...",
                     "facts": [], "accusations": [
                         {"subject": "Caio", "proposition": "Caio pode estar envolvido.", "weight": 0.15}
                     ]}
        caio.receive(accusation)
        self.assertIsNone(caio.belief("Caio é o culpado"))

    def test_third_party_absorbs_the_accusation_as_a_weak_belief(self):
        ana = self.create_actor("Ana")
        accusation = {"from": "Joao", "target": "Ana", "tactic": "NONE", "text": "...",
                     "facts": [], "accusations": [
                         {"subject": "Caio", "proposition": "Caio pode estar envolvido.", "weight": 0.15}
                     ]}
        ana.receive(accusation)
        belief = ana.belief("Caio é o culpado")
        self.assertIsNotNone(belief)
        self.assertAlmostEqual(belief["confidence"], 0.65)


class TestBeliefs(ActorTestCase):

    def test_form_and_update_belief(self):
        ana = self.create_actor("Ana")
        self.assertIsNone(ana.belief("Joao é o culpado"))

        ana.update_belief("Joao é o culpado", delta=0.2, origin="Bia", subject="Joao",
                          evidence="evidence:1")
        c = ana.belief("Joao é o culpado")
        self.assertAlmostEqual(c["confidence"], 0.7)

        ana.update_belief("Joao é o culpado", delta=0.5, origin="Dora", evidence="evidence:2")
        c = ana.belief("Joao é o culpado")
        self.assertAlmostEqual(c["confidence"], 1.0)  # limitado em 1.0
        self.assertEqual(c["evidence"], ["evidence:1", "evidence:2"])

    def test_beliefs_about_filters_by_subject(self):
        ana = self.create_actor("Ana")
        ana.update_belief("Joao é o culpado", delta=0.3, subject="Joao")
        ana.update_belief("Caio sabe de algo", delta=0.1, subject="Caio")
        about_joao = ana.beliefs_about("Joao")
        self.assertEqual(len(about_joao), 1)
        self.assertEqual(about_joao[0]["proposition"], "Joao é o culpado")


class TestGoals(ActorTestCase):

    def test_goal_lifecycle(self):
        joao = self.create_actor("Joao")
        joao.form_goal("Não ser descoberto", priority=0.9, risk=0.8)

        self.assertEqual(len(joao.active_goals()), 1)
        self.assertEqual(joao.main_goal()["description"], "Não ser descoberto")

        joao.update_goal("Não ser descoberto", status="failed")
        self.assertEqual(joao.active_goals(), [])
        self.assertEqual(joao.goal("Não ser descoberto")["status"], "failed")

    def test_main_goal_prioritizes_highest_priority(self):
        joao = self.create_actor("Joao")
        joao.form_goal("Escapar da cidade", priority=0.4)
        joao.form_goal("Não ser descoberto", priority=0.9)
        self.assertEqual(joao.main_goal()["description"], "Não ser descoberto")


class TestStateAndRelationships(ActorTestCase):

    def test_fear_decays_over_time(self):
        joao = self.create_actor("Joao")
        joao.change_relationship("Maria", fear=0.8)

        # Simula que a última atualização aconteceu há uma meia-vida inteira.
        past = time.time() - HALF_LIFE["fear"]
        joao.db.execute("UPDATE relationships SET updated_at=? WHERE other=?", (past, "Maria"))
        joao.db.commit()

        self.assertAlmostEqual(joao.relationship("Maria")["fear"], 0.4, delta=0.01)

    def test_threat_increases_fear_of_the_target(self):
        joao = self.create_actor("Joao")
        threat = {"from": "Carlos", "target": "Joao", "tactic": "THREATEN", "text": "...",
                 "facts": [], "accusations": []}
        before = joao.relationship("Carlos")["fear"]
        joao.receive(threat)
        self.assertGreater(joao.relationship("Carlos")["fear"], before)


class LLMSaysName:
    """LLM falso que sempre 'escolhe' um nome específico, com texto ao redor (como um LLM de
    verdade faria: 'Vou interrogar X agora.'), para testar choose_investigation_target()."""

    def __init__(self, name):
        self.name = name

    def generate(self, *args, **kwargs):
        return f"Vou interrogar {self.name} agora."


class LLMUnusableResponse:
    """LLM falso que nunca dá uma resposta que bata com nenhum candidato."""

    def generate(self, *args, **kwargs):
        return "Hmm, não sei bem o que dizer aqui."


class TestChooseInvestigationTarget(ActorTestCase):
    """choose_investigation_target(): o investigador SEMPRE decide sozinho quem interrogar -
    nunca um menu pro usuário, nunca um sorteio puro. O LLM propõe um nome; o código valida
    antes de aceitar (roadmap, seções 12 e 19)."""

    def test_accepts_the_llms_valid_choice(self):
        ana = self.create_actor("Ana")
        target = ana.choose_investigation_target(["Bia", "Caio", "Joao"], "quem roubou", LLMSaysName("Caio"))
        self.assertEqual(target, "Caio")

    def test_invalid_response_falls_back_to_scoring(self):
        ana = self.create_actor("Ana")
        ana.update_belief("Joao é o culpado", delta=0.3, subject="Joao")
        target = ana.choose_investigation_target(["Bia", "Caio", "Joao"], "quem roubou",
                                                  LLMUnusableResponse())
        self.assertEqual(target, "Joao")  # maior pontuação (crença já formada sobre ele)

    def test_never_chooses_someone_outside_the_list(self):
        ana = self.create_actor("Ana")
        target = ana.choose_investigation_target(["Bia", "Caio"], "x", LLMSaysName("Pedro"))
        self.assertIn(target, ["Bia", "Caio"])


class TestPanel(unittest.TestCase):

    def test_bar_respects_limits(self):
        self.assertEqual(bar(0.0, width=10), "░" * 10 + " 0.00")
        self.assertEqual(bar(1.0, width=10), "█" * 10 + " 1.00")
        self.assertEqual(bar(1.5, width=10), "█" * 10 + " 1.00")  # limitado em 1.0
        self.assertEqual(bar(-0.5, width=10), "░" * 10 + " 0.00")  # limitado em 0.0


if __name__ == "__main__":
    unittest.main()
