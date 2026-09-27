"""
mundo.py - o WorldState: a verdade objetiva da simulação.

Diferente da memória de um Ator (o que ELE crê que sabe, em Ator.py), o mundo guarda o que
realmente aconteceu, sem opinião de ninguém. Eventos e evidências vivem aqui; a crença de
cada personagem sobre eles é só o que passar pelo filtro de percepção.

IDEIA CENTRAL (Contracenador_Roadmap.md, seção 10):

    VERDADE (aqui, em mundo.db)  !=  CONHECIMENTO  !=  CRENÇA (em cada Ator.db)

Um `.db` de mundo por cenário (fica em `cenario/mundo.db`, ao lado do `cena.json`). O `cena.json`
continua sendo o "roteiro" gerado pelo Roteirista (papéis, personalidades); o `mundo.db` é o estado
vivo da simulação: onde cada um está, o que aconteceu e quais evidências existem.

ESCOPO DESTA V1 (de propósito, para não superdimensionar antes da hora):
  * Só existe UM local por cenário hoje (o Roteirista ainda não gera múltiplos locais — isso é
    trabalho futuro do Cenógrafo, seção 6 do roadmap). Por isso a percepção automática por local
    em `registrar_evento()` não é usada ainda para decidir quem viu o crime inicial: isso continua
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

# Só na criação do arquivo (mesmo padrão de Ator.py): marca o tipo do banco.
MARCA = """
PRAGMA application_id = 1297044845;  -- 0x4D554E44 = "MUND"
PRAGMA user_version = 1;
"""

TABELAS = """
CREATE TABLE IF NOT EXISTS mundo (chave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS locais (
    nome      TEXT PRIMARY KEY,
    descricao TEXT DEFAULT '',
    publico   INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS personagens (
    nome  TEXT PRIMARY KEY,
    papel TEXT,
    local TEXT REFERENCES locais(nome)
);

CREATE TABLE IF NOT EXISTS objetos (
    nome   TEXT PRIMARY KEY,
    local  TEXT REFERENCES locais(nome),
    estado TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS eventos (
    id      INTEGER PRIMARY KEY,
    tipo    TEXT NOT NULL,      -- 'crime' | 'fala' | 'revelacao' | 'ameaca' | 'movimento' | ...
    ator    TEXT,               -- quem fez/disse (dono da verdade sobre este evento)
    alvo    TEXT,               -- alvo, se houver
    local   TEXT REFERENCES locais(nome),
    publico INTEGER DEFAULT 1,  -- 0 = só quem estava no local percebe (ver perceber_evento)
    dados   TEXT DEFAULT '{}',  -- JSON livre; convenção: {"proposicao": "texto da verdade"}
    hora    REAL
);

CREATE TABLE IF NOT EXISTS evidencias (
    id             INTEGER PRIMARY KEY,
    evento_id      INTEGER REFERENCES eventos(id),
    tipo           TEXT DEFAULT 'testemunho',  -- 'testemunho' | 'fisica' | 'documental' ...
    conteudo       TEXT NOT NULL,
    origem         TEXT,                        -- quem forneceu a evidência
    assunto        TEXT,                        -- de quem/o que ela é evidência (ex.: suspeito)
    confiabilidade REAL DEFAULT 0.7,
    hora           REAL
);
"""


def abrir_mundo(caminho):
    """Abre (ou cria) o arquivo mundo.db de um cenário e garante que as tabelas existem."""
    db = sqlite3.connect(caminho)
    db.row_factory = sqlite3.Row
    db.executescript(PRAGMAS + TABELAS)
    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
        db.executescript(MARCA)
    return db


# ============================================================================
# LOCAIS, PERSONAGENS E OBJETOS
# ============================================================================

def registrar_local(mundo, nome, descricao="", publico=True):
    mundo.execute(
        "INSERT OR REPLACE INTO locais(nome, descricao, publico) VALUES (?, ?, ?)",
        (nome, descricao, int(publico)))
    mundo.commit()


def posicionar(mundo, personagem, local, papel=None):
    """Registra (ou atualiza) onde um personagem está e, opcionalmente, seu papel na cena."""
    existente = mundo.execute("SELECT papel FROM personagens WHERE nome=?", (personagem,)).fetchone()
    papel = papel if papel is not None else (existente["papel"] if existente else None)
    mundo.execute(
        "INSERT OR REPLACE INTO personagens(nome, papel, local) VALUES (?, ?, ?)",
        (personagem, papel, local))
    mundo.commit()


def mover(mundo, personagem, novo_local):
    """Move um personagem e registra o deslocamento como evento (útil para percepção futura)."""
    posicionar(mundo, personagem, novo_local)
    return registrar_evento(mundo, "movimento", ator=personagem, local=novo_local,
                             dados={"proposicao": f"{personagem} foi para {novo_local}"})


def registrar_objeto(mundo, nome, local, estado=""):
    mundo.execute(
        "INSERT OR REPLACE INTO objetos(nome, local, estado) VALUES (?, ?, ?)",
        (nome, local, estado))
    mundo.commit()


def onde_esta(mundo, personagem):
    linha = mundo.execute("SELECT local FROM personagens WHERE nome=?", (personagem,)).fetchone()
    return linha["local"] if linha else None


# ============================================================================
# EVENTOS: a verdade objetiva, ordenada no tempo
# ============================================================================

def registrar_evento(mundo, tipo, ator=None, alvo=None, local=None, dados=None, publico=True):
    """
    Grava um evento (a verdade) e devolve (id_evento, testemunhas), onde `testemunhas` é a
    lista de personagens que PERCEBERAM o evento por estarem no mesmo local - exceto o próprio
    `ator`, que já sabe por definição.

    Se `publico=True` (padrão), todo mundo no local percebe. Se `publico=False`, o evento ainda
    é gravado (é a verdade do mundo), mas ninguém é adicionado como testemunha automaticamente -
    quem souber precisa ser ligado explicitamente via `registrar_evidencia()` (é o caso do crime
    inicial de uma cena, decidido pelo Roteirista, não pela posição de cada um no único local
    que existe hoje - ver nota de escopo no topo do arquivo).
    """
    dados_json = json.dumps(dados or {}, ensure_ascii=False)
    cursor = mundo.execute(
        "INSERT INTO eventos(tipo, ator, alvo, local, publico, dados, hora) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tipo, ator, alvo, local, int(publico), dados_json, time.time()))
    mundo.commit()
    id_evento = cursor.lastrowid

    testemunhas = []
    if publico and local:
        presentes = mundo.execute(
            "SELECT nome FROM personagens WHERE local=?", (local,))
        testemunhas = [p["nome"] for p in presentes if p["nome"] != ator]
    return id_evento, testemunhas


def evento(mundo, id_evento):
    linha = mundo.execute("SELECT * FROM eventos WHERE id=?", (id_evento,)).fetchone()
    if linha is None:
        return None
    d = dict(linha)
    d["dados"] = json.loads(d["dados"] or "{}")
    return d


def buscar_evento_tipo(mundo, tipo):
    """Primeiro evento de um tipo (ex.: buscar_evento_tipo(mundo, 'crime') numa cena de
    investigação, que só tem um)."""
    linha = mundo.execute(
        "SELECT id FROM eventos WHERE tipo=? ORDER BY id LIMIT 1", (tipo,)).fetchone()
    return evento(mundo, linha["id"]) if linha else None


def verdade_evento(mundo, id_evento):
    """A proposição verdadeira de um evento (convenção: dados['proposicao'])."""
    ev = evento(mundo, id_evento)
    return ev["dados"].get("proposicao") if ev else None


def percepcao_textual(mundo, id_evento, testemunha=None):
    """
    Texto que uma testemunha comum recebe ao perceber um evento (v1: sem distorção - a
    testemunha percebe a proposição inteira). Ponto de extensão futuro para embaralhar/perder
    detalhe conforme distância, atenção etc. (roadmap, seção 16).
    """
    ev = evento(mundo, id_evento)
    if ev is None:
        return None
    proposicao = ev["dados"].get("proposicao")
    if proposicao:
        return proposicao
    if ev["tipo"] == "movimento":
        return f"{ev['ator']} esteve em {ev['local']}."
    return f"Algo aconteceu envolvendo {ev['ator']}." if ev["ator"] else "Algo aconteceu."


# ============================================================================
# EVIDÊNCIAS: entidades próprias, sempre ligadas a um evento
# ============================================================================

def registrar_evidencia(mundo, id_evento, conteudo, origem, assunto=None, tipo="testemunho",
                         confiabilidade=0.7):
    cursor = mundo.execute(
        "INSERT INTO evidencias(evento_id, tipo, conteudo, origem, assunto, confiabilidade, hora) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (id_evento, tipo, conteudo, origem, assunto, confiabilidade, time.time()))
    mundo.commit()
    return cursor.lastrowid


def evidencias_do_evento(mundo, id_evento):
    return [dict(e) for e in mundo.execute(
        "SELECT * FROM evidencias WHERE evento_id=? ORDER BY id", (id_evento,))]


def evidencias_por_origem(mundo, origem):
    """Evidências fornecidas por alguém (ex.: o que uma testemunha específica contou), da mais
    recente para a mais antiga. Usado para o investigador ligar 'quem acabou de me contar algo'
    a 'sobre quem era isso', ao formar/atualizar uma crença."""
    return [dict(e) for e in mundo.execute(
        "SELECT * FROM evidencias WHERE origem=? ORDER BY id DESC", (origem,))]
