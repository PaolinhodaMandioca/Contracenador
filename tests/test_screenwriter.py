"""Testes determinísticos do Roteirista (screenwriter.py): validação de JSON, materialização
da cena (WorldState + Atores) e o uso dos nomes sorteados pelo código. Nenhum LLM de verdade -
os testes de generate_scene_llm usam um FakeLLM que devolve um JSON fixo."""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracenador.agents.agent import Actor
from contracenador.scenarios.generator import extract_json, generate_scene_llm, materialize_scene, validate_scene_data
from contracenador.world import evidence_for_event, find_event_by_type, open_world


class TestExtractJson(unittest.TestCase):

    def test_extracts_from_markdown_fences(self):
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_trims_surrounding_text(self):
        text = 'Aqui está: {"a": 1} obrigado!'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_raises_error_without_json(self):
        with self.assertRaises(ValueError):
            extract_json("nada de json aqui")


class ObedientFakeLLM:
    """Simula um roteirista que usa exatamente os nomes exigidos no prompt (o caso normal:
    ver aviso em generate_scene_llm se algum nome sorteado não for usado)."""

    url = "fake"

    def generate(self, messages, **kwargs):
        content = messages[-1]["content"]
        names = [n.strip() for n in
                re.search(r"Use OBRIGATORIAMENTE estes 5 nomes.*?: (.+?)\.", content).group(1).split(",")]
        characters = [
            {"name": names[0], "role": "guilty", "truth": "v", "alibi": "a",
             "description": "d", "examples": [], "traits": {}},
            {"name": names[1], "role": "investigator", "goal": "o",
             "description": "d", "examples": [], "traits": {}},
        ] + [
            {"name": n, "role": "witness", "saw": None,
             "description": "d", "examples": [], "traits": {}}
            for n in names[2:]
        ]
        return json.dumps({"scene": "teste", "characters": characters})


class DisobedientFakeLLM:
    """Simula um roteirista que ignora os nomes exigidos e inventa os seus - generate_scene_llm
    deve seguir em frente mesmo assim (só avisa), nunca travar por isso."""

    url = "fake"

    def generate(self, messages, **kwargs):
        characters = [
            {"name": "Fulano Um", "role": "guilty", "truth": "v", "alibi": "a",
             "description": "d", "examples": [], "traits": {}},
            {"name": "Fulano Dois", "role": "investigator", "goal": "o",
             "description": "d", "examples": [], "traits": {}},
        ]
        return json.dumps({"scene": "teste", "characters": characters})


class TestGenerateSceneLlm(unittest.TestCase):
    """Os nomes vêm do banco sorteado pelo código (names.py), não da criatividade do LLM - ver
    generate_scene_llm. O prompt exige que o modelo use exatamente os nomes sorteados."""

    def test_uses_the_5_names_drawn_by_the_code(self):
        data = generate_scene_llm(ObedientFakeLLM(), "tema qualquer")
        used_names = [c["name"] for c in data["characters"]]
        self.assertEqual(len(used_names), 5)
        self.assertEqual(len(set(used_names)), 5)  # sem repetição
        for name in used_names:
            self.assertEqual(len(name.split()), 2)  # "Primeiro Sobrenome"

    def test_llm_that_ignores_the_names_does_not_break_generation(self):
        data = generate_scene_llm(DisobedientFakeLLM(), "tema qualquer")
        self.assertEqual(data["characters"][0]["name"], "Fulano Um")


class TestValidateSceneData(unittest.TestCase):

    def test_requires_guilty_and_investigator(self):
        with self.assertRaises(ValueError):
            validate_scene_data({"scene": "x", "characters": [{"name": "A", "role": "witness"}]})

    def test_fills_default_goal_and_traits(self):
        data = {"scene": "x", "characters": [
            {"name": "Joao", "role": "guilty", "truth": "v", "alibi": "a"},
            {"name": "Ana", "role": "investigator"},
        ]}
        validate_scene_data(data)
        self.assertIn("goal", data["characters"][1])
        self.assertEqual(data["characters"][0]["traits"]["honesty"], 0.5)


