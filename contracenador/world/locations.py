"""Locais, posições de personagens e objetos do mundo."""
from .events import register_event


def register_location(world, name, description="", public=True):
    world.execute(
        "INSERT OR REPLACE INTO locations(name, description, public) VALUES (?, ?, ?)",
        (name, description, int(public)))
    world.commit()


def position(world, character, location, role=None):
    existing = world.execute("SELECT role FROM characters WHERE name=?", (character,)).fetchone()
    role = role if role is not None else (existing["role"] if existing else None)
    world.execute(
        "INSERT OR REPLACE INTO characters(name, role, location) VALUES (?, ?, ?)",
        (character, role, location))
    world.commit()


def move(world, character, new_location):
    position(world, character, new_location)
    return register_event(world, "movement", actor=character, location=new_location,
                          data={"proposition": f"{character} foi para {new_location}"})


def register_object(world, name, location, state=""):
    world.execute(
        "INSERT OR REPLACE INTO objects(name, location, state) VALUES (?, ?, ?)",
        (name, location, state))
    world.commit()


def where_is(world, character):
    row = world.execute("SELECT location FROM characters WHERE name=?", (character,)).fetchone()
    return row["location"] if row else None