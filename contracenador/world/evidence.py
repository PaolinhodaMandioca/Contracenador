"""Evidências ligadas aos eventos do mundo."""
import time


def register_evidence(world, event_id, content, origin, subject=None, type_="testimony",
                      reliability=0.7):
    reliability = max(0.0, min(1.0, float(reliability)))
    cursor = world.execute(
        "INSERT INTO evidence(event_id, type, content, origin, subject, reliability, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, type_, content, origin, subject, reliability, time.time()))
    world.commit()
    return cursor.lastrowid


def evidence_for_event(world, event_id):
    return [dict(evidence) for evidence in world.execute(
        "SELECT * FROM evidence WHERE event_id=? ORDER BY id", (event_id,))]


def evidence_by_origin(world, origin):
    return [dict(evidence) for evidence in world.execute(
        "SELECT * FROM evidence WHERE origin=? ORDER BY id DESC", (origin,))]