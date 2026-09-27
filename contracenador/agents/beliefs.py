"""Operações de crenças persistidas por agente."""
import json
import time


def _clamp(value):
    return max(0.0, min(1.0, value))


def belief(agent, proposition):
    row = agent.db.execute("SELECT * FROM beliefs WHERE proposition=?",
                           (proposition.strip(),)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["evidence"] = json.loads(result["evidence"] or "[]")
    return result


def form_belief(agent, proposition, confidence=0.5, origin=None, subject=None, evidence=None):
    proposition = proposition.strip()
    existing = belief(agent, proposition)
    if existing:
        return existing
    now = time.time()
    agent.db.execute(
        "INSERT INTO beliefs(proposition, subject, confidence, origin, evidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (proposition, subject, _clamp(confidence), origin,
         json.dumps([evidence] if evidence else []), now, now))
    agent.db.commit()
    return belief(agent, proposition)


def update_belief(agent, proposition, delta, origin=None, subject=None, evidence=None):
    current = form_belief(agent, proposition, confidence=0.5, origin=origin, subject=subject)
    evidence_list = current["evidence"]
    if evidence is not None and evidence in evidence_list:
        return current
    confidence = _clamp(current["confidence"] + delta)
    if evidence is not None:
        evidence_list.append(evidence)
    agent.db.execute(
        "UPDATE beliefs SET confidence=?, origin=?, subject=?, evidence=?, updated_at=? "
        "WHERE proposition=?",
        (confidence, origin or current["origin"], subject or current["subject"],
         json.dumps(evidence_list), time.time(), proposition.strip()))
    agent.db.commit()
    return belief(agent, proposition)


def beliefs_about(agent, subject, k=5):
    rows = agent.db.execute(
        "SELECT * FROM beliefs WHERE subject=? ORDER BY confidence DESC LIMIT ?", (subject, k))
    result = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item["evidence"] or "[]")
        result.append(item)
    return result


def list_beliefs(agent, k=10):
    rows = agent.db.execute("SELECT * FROM beliefs ORDER BY confidence DESC LIMIT ?", (k,))
    result = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item["evidence"] or "[]")
        result.append(item)
    return result