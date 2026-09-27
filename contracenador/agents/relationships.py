"""Relações sociais persistidas entre agentes."""
import time

from .emotions import HALF_LIFE


def relationship(agent, other):
    agent.db.execute("INSERT OR IGNORE INTO relationships(other, updated_at) VALUES (?, ?)",
                     (other, time.time()))
    relation = dict(agent.db.execute(
        "SELECT * FROM relationships WHERE other=?", (other,)).fetchone())
    relation["fear"] *= 0.5 ** ((time.time() - relation["updated_at"]) / HALF_LIFE["fear"])
    return relation


def change_relationship(agent, other, **deltas):
    relation = relationship(agent, other)
    for field, delta in deltas.items():
        relation[field] = max(0.0, min(1.0, relation[field] + delta))
    agent.db.execute(
        "UPDATE relationships SET trust=?, fear=?, distrust=?, favor_owed=?, "
        "updated_at=? WHERE other=?",
        (relation["trust"], relation["fear"], relation["distrust"],
         relation["favor_owed"], time.time(), other))
    agent.db.commit()