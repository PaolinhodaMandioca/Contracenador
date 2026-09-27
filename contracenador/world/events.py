"""Eventos objetivos e percepção básica de acontecimentos."""
import json
import time


def register_event(world, type_, actor=None, target=None, location=None, data=None, public=True):
    """Grava um evento e devolve seu id e as testemunhas presentes no local."""
    data_json = json.dumps(data or {}, ensure_ascii=False)
    cursor = world.execute(
        "INSERT INTO events(type, actor, target, location, public, data, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (type_, actor, target, location, int(public), data_json, time.time()))
    world.commit()
    event_id = cursor.lastrowid

    witnesses = []
    if public and location:
        present = world.execute(
            "SELECT name FROM characters WHERE location=?", (location,))
        witnesses = [person["name"] for person in present if person["name"] != actor]
    return event_id, witnesses


def get_event(world, event_id):
    row = world.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["data"] = json.loads(result["data"] or "{}")
    return result


def find_event_by_type(world, type_):
    row = world.execute(
        "SELECT id FROM events WHERE type=? ORDER BY id LIMIT 1", (type_,)).fetchone()
    return get_event(world, row["id"]) if row else None


def event_truth(world, event_id):
    event = get_event(world, event_id)
    return event["data"].get("proposition") if event else None


def perception_text(world, event_id, witness=None):
    """Devolve a descrição ainda não distorcida de um evento percebido."""
    event = get_event(world, event_id)
    if event is None:
        return None
    proposition = event["data"].get("proposition")
    if proposition:
        return proposition
    if event["type"] == "movement":
        return f"{event['actor']} esteve em {event['location']}."
    return f"Algo aconteceu envolvendo {event['actor']}." if event["actor"] else "Algo aconteceu."