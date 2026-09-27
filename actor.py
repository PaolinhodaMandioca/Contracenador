"""
actor.py - o "cérebro" de cada ator.

UM ator = UM arquivo .db (SQLite) com tudo dentro:
    config        -> personalidade (nome, jeito de falar, exemplos, traços numéricos)
    memories      -> o que o ator sabe (com origem, assunto, sensibilidade e uma versão falsa)
    state         -> emoções que mudam e esmaecem com o tempo (culpa, frustração)
    relationships -> o que ele sente por cada outro ator (confiança, medo, ...)

IDEIA CENTRAL: quem DECIDE (revelar? esconder? mentir? ameaçar?) é o CÓDIGO
(`choose_action` e `choose_tactic`), não o modelo. O LLM só recebe uma instrução
concreta e escreve a fala. Vantagens:
  * zero inferência extra para decidir (CPU poupada);
  * comportamento previsível, com pesos que você ajusta e testa;
  * a verdade nem entra no prompt quando o ator vai esconder ou mentir,
    então o modelo pequeno não tem como "vazar" o que não recebeu.
"""
import array
import json
import math
import random
import re
import sqlite3
import time
import unicodedata

import colors

# ============================================================================
# 1) CONFIGURAÇÕES GERAIS
# ============================================================================

# Em quantos segundos cada emoção cai pela metade. O decaimento é "preguiçoso":
# só é calculado quando o valor é lido (nada fica rodando em segundo plano).
HALF_LIFE = {"guilt": 1800, "fear": 900, "frustration": 600}

# Traços de personalidade (0 a 1). Servem de "pesos" nas funções de decisão.
DEFAULT_TRAITS = {
    "honesty": 0.5,          # tendência a falar a verdade
    "deceit": 0.5,           # habilidade/vontade de enganar
    "empathy": 0.5,          # sente mais culpa ao mentir; pressiona menos os outros
    "courage": 0.5,          # resiste melhor a ameaças
    "aggressiveness": 0.5,   # tendência a pressionar e ameaçar
    "greed": 0.5,            # quanto quer arrancar informação dos outros
}

# ============================================================================
# 2) O ARQUIVO .db (esquema do banco)
# ============================================================================

# WAL = vários leitores + um escritor sem travar; synchronous NORMAL é seguro em WAL e mais rápido.
PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
"""

# Só na criação do arquivo: marca que ele é de um ator e guarda a versão do esquema
# (para migrar arquivos antigos no futuro sem precisar de outra extensão).
MARK = """
PRAGMA application_id = 1095192148;  -- 0x41474E54 = "AGNT"
PRAGMA user_version = 2;
"""

TABLES = """
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS memories (
    id             INTEGER PRIMARY KEY,
    text           TEXT NOT NULL,
    timestamp      REAL,                    -- quando foi salvo (time.time())
    origin         TEXT DEFAULT 'user',     -- 'user' ou o nome do ator que contou
    shareable      INTEGER DEFAULT 1,       -- 0 = nunca sai deste ator
    sensitivity    REAL DEFAULT 0.3,        -- 0 = qualquer um pode saber ... 1 = segredo
    false_version  TEXT,                    -- versão pré-gerada, usada só quando ele mentir
    about          TEXT,                    -- nome de quem é o assunto (ex.: outro ator), se houver
    source_id      INTEGER,                 -- id da memória ORIGINAL de quem contou (mesmo fato-base
                                             -- na cabeça de quem contou); permite notar quando ela
                                             -- muda de versão sobre a MESMA coisa - ver detecção de
                                             -- contradição em receive()
    contradictory  INTEGER DEFAULT 0,       -- 1 = essa memória entrou em conflito com outra já
                                             -- registrada da mesma origem sobre o mesmo source_id
    embedding      BLOB                     -- vetor de embedding (calculado sob demanda na 1ª
                                             -- busca semântica, ver recall_semantic); NULL até lá,
                                             -- e sempre NULL se o servidor não suportar
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY, value REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS relationships (
    other         TEXT PRIMARY KEY,
    trust         REAL DEFAULT 0.5,
    fear          REAL DEFAULT 0,
    distrust      REAL DEFAULT 0,
    favor_owed    REAL DEFAULT 0,   -- o quanto EU devo a esse ator (ele me contou coisas)
    updated_at    REAL
);

-- Crença = uma proposição em que este Ator confia mais ou menos (0 a 1), e que evidências vão
-- ajustando aos poucos. Serve tanto para crença social ("Maria confia em mim") quanto para
-- hipótese de investigação ("João é o culpado") - roadmap, seções 12 e 22, tratadas aqui como o
-- MESMO mecanismo, só com `subject` diferente. Diferente de `memories`: memória é "eu soube que
-- X", crença é "o quanto eu acho que X é verdade" (pode subir e descer com o tempo).
-- Objetivo estruturado (roadmap, seção 17): não é só uma memória de texto solta, tem prioridade,
-- progresso e risco - o código usa isso para decidir quais AÇÕES ficam disponíveis (ver
-- choose_action), não só o que dizer.
CREATE TABLE IF NOT EXISTS goals (
    id            INTEGER PRIMARY KEY,
    description   TEXT NOT NULL UNIQUE,
    priority      REAL DEFAULT 0.5,
    progress      REAL DEFAULT 0.0,
    risk          REAL DEFAULT 0.0,
    status        TEXT DEFAULT 'active',    -- 'active' | 'done' | 'failed'
    created_at    REAL,
    updated_at    REAL
);

CREATE TABLE IF NOT EXISTS beliefs (
    id            INTEGER PRIMARY KEY,
    proposition   TEXT NOT NULL UNIQUE,   -- ex.: "Joao é o culpado"
    subject       TEXT,                   -- de quem/o que é a crença (ex.: "Joao") - permite listar por assunto
    confidence    REAL DEFAULT 0.5,
    origin        TEXT,                   -- quem/o que motivou a última mudança
    evidence      TEXT DEFAULT '[]',      -- JSON: lista de referências soltas (ex.: ids de world.py)
    created_at    REAL,
    updated_at    REAL
);
"""


def open_database(path):
    """Abre (ou cria) o arquivo .db de um ator e garante que as tabelas existem."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row  # permite ler colunas por nome: row["text"]
    db.executescript(PRAGMAS + TABLES)
    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
        db.executescript(MARK)
    _migrate(db)
    return db


def _migrate(db):
    """
    Ajusta bancos .db criados por uma versão anterior do projeto, sem apagar nada do que já
    está salvo. Cobre tanto colunas novas (ALTER TABLE ADD COLUMN) quanto o esquema antigo em
    português, que é renomeado coluna a coluna via ALTER TABLE ... RENAME COLUMN (suportado
    desde o SQLite 3.25).
    """
    old_to_new = {
        "texto": "text", "data": "timestamp", "origem": "origin", "compartilhavel": "shareable",
        "sensibilidade": "sensitivity", "versao_falsa": "false_version", "sobre": "about",
        "origem_id": "source_id", "contraditoria": "contradictory",
    }
    columns = {row["name"] for row in db.execute("PRAGMA table_info(memories)")}
    for old_name, new_name in old_to_new.items():
        if old_name in columns and new_name not in columns:
            db.execute(f"ALTER TABLE memories RENAME COLUMN {old_name} TO {new_name}")
            db.commit()
            columns.discard(old_name)
            columns.add(new_name)

    if "about" not in columns and "sobre" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN about TEXT")
        db.commit()
        columns.add("about")
    if "source_id" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN source_id INTEGER")
        db.commit()
    if "contradictory" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN contradictory INTEGER DEFAULT 0")
        db.commit()
    if "embedding" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
        db.commit()


def create_actor(path, name, description, examples, traits):
    """Cria o arquivo .db de um ator novo, já com a personalidade gravada."""
    personality = {
        "name": name,
        "description": description,  # jeito de falar e de ser (vai para o prompt)
        "examples": examples,        # frases de exemplo (modelos pequenos imitam melhor do que obedecem)
        "traits": {**DEFAULT_TRAITS, **traits},
    }
    db = open_database(path)
    db.execute("INSERT OR REPLACE INTO config(key, value) VALUES ('personality', ?)",
               (json.dumps(personality, ensure_ascii=False),))
    db.commit()
    db.close()


# ============================================================================
# 3) FUNÇÕES AUXILIARES
# ============================================================================

def sigmoid(x):
    """Transforma qualquer número em uma probabilidade entre 0 e 1."""
    x = max(-30.0, min(30.0, x))
    return 1 / (1 + math.exp(-x))


def clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, value))


def bar(value, width=10):
    """Barra de progresso textual para o painel de debug (ex.: '███████░░░ 0.71')."""
    value = clamp(value)
    full = round(value * width)
    return "█" * full + "░" * (width - full) + f" {value:.2f}"


# Valores de `origin` que significam "eu sei disso por mim mesmo" (vivi/vi/fui informado
# diretamente), não "outra pessoa me contou". Qualquer outro valor de origin é o NOME de quem
# contou - ou seja, é uma fofoca/relato de terceiro (ver respond(), item 4).
OWN_ORIGINS = {"system", "observation", "user"}

# Palavras que não ajudam a achar memórias parecidas.
COMMON_WORDS = {
    "que", "com", "por", "para", "pra", "uma", "dos", "das", "nos", "nas", "mas", "como",
    "mais", "isso", "esse", "essa", "este", "esta", "qual", "quem", "onde", "sobre",
    "voce", "ele", "ela", "seu", "sua", "meu", "minha", "foi", "tem", "sao", "ser",
    "tudo", "algo", "muito", "aqui", "estou", "sei", "quer", "diga", "conta", "disse",
}


def normalize(text):
    """Minúsculas e sem acento (ex.: 'É a Bia' -> 'e a bia'). Usada na busca de memória e
    também para reconhecer o nome de um ator dentro de um texto (ver main.py)."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def words(text):
    """
    Busca de memória SIMPLES (sem embeddings, custo de CPU praticamente zero):
    quebra o texto em palavras importantes - sem acento/maiúscula, sem palavras
    comuns e cortadas em 5 letras (truque barato para 'segredo' casar com 'segredos').
    """
    return {p[:5] for p in re.findall(r"[a-z0-9]+", normalize(text))
            if len(p) >= 3 and p not in COMMON_WORDS}


def verbalized(fact_text, response):
    """
    Checa se `response` (o texto que o LLM realmente disse) chegou a passar o conteúdo de
    `fact_text` (o que o CÓDIGO decidiu que devia ser dito) - mesma lógica de palavras-chave de
    `words()`, sem exigir o texto exato (o LLM sempre parafraseia). Existe porque o LLM às vezes
    ignora a instrução "conte isso: ..." e simplesmente muda de assunto; sem essa checagem, o
    jogo registrava o fato como revelado/mentido pra todo mundo (memória, contradição, crenças)
    mesmo quando a fala impressa na tela não dizia nada daquilo - ver respond().
    """
    fact_words = words(fact_text)
    if not fact_words:
        return True
    overlap = fact_words & words(response)
    return len(overlap) >= max(1, len(fact_words) // 2)


def detect_subject(text, names):
    """
    Acha, dentro do texto, o nome de um ator conhecido - ignora acento/maiúscula e
    respeita fronteira de palavra (então 'Bianca' não casa com 'Bia'). Devolve o nome
    (na grafia original) se achar exatamente um; None se não achar ou achar mais de um.
    Usado tanto pelo orquestrador (main.py) quanto por choose_investigation_target() abaixo,
    para interpretar a resposta em texto livre de um LLM e casá-la com uma lista de nomes válidos.
    """
    target = normalize(text)
    matches = [name for name in names if re.search(rf"\b{re.escape(normalize(name))}\b", target)]
    return matches[0] if len(matches) == 1 else None


# ============================================================================
# 3b) MEMÓRIA SEMÂNTICA: vetores de embedding (roadmap, seção 8)
# ============================================================================
# Guardados como BLOB (array de floats, sem depender de numpy) em vez de JSON: mais compacto
# e sem custo de parsing de texto a cada leitura.

def _serialize_vector(vector):
    return array.array("f", vector).tobytes()


def _deserialize_vector(blob):
    vector = array.array("f")
    vector.frombytes(blob)
    return vector.tolist()


def _cosine_similarity(a, b):
    """Similaridade de cosseno entre dois vetores (1 = mesma direção, 0 = ortogonais). Puro
    Python de propósito: os vetores são poucos por Ator, não vale a pena depender de numpy."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ============================================================================
# 4) O ATOR
# ============================================================================

