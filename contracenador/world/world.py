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
import sqlite3

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


