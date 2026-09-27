"""Evidências ligadas aos eventos do mundo."""
import time

EVIDENCE_EFFECTS = {"supports": 1, "refutes": -1, "neutral": 0}


def register_evidence(world, event_id, content, origin, subject=None, type_="testimony",
                      reliability=0.7, effect="neutral"):
    if effect not in EVIDENCE_EFFECTS:
        raise ValueError("Efeito da evidência deve ser supports, refutes ou neutral")
    reliability = max(0.0, min(1.0, float(reliability)))
    cursor = world.execute(
        "INSERT INTO evidence(event_id, type, content, origin, subject, reliability, timestamp, effect) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, type_, content, origin, subject, reliability, time.time(), effect))
    world.commit()
    return cursor.lastrowid


def delivered_evidence(world, evidence_id, recipient):
    """Lê uma evidência específica somente se foi dita ao destinatário indicado."""
    row = world.execute(
        "SELECT evidence.* FROM evidence JOIN events ON events.id=evidence.event_id "
        "WHERE evidence.id=? AND events.target=? AND events.type='revelation'",
        (evidence_id, recipient)).fetchone()
    return dict(row) if row else None


def evidence_for_event(world, event_id):
    return [dict(evidence) for evidence in world.execute(
        "SELECT * FROM evidence WHERE event_id=? ORDER BY id", (event_id,))]


def evidence_by_origin(world, origin):
    return [dict(evidence) for evidence in world.execute(
        "SELECT * FROM evidence WHERE origin=? ORDER BY id DESC", (origin,))]