class Actor:
    def __init__(self, path, slot=0):
        self.path = path
        self.slot = slot  # slot do llama-server reservado a este ator (cache do prefixo)
        self.db = open_database(path)
        row = self.db.execute("SELECT value FROM config WHERE key='personality'").fetchone()
        if row is None:
            raise ValueError(f"{path} não tem personalidade. Crie o ator com create_actor().")
        self.personality = json.loads(row["value"])
        self.name = self.personality["name"]

        # Coisas que vivem só na RAM (somem quando o programa fecha):
        self.history = {}         # conversa recente com cada interlocutor
        self.disclosed = set()    # (interlocutor, id_da_memoria) que já foram revelados de verdade
        self.stances = {}         # (interlocutor, id_da_memoria) -> (decisão, placar) da última decisão
        self.satisfied = set()    # interlocutores de quem já consegui a informação que queria
        self.waiting = False      # True se o último passo foi pedir algo e a resposta ainda não veio
        self.verbose = True       # imprime no terminal as decisões internas (bom para ajustar pesos)
        self.evasion_count = {}   # interlocutor -> nº de vezes que desviei (roda táticas de evasão)
        self.debug = False        # modo debug: imprime o painel completo após cada resposta
        self.last_decision = {}   # interlocutor -> dados do último turno (para o painel de debug)
        self._no_embedding = False  # True assim que o servidor recusar embeddings 1x (ver _recall_best)

    def _log(self, message):
        if self.verbose:
            print(colors.dim(f"   . {message}"))

    def save_personality(self):
        self.db.execute("INSERT OR REPLACE INTO config(key, value) VALUES ('personality', ?)",
                        (json.dumps(self.personality, ensure_ascii=False),))
        self.db.commit()

    # ------------------------------------------------------------------
    # 4.1) MEMÓRIA: salvar e buscar fatos
    # ------------------------------------------------------------------

    def remember(self, text, origin="user", sensitivity=0.3, shareable=1, about=None,
                 source_id=None):
        """
        Salva um fato. Gravar é só um INSERT (não reescreve o arquivo inteiro).
        `about` é o nome de quem é o assunto do fato (ex.: outro ator) - ver knows_about().
        `source_id` é o id da memória ORIGINAL na cabeça de quem contou (mesmo fato-base) -
        permite notar quando a mesma origem muda de versão sobre a mesma coisa, ver receive().
        """
        text = text.strip()
        existing = self.db.execute("SELECT id FROM memories WHERE text=?", (text,)).fetchone()
        if existing:  # não duplica o mesmo fato
            return existing["id"]
        cursor = self.db.execute(
            "INSERT INTO memories(text, timestamp, origin, shareable, sensitivity, about, source_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (text, time.time(), origin, shareable, sensitivity, about, source_id))
        self.db.commit()
        return cursor.lastrowid

    def memory_id_by_text(self, text):
        row = self.db.execute("SELECT id FROM memories WHERE text=?", (text.strip(),)).fetchone()
        return row["id"] if row else None

    def contradictions(self, k=5):
        """Memórias marcadas como contraditórias (ver receive()), da mais recente pra mais antiga."""
        return [dict(m) for m in self.db.execute(
            "SELECT * FROM memories WHERE contradictory=1 ORDER BY id DESC LIMIT ?", (k,))]

    def recall(self, query, k=3, shareable_only=False):
        """
        Devolve até k memórias parecidas com a consulta (mais palavras em comum = melhor;
        empate = a mais recente). Só as poucas memórias relevantes entram no prompt, e é
        isso que mantém o contexto curto (e o modelo pequeno).
        `shareable_only=True` é o FILTRO DE ISOLAMENTO: nas conversas com outros
        atores, o que é privado nem é lido do banco, então não tem como vazar.
        Busca por PALAVRA: não pega paráfrase (ver recall_semantic para isso).
        """
        wanted = words(query)
        sql = "SELECT * FROM memories" + (" WHERE shareable=1" if shareable_only else "")
        candidates = []
        for memory in self.db.execute(sql):
            score = len(wanted & words(memory["text"]))
            if score > 0:
                candidates.append((score, memory["timestamp"], dict(memory)))
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        return [c[2] for c in candidates[:k]]

    def recall_semantic(self, query, llm, k=3, shareable_only=False):
        """
        Como recall(), mas por SIGNIFICADO em vez de palavra em comum: usa o embedding do
        próprio LLM (LLM.embedding em llm.py) - o mesmo servidor de sempre, sem modelo nem
        dependência extra (roadmap, seção 8). Pega paráfrases que recall() não pegaria (ex.:
        "perto do cofre" e "saindo do escritório" podem ficar próximos no espaço de embeddings
        mesmo sem nenhuma palavra igual).

        Embeddings de cada memória são calculados PREGUIÇOSAMENTE (só na primeira vez que ela
        entra numa busca semântica) e ficam salvos no `.db` - buscas seguintes gastam UMA
        chamada ao LLM (a da consulta), não uma por memória.

        Levanta RuntimeError se o servidor não tiver o endpoint de embedding (não foi iniciado
        com --embeddings). Quem chama decide o que fazer - ver `_recall_best`, que cai para
        `recall()` nesse caso; esta função em si NUNCA cai sozinha para a busca lexical.
        """
        sql = "SELECT * FROM memories" + (" WHERE shareable=1" if shareable_only else "")
        candidates = [dict(m) for m in self.db.execute(sql)]
        if not candidates:
            return []

        query_vector = llm.embedding(query)

        scored = []
        for memory in candidates:
            if memory["embedding"] is None:
                vector = llm.embedding(memory["text"])
                self.db.execute("UPDATE memories SET embedding=? WHERE id=?",
                                (_serialize_vector(vector), memory["id"]))
                self.db.commit()
            else:
                vector = _deserialize_vector(memory["embedding"])
            similarity = _cosine_similarity(query_vector, vector)
            scored.append((similarity, memory["timestamp"], memory))

        scored.sort(key=lambda c: (c[0], c[1]), reverse=True)
        return [c[2] for c in scored[:k]]

    def _recall_best(self, query, llm, k=3, shareable_only=False):
        """
        COMBINA busca lexical com semântica, em vez de a semântica substituir a lexical.

        Por quê: testado ao vivo contra o llama-server real, o embedding de um modelo genérico
        de chat (não treinado com objetivo de embedding) é um sinal RUIDOSO - em alguns casos
        rankeia uma memória completamente aleatória acima da que realmente importa. Não dá pra
        confiar nele sozinho para decidir a ORDEM. Mas ele ainda é útil para achar candidatos
        que a busca por palavra jamais acharia (paráfrases sem nenhuma palavra em comum).

        Por isso: a lexical roda sempre primeiro (garante que combinação óbvia de palavra nunca
        se perde por causa de um ranking semântico ruim); a semântica só ACRESCENTA candidatos
        que a lexical não achou, sem reordenar os que ela já achou.

        Se o servidor não suportar embeddings (não iniciado com --embeddings) ou der erro de
        rede, usa só a lexical - nunca quebra uma conversa por causa disso. Uma vez que o
        servidor falha, fica assim pelo resto da sessão (não fica tentando de novo a cada turno).
        """
        lexical = self.recall(query, k=k, shareable_only=shareable_only)
        if getattr(self, "_no_embedding", False):
            return lexical

        try:
            semantic = self.recall_semantic(query, llm, k=k, shareable_only=shareable_only)
        except RuntimeError:
            self._no_embedding = True
            return lexical

        seen = {m["id"] for m in lexical}
        combined = lexical + [m for m in semantic if m["id"] not in seen]
        return combined[:k]

    def list_memories(self):
        return [dict(m) for m in self.db.execute("SELECT * FROM memories ORDER BY id")]

    def knows_about(self, who, k=2):
        """
        RECONHECIMENTO: fatos compartilháveis cujo assunto (`about`) é `who`. É o que
        permite um ator perceber que já conhece a pessoa com quem está falando.
        Diferente de recall(): não depende de bater palavra com o assunto do momento
        (o reconhecimento vale a conversa toda) e não passa pelo filtro de revelar/
        esconder/mentir em choose_action() - não é fofoca sendo repassada a um terceiro, é o
        que o Ator já sabe sobre a própria pessoa à sua frente.
        """
        rows = self.db.execute(
            "SELECT * FROM memories WHERE shareable=1 AND about=? ORDER BY id DESC LIMIT ?",
            (who, k))
        return [dict(m) for m in rows]

    def generate_false_version(self, memory_id, llm):
        """
        Gera UMA vez (na hora de salvar) uma versão falsa mas plausível do fato e guarda
        no banco. Assim, mentir depois não custa nenhuma inferência extra.
        Um modelo pequeno às vezes devolve a frase igual: tentamos de novo uma vez com mais
        "criatividade". (Se falhar, dá para escrever a mentira à mão com o comando /falsa.)
        """
        memory = self.db.execute("SELECT text FROM memories WHERE id=?", (memory_id,)).fetchone()
        request = [
            {"role": "system", "content": "Você reescreve frases. Responda só com a frase reescrita."},
            {"role": "user", "content":
                "Reescreva a frase trocando UM detalhe importante (lugar, número, nome ou objeto) "
                f'para que ela fique falsa, mas plausível.\nFrase: "{memory["text"]}"'},
        ]
        for temperature in (0.8, 1.1):
            false = llm.generate(request, max_tokens=60, temperature=temperature).strip().strip('"')
            if false and false != memory["text"]:
                self.set_false_version(memory_id, false)
                return false
        return None

    def set_false_version(self, memory_id, text):
        cursor = self.db.execute("UPDATE memories SET false_version=? WHERE id=?",
                                 (text.strip(), memory_id))
        self.db.commit()
        return cursor.rowcount > 0

    def set_about(self, memory_id, name):
        """Define (ou remove, com name=None/vazio) o assunto de uma memória já salva."""
        cursor = self.db.execute("UPDATE memories SET about=? WHERE id=?",
                                 (name.strip() if name else None, memory_id))
        self.db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # 4.1b) CRENÇAS: o quanto confio em cada proposição (verdade != crença, seção 10)
    # ------------------------------------------------------------------

    def belief(self, proposition):
        """Devolve a crença (dict) para uma proposição exata, ou None se não existir ainda."""
        row = self.db.execute("SELECT * FROM beliefs WHERE proposition=?",
                              (proposition.strip(),)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["evidence"] = json.loads(d["evidence"] or "[]")
        return d

    def form_belief(self, proposition, confidence=0.5, origin=None, subject=None, evidence=None):
        """Cria a crença se a proposição ainda não existir (não duplica). Devolve a crença atual."""
        proposition = proposition.strip()
        existing = self.belief(proposition)
        if existing:
            return existing
        now = time.time()
        self.db.execute(
            "INSERT INTO beliefs(proposition, subject, confidence, origin, evidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (proposition, subject, clamp(confidence), origin,
             json.dumps([evidence] if evidence else []), now, now))
        self.db.commit()
        return self.belief(proposition)

    def update_belief(self, proposition, delta, origin=None, subject=None, evidence=None):
        """
        Ajusta a confiança de uma crença por `delta` (positivo reforça, negativo enfraquece),
        criando-a com confiança-base 0.5 se ainda não existir. Cada evidência que motivou a
        mudança fica registrada (útil para reconstruir por que o Ator acredita nisso).
        """
        c = self.form_belief(proposition, confidence=0.5, origin=origin, subject=subject)
        new_confidence = clamp(c["confidence"] + delta)
        evidence_list = c["evidence"]
        if evidence and evidence not in evidence_list:
            evidence_list.append(evidence)
        self.db.execute(
            "UPDATE beliefs SET confidence=?, origin=?, subject=?, evidence=?, updated_at=? "
            "WHERE proposition=?",
            (new_confidence, origin or c["origin"], subject or c["subject"],
             json.dumps(evidence_list), time.time(), proposition.strip()))
        self.db.commit()
        return self.belief(proposition)

    def beliefs_about(self, subject, k=5):
        """Crenças cujo assunto é `subject`, da mais para a menos confiante (ex.: hipóteses de
        um investigador sobre um suspeito específico)."""
        rows = self.db.execute(
            "SELECT * FROM beliefs WHERE subject=? ORDER BY confidence DESC LIMIT ?", (subject, k))
        result = []
        for row in rows:
            d = dict(row)
            d["evidence"] = json.loads(d["evidence"] or "[]")
            result.append(d)
        return result

    def list_beliefs(self, k=10):
        rows = self.db.execute("SELECT * FROM beliefs ORDER BY confidence DESC LIMIT ?", (k,))
        result = []
        for row in rows:
            d = dict(row)
            d["evidence"] = json.loads(d["evidence"] or "[]")
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # 4.1c) OBJETIVOS: metas com prioridade/progresso/risco (roadmap, seção 17)
    # ------------------------------------------------------------------

    def goal(self, description):
        row = self.db.execute("SELECT * FROM goals WHERE description=?",
                              (description.strip(),)).fetchone()
        return dict(row) if row else None

    def form_goal(self, description, priority=0.5, risk=0.0):
        """Cria o objetivo se ainda não existir (não duplica). Devolve o objetivo atual."""
        description = description.strip()
        existing = self.goal(description)
        if existing:
            return existing
        now = time.time()
        self.db.execute(
            "INSERT INTO goals(description, priority, progress, risk, status, created_at, updated_at) "
            "VALUES (?, ?, 0.0, ?, 'active', ?, ?)",
            (description, clamp(priority), clamp(risk), now, now))
        self.db.commit()
        return self.goal(description)

    def update_goal(self, description, progress=None, status=None):
        g = self.goal(description)
        if g is None:
            return None
        progress = clamp(progress) if progress is not None else g["progress"]
        status = status or g["status"]
        self.db.execute("UPDATE goals SET progress=?, status=?, updated_at=? WHERE description=?",
                        (progress, status, time.time(), description))
        self.db.commit()
        return self.goal(description)

    def active_goals(self, k=5):
        rows = self.db.execute(
            "SELECT * FROM goals WHERE status='active' ORDER BY priority DESC LIMIT ?", (k,))
        return [dict(g) for g in rows]

    def main_goal(self):
        """O objetivo ativo de maior prioridade, ou None se não houver nenhum - usado pelo
        código para liberar (ou não) ações mais arriscadas, como DEFLECT em choose_action()."""
        active = self.active_goals(k=1)
        return active[0] if active else None

    # ------------------------------------------------------------------
    # 4.2) ESTADO EMOCIONAL: culpa e frustração (com decaimento preguiçoso)
    # ------------------------------------------------------------------

    def state(self, key):
        """Valor atual (0 a 1). Guardamos valor + hora da última mudança e calculamos aqui
        quanto ele já esmaeceu: valor * 0.5 ** (tempo_passado / meia_vida)."""
        row = self.db.execute("SELECT value, updated_at FROM state WHERE key=?",
                              (key,)).fetchone()
        if row is None:
            return 0.0
        return row["value"] * 0.5 ** ((time.time() - row["updated_at"]) / HALF_LIFE[key])

    def change_state(self, key, delta):
        new_value = clamp(self.state(key) + delta)
        self.db.execute(
            "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, new_value, time.time()))
        self.db.commit()

    # ------------------------------------------------------------------
    # 4.3) RELAÇÕES: o que sinto por cada outro ator
    # ------------------------------------------------------------------

    def relationship(self, other):
        self.db.execute("INSERT OR IGNORE INTO relationships(other, updated_at) VALUES (?, ?)",
                        (other, time.time()))
        r = dict(self.db.execute("SELECT * FROM relationships WHERE other=?", (other,)).fetchone())
        # O medo esmaece com o tempo, como as outras emoções.
        r["fear"] *= 0.5 ** ((time.time() - r["updated_at"]) / HALF_LIFE["fear"])
        return r

    def change_relationship(self, other, **deltas):
        r = self.relationship(other)
        for field, delta in deltas.items():
            r[field] = clamp(r[field] + delta)
        self.db.execute(
            "UPDATE relationships SET trust=?, fear=?, distrust=?, favor_owed=?, "
            "updated_at=? WHERE other=?",
            (r["trust"], r["fear"], r["distrust"], r["favor_owed"], time.time(), other))
        self.db.commit()

    # ------------------------------------------------------------------
    # 4.4) DECISÕES (o coração do comportamento) - tudo em código, sem LLM
    # ------------------------------------------------------------------

    def _choose_scapegoat(self, other):
        """
        Escolhe um terceiro conhecido (que não seja `other` nem eu) para culpar em DEFLECT.
        Só considera quem já tem uma relação registrada - ou seja, quem já está "na sala",
        já que `relationship()` cria essa linha para todo participante presente no início da
        cena (ver cmd_scene em main.py). Sem candidato, DEFLECT simplesmente não é oferecido.
        """
        candidates = [row["other"] for row in
                     self.db.execute("SELECT other FROM relationships WHERE other != ?", (other,))]
        return random.choice(candidates) if candidates else None

    def choose_action(self, fact, other):
        """
        O que fazer com UM fato quando `other` pergunta sobre ele? Generaliza o antigo
        REVEAL/HIDE/LIE (roadmap, seções 18-19): quem tem um objetivo ativo de alta
        prioridade (ex.: "Não ser descoberto") e o perfil certo para isso pode arriscar DEFLECT
        a suspeita para um terceiro, em vez de só se esquivar. Não é uma opção sempre
        disponível - é uma AÇÃO POSSÍVEL que o código libera conforme a situação, não o LLM
        que inventa.

        Retorna (decision, extra, reveal_chance):
          decision = REVEAL | HIDE | LIE | DEFLECT
          extra    = None, exceto para DEFLECT, onde é {"fake_target": nome}
        """
        t = self.personality["traits"]
        rel = self.relationship(other)
        guilt = self.state("guilt")

        wants_to_reveal = (
            2.0 * rel["trust"]                          # confio em quem pergunta
            + 1.5 * t["honesty"]                         # sou honesto
            + 1.0 * guilt                                # estou com a consciência pesada
            + 3.0 * rel["fear"] * (1 - t["courage"])     # tenho medo dele (e pouca coragem)
            + 0.8 * rel["favor_owed"]                    # devo um favor a ele
            - 2.5 * fact["sensitivity"]                  # o fato é delicado
            - 1.0 * rel["distrust"]                      # desconfio dele
        )
        reveal_chance = sigmoid(wants_to_reveal)

        # POSTURA PERSISTENTE: se eu já decidi esconder/mentir/desviar sobre este fato nesta
        # conversa, mantenho a decisão enquanto nada mudar de verdade. Sem isso, sortear de novo
        # a cada fala faria qualquer um acabar contando (é só esperar o sorteio). Ameaça, culpa
        # ou confiança que mexem no placar em 0.5 ou mais fazem o ator reconsiderar.
        key = (other, fact["id"])
        previous = self.stances.get(key)
        if previous and abs(wants_to_reveal - previous[1]) < 0.5:
            decision, _, extra = previous
            return decision, extra, reveal_chance

        if random.random() < reveal_chance:
            decision, extra = "REVEAL", None
        else:
            # Não vou contar a verdade: minto, escondo ou desvio a suspeita? DEFLECT só entra em
            # jogo se um objetivo concreto justificar o risco (não é personalidade sozinha).
            goal = self.main_goal()
            risks_deflecting = (
                goal is not None and goal["priority"] >= 0.7
                and t["deceit"] >= 0.6 and t["empathy"] < 0.5
                and fact["sensitivity"] >= 0.7
            )
            fake_target = self._choose_scapegoat(other) if risks_deflecting else None
            if fake_target:
                decision, extra = "DEFLECT", {"fake_target": fake_target}
            else:
                # Mentir exige dissimulação, pouca honestidade e pouca culpa, e só vale a pena
                # para fatos sensíveis.
                lie_chance = (t["deceit"] * (1 - t["honesty"])
                              * (1 - 0.7 * guilt) * fact["sensitivity"])
                if fact["false_version"] and random.random() < lie_chance:
                    decision, extra = "LIE", None
                else:
                    decision, extra = "HIDE", None
        self.stances[key] = (decision, wants_to_reveal, extra)
        return decision, extra, reveal_chance

    def choose_tactic(self, other):
        """
        Como pedir informação ao outro ator: ASK (educado) ou THREATEN.
        - A "disposição" natural para ameaçar vem dos traços: agressividade, ganância e falta
          de empatia.
        - A FRUSTRAÇÃO (o outro já se recusou a contar) AMPLIFICA essa disposição: quem é
          gentil quase nunca ameaça, mesmo frustrado; quem é agressivo escala rápido.
        - Confiar no outro reduz a chance.
        """
        t = self.personality["traits"]
        disposition = 2.0 * t["aggressiveness"] + 1.0 * t["greed"] + 1.5 * (1 - t["empathy"])
        threat_chance = sigmoid(
            disposition * (1 + 1.4 * self.state("frustration"))
            - 2.0 * self.relationship(other)["trust"]
            - 5.7
        )
        tactic = "THREATEN" if random.random() < threat_chance else "ASK"
        self._log(f"{self.name} escolheu a tatica {tactic} (chance de ameacar: {threat_chance:.0%})")
        return tactic

    @staticmethod
    def tactic_instruction(tactic, other, topic):
        """Traduz a tática escolhida pelo código em uma instrução para o LLM."""
        if tactic == "THREATEN":
            return (f'Ameace {other} (dentro do jogo: parar de confiar, cortar a troca de '
                    f'informações, contar aos outros que esconde coisas) para que conte o que '
                    f'sabe sobre "{topic}".')
        return f'Pergunte a {other} o que sabe sobre "{topic}".'

    def _suspicion_score(self, candidate):
        """
        O quanto eu suspeito de `candidate`, combinando a crença já formada (se houver
        evidência ligando esse nome a "é o culpado") com a desconfiança da relação e um
        empurrão para quem eu ainda não consegui arrancar nada. Serve tanto de contexto para
        o LLM decidir (ver choose_investigation_target) quanto de fallback caso ele não
        responda nada aproveitável.
        """
        belief = self.belief(f"{candidate} é o culpado")
        suspicion = belief["confidence"] if belief else 0.0
        return (
            0.6 * suspicion
            + 0.4 * self.relationship(candidate)["distrust"]
            + (0.3 if candidate not in self.satisfied else 0.0)
        )

    def choose_investigation_target(self, candidates, topic, llm):
        """
        Quem interrogar agora, entre `candidates`? Diferente das outras decisões da classe,
        aqui o LLM entra como PLANEJADOR (roadmap, seções 12 e 19), não só como narrador: o
        código monta as opções e o contexto (suspeita já reunida sobre cada um), pede que o
        LLM escolha UM nome, e SEMPRE valida a resposta antes de usá-la - se vier algo fora da
        lista, ambíguo ou vazio, o código decide sozinho pela pontuação. O LLM nunca pode travar
        o jogo nem inventar um alvo que não existe.

        Escopo: a decisão hoje só considera os números já calculados (suspeita, desconfiança,
        quem já foi pressionado) - não inclui o histórico de diálogo desta cena, que ainda não
        é resumido em lugar nenhum. Dar ao LLM o teor das falas já trocadas (não só os números)
        é uma extensão futura natural.
        """
        scores = {c: self._suspicion_score(c) for c in candidates}
        ranking = "\n".join(
            f"- {name}: suspeita {p:.2f}" + (" (já interrogado)" if name in self.satisfied else "")
            for name, p in sorted(scores.items(), key=lambda kv: -kv[1])
        )
        request = [
            {"role": "system", "content": (
                "Você é um investigador decidindo quem interrogar a seguir numa investigação. "
                "Responda SOMENTE com o nome exato de uma pessoa da lista, sem mais nada.")},
            {"role": "user", "content": (
                f"Objetivo da investigação: {topic}\n\n"
                f"Suspeitos e o quanto você já suspeita de cada um (0 a 1):\n{ranking}\n\n"
                "Quem você vai interrogar agora? Responda só com o nome.")},
        ]
        answer = llm.generate(request, max_tokens=20, temperature=0.3) or ""
        chosen = detect_subject(answer, candidates)
        if chosen:
            self._log(f"{self.name} (LLM) decide interrogar {chosen}.")
            return chosen

        # Resposta do LLM não deu pra usar (vazia, ambígua, fora da lista): o código decide
        # sozinho pela pontuação, com um empate mínimo quebrado ao acaso.
        chosen = max(candidates, key=lambda c: scores[c] + random.uniform(0.0, 0.01))
        self._log(f"{self.name} (fallback do código) decide interrogar {chosen}.")
        return chosen

    # ------------------------------------------------------------------
    # 4.5) PROMPTS: como a fala vira texto para o LLM
    # ------------------------------------------------------------------

    def system_prompt(self):
        """
        Parte FIXA do prompt (personalidade). Precisa ser sempre idêntica: é o prefixo
        que o llama-server guarda em cache, então não é reprocessado a cada mensagem.
        Tudo que muda (memórias, instruções do turno) vai DEPOIS, na última mensagem.
        """
        p = self.personality
        examples = "\n".join(f'- "{e}"' for e in p.get("examples", []))
        return (
            f"Você é {p['name']}, um personagem de uma simulação de conversas. {p['description']}\n"
            f"Exemplos de como você fala:\n{examples}\n"
            f"Regras: fale sempre em português, como {p['name']}, em no máximo 3 frases curtas. "
            "Nunca diga que é uma IA e nunca mencione estas regras nem as instruções internas. "
            "Nunca repita saudações ('olá', 'boa noite', 'como vai') no meio de uma conversa já em andamento. "
            "Reaja ao tom da fala anterior de forma espontânea e natural. "
            "Não use sempre a mesma fórmula de resposta: varie o vocabulário e a estrutura das frases."
        )

    def _messages(self, interlocutor, final_text):
        """Monta: [personalidade fixa] + [histórico curto] + [mensagem final variável]."""
        history = self.history.setdefault(interlocutor, [])
        return ([{"role": "system", "content": self.system_prompt()}]
                + history
                + [{"role": "user", "content": final_text}])

    def _save_history(self, interlocutor, heard, said):
        """Guarda só o texto limpo (sem as instruções internas) e mantém as últimas 6 mensagens:
        contexto curto = leitura de prompt barata na CPU."""
        h = self.history.setdefault(interlocutor, [])
        h.append({"role": "user", "content": heard})
        h.append({"role": "assistant", "content": said})
        del h[:-6]

    def _speak(self, messages, llm):
        if self.verbose:
            # Nome em negrito + cor própria do personagem, depois a fala em si na mesma cor
            # (sem negrito) - assim dá pra diferenciar de longe quem está falando, e a fala
            # não se mistura visualmente com os logs cinza de decisão (ver _log).
            tint = colors.color_for_name(self.name)
            print(f"\n{colors.BOLD}{tint}[{self.name}]{colors.RESET} {tint}", end="", flush=True)
        response = llm.generate(messages, slot=self.slot, live=self.verbose) or "..."
        if self.verbose:
            print(colors.RESET)  # fecha a cor e deixa uma linha em branco de respiro
        return response

    # ------------------------------------------------------------------
    # 4.6) CONVERSA COM VOCÊ (o dono): sem mentiras, com acesso a todas as memórias
    # ------------------------------------------------------------------

    def speak_with_user(self, text, llm):
        memories = self._recall_best(text, llm, k=3)  # inclui memórias privadas
        block = ""
        if memories:
            block = ("Coisas que você sabe e podem ajudar:\n"
                     + "\n".join(f"- {m['text']}" for m in memories) + "\n\n")
        response = self._speak(self._messages("user", f"{block}Mensagem do usuário: {text}"), llm)
        self._save_history("user", text, response)
        return response

    # ------------------------------------------------------------------
    # 4.7) CONVERSA COM OUTRO ATOR (o orquestrador faz de "carteiro")
    #
    # Cada fala viaja num ENVELOPE: {"from", "target", "tactic", "text", "facts", "accusations"}
    #   text        -> o que foi dito (escrito pelo LLM)
    #   facts       -> [{"text", "source_id"}] as informações que o código decidiu passar
    #                  (verdadeiras ou falsas); source_id identifica o fato-base na cabeça de
    #                  quem contou, e é o que permite notar quando ele muda de versão depois
    #                  (ver detecção de contradição em receive())
    #   accusations -> [{"subject", "proposition", "weight"}] insinuações da ação DEFLECT
    #   tactic      -> a intenção da fala (ASK, THREATEN ou NONE)
    # Assim o outro ator atualiza medo/memória por CÓDIGO, sem gastar inferência para
    # "interpretar" a fala. Um ator nunca enxerga o banco nem o prompt do outro.
    #
    # ESCOPO: só detectamos contradição quando é a MESMA origem mudando de versão sobre o
    # MESMO fato (source_id bate). Duas testemunhas diferentes discordando uma da outra sobre
    # o mesmo assunto não é pego aqui - isso exigiria comparar texto livre semanticamente
    # (roadmap, seção 8: memória semântica), o que é trabalho futuro.
    # ------------------------------------------------------------------

    def new_conversation(self, other):
        self.history[other] = []
        self.disclosed = {c for c in self.disclosed if c[0] != other}
        self.stances = {c: v for c, v in self.stances.items() if c[0] != other}
        self.satisfied.discard(other)
        self.waiting = False

    def open_conversation(self, other, topic, llm):
        """Primeira fala: puxa assunto e já usa a tática escolhida pelo código."""
        tactic = self.choose_tactic(other)
        instruction = f"Comece uma conversa com {other}. "
        known = self.knows_about(other)
        if known:  # RECONHECIMENTO: já sei quem é {other}, mesmo antes de ela falar
            facts = "; ".join(f'"{m["text"]}"' for m in known)
            instruction += f"Você já conhece {other} e sabe disto sobre ela/ele: {facts}. "
            self._log(f"{self.name} reconhece {other} ({len(known)} fato(s) conhecido(s))")
        instruction += self.tactic_instruction(tactic, other, topic)
        text = self._speak(self._messages(other, instruction), llm)
        self._save_history(other, f"(Você começa a conversa com {other}.)", text)
        self.waiting = True
        return {"from": self.name, "target": other, "tactic": tactic, "text": text, "facts": []}

    def receive(self, env):
        """Efeitos de ouvir uma fala do outro. Só código: nenhuma chamada ao LLM."""
        other = env["from"]
        target = env.get("target")
        i_am_target = (target is None or target == self.name)

        # (a) Ameaça:
        # Se eu sou o alvo da ameaça: o medo sobe, confiança cai e desconfiança sobe.
        # Se sou testemunha na sala: observo a agressividade e a desconfiança de quem ameaçou sobe.
        if env.get("tactic") == "THREATEN":
            if i_am_target:
                self.change_relationship(other, fear=0.5, trust=-0.1, distrust=0.1)
                self._log(f"{self.name} foi ameacado(a) por {other}: medo agora "
                          f"{self.relationship(other)['fear']:.2f}")
            else:
                self.change_relationship(other, distrust=0.15, trust=-0.05)
                self._log(f"{self.name} presenciou {other} ameacando {target}: desconfiança de {other} subiu")

        # (b) Informação recebida vira memória MINHA, com origin = quem contou. Antes de
        #     guardar, checo se {other} já me disse algo DIFERENTE sobre o mesmo fato-base
        #     (mesmo source_id) - é a detecção de contradição (roadmap, seção 14). Se bateu,
        #     ele perde confiança em vez de ganhar; senão, quem conta ganha um pouco de
        #     confiança e eu passo a dever um favor.
        for fact in env.get("facts", []):
            text, source_id = fact["text"], fact.get("source_id")
            previous = None
            if source_id is not None:
                previous = self.db.execute(
                    "SELECT * FROM memories WHERE origin=? AND source_id=? AND text!=? "
                    "ORDER BY id DESC LIMIT 1", (other, source_id, text)).fetchone()

            memory_id = self.remember(text, origin=other, sensitivity=0.5, source_id=source_id)

            if previous is not None:
                self.db.execute("UPDATE memories SET contradictory=1 WHERE id IN (?, ?)",
                                (previous["id"], memory_id))
                self.db.commit()
                self.change_relationship(other, distrust=0.3, trust=-0.2)
                self._log(f"{self.name} pegou {other} se contradizendo: antes disse "
                          f"{previous['text']!r}, agora diz {text!r}")
            else:
                self.change_relationship(other, trust=0.05, favor_owed=0.1)
                self._log(f"{self.name} aprendeu com {other}: {text!r}")

        # (b2) Acusações (ação DEFLECT, ver choose_action): reforçam uma CRENÇA meu sobre o
        #      acusado, não uma memória de fato consumado - é só a palavra de {other} contra
        #      alguém, com peso reduzido (ver 'weight' na acusação). Quem é o próprio acusado
        #      ignora o boato: ele já sabe se é inocente ou não, não aprende isso ouvindo.
        for acc in env.get("accusations", []):
            if acc["subject"] == self.name:
                continue
            weight = acc.get("weight", 0.15)
            self.update_belief(f"{acc['subject']} é o culpado", delta=weight, origin=other,
                               subject=acc["subject"], evidence=f"accusation:{other}")
            self._log(f"{self.name} ouviu {other} insinuar que {acc['subject']} pode estar envolvido")

        # (c) Se eu tinha pedido algo (e sou o alvo da resposta): vieram fatos? Sem informação, a
        #     frustração sobe (e alimenta a chance de ameaçar); com informação, ela cai.
        if self.waiting and i_am_target:
            self.change_state("frustration", -0.5 if env.get("facts") else 0.35)
            if env.get("facts"):
                self.satisfied.add(other)  # consegui o que queria: não preciso mais pressionar
            self.waiting = False

    def respond(self, env, topic, llm, last=False):
        """Ouve o envelope do outro e responde: decide o que contar e como pedir de volta."""
        other = env["from"]
        self.receive(env)

        # 1) RECONHECIMENTO: o que já sei especificamente sobre {other} - independe do
        #    assunto do momento e não passa pelo filtro de revelar/esconder/mentir (não é
        #    fofoca sobre terceiros, é eu reconhecendo quem está falando comigo).
        instructions = []
        known = self.knows_about(other)
        if known:
            facts = "; ".join(f'"{m["text"]}"' for m in known)
            instructions.append(f"Você já conhece {other} e sabe disto sobre ela/ele: {facts}. "
                                "Pode usar isso com naturalidade, sem parecer um interrogatório.")
            self._log(f"{self.name} reconhece {other} ({len(known)} fato(s) conhecido(s))")

        # 2) Memórias relevantes E compartilháveis para a troca sobre TERCEIROS (o
        #    isolamento é garantido aqui). Ficam de fora: o que já contei a este ator, o
        #    que ELE mesmo me contou, o que é sobre ele mesmo (isso já foi tratado acima, como
        #    reconhecimento, não como fofoca) e o que é sobre MIM MESMO - se alguém me contou
        #    (fofoca) que "Fulano é o culpado" e Fulano sou eu, isso NÃO é uma fofoca de
        #    terceiro que eu preciso decidir revelar/esconder/mentir: é sobre a MINHA própria
        #    verdade, que já é tratada separadamente (minha memória com origin='system'/minha
        #    própria versão falsa). Sem essa exclusão, o mesmo fato "sobre mim" podia ser
        #    decidido duas vezes no mesmo turno - uma vez pela minha verdade, outra pela
        #    fofoca ecoada - e sair uma fala confessando e mentindo ao mesmo tempo.
        relevant = self._recall_best(f"{env['text']} {topic}", llm, k=4, shareable_only=True)
        about_others = [m for m in relevant if m["about"] not in (other, self.name)]
        pending = [m for m in about_others
                   if (other, m["id"]) not in self.disclosed and m["origin"] != other][:2]

        # 3) Para cada fato sobre terceiros, o CÓDIGO decide REVEAL, HIDE, LIE ou DEFLECT.
        outgoing_facts, accusations, hid, debug_decisions = [], [], False, []
        for fact in pending:
            decision, extra, p = self.choose_action(fact, other)
            self._log(f"{self.name} decidiu {decision} (chance de revelar: {p:.0%}) "
                      f"sobre: {fact['text']!r}")
            debug_decisions.append((fact["text"], decision, p))
            if decision == "REVEAL":
                outgoing_facts.append({"text": fact["text"], "source_id": fact["id"],
                                       "origin": fact["origin"], "kind": "REVEAL"})
                self.disclosed.add((other, fact["id"]))
            elif decision == "LIE":
                # O prompt recebe SÓ a versão falsa: a verdade não entra nele. source_id é o
                # MESMO da verdade (é o mesmo fato-base) - se este Ator revelar a verdade sobre
                # ele depois, quem ouviu as duas versões pega a contradição (ver receive()).
                outgoing_facts.append({"text": fact["false_version"], "source_id": fact["id"],
                                       "origin": fact["origin"], "kind": "LIE"})
                self.change_state("guilt", 0.1 + 0.4 * self.personality["traits"]["empathy"])  # empatia = mais culpa
            elif decision == "DEFLECT":
                fake_target = extra["fake_target"]
                accusations.append({"subject": fake_target,
                                    "proposition": f"{fake_target} pode estar envolvido nisso.",
                                    "weight": 0.15})
                self.change_state("guilt", 0.05 + 0.3 * self.personality["traits"]["empathy"])
                self._log(f"{self.name} desviou a suspeita para {fake_target}")
            else:
                hid = True

        # 4) Transforma as decisões em instruções concretas para o LLM. Um fato que EU vivi ou
        # sei por mim mesmo (origin 'system'/'observation'/'user') pode ser contado em
        # primeira pessoa direto. Um fato que outra PESSOA me contou (fofoca/testemunho
        # relatado) precisa ser instruído como relato de terceiro - senão o LLM repete um texto
        # em primeira pessoa (ex.: uma confissão) como se fosse dele mesmo, um bug real: alguém
        # relatando "fui eu quem roubou" ao repassar a confissão de outra pessoa.
        for fact in outgoing_facts:
            if fact["origin"] not in OWN_ORIGINS:
                instructions.append(
                    f'Você soube por {fact["origin"]}: "{fact["text"]}". Conte isso a {other} '
                    f'como algo que você ouviu de {fact["origin"]} ("ouvi dizer que...", '
                    f'"{fact["origin"]} me contou que..."), NUNCA como se fosse sobre você mesmo '
                    f"ou algo que você fez.")
            else:
                instructions.append(f'Conte a {other}, com suas palavras: "{fact["text"]}".')
        for acc in accusations:
            instructions.append(f'Sugira, com cautela e sem provas concretas, que {acc["subject"]} '
                               f"pode ter algo a ver com isso. Não admita nada sobre você mesmo.")
        if not outgoing_facts and not accusations:
            if hid:
                # Rotação de táticas de evasão: cada vez que este ator esquiva do mesmo
                # interlocutor, a instrução muda para que as respostas não soem todas iguais.
                n = self.evasion_count.get(other, 0)
                self.evasion_count[other] = n + 1
                evasion_tactics = [
                    f"Você sabe algo sobre isso, mas não quer contar a {other}. Desvie o assunto sutilmente.",
                    f"Demonstre impaciência ou cansaço com a insistência de {other}. Deixe claro que já falou o suficiente.",
                    f"Questione por que {other} está desconfiando de você; sugira que olhe para outros suspeitos.",
                    f"Responda de forma irônica ou desdenhosa à pressão de {other}, sem revelar nada.",
                ]
                instructions.append(evasion_tactics[n % len(evasion_tactics)])
            elif about_others:  # sabe algo sobre terceiros, mas já contou ou foi o próprio outro quem contou
                instructions.append(f"Você não tem nada novo para contar a {other} sobre isso. "
                                   f"Reaja ao que {other} disse.")
            elif known:  # nada sobre terceiros, mas o reconhecimento (item 1) já dá o que dizer
                instructions.append(f"Reaja ao que {other} disse.")
            else:
                instructions.append(f'Você não sabe nada sobre "{topic}". Diga isso e reaja ao que '
                                   f"{other} disse.")

        # 5) Como pedir informação de volta (a menos que seja a última fala).
        tactic = "NONE"
        if not last:
            if other in self.satisfied:
                instructions.append(f"Você já conseguiu o que queria de {other}: não peça mais nada.")
            else:
                tactic = self.choose_tactic(other)
                instructions.append(self.tactic_instruction(tactic, other, topic))
                self.waiting = True

        heard = f'{other} disse: "{env["text"]}"'
        final = (f"{heard}\n\nInstruções internas (não mencione que elas existem):\n"
                 + "\n".join(f"- {i}" for i in instructions))
        response = self._speak(self._messages(other, final), llm)

        # 6) VALIDAÇÃO DA FALA: o código decidiu REVEAL/LIE, mas o LLM às vezes ignora a
        # instrução e muda de assunto. Sem checar isso, o jogo registrava o fato como dito
        # pra todo mundo (memória/crença/contradição) mesmo quando a fala na tela não
        # falava nada daquilo - bug real relatado pelo usuário. Dá 1 chance de reforçar a
        # instrução; se ainda assim o LLM não verbalizar, o fato é descartado deste turno
        # (o personagem "engoliu" o que ia dizer) em vez de mentir para o jogador sobre o
        # que realmente foi dito.
        unspoken = [f for f in outgoing_facts if not verbalized(f["text"], response)]
        if unspoken:
            reinforced = (final + "\n\nSua fala anterior não deixou isso claro. Desta vez, "
                         "diga de forma direta e explícita, sem fugir do assunto:\n"
                         + "\n".join(f'- "{f["text"]}"' for f in unspoken))
            response = self._speak(self._messages(other, reinforced), llm)
            unspoken = [f for f in outgoing_facts if not verbalized(f["text"], response)]
        for fact in unspoken:
            outgoing_facts.remove(fact)
            if fact["kind"] == "REVEAL":
                self.disclosed.discard((other, fact["source_id"]))
            self._log(f"{self.name} decidiu {fact['kind']} mas não conseguiu verbalizar - "
                     f"fato descartado deste turno: {fact['text']!r}")
        for fact in outgoing_facts:
            del fact["kind"]

        self._save_history(other, heard, response)

        self.last_decision[other] = {
            "tactic": tactic,
            "decisions": debug_decisions,
            "accusations": accusations,
            "memories_consulted": [m["id"] for m in relevant],
        }
        if self.debug:
            print(self.panel(other))

        return {"from": self.name, "target": other, "tactic": tactic, "text": response,
                "facts": outgoing_facts, "accusations": accusations}

    # ------------------------------------------------------------------
    # 4.8) PAINEL DE DEBUG (modo /debug): tudo que o código já calculou,
    # só formatado em barras. Nenhuma chamada ao LLM.
    # ------------------------------------------------------------------

    def panel(self, other=None):
        t = self.personality["traits"]
        width = 22
        lines = [f"{'━' * width} {self.name.upper()} {'━' * width}", ""]

        lines.append("EMOÇÕES")
        lines.append(f"  culpa       {bar(self.state('guilt'))}")
        lines.append(f"  frustração  {bar(self.state('frustration'))}")
        lines.append("")

        lines.append("TRAÇOS")
        for key, value in t.items():
            lines.append(f"  {key:<15} {bar(value)}")
        lines.append("")

        if other:
            r = self.relationship(other)
            lines.append(f"RELAÇÃO COM {other.upper()}")
            lines.append(f"  confiança     {bar(r['trust'])}")
            lines.append(f"  medo          {bar(r['fear'])}")
            lines.append(f"  desconfiança  {bar(r['distrust'])}")
            lines.append(f"  favor devido  {bar(r['favor_owed'])}")
            lines.append("")

            data = self.last_decision.get(other)
            if data:
                lines.append(f"ÚLTIMA TÁTICA: {data['tactic']}")
                if data["decisions"]:
                    lines.append("PROBABILIDADES (chance de revelar)")
                    for text, decision, p in data["decisions"]:
                        lines.append(f"  [{decision:<8}] {bar(p)}  {text[:40]!r}")
                if data["memories_consulted"]:
                    ids = ", ".join(f"#{i}" for i in data["memories_consulted"])
                    lines.append(f"MEMÓRIAS CONSULTADAS: {ids}")
                if data.get("accusations"):
                    targets = ", ".join(acc["subject"] for acc in data["accusations"])
                    lines.append(f"DESVIOU A SUSPEITA PARA: {targets}")
                lines.append("")

        goals = self.active_goals()
        if goals:
            lines.append("OBJETIVOS")
            for g in goals:
                lines.append(f"  {bar(g['priority'])}  {g['description']} "
                            f"(progresso {g['progress']:.0%})")
            lines.append("")

        beliefs = self.list_beliefs()
        if beliefs:
            lines.append("HIPÓTESES / CRENÇAS")
            for b in beliefs:
                lines.append(f"  {bar(b['confidence'])}  {b['proposition']}")
            lines.append("")

        contradictions = self.contradictions()
        if contradictions:
            lines.append("CONTRADIÇÕES PEGAS")
            for c in contradictions:
                lines.append(f"  {c['origin']} disse: {c['text']!r}")
            lines.append("")

        lines.append("━" * (2 * width + len(self.name) + 2))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 4.9) RESUMO para o terminal
    # ------------------------------------------------------------------

    def summary(self):
        t = self.personality["traits"]
        lines = [f"{self.name} | " + ", ".join(f"{k} {v:.2f}" for k, v in t.items()),
                 f"   culpa {self.state('guilt'):.2f} | frustracao {self.state('frustration'):.2f}"]
        for row in self.db.execute("SELECT other FROM relationships ORDER BY other"):
            r = self.relationship(row["other"])
            lines.append(f"   com {row['other']}: confianca {r['trust']:.2f} | "
                        f"medo {r['fear']:.2f} | desconfianca {r['distrust']:.2f} | "
                        f"deve favor {r['favor_owed']:.2f}")
        return "\n".join(lines)
