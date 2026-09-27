"""Leitura de cenas e materialização de atores e WorldState."""
import json
import os
import shutil

from ..agents.agent import Actor, create_actor
from ..world import open_world, position, register_evidence, register_event, register_location
from .validator import validate_scene_data


def load_scenario(scenario_folder):
    path = os.path.join(scenario_folder, "cena.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def _clear_folder(folder):
    os.makedirs(folder, exist_ok=True)
    for entry in os.scandir(folder):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.remove(entry.path)


def clear_scene_storage(actors_folder="atores", scenario_folder="cenario"):
    actors_path = os.path.realpath(actors_folder)
    scenario_path = os.path.realpath(scenario_folder)
    try:
        common_path = os.path.commonpath((actors_path, scenario_path))
    except ValueError:
        common_path = None
    if common_path in (actors_path, scenario_path):
        raise ValueError("As pastas de atores e cenário devem ser separadas e não podem conter uma à outra.")

    _clear_folder(actors_folder)
    _clear_folder(scenario_folder)


def materialize_scene(scene_data, actors_folder="atores", scenario_folder="cenario", slots=2):
    """Grava a cena, o WorldState e os bancos SQLite individuais dos atores."""
    validate_scene_data(scene_data)
    clear_scene_storage(actors_folder, scenario_folder)

    characters_data = scene_data["characters"]
    guilty_name = next((character["name"] for character in characters_data
                        if character["role"] == "guilty"), None)
    investigator_name = next((character["name"] for character in characters_data
                              if character["role"] == "investigator"), None)

    print("\n=== Materializando cena ===")
    print(f"Cenário salvo em: '{scenario_folder}/cena.json'")
    print(f"Atores salvos em: '{actors_folder}/'")
    print(f"Cena: {scene_data['scene']}")
    print(f"Culpado: {guilty_name} | Investigador: {investigator_name}")

    meta_path = os.path.join(scenario_folder, "cena.json")
    with open(meta_path, "w", encoding="utf-8") as file:
        json.dump(scene_data, file, ensure_ascii=False, indent=2)
    print(f"Metadados do cenário gravados em: {meta_path}")

    world_path = os.path.join(scenario_folder, "world.db")
    world = open_world(world_path)
    register_location(world, "cena", description=scene_data["scene"], public=False)
    for character in characters_data:
        position(world, character["name"], "cena", role=character["role"])

    crime_event_id = None
    if guilty_name:
        guilty_info = next(character for character in characters_data if character["role"] == "guilty")
        crime_event_id, _ = register_event(
            world, "crime", actor=guilty_name, location="cena",
            data={"proposition": guilty_info["truth"]}, public=False)
    print(f"Mundo (verdade objetiva) gravado em: {world_path}")

    created_actors = {}
    for index, character in enumerate(characters_data):
        name = character["name"]
        role = character["role"]
        db_path = os.path.join(actors_folder, f"{name.lower()}.db")
        create_actor(
            db_path,
            name=name,
            description=character["description"],
            examples=character.get("examples", []),
            traits=character["traits"],
        )
        actor = Actor(db_path, slot=index % slots)

        if role == "guilty":
            truth = character["truth"]
            goal = actor.form_goal("Não ser descoberto", priority=0.9, risk=0.9)
            memory_id = actor.remember(truth, origin="system", sensitivity=0.9, shareable=1,
                                       about=name, effect="supports", protected_by_goal=goal["id"])
            actor.set_false_version(memory_id, character["alibi"])
            print(f"   [Culpado] {name}: gravada a verdade (memória #{memory_id}) e o álibi pré-gerado.")
        elif role == "investigator" and character.get("goal"):
            goal = character["goal"]
            actor.form_goal(goal, priority=0.9)
            actor.remember(f"Incidente informado: {scene_data['scene']}", origin="system",
                           kind="context")
            print(f"   [Investigador] {name}: objetivo definido e contexto público registrado.")
        elif role == "witness":
            saw = character.get("saw")
            if saw:
                memory_id = actor.remember(saw, origin="observation", sensitivity=0.6,
                                           shareable=1, about=guilty_name,
                                           effect=character.get("saw_effect", "neutral"))
                print(f"   [Testemunha] {name}: gravado fato observado sobre {guilty_name} (memória #{memory_id}).")
                if crime_event_id is not None:
                    register_evidence(world, crime_event_id, saw, origin=name, subject=guilty_name,
                                      effect=character.get("saw_effect", "neutral"))
            else:
                print(f"   [Testemunha] {name}: não presenciou nada relevante (sem memórias iniciais).")

        actor.db.close()
        created_actors[name.lower()] = db_path

    world.close()
    print(f"\nSucesso! {len(created_actors)} atores materializados prontos para a cena.")
    return created_actors
