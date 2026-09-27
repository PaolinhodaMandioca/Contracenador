"""
world.py - o WorldState: a verdade objetiva da simulação.

Diferente da memória de um Ator (o que ELE crê que sabe, em actor.py), o mundo guarda o que
realmente aconteceu, sem opinião de ninguém. Eventos e evidências vivem aqui; a crença de
cada personagem sobre eles é só o que passar pelo filtro de percepção.

IDEIA CENTRAL (Contracenador_Roadmap.md, seção 10):

    VERDADE (aqui, em world.db)  !=  CONHECIMENTO  !=  CRENÇA (em cada Actor.db)

Um `.db` de mundo por cenário (fica em `cenario/world.db`, ao lado do `cena.json`). O `cena.json`
continua sendo o "roteiro" gerado pelo Roteirista (papéis, personalidades); o `world.db` é o
estado vivo da simulação: onde cada um está, o que aconteceu e quais evidências existem.

ESCOPO DESTA V1 (de propósito, para não superdimensionar antes da hora):
  * Só existe UM local por cenário hoje (o Roteirista ainda não gera múltiplos locais — isso é
    trabalho futuro do Cenógrafo, seção 6 do roadmap). Por isso a percepção automática por local
    em `register_event()` não é usada ainda para decidir quem viu o crime inicial: isso continua
    vindo explicitamente do campo `viu` de cada testemunha, gerado pelo Roteirista, e é gravado
    como evidência ligada ao evento do crime.
  * A função de percepção por local já existe e É usada para eventos que acontecem DURANTE a cena
    (revelações, ameaças), preparando o terreno para quando houver múltiplos locais.
  * Sem distorção de percepção (distância/ruído/atenção) ainda - ver seção 16 do roadmap.
"""
import json
import sqlite3
import time

PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
"""

# Só na criação do arquivo (mesmo padrão de actor.py): marca o tipo do banco.
MARK = """
PRAGMA application_id = 1297044845;  -- 0x4D554E44 = "MUND"
PRAGMA user_version = 1;
"""

TABLES = """
CREATE TABLE IF NOT EXISTS world (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS locations (
    name        TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    public      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS characters (
    name     TEXT PRIMARY KEY,
    role     TEXT,
    location TEXT REFERENCES locations(name)
);

CREATE TABLE IF NOT EXISTS objects (
    name     TEXT PRIMARY KEY,
    location TEXT REFERENCES locations(name),
    state    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    type      TEXT NOT NULL,      -- 'crime' | 'speech' | 'revelation' | 'threat' | 'movement' | ...
    actor     TEXT,               -- quem fez/disse (dono da verdade sobre este evento)
    target    TEXT,               -- alvo, se houver
    location  TEXT REFERENCES locations(name),
    public    INTEGER DEFAULT 1,  -- 0 = só quem estava no local percebe (ver perception_text)
    data      TEXT DEFAULT '{}',  -- JSON livre; convenção: {"proposition": "texto da verdade"}
    timestamp REAL
);

CREATE TABLE IF NOT EXISTS evidence (
    id           INTEGER PRIMARY KEY,
    event_id     INTEGER REFERENCES events(id),
    type         TEXT DEFAULT 'testimony',  -- 'testimony' | 'physical' | 'documental' ...
    content      TEXT NOT NULL,
    origin       TEXT,                        -- quem forneceu a evidência
    subject      TEXT,                        -- de quem/o que ela é evidência (ex.: suspeito)
    reliability  REAL DEFAULT 0.7,
    timestamp    REAL
);
"""


def open_world(path):
    """Abre (ou cria) o arquivo world.db de um cenário e garante que as tabelas existem."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(PRAGMAS + TABLES)
    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
        db.executescript(MARK)
    return db


# ============================================================================
# LOCAIS, PERSONAGENS E OBJETOS
# ============================================================================

def register_location(world, name, description="", public=True):
    world.execute(
        "INSERT OR REPLACE INTO locations(name, description, public) VALUES (?, ?, ?)",
        (name, description, int(public)))
    world.commit()


def position(world, character, location, role=None):
    """Registra (ou atualiza) onde um personagem está e, opcionalmente, seu papel na cena."""
    existing = world.execute("SELECT role FROM characters WHERE name=?", (character,)).fetchone()
    role = role if role is not None else (existing["role"] if existing else None)
    world.execute(
        "INSERT OR REPLACE INTO characters(name, role, location) VALUES (?, ?, ?)",
        (character, role, location))
    world.commit()


def move(world, character, new_location):
    """Move um personagem e registra o deslocamento como evento (útil para percepção futura)."""
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


# ============================================================================
# EVENTOS: a verdade objetiva, ordenada no tempo
# ============================================================================

def register_event(world, type_, actor=None, target=None, location=None, data=None, public=True):
    """
    Grava um evento (a verdade) e devolve (event_id, witnesses), onde `witnesses` é a
    lista de personagens que PERCEBERAM o evento por estarem no mesmo local - exceto o próprio
    `actor`, que já sabe por definição.

    Se `public=True` (padrão), todo mundo no local percebe. Se `public=False`, o evento ainda
    é gravado (é a verdade do mundo), mas ninguém é adicionado como testemunha automaticamente -
    quem souber precisa ser ligado explicitamente via `register_evidence()` (é o caso do crime
    inicial de uma cena, decidido pelo Roteirista, não pela posição de cada um no único local
    que existe hoje - ver nota de escopo no topo do arquivo).
    """
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
        witnesses = [p["name"] for p in present if p["name"] != actor]
    return event_id, witnesses


def get_event(world, event_id):
    row = world.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["data"] = json.loads(d["data"] or "{}")
    return d


def find_event_by_type(world, type_):
    """Primeiro evento de um tipo (ex.: find_event_by_type(world, 'crime') numa cena de
    investigação, que só tem um)."""
    row = world.execute(
        "SELECT id FROM events WHERE type=? ORDER BY id LIMIT 1", (type_,)).fetchone()
    return get_event(world, row["id"]) if row else None


def event_truth(world, event_id):
    """A proposição verdadeira de um evento (convenção: data['proposition'])."""
    ev = get_event(world, event_id)
    return ev["data"].get("proposition") if ev else None


def perception_text(world, event_id, witness=None):
    """
    Texto que uma testemunha comum recebe ao perceber um evento (v1: sem distorção - a
    testemunha percebe a proposição inteira). Ponto de extensão futuro para embaralhar/perder
    detalhe conforme distância, atenção etc. (roadmap, seção 16).
    """
    ev = get_event(world, event_id)
    if ev is None:
        return None
    proposition = ev["data"].get("proposition")
    if proposition:
        return proposition
    if ev["type"] == "movement":
        return f"{ev['actor']} esteve em {ev['location']}."
    return f"Algo aconteceu envolvendo {ev['actor']}." if ev["actor"] else "Algo aconteceu."


# ============================================================================
# EVIDÊNCIAS: entidades próprias, sempre ligadas a um evento
# ============================================================================

def register_evidence(world, event_id, content, origin, subject=None, type_="testimony",
                       reliability=0.7):
    cursor = world.execute(
        "INSERT INTO evidence(event_id, type, content, origin, subject, reliability, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, type_, content, origin, subject, reliability, time.time()))
    world.commit()
    return cursor.lastrowid


def evidence_for_event(world, event_id):
    return [dict(e) for e in world.execute(
        "SELECT * FROM evidence WHERE event_id=? ORDER BY id", (event_id,))]


def evidence_by_origin(world, origin):
    """Evidências fornecidas por alguém (ex.: o que uma testemunha específica contou), da mais
    recente para a mais antiga. Usado para o investigador ligar 'quem acabou de me contar algo'
    a 'sobre quem era isso', ao formar/atualizar uma crença."""
    return [dict(e) for e in world.execute(
        "SELECT * FROM evidence WHERE origin=? ORDER BY id DESC", (origin,))]
