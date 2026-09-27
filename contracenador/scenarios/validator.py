"""Schema e validação de dados de cena gerados ou carregados."""
import json
import re

from ..agents.agent import DEFAULT_TRAITS, clamp

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string", "description": "Descrição sucinta do incidente/crime."},
        "characters": {
            "type": "array",
            "description": "Lista dos personagens da cena.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "traits": {
                        "type": "object",
                        "properties": {trait: {"type": "number"} for trait in DEFAULT_TRAITS},
                        "required": list(DEFAULT_TRAITS),
                    },
                    "role": {"type": "string", "enum": ["guilty", "investigator", "witness"]},
                    "truth": {"type": ["string", "null"]},
                    "alibi": {"type": ["string", "null"]},
                    "goal": {"type": ["string", "null"]},
                    "saw": {"type": ["string", "null"]},
                    "saw_effect": {"type": "string", "enum": ["supports", "refutes", "neutral"]},
                },
                "required": ["name", "description", "examples", "traits", "role"],
            },
        },
    },
    "required": ["scene", "characters"],
}


def extract_json(text):
    """Extrai um objeto JSON mesmo com cercas Markdown ou texto ao redor."""
    text = text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as error:
            raise ValueError(f"Não foi possível decodificar o JSON retornado: {error}\nTexto bruto:\n{text}")
    raise ValueError(f"Nenhum objeto JSON encontrado na resposta:\n{text}")


def validate_scene_data(data):
    """Garante os campos obrigatórios e normaliza traços para o intervalo 0..1."""
    if not isinstance(data, dict):
        raise ValueError("A raiz do JSON deve ser um objeto.")
    if "scene" not in data or not isinstance(data["scene"], str):
        raise ValueError("O campo 'scene' é obrigatório e deve ser uma string descritiva.")
    if "characters" not in data or not isinstance(data["characters"], list):
        raise ValueError("O campo 'characters' é obrigatório e deve ser uma lista.")

    characters = data["characters"]
    if len(characters) != 5:
        print(f"   [aviso] A cena tem {len(characters)} personagens (o padrão recomendado é 5).")

    roles = [character.get("role") for character in characters]
    if "guilty" not in roles:
        raise ValueError("A cena precisa ter pelo menos 1 personagem com role 'guilty'.")
    if "investigator" not in roles:
        raise ValueError("A cena precisa ter pelo menos 1 personagem com role 'investigator'.")

    for character in characters:
        name = character.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("Cada personagem deve ter um 'name' válido.")
        role = character.get("role")
        if character.get("saw_effect", "neutral") not in {"supports", "refutes", "neutral"}:
            raise ValueError(f"saw_effect inválido para {name}")
        character.setdefault("saw_effect", "neutral")
        if role == "guilty":
            if not character.get("truth"):
                raise ValueError(f"O culpado ({name}) precisa ter o campo 'truth' preenchido.")
            if not character.get("alibi"):
                raise ValueError(f"O culpado ({name}) precisa ter o campo 'alibi' preenchido.")
        elif role == "investigator" and not character.get("goal"):
            character["goal"] = f"Descobrir quem cometeu o ato em: {data['scene']}"

        traits = character.setdefault("traits", {})
        for trait, default in DEFAULT_TRAITS.items():
            try:
                traits[trait] = clamp(float(traits.get(trait, default)))
            except (ValueError, TypeError):
                traits[trait] = default
        character.setdefault("examples", [])
        character.setdefault("description", f"Personagem {name}")