class TestMaterializeScene(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contracenador_screenwriter_")
        self.actors_folder = os.path.join(self.tmp, "atores")
        self.scenario_folder = os.path.join(self.tmp, "cenario")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_scene_clears_previous_actor_and_scenario_files(self):
        def make_scene(title, guilty, investigator):
            return {
                "scene": title,
                "characters": [
                    {"name": guilty, "role": "guilty", "truth": f"{guilty} fez isso.",
                     "alibi": "Eu estava em outro lugar.", "traits": {}, "examples": []},
                    {"name": investigator, "role": "investigator", "goal": "Descobrir a verdade",
                     "traits": {}, "examples": []},
                ],
            }

        materialize_scene(make_scene("História antiga", "Joao", "Ana"),
                          actors_folder=self.actors_folder,
                          scenario_folder=self.scenario_folder, slots=2)
        os.makedirs(os.path.join(self.actors_folder, "backup"))
        with open(os.path.join(self.scenario_folder, "anotacoes.txt"), "w", encoding="utf-8") as file:
            file.write("estado antigo")

        materialize_scene(make_scene("História nova", "Caio", "Bia"),
                          actors_folder=self.actors_folder,
                          scenario_folder=self.scenario_folder, slots=2)

        self.assertEqual(set(os.listdir(self.actors_folder)), {"caio.db", "bia.db"})
        self.assertEqual(set(os.listdir(self.scenario_folder)), {"cena.json", "world.db"})

    def test_overlapping_output_folders_are_rejected_without_deleting_data(self):
        scene = {
            "scene": "Uma cena",
            "characters": [
                {"name": "Joao", "role": "guilty", "truth": "Joao fez isso.",
                 "alibi": "Eu estava em outro lugar."},
                {"name": "Ana", "role": "investigator", "goal": "Descobrir a verdade"},
            ],
        }
        marker = os.path.join(self.tmp, "preservar.txt")
        with open(marker, "w", encoding="utf-8") as file:
            file.write("nao apagar")

        with self.assertRaisesRegex(ValueError, "devem ser separadas"):
            materialize_scene(scene, actors_folder=self.tmp,
                              scenario_folder=os.path.join(self.tmp, "cenario"))

        self.assertTrue(os.path.exists(marker))

    def test_records_crime_and_evidence_in_the_worldstate(self):
        data = {
            "scene": "Uma joia sumiu",
            "characters": [
                {"name": "Joao", "role": "guilty", "truth": "Joao pegou a joia.",
                 "alibi": "Estava no jardim.", "traits": {}, "examples": []},
                {"name": "Ana", "role": "investigator", "goal": "Achar a joia",
                 "traits": {}, "examples": []},
                {"name": "Bia", "role": "witness", "saw": "Vi Joao perto da vitrine.",
                 "traits": {}, "examples": []},
            ],
        }
        validate_scene_data(data)
        materialize_scene(data, actors_folder=self.actors_folder,
                          scenario_folder=self.scenario_folder, slots=2)

        world = open_world(os.path.join(self.scenario_folder, "world.db"))
        try:
            event = find_event_by_type(world, "crime")
            self.assertIsNotNone(event)
            self.assertEqual(event["data"]["proposition"], "Joao pegou a joia.")

            evidence = evidence_for_event(world, event["id"])
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["subject"], "Joao")
            self.assertEqual(evidence[0]["origin"], "Bia")
        finally:
            world.close()

    def test_guilty_and_investigator_get_structured_goals(self):
        data = {
            "scene": "Uma joia sumiu",
            "characters": [
                {"name": "Joao", "role": "guilty", "truth": "Joao pegou a joia.",
                 "alibi": "Estava no jardim.", "traits": {}, "examples": []},
                {"name": "Ana", "role": "investigator", "goal": "Achar a joia",
                 "traits": {}, "examples": []},
            ],
        }
        validate_scene_data(data)
        materialize_scene(data, actors_folder=self.actors_folder,
                          scenario_folder=self.scenario_folder, slots=2)

        joao = Actor(os.path.join(self.actors_folder, "joao.db"), slot=0)
        self.assertIsNotNone(joao.goal("Não ser descoberto"))
        self.assertEqual(joao.list_memories()[0]["protected_by_goal"],
                         joao.goal("Não ser descoberto")["id"])

        ana = Actor(os.path.join(self.actors_folder, "ana.db"), slot=0)
        self.assertIsNotNone(ana.goal("Achar a joia"))
        self.assertEqual(ana.recall("joia", shareable_only=True), [])
        self.assertEqual(ana.list_memories()[0]["kind"], "context")
        self.assertIn("Achar a joia", ana._messages("Joao", "pergunte")[-1]["content"])
        joao.db.close()
        ana.db.close()


if __name__ == "__main__":
    unittest.main()
