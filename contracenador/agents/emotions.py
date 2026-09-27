"""Estado emocional persistido e decaimento temporal."""
import time

HALF_LIFE = {"guilt": 1800, "fear": 900, "frustration": 600}


def state(agent, key):
    row = agent.db.execute("SELECT value, updated_at FROM state WHERE key=?", (key,)).fetchone()
    if row is None:
        return 0.0
    return row["value"] * 0.5 ** ((time.time() - row["updated_at"]) / HALF_LIFE[key])


def change_state(agent, key, delta):
    value = max(0.0, min(1.0, state(agent, key) + delta))
    agent.db.execute(
        "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, time.time()))
    agent.db.commit()