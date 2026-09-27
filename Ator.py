"""
agente.py - o "cérebro" de cada agente.

UM agente = UM arquivo .db (SQLite) com tudo dentro:
    config    -> personalidade (nome, jeito de falar, exemplos, traços numéricos)
    memorias  -> o que o agente sabe (com origem, assunto, sensibilidade e uma versão falsa)
    estado    -> emoções que mudam e esmaecem com o tempo (culpa, frustração)
    relacoes  -> o que ele sente por cada outro agente (confiança, medo, ...)

IDEIA CENTRAL: quem DECIDE (revelar? esconder? mentir? ameaçar?) é o CÓDIGO
(`decidir` e `escolher_tatica`), não o modelo. O LLM só recebe uma instrução
concreta e escreve a fala. Vantagens:
  * zero inferência extra para decidir (CPU poupada);
  * comportamento previsível, com pesos que você ajusta e testa;
  * a verdade nem entra no prompt quando o agente vai esconder ou mentir,
    então o modelo de 3B não tem como "vazar" o que não recebeu.
"""
import json
import math
import random
import re
import sqlite3
import time
import unicodedata

# ============================================================================
# 1) CONFIGURAÇÕES GERAIS
# ============================================================================

# Em quantos segundos cada emoção cai pela metade. O decaimento é "preguiçoso":
# só é calculado quando o valor é lido (nada fica rodando em segundo plano).
MEIA_VIDA = {"culpa": 1800, "medo": 900, "frustracao": 600}

# Traços de personalidade (0 a 1). Servem de "pesos" nas funções de decisão.
TRACOS_PADRAO = {
    "honestidade": 0.5,    # tendência a falar a verdade
    "dissimulacao": 0.5,   # habilidade/vontade de enganar
    "empatia": 0.5,        # sente mais culpa ao mentir; pressiona menos os outros
    "coragem": 0.5,        # resiste melhor a ameaças
    "agressividade": 0.5,  # tendência a pressionar e ameaçar
    "ganancia": 0.5,       # quanto quer arrancar informação dos outros
}

# ============================================================================
# 2) O ARQUIVO .db (esquema do banco)
# ============================================================================

# WAL = vários leitores + um escritor sem travar; synchronous NORMAL é seguro em WAL e mais rápido.
PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
"""

# Só na criação do arquivo: marca que ele é de um agente e guarda a versão do esquema
# (para migrar arquivos antigos no futuro sem precisar de outra extensão).
# Versão 1 = esquema original; versão 2 = com a coluna 'sobre' em memorias (ver _migrar).
MARCA = """
PRAGMA application_id = 1095192148;  -- 0x41474E54 = "AGNT"
PRAGMA user_version = 2;
"""

TABELAS = """
CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS memorias (
    id             INTEGER PRIMARY KEY,
    texto          TEXT NOT NULL,
    data           REAL,                    -- quando foi salvo (time.time())
    origem         TEXT DEFAULT 'usuario',  -- 'usuario' ou o nome do agente que contou
    compartilhavel INTEGER DEFAULT 1,       -- 0 = nunca sai deste agente
    sensibilidade  REAL DEFAULT 0.3,        -- 0 = qualquer um pode saber ... 1 = segredo
    versao_falsa   TEXT,                    -- versão pré-gerada, usada só quando ele mentir
    sobre          TEXT,                    -- nome de quem é o assunto (ex.: outro agente), se houver
    origem_id      INTEGER,                 -- id da memória ORIGINAL de quem contou (mesmo fato-base
                                             -- na cabeça de quem contou); permite notar quando ela
                                             -- muda de versão sobre a MESMA coisa - ver detectar
                                             -- contradição em receber()
    contraditoria  INTEGER DEFAULT 0        -- 1 = essa memória entrou em conflito com outra já
                                             -- registrada da mesma origem sobre o mesmo origem_id
);

CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY, valor REAL, atualizado_em REAL
);

CREATE TABLE IF NOT EXISTS relacoes (
    outro         TEXT PRIMARY KEY,
    confianca     REAL DEFAULT 0.5,
    medo          REAL DEFAULT 0,
    desconfianca  REAL DEFAULT 0,
    favor_devido  REAL DEFAULT 0,   -- o quanto EU devo a esse agente (ele me contou coisas)
    atualizado_em REAL
);

-- Crença = uma proposição em que este Ator confia mais ou menos (0 a 1), e que evidências vão
-- ajustando aos poucos. Serve tanto para crença social ("Maria confia em mim") quanto para
-- hipótese de investigação ("João é o culpado") - roadmap, seções 12 e 22, tratadas aqui como o
-- MESMO mecanismo, só com `assunto` diferente. Diferente de `memorias`: memória é "eu soube que
-- X", crença é "o quanto eu acho que X é verdade" (pode subir e descer com o tempo).
-- Objetivo estruturado (roadmap, seção 17): não é só uma memória de texto solta, tem prioridade,
-- progresso e risco - o código usa isso para decidir quais AÇÕES ficam disponíveis (ver
-- escolher_acao), não só o que dizer.
CREATE TABLE IF NOT EXISTS objetivos (
    id            INTEGER PRIMARY KEY,
    descricao     TEXT NOT NULL UNIQUE,
    prioridade    REAL DEFAULT 0.5,
    progresso     REAL DEFAULT 0.0,
    risco         REAL DEFAULT 0.0,
    status        TEXT DEFAULT 'ativo',    -- 'ativo' | 'concluido' | 'falhou'
    criado_em     REAL,
    atualizado_em REAL
);

CREATE TABLE IF NOT EXISTS crencas (
    id            INTEGER PRIMARY KEY,
    proposicao    TEXT NOT NULL UNIQUE,   -- ex.: "Joao é o culpado"
    assunto       TEXT,                   -- de quem/o que é a crença (ex.: "Joao") - permite listar por assunto
    confianca     REAL DEFAULT 0.5,
    origem        TEXT,                   -- quem/o que motivou a última mudança
    evidencias    TEXT DEFAULT '[]',      -- JSON: lista de referências soltas (ex.: ids de mundo.db)
    criada_em     REAL,
    atualizada_em REAL
);
"""


def abrir_banco(caminho):
    """Abre (ou cria) o arquivo .db de um agente e garante que as tabelas existem."""
    db = sqlite3.connect(caminho)
    db.row_factory = sqlite3.Row  # permite ler colunas por nome: linha["texto"]
    db.executescript(PRAGMAS + TABELAS)
    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
        db.executescript(MARCA)
    _migrar(db)
    return db


def _migrar(db):
    """Ajusta bancos .db criados por uma versão anterior do projeto (ex.: sem a coluna
    'sobre'), sem apagar nada do que já está salvo."""
    colunas = {linha["name"] for linha in db.execute("PRAGMA table_info(memorias)")}
    if "sobre" not in colunas:
        db.execute("ALTER TABLE memorias ADD COLUMN sobre TEXT")
        db.execute("PRAGMA user_version = 2")
        db.commit()
        colunas.add("sobre")
    if "origem_id" not in colunas:
        db.execute("ALTER TABLE memorias ADD COLUMN origem_id INTEGER")
        db.commit()
    if "contraditoria" not in colunas:
        db.execute("ALTER TABLE memorias ADD COLUMN contraditoria INTEGER DEFAULT 0")
        db.commit()


def criar_ator(caminho, nome, descricao, exemplos, tracos):
    """Cria o arquivo .db de um agente novo, já com a personalidade gravada."""
    pers = {
        "nome": nome,
        "descricao": descricao,   # jeito de falar e de ser (vai para o prompt)
        "exemplos": exemplos,     # frases de exemplo (modelos pequenos imitam melhor do que obedecem)
        "tracos": {**TRACOS_PADRAO, **tracos},
    }
    db = abrir_banco(caminho)
    db.execute("INSERT OR REPLACE INTO config(chave, valor) VALUES ('personalidade', ?)",
               (json.dumps(pers, ensure_ascii=False),))
    db.commit()
    db.close()


# Aliases para compatibilidade entre nomenclatura 'agente' e 'ator'
criar_ator = criar_ator
criar_Ator = criar_ator


# ============================================================================
# 3) FUNÇÕES AUXILIARES
# ============================================================================

def sigmoid(x):
    """Transforma qualquer número em uma probabilidade entre 0 e 1."""
    x = max(-30.0, min(30.0, x))
    return 1 / (1 + math.exp(-x))


def limitar(valor, minimo=0.0, maximo=1.0):
    return max(minimo, min(maximo, valor))


def barra(valor, largura=10):
    """Barra de progresso textual para o painel de debug (ex.: '███████░░░ 0.71')."""
    valor = limitar(valor)
    cheio = round(valor * largura)
    return "█" * cheio + "░" * (largura - cheio) + f" {valor:.2f}"


# Valores de `origem` que significam "eu sei disso por mim mesmo" (vivi/vi/fui informado
# diretamente), não "outra pessoa me contou". Qualquer outro valor de origem é o NOME de quem
# contou - ou seja, é uma fofoca/relato de terceiro (ver responder(), item 4).
ORIGENS_PROPRIAS = {"sistema", "observacao", "usuario"}

# Palavras que não ajudam a achar memórias parecidas.
PALAVRAS_COMUNS = {
    "que", "com", "por", "para", "pra", "uma", "dos", "das", "nos", "nas", "mas", "como",
    "mais", "isso", "esse", "essa", "este", "esta", "qual", "quem", "onde", "sobre",
    "voce", "ele", "ela", "seu", "sua", "meu", "minha", "foi", "tem", "sao", "ser",
    "tudo", "algo", "muito", "aqui", "estou", "sei", "quer", "diga", "conta", "disse",
}


def normalizar(texto):
    """Minúsculas e sem acento (ex.: 'É a Bia' -> 'e a bia'). Usada na busca de memória e
    também para reconhecer o nome de um agente dentro de um texto (ver main.py)."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def palavras(texto):
    """
    Busca de memória SIMPLES (sem embeddings, custo de CPU praticamente zero):
    quebra o texto em palavras importantes - sem acento/maiúscula, sem palavras
    comuns e cortadas em 5 letras (truque barato para 'segredo' casar com 'segredos').
    """
    return {p[:5] for p in re.findall(r"[a-z0-9]+", normalizar(texto))
            if len(p) >= 3 and p not in PALAVRAS_COMUNS}


def detectar_sujeito(texto, nomes):
    """
    Acha, dentro do texto, o nome de um ator conhecido - ignora acento/maiúscula e
    respeita fronteira de palavra (então 'Bianca' não casa com 'Bia'). Devolve o nome
    (na grafia original) se achar exatamente um; None se não achar ou achar mais de um.
    Usado tanto pelo orquestrador (main.py) quanto por escolher_investigado() abaixo, para
    interpretar a resposta em texto livre de um LLM e casá-la com uma lista de nomes válidos.
    """
    alvo = normalizar(texto)
    achados = [nome for nome in nomes if re.search(rf"\b{re.escape(normalizar(nome))}\b", alvo)]
    return achados[0] if len(achados) == 1 else None


# ============================================================================
# 4) O AGENTE
# ============================================================================

class Agente:
    def __init__(self, caminho, slot=0):
        self.caminho = caminho
        self.slot = slot  # slot do llama-server reservado a este agente (cache do prefixo)
        self.db = abrir_banco(caminho)
        linha = self.db.execute("SELECT valor FROM config WHERE chave='personalidade'").fetchone()
        if linha is None:
            raise ValueError(f"{caminho} não tem personalidade. Crie o agente com criar_ator().")
        self.pers = json.loads(linha["valor"])
        self.nome = self.pers["nome"]

        # Coisas que vivem só na RAM (somem quando o programa fecha):
        self.historico = {}      # conversa recente com cada interlocutor
        self.contados = set()    # (interlocutor, id_da_memoria) que já foram revelados de verdade
        self.posturas = {}       # (interlocutor, id_da_memoria) -> (decisão, placar) da última decisão
        self.satisfeitos = set() # interlocutores de quem já consegui a informação que queria
        self.aguardando = False  # True se o último passo foi pedir algo e a resposta ainda não veio
        self.mostrar = True      # imprime no terminal as decisões internas (bom para ajustar pesos)
        self.contagem_esquiva = {}  # interlocutor -> nº de vezes que desviei (roda táticas de evasão)
        self.debug = False       # modo debug: imprime o painel completo após cada resposta
        self.ultima_decisao = {} # interlocutor -> dados do último turno (para o painel de debug)

    def _log(self, mensagem):
        if self.mostrar:
            print(f"   . {mensagem}")

    def salvar_personalidade(self):
        self.db.execute("INSERT OR REPLACE INTO config(chave, valor) VALUES ('personalidade', ?)",
                        (json.dumps(self.pers, ensure_ascii=False),))
        self.db.commit()

    # ------------------------------------------------------------------
    # 4.1) MEMÓRIA: salvar e buscar fatos
    # ------------------------------------------------------------------

    def lembrar(self, texto, origem="usuario", sensibilidade=0.3, compartilhavel=1, sobre=None,
                origem_id=None):
        """
        Salva um fato. Gravar é só um INSERT (não reescreve o arquivo inteiro).
        `sobre` é o nome de quem é o assunto do fato (ex.: outro agente) - ver sabe_sobre().
        `origem_id` é o id da memória ORIGINAL na cabeça de quem contou (mesmo fato-base) -
        permite notar quando a mesma origem muda de versão sobre a mesma coisa, ver receber().
        """
        texto = texto.strip()
        existente = self.db.execute("SELECT id FROM memorias WHERE texto=?", (texto,)).fetchone()
        if existente:  # não duplica o mesmo fato
            return existente["id"]
        cursor = self.db.execute(
            "INSERT INTO memorias(texto, data, origem, compartilhavel, sensibilidade, sobre, origem_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (texto, time.time(), origem, compartilhavel, sensibilidade, sobre, origem_id))
        self.db.commit()
        return cursor.lastrowid

    def memoria_id_por_texto(self, texto):
        linha = self.db.execute("SELECT id FROM memorias WHERE texto=?", (texto.strip(),)).fetchone()
        return linha["id"] if linha else None

    def contradicoes(self, k=5):
        """Memórias marcadas como contraditórias (ver receber()), da mais recente pra mais antiga."""
        return [dict(m) for m in self.db.execute(
            "SELECT * FROM memorias WHERE contraditoria=1 ORDER BY id DESC LIMIT ?", (k,))]

    def recordar(self, consulta, k=3, so_compartilhaveis=False):
        """
        Devolve até k memórias parecidas com a consulta (mais palavras em comum = melhor;
        empate = a mais recente). Só as poucas memórias relevantes entram no prompt, e é
        isso que mantém o contexto curto (e o modelo pequeno).
        `so_compartilhaveis=True` é o FILTRO DE ISOLAMENTO: nas conversas com outros
        agentes, o que é privado nem é lido do banco, então não tem como vazar.
        (Evolução futura: trocar por FTS5 ou embeddings pequenos + sqlite-vec.)
        """
        procuradas = palavras(consulta)
        sql = "SELECT * FROM memorias" + (" WHERE compartilhavel=1" if so_compartilhaveis else "")
        candidatas = []
        for memoria in self.db.execute(sql):
            pontos = len(procuradas & palavras(memoria["texto"]))
            if pontos > 0:
                candidatas.append((pontos, memoria["data"], dict(memoria)))
        candidatas.sort(key=lambda c: (c[0], c[1]), reverse=True)
        return [c[2] for c in candidatas[:k]]

    def listar_memorias(self):
        return [dict(m) for m in self.db.execute("SELECT * FROM memorias ORDER BY id")]

    def sabe_sobre(self, quem, k=2):
        """
        RECONHECIMENTO: fatos compartilháveis cujo assunto (`sobre`) é `quem`. É o que
        permite um agente perceber que já conhece a pessoa com quem está falando.
        Diferente de recordar(): não depende de bater palavra com o assunto do momento
        (o reconhecimento vale a conversa toda) e não passa pelo filtro de revelar/
        esconder/mentir em decidir() - não é fofoca sendo repassada a um terceiro, é o
        que o Ator já sabe sobre a própria pessoa à sua frente.
        """
        linhas = self.db.execute(
            "SELECT * FROM memorias WHERE compartilhavel=1 AND sobre=? ORDER BY id DESC LIMIT ?",
            (quem, k))
        return [dict(m) for m in linhas]

    def gerar_versao_falsa(self, id_memoria, llm):
        """
        Gera UMA vez (na hora de salvar) uma versão falsa mas plausível do fato e guarda
        no banco. Assim, mentir depois não custa nenhuma inferência extra.
        Um 3B às vezes devolve a frase igual: tentamos de novo uma vez com mais "criatividade".
        (Se falhar, dá para escrever a mentira à mão com o comando /falsa.)
        """
        memoria = self.db.execute("SELECT texto FROM memorias WHERE id=?", (id_memoria,)).fetchone()
        pedido = [
            {"role": "system", "content": "Você reescreve frases. Responda só com a frase reescrita."},
            {"role": "user", "content":
                "Reescreva a frase trocando UM detalhe importante (lugar, número, nome ou objeto) "
                f'para que ela fique falsa, mas plausível.\nFrase: "{memoria["texto"]}"'},
        ]
        for temperatura in (0.8, 1.1):
            falsa = llm.gerar(pedido, max_tokens=60, temperatura=temperatura).strip().strip('"')
            if falsa and falsa != memoria["texto"]:
                self.definir_versao_falsa(id_memoria, falsa)
                return falsa
        return None

    def definir_versao_falsa(self, id_memoria, texto):
        cursor = self.db.execute("UPDATE memorias SET versao_falsa=? WHERE id=?",
                                 (texto.strip(), id_memoria))
        self.db.commit()
        return cursor.rowcount > 0

    def definir_sobre(self, id_memoria, nome):
        """Define (ou remove, com nome=None/vazio) o assunto de uma memória já salva."""
        cursor = self.db.execute("UPDATE memorias SET sobre=? WHERE id=?",
                                 (nome.strip() if nome else None, id_memoria))
        self.db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # 4.1b) CRENÇAS: o quanto confio em cada proposição (verdade != crença, seção 10)
    # ------------------------------------------------------------------

    def crenca(self, proposicao):
        """Devolve a crença (dict) para uma proposição exata, ou None se não existir ainda."""
        linha = self.db.execute("SELECT * FROM crencas WHERE proposicao=?",
                                (proposicao.strip(),)).fetchone()
        if linha is None:
            return None
        d = dict(linha)
        d["evidencias"] = json.loads(d["evidencias"] or "[]")
        return d

    def formar_crenca(self, proposicao, confianca=0.5, origem=None, assunto=None, evidencia=None):
        """Cria a crença se a proposição ainda não existir (não duplica). Devolve a crença atual."""
        proposicao = proposicao.strip()
        existente = self.crenca(proposicao)
        if existente:
            return existente
        agora = time.time()
        self.db.execute(
            "INSERT INTO crencas(proposicao, assunto, confianca, origem, evidencias, criada_em, atualizada_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (proposicao, assunto, limitar(confianca), origem,
             json.dumps([evidencia] if evidencia else []), agora, agora))
        self.db.commit()
        return self.crenca(proposicao)

    def atualizar_crenca(self, proposicao, delta, origem=None, assunto=None, evidencia=None):
        """
        Ajusta a confiança de uma crença por `delta` (positivo reforça, negativo enfraquece),
        criando-a com confiança-base 0.5 se ainda não existir. Cada evidência que motivou a
        mudança fica registrada (útil para reconstruir por que o Ator acredita nisso).
        """
        c = self.formar_crenca(proposicao, confianca=0.5, origem=origem, assunto=assunto)
        nova_confianca = limitar(c["confianca"] + delta)
        evidencias = c["evidencias"]
        if evidencia and evidencia not in evidencias:
            evidencias.append(evidencia)
        self.db.execute(
            "UPDATE crencas SET confianca=?, origem=?, assunto=?, evidencias=?, atualizada_em=? "
            "WHERE proposicao=?",
            (nova_confianca, origem or c["origem"], assunto or c["assunto"],
             json.dumps(evidencias), time.time(), proposicao.strip()))
        self.db.commit()
        return self.crenca(proposicao)

    def crencas_sobre(self, assunto, k=5):
        """Crenças cujo assunto é `assunto`, da mais para a menos confiante (ex.: hipóteses de
        um investigador sobre um suspeito específico)."""
        linhas = self.db.execute(
            "SELECT * FROM crencas WHERE assunto=? ORDER BY confianca DESC LIMIT ?", (assunto, k))
        resultado = []
        for linha in linhas:
            d = dict(linha)
            d["evidencias"] = json.loads(d["evidencias"] or "[]")
            resultado.append(d)
        return resultado

    def listar_crencas(self, k=10):
        linhas = self.db.execute("SELECT * FROM crencas ORDER BY confianca DESC LIMIT ?", (k,))
        resultado = []
        for linha in linhas:
            d = dict(linha)
            d["evidencias"] = json.loads(d["evidencias"] or "[]")
            resultado.append(d)
        return resultado

    # ------------------------------------------------------------------
    # 4.1c) OBJETIVOS: metas com prioridade/progresso/risco (roadmap, seção 17)
    # ------------------------------------------------------------------

    def objetivo(self, descricao):
        linha = self.db.execute("SELECT * FROM objetivos WHERE descricao=?",
                                (descricao.strip(),)).fetchone()
        return dict(linha) if linha else None

    def formar_objetivo(self, descricao, prioridade=0.5, risco=0.0):
        """Cria o objetivo se ainda não existir (não duplica). Devolve o objetivo atual."""
        descricao = descricao.strip()
        existente = self.objetivo(descricao)
        if existente:
            return existente
        agora = time.time()
        self.db.execute(
            "INSERT INTO objetivos(descricao, prioridade, progresso, risco, status, criado_em, atualizado_em) "
            "VALUES (?, ?, 0.0, ?, 'ativo', ?, ?)",
            (descricao, limitar(prioridade), limitar(risco), agora, agora))
        self.db.commit()
        return self.objetivo(descricao)

    def atualizar_objetivo(self, descricao, progresso=None, status=None):
        o = self.objetivo(descricao)
        if o is None:
            return None
        progresso = limitar(progresso) if progresso is not None else o["progresso"]
        status = status or o["status"]
        self.db.execute("UPDATE objetivos SET progresso=?, status=?, atualizado_em=? WHERE descricao=?",
                        (progresso, status, time.time(), descricao))
        self.db.commit()
        return self.objetivo(descricao)

    def objetivos_ativos(self, k=5):
        linhas = self.db.execute(
            "SELECT * FROM objetivos WHERE status='ativo' ORDER BY prioridade DESC LIMIT ?", (k,))
        return [dict(l) for l in linhas]

    def objetivo_principal(self):
        """O objetivo ativo de maior prioridade, ou None se não houver nenhum - usado pelo
        código para liberar (ou não) ações mais arriscadas, como DESVIAR em escolher_acao()."""
        ativos = self.objetivos_ativos(k=1)
        return ativos[0] if ativos else None

    # ------------------------------------------------------------------
    # 4.2) ESTADO EMOCIONAL: culpa e frustração (com decaimento preguiçoso)
    # ------------------------------------------------------------------

    def estado(self, chave):
        """Valor atual (0 a 1). Guardamos valor + hora da última mudança e calculamos aqui
        quanto ele já esmaeceu: valor * 0.5 ** (tempo_passado / meia_vida)."""
        linha = self.db.execute("SELECT valor, atualizado_em FROM estado WHERE chave=?",
                                (chave,)).fetchone()
        if linha is None:
            return 0.0
        return linha["valor"] * 0.5 ** ((time.time() - linha["atualizado_em"]) / MEIA_VIDA[chave])

    def mudar_estado(self, chave, delta):
        novo = limitar(self.estado(chave) + delta)
        self.db.execute(
            "INSERT INTO estado(chave, valor, atualizado_em) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor, atualizado_em=excluded.atualizado_em",
            (chave, novo, time.time()))
        self.db.commit()

    # ------------------------------------------------------------------
    # 4.3) RELAÇÕES: o que sinto por cada outro agente
    # ------------------------------------------------------------------

    def relacao(self, outro):
        self.db.execute("INSERT OR IGNORE INTO relacoes(outro, atualizado_em) VALUES (?, ?)",
                        (outro, time.time()))
        r = dict(self.db.execute("SELECT * FROM relacoes WHERE outro=?", (outro,)).fetchone())
        # O medo esmaece com o tempo, como as outras emoções.
        r["medo"] *= 0.5 ** ((time.time() - r["atualizado_em"]) / MEIA_VIDA["medo"])
        return r

    def mudar_relacao(self, outro, **deltas):
        r = self.relacao(outro)
        for campo, delta in deltas.items():
            r[campo] = limitar(r[campo] + delta)
        self.db.execute(
            "UPDATE relacoes SET confianca=?, medo=?, desconfianca=?, favor_devido=?, "
            "atualizado_em=? WHERE outro=?",
            (r["confianca"], r["medo"], r["desconfianca"], r["favor_devido"], time.time(), outro))
        self.db.commit()

    # ------------------------------------------------------------------
    # 4.4) DECISÕES (o coração do comportamento) - tudo em código, sem LLM
    # ------------------------------------------------------------------

    def _escolher_bode_expiatorio(self, outro):
        """
        Escolhe um terceiro conhecido (que não seja `outro` nem eu) para culpar em DESVIAR.
        Só considera quem já tem uma relação registrada - ou seja, quem já está "na sala",
        já que `relacao()` cria essa linha para todo participante presente no início da cena
        (ver cmd_cena em main.py). Sem candidato, DESVIAR simplesmente não é oferecido.
        """
        candidatos = [linha["outro"] for linha in
                      self.db.execute("SELECT outro FROM relacoes WHERE outro != ?", (outro,))]
        return random.choice(candidatos) if candidatos else None

    def escolher_acao(self, fato, outro):
        """
        O que fazer com UM fato quando `outro` pergunta sobre ele? Generaliza o antigo
        REVELAR/ESCONDER/MENTIR (roadmap, seções 18-19): quem tem um objetivo ativo de alta
        prioridade (ex.: "Não ser descoberto") e o perfil certo para isso pode arriscar DESVIAR
        a suspeita para um terceiro, em vez de só se esquivar. Não é uma opção sempre
        disponível - é uma AÇÃO POSSÍVEL que o código libera conforme a situação, não o LLM
        que inventa.

        Retorna (decisao, extra, chance_de_revelar):
          decisao = REVELAR | ESCONDER | MENTIR | DESVIAR
          extra   = None, exceto para DESVIAR, onde é {"alvo_falso": nome}
        """
        t = self.pers["tracos"]
        rel = self.relacao(outro)
        culpa = self.estado("culpa")

        quer_revelar = (
            2.0 * rel["confianca"]                       # confio em quem pergunta
            + 1.5 * t["honestidade"]                     # sou honesto
            + 1.0 * culpa                                # estou com a consciência pesada
            + 3.0 * rel["medo"] * (1 - t["coragem"])     # tenho medo dele (e pouca coragem)
            + 0.8 * rel["favor_devido"]                  # devo um favor a ele
            - 2.5 * fato["sensibilidade"]                # o fato é delicado
            - 1.0 * rel["desconfianca"]                  # desconfio dele
        )
        p_revelar = sigmoid(quer_revelar)

        # POSTURA PERSISTENTE: se eu já decidi esconder/mentir/desviar sobre este fato nesta
        # conversa, mantenho a decisão enquanto nada mudar de verdade. Sem isso, sortear de novo
        # a cada fala faria qualquer um acabar contando (é só esperar o sorteio). Ameaça, culpa
        # ou confiança que mexem no placar em 0.5 ou mais fazem o agente reconsiderar.
        chave = (outro, fato["id"])
        anterior = self.posturas.get(chave)
        if anterior and abs(quer_revelar - anterior[1]) < 0.5:
            decisao, _, extra = anterior
            return decisao, extra, p_revelar

        if random.random() < p_revelar:
            decisao, extra = "REVELAR", None
        else:
            # Não vou contar a verdade: minto, escondo ou desvio a suspeita? DESVIAR só entra em
            # jogo se um objetivo concreto justificar o risco (não é personalidade sozinha).
            objetivo = self.objetivo_principal()
            arrisca_desviar = (
                objetivo is not None and objetivo["prioridade"] >= 0.7
                and t["dissimulacao"] >= 0.6 and t["empatia"] < 0.5
                and fato["sensibilidade"] >= 0.7
            )
            alvo_falso = self._escolher_bode_expiatorio(outro) if arrisca_desviar else None
            if alvo_falso:
                decisao, extra = "DESVIAR", {"alvo_falso": alvo_falso}
            else:
                # Mentir exige dissimulação, pouca honestidade e pouca culpa, e só vale a pena
                # para fatos sensíveis.
                p_mentir = (t["dissimulacao"] * (1 - t["honestidade"])
                            * (1 - 0.7 * culpa) * fato["sensibilidade"])
                if fato["versao_falsa"] and random.random() < p_mentir:
                    decisao, extra = "MENTIR", None
                else:
                    decisao, extra = "ESCONDER", None
        self.posturas[chave] = (decisao, quer_revelar, extra)
        return decisao, extra, p_revelar

    def escolher_tatica(self, outro):
        """
        Como pedir informação ao outro agente: PEDIR (educado) ou AMEACAR.
        - A "disposição" natural para ameaçar vem dos traços: agressividade, ganância e falta
          de empatia.
        - A FRUSTRAÇÃO (o outro já se recusou a contar) AMPLIFICA essa disposição: quem é
          gentil quase nunca ameaça, mesmo frustrado; quem é agressivo escala rápido.
        - Confiar no outro reduz a chance.
        """
        t = self.pers["tracos"]
        disposicao = 2.0 * t["agressividade"] + 1.0 * t["ganancia"] + 1.5 * (1 - t["empatia"])
        p_ameaca = sigmoid(
            disposicao * (1 + 1.4 * self.estado("frustracao"))
            - 2.0 * self.relacao(outro)["confianca"]
            - 5.7
        )
        tatica = "AMEACAR" if random.random() < p_ameaca else "PEDIR"
        self._log(f"{self.nome} escolheu a tatica {tatica} (chance de ameacar: {p_ameaca:.0%})")
        return tatica

    @staticmethod
    def instrucao_tatica(tatica, outro, topico):
        """Traduz a tática escolhida pelo código em uma instrução para o LLM."""
        if tatica == "AMEACAR":
            return (f'Ameace {outro} (dentro do jogo: parar de confiar, cortar a troca de '
                    f'informações, contar aos outros que esconde coisas) para que conte o que '
                    f'sabe sobre "{topico}".')
        return f'Pergunte a {outro} o que sabe sobre "{topico}".'

    def _pontuacao_suspeita(self, candidato):
        """
        O quanto eu suspeito de `candidato`, combinando a crença já formada (se houver
        evidência ligando esse nome a "é o culpado") com a desconfiança da relação e um
        empurrão para quem eu ainda não consegui arrancar nada. Serve tanto de contexto para
        o LLM decidir (ver escolher_investigado) quanto de fallback caso ele não responda
        nada aproveitável.
        """
        crenca = self.crenca(f"{candidato} é o culpado")
        suspeita = crenca["confianca"] if crenca else 0.0
        return (
            0.6 * suspeita
            + 0.4 * self.relacao(candidato)["desconfianca"]
            + (0.3 if candidato not in self.satisfeitos else 0.0)
        )

    def escolher_investigado(self, candidatos, topico, llm):
        """
        Quem interrogar agora, entre `candidatos`? Diferente das outras decisões da classe,
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
        pontuacoes = {c: self._pontuacao_suspeita(c) for c in candidatos}
        ranking = "\n".join(
            f"- {nome}: suspeita {p:.2f}" + (" (já interrogado)" if nome in self.satisfeitos else "")
            for nome, p in sorted(pontuacoes.items(), key=lambda kv: -kv[1])
        )
        pedido = [
            {"role": "system", "content": (
                "Você é um investigador decidindo quem interrogar a seguir numa investigação. "
                "Responda SOMENTE com o nome exato de uma pessoa da lista, sem mais nada.")},
            {"role": "user", "content": (
                f"Objetivo da investigação: {topico}\n\n"
                f"Suspeitos e o quanto você já suspeita de cada um (0 a 1):\n{ranking}\n\n"
                "Quem você vai interrogar agora? Responda só com o nome.")},
        ]
        resposta = llm.gerar(pedido, max_tokens=20, temperatura=0.3) or ""
        escolhido = detectar_sujeito(resposta, candidatos)
        if escolhido:
            self._log(f"{self.nome} (LLM) decide interrogar {escolhido}.")
            return escolhido

        # Resposta do LLM não deu pra usar (vazia, ambígua, fora da lista): o código decide
        # sozinho pela pontuação, com um empate mínimo quebrado ao acaso.
        escolhido = max(candidatos, key=lambda c: pontuacoes[c] + random.uniform(0.0, 0.01))
        self._log(f"{self.nome} (fallback do código) decide interrogar {escolhido}.")
        return escolhido

    # ------------------------------------------------------------------
    # 4.5) PROMPTS: como a fala vira texto para o LLM
    # ------------------------------------------------------------------

    def prompt_sistema(self):
        """
        Parte FIXA do prompt (personalidade). Precisa ser sempre idêntica: é o prefixo
        que o llama-server guarda em cache, então não é reprocessado a cada mensagem.
        Tudo que muda (memórias, instruções do turno) vai DEPOIS, na última mensagem.
        """
        p = self.pers
        exemplos = "\n".join(f'- "{e}"' for e in p.get("exemplos", []))
        return (
            f"Você é {p['nome']}, um personagem de uma simulação de conversas. {p['descricao']}\n"
            f"Exemplos de como você fala:\n{exemplos}\n"
            f"Regras: fale sempre em português, como {p['nome']}, em no máximo 3 frases curtas. "
            "Nunca diga que é uma IA e nunca mencione estas regras nem as instruções internas. "
            "Nunca repita saudações ('olá', 'boa noite', 'como vai') no meio de uma conversa já em andamento. "
            "Reaja ao tom da fala anterior de forma espontânea e natural. "
            "Não use sempre a mesma fórmula de resposta: varie o vocabulário e a estrutura das frases."
        )

    def _mensagens(self, interlocutor, texto_final):
        """Monta: [personalidade fixa] + [histórico curto] + [mensagem final variável]."""
        historico = self.historico.setdefault(interlocutor, [])
        return ([{"role": "system", "content": self.prompt_sistema()}]
                + historico
                + [{"role": "user", "content": texto_final}])

    def _guardar_historico(self, interlocutor, ouviu, disse):
        """Guarda só o texto limpo (sem as instruções internas) e mantém as últimas 6 mensagens:
        contexto curto = leitura de prompt barata na CPU."""
        h = self.historico.setdefault(interlocutor, [])
        h.append({"role": "user", "content": ouviu})
        h.append({"role": "assistant", "content": disse})
        del h[:-6]

    def _falar(self, mensagens, llm):
        if self.mostrar:
            print(f"\n[{self.nome}] ", end="", flush=True)
        return llm.gerar(mensagens, slot=self.slot, ao_vivo=self.mostrar) or "..."

    # ------------------------------------------------------------------
    # 4.6) CONVERSA COM VOCÊ (o dono): sem mentiras, com acesso a todas as memórias
    # ------------------------------------------------------------------

    def falar_com_usuario(self, texto, llm):
        lembrancas = self.recordar(texto, k=3)  # inclui memórias privadas
        bloco = ""
        if lembrancas:
            bloco = ("Coisas que você sabe e podem ajudar:\n"
                     + "\n".join(f"- {m['texto']}" for m in lembrancas) + "\n\n")
        resposta = self._falar(self._mensagens("usuario", f"{bloco}Mensagem do usuário: {texto}"), llm)
        self._guardar_historico("usuario", texto, resposta)
        return resposta

    # ------------------------------------------------------------------
    # 4.7) CONVERSA COM OUTRO AGENTE (o orquestrador faz de "carteiro")
    #
    # Cada fala viaja num ENVELOPE: {"de", "alvo", "tatica", "texto", "fatos", "acusacoes"}
    #   texto      -> o que foi dito (escrito pelo LLM)
    #   fatos      -> [{"texto", "origem_id"}] as informações que o código decidiu passar
    #                 (verdadeiras ou falsas); origem_id identifica o fato-base na cabeça de
    #                 quem contou, e é o que permite notar quando ele muda de versão depois
    #                 (ver detecção de contradição em receber())
    #   acusacoes  -> [{"assunto", "proposicao", "peso"}] insinuações da ação DESVIAR
    #   tatica     -> a intenção da fala (PEDIR, AMEACAR ou NENHUMA)
    # Assim o outro agente atualiza medo/memória por CÓDIGO, sem gastar inferência para
    # "interpretar" a fala. Um agente nunca enxerga o banco nem o prompt do outro.
    #
    # ESCOPO: só detectamos contradição quando é a MESMA origem mudando de versão sobre o
    # MESMO fato (origem_id bate). Duas testemunhas diferentes discordando uma da outra sobre
    # o mesmo assunto não é pego aqui - isso exigiria comparar texto livre semanticamente
    # (roadmap, seção 8: memória semântica), o que é trabalho futuro.
    # ------------------------------------------------------------------

    def nova_conversa(self, outro):
        self.historico[outro] = []
        self.contados = {c for c in self.contados if c[0] != outro}
        self.posturas = {c: v for c, v in self.posturas.items() if c[0] != outro}
        self.satisfeitos.discard(outro)
        self.aguardando = False

    def abrir_conversa(self, outro, topico, llm):
        """Primeira fala: puxa assunto e já usa a tática escolhida pelo código."""
        tatica = self.escolher_tatica(outro)
        instrucao = f"Comece uma conversa com {outro}. "
        conhecido = self.sabe_sobre(outro)
        if conhecido:  # RECONHECIMENTO: já sei quem é {outro}, mesmo antes de ela falar
            fatos = "; ".join(f'"{m["texto"]}"' for m in conhecido)
            instrucao += f"Você já conhece {outro} e sabe disto sobre ela/ele: {fatos}. "
            self._log(f"{self.nome} reconhece {outro} ({len(conhecido)} fato(s) conhecido(s))")
        instrucao += self.instrucao_tatica(tatica, outro, topico)
        texto = self._falar(self._mensagens(outro, instrucao), llm)
        self._guardar_historico(outro, f"(Você começa a conversa com {outro}.)", texto)
        self.aguardando = True
        return {"de": self.nome, "alvo": outro, "tatica": tatica, "texto": texto, "fatos": []}

    def receber(self, env):
        """Efeitos de ouvir uma fala do outro. Só código: nenhuma chamada ao LLM."""
        outro = env["de"]
        alvo = env.get("alvo")
        sou_alvo = (alvo is None or alvo == self.nome)

        # (a) Ameaça:
        # Se eu sou o alvo da ameaça: o medo sobe, confiança cai e desconfiança sobe.
        # Se sou testemunha na sala: observo a agressividade e a desconfiança de quem ameaçou sobe.
        if env.get("tatica") == "AMEACAR":
            if sou_alvo:
                self.mudar_relacao(outro, medo=0.5, confianca=-0.1, desconfianca=0.1)
                self._log(f"{self.nome} foi ameacado(a) por {outro}: medo agora "
                          f"{self.relacao(outro)['medo']:.2f}")
            else:
                self.mudar_relacao(outro, desconfianca=0.15, confianca=-0.05)
                self._log(f"{self.nome} presenciou {outro} ameacando {alvo}: desconfiança de {outro} subiu")

        # (b) Informação recebida vira memória MINHA, com origem = quem contou. Antes de
        #     guardar, checo se {outro} já me disse algo DIFERENTE sobre o mesmo fato-base
        #     (mesmo origem_id) - é a detecção de contradição (roadmap, seção 14). Se bateu,
        #     ele perde confiança em vez de ganhar; senão, quem conta ganha um pouco de
        #     confiança e eu passo a dever um favor.
        for fato in env.get("fatos", []):
            texto, origem_id = fato["texto"], fato.get("origem_id")
            anterior = None
            if origem_id is not None:
                anterior = self.db.execute(
                    "SELECT * FROM memorias WHERE origem=? AND origem_id=? AND texto!=? "
                    "ORDER BY id DESC LIMIT 1", (outro, origem_id, texto)).fetchone()

            id_mem = self.lembrar(texto, origem=outro, sensibilidade=0.5, origem_id=origem_id)

            if anterior is not None:
                self.db.execute("UPDATE memorias SET contraditoria=1 WHERE id IN (?, ?)",
                                (anterior["id"], id_mem))
                self.db.commit()
                self.mudar_relacao(outro, desconfianca=0.3, confianca=-0.2)
                self._log(f"{self.nome} pegou {outro} se contradizendo: antes disse "
                          f"{anterior['texto']!r}, agora diz {texto!r}")
            else:
                self.mudar_relacao(outro, confianca=0.05, favor_devido=0.1)
                self._log(f"{self.nome} aprendeu com {outro}: {texto!r}")

        # (b2) Acusações (ação DESVIAR, ver escolher_acao): reforçam uma CRENÇA meu sobre o
        #      acusado, não uma memória de fato consumado - é só a palavra de {outro} contra
        #      alguém, com peso reduzido (ver 'peso' na acusação). Quem é o próprio acusado
        #      ignora o boato: ele já sabe se é inocente ou não, não aprende isso ouvindo.
        for ac in env.get("acusacoes", []):
            if ac["assunto"] == self.nome:
                continue
            peso = ac.get("peso", 0.15)
            self.atualizar_crenca(f"{ac['assunto']} é o culpado", delta=peso, origem=outro,
                                  assunto=ac["assunto"], evidencia=f"acusacao:{outro}")
            self._log(f"{self.nome} ouviu {outro} insinuar que {ac['assunto']} pode estar envolvido")

        # (c) Se eu tinha pedido algo (e sou o alvo da resposta): vieram fatos? Sem informação, a frustração sobe
        #     (e alimenta a chance de ameaçar); com informação, ela cai.
        if self.aguardando and sou_alvo:
            self.mudar_estado("frustracao", -0.5 if env.get("fatos") else 0.35)
            if env.get("fatos"):
                self.satisfeitos.add(outro)  # consegui o que queria: não preciso mais pressionar
            self.aguardando = False

    def responder(self, env, topico, llm, ultima=False):
        """Ouve o envelope do outro e responde: decide o que contar e como pedir de volta."""
        outro = env["de"]
        self.receber(env)

        # 1) RECONHECIMENTO: o que já sei especificamente sobre {outro} - independe do
        #    assunto do momento e não passa pelo filtro de revelar/esconder/mentir (não é
        #    fofoca sobre terceiros, é eu reconhecendo quem está falando comigo).
        instrucoes = []
        conhecido = self.sabe_sobre(outro)
        if conhecido:
            fatos = "; ".join(f'"{m["texto"]}"' for m in conhecido)
            instrucoes.append(f"Você já conhece {outro} e sabe disto sobre ela/ele: {fatos}. "
                              "Pode usar isso com naturalidade, sem parecer um interrogatório.")
            self._log(f"{self.nome} reconhece {outro} ({len(conhecido)} fato(s) conhecido(s))")

        # 2) Memórias relevantes E compartilháveis para a troca sobre TERCEIROS (o
        #    isolamento é garantido aqui). Ficam de fora: o que já contei a este agente, o
        #    que ELE mesmo me contou, o que é sobre ele mesmo (isso já foi tratado acima, como
        #    reconhecimento, não como fofoca) e o que é sobre MIM MESMO - se alguém me contou
        #    (fofoca) que "Fulano é o culpado" e Fulano sou eu, isso NÃO é uma fofoca de
        #    terceiro que eu preciso decidir revelar/esconder/mentir: é sobre a MINHA própria
        #    verdade, que já é tratada separadamente (minha memória com origem='sistema'/minha
        #    própria versão falsa). Sem essa exclusão, o mesmo fato "sobre mim" podia ser
        #    decidido duas vezes no mesmo turno - uma vez pela minha verdade, outra pela
        #    fofoca ecoada - e sair uma fala confessando e mentindo ao mesmo tempo.
        relevantes = self.recordar(f"{env['texto']} {topico}", k=4, so_compartilhaveis=True)
        sobre_terceiros = [m for m in relevantes if m["sobre"] not in (outro, self.nome)]
        pendentes = [m for m in sobre_terceiros
                     if (outro, m["id"]) not in self.contados and m["origem"] != outro][:2]

        # 3) Para cada fato sobre terceiros, o CÓDIGO decide REVELAR, ESCONDER, MENTIR ou DESVIAR.
        fatos_saida, acusacoes, escondeu, decisoes_debug = [], [], False, []
        for fato in pendentes:
            decisao, extra, p = self.escolher_acao(fato, outro)
            self._log(f"{self.nome} decidiu {decisao} (chance de revelar: {p:.0%}) "
                      f"sobre: {fato['texto']!r}")
            decisoes_debug.append((fato["texto"], decisao, p))
            if decisao == "REVELAR":
                fatos_saida.append({"texto": fato["texto"], "origem_id": fato["id"],
                                    "origem": fato["origem"]})
                self.contados.add((outro, fato["id"]))
            elif decisao == "MENTIR":
                # O prompt recebe SÓ a versão falsa: a verdade não entra nele. origem_id é o
                # MESMO da verdade (é o mesmo fato-base) - se este Ator revelar a verdade sobre
                # ele depois, quem ouviu as duas versões pega a contradição (ver receber()).
                fatos_saida.append({"texto": fato["versao_falsa"], "origem_id": fato["id"],
                                    "origem": fato["origem"]})
                self.mudar_estado("culpa", 0.1 + 0.4 * self.pers["tracos"]["empatia"])  # empatia = mais culpa
            elif decisao == "DESVIAR":
                alvo_falso = extra["alvo_falso"]
                acusacoes.append({"assunto": alvo_falso,
                                  "proposicao": f"{alvo_falso} pode estar envolvido nisso.",
                                  "peso": 0.15})
                self.mudar_estado("culpa", 0.05 + 0.3 * self.pers["tracos"]["empatia"])
                self._log(f"{self.nome} desviou a suspeita para {alvo_falso}")
            else:
                escondeu = True

        # 4) Transforma as decisões em instruções concretas para o LLM. Um fato que EU vivi ou
        # sei por mim mesmo (origem 'sistema'/'observacao'/'usuario') pode ser contado em
        # primeira pessoa direto. Um fato que outra PESSOA me contou (fofoca/testemunho
        # relatado) precisa ser instruído como relato de terceiro - senão o LLM repete um texto
        # em primeira pessoa (ex.: uma confissão) como se fosse dele mesmo, um bug real: alguém
        # relatando "fui eu quem roubou" ao repassar a confissão de outra pessoa.
        for fato in fatos_saida:
            if fato["origem"] not in ORIGENS_PROPRIAS:
                instrucoes.append(
                    f'Você soube por {fato["origem"]}: "{fato["texto"]}". Conte isso a {outro} '
                    f'como algo que você ouviu de {fato["origem"]} ("ouvi dizer que...", '
                    f'"{fato["origem"]} me contou que..."), NUNCA como se fosse sobre você mesmo '
                    f"ou algo que você fez.")
            else:
                instrucoes.append(f'Conte a {outro}, com suas palavras: "{fato["texto"]}".')
        for ac in acusacoes:
            instrucoes.append(f'Sugira, com cautela e sem provas concretas, que {ac["assunto"]} '
                              f"pode ter algo a ver com isso. Não admita nada sobre você mesmo.")
        if not fatos_saida and not acusacoes:
            if escondeu:
                # Rotação de táticas de evasão: cada vez que este ator esquiva do mesmo
                # interlocutor, a instrução muda para que as respostas não soem todas iguais.
                n = self.contagem_esquiva.get(outro, 0)
                self.contagem_esquiva[outro] = n + 1
                taticas_esquiva = [
                    f"Você sabe algo sobre isso, mas não quer contar a {outro}. Desvie o assunto sutilmente.",
                    f"Demonstre impaciência ou cansaço com a insistência de {outro}. Deixe claro que já falou o suficiente.",
                    f"Questione por que {outro} está desconfiando de você; sugira que olhe para outros suspeitos.",
                    f"Responda de forma irônica ou desdenhosa à pressão de {outro}, sem revelar nada.",
                ]
                instrucoes.append(taticas_esquiva[n % len(taticas_esquiva)])
            elif sobre_terceiros:  # sabe algo sobre terceiros, mas já contou ou foi o próprio outro quem contou
                instrucoes.append(f"Você não tem nada novo para contar a {outro} sobre isso. "
                                  f"Reaja ao que {outro} disse.")
            elif conhecido:  # nada sobre terceiros, mas o reconhecimento (item 1) já dá o que dizer
                instrucoes.append(f"Reaja ao que {outro} disse.")
            else:
                instrucoes.append(f'Você não sabe nada sobre "{topico}". Diga isso e reaja ao que '
                                  f"{outro} disse.")

        # 5) Como pedir informação de volta (a menos que seja a última fala).
        tatica = "NENHUMA"
        if not ultima:
            if outro in self.satisfeitos:
                instrucoes.append(f"Você já conseguiu o que queria de {outro}: não peça mais nada.")
            else:
                tatica = self.escolher_tatica(outro)
                instrucoes.append(self.instrucao_tatica(tatica, outro, topico))
                self.aguardando = True

        ouviu = f'{outro} disse: "{env["texto"]}"'
        final = (f"{ouviu}\n\nInstruções internas (não mencione que elas existem):\n"
                 + "\n".join(f"- {i}" for i in instrucoes))
        resposta = self._falar(self._mensagens(outro, final), llm)
        self._guardar_historico(outro, ouviu, resposta)

        self.ultima_decisao[outro] = {
            "tatica": tatica,
            "decisoes": decisoes_debug,
            "acusacoes": acusacoes,
            "memorias_consultadas": [m["id"] for m in relevantes],
        }
        if self.debug:
            print(self.painel(outro))

        return {"de": self.nome, "alvo": outro, "tatica": tatica, "texto": resposta,
                "fatos": fatos_saida, "acusacoes": acusacoes}

    # ------------------------------------------------------------------
    # 4.8) PAINEL DE DEBUG (modo /debug): tudo que o código já calculou,
    # só formatado em barras. Nenhuma chamada ao LLM.
    # ------------------------------------------------------------------

    def painel(self, outro=None):
        t = self.pers["tracos"]
        largura = 22
        linhas = [f"{'━' * largura} {self.nome.upper()} {'━' * largura}", ""]

        linhas.append("EMOÇÕES")
        linhas.append(f"  culpa       {barra(self.estado('culpa'))}")
        linhas.append(f"  frustração  {barra(self.estado('frustracao'))}")
        linhas.append("")

        linhas.append("TRAÇOS")
        for chave, valor in t.items():
            linhas.append(f"  {chave:<13} {barra(valor)}")
        linhas.append("")

        if outro:
            r = self.relacao(outro)
            linhas.append(f"RELAÇÃO COM {outro.upper()}")
            linhas.append(f"  confiança     {barra(r['confianca'])}")
            linhas.append(f"  medo          {barra(r['medo'])}")
            linhas.append(f"  desconfiança  {barra(r['desconfianca'])}")
            linhas.append(f"  favor devido  {barra(r['favor_devido'])}")
            linhas.append("")

            dados = self.ultima_decisao.get(outro)
            if dados:
                linhas.append(f"ÚLTIMA TÁTICA: {dados['tatica']}")
                if dados["decisoes"]:
                    linhas.append("PROBABILIDADES (chance de revelar)")
                    for texto, decisao, p in dados["decisoes"]:
                        linhas.append(f"  [{decisao:<8}] {barra(p)}  {texto[:40]!r}")
                if dados["memorias_consultadas"]:
                    ids = ", ".join(f"#{i}" for i in dados["memorias_consultadas"])
                    linhas.append(f"MEMÓRIAS CONSULTADAS: {ids}")
                if dados.get("acusacoes"):
                    alvos = ", ".join(ac["assunto"] for ac in dados["acusacoes"])
                    linhas.append(f"DESVIOU A SUSPEITA PARA: {alvos}")
                linhas.append("")

        objetivos = self.objetivos_ativos()
        if objetivos:
            linhas.append("OBJETIVOS")
            for o in objetivos:
                linhas.append(f"  {barra(o['prioridade'])}  {o['descricao']} "
                              f"(progresso {o['progresso']:.0%})")
            linhas.append("")

        crencas = self.listar_crencas()
        if crencas:
            linhas.append("HIPÓTESES / CRENÇAS")
            for c in crencas:
                linhas.append(f"  {barra(c['confianca'])}  {c['proposicao']}")
            linhas.append("")

        contradicoes = self.contradicoes()
        if contradicoes:
            linhas.append("CONTRADIÇÕES PEGAS")
            for c in contradicoes:
                linhas.append(f"  {c['origem']} disse: {c['texto']!r}")
            linhas.append("")

        linhas.append("━" * (2 * largura + len(self.nome) + 2))
        return "\n".join(linhas)

    # ------------------------------------------------------------------
    # 4.9) RESUMO para o terminal
    # ------------------------------------------------------------------

    def resumo(self):
        t = self.pers["tracos"]
        linhas = [f"{self.nome} | " + ", ".join(f"{k} {v:.2f}" for k, v in t.items()),
                  f"   culpa {self.estado('culpa'):.2f} | frustracao {self.estado('frustracao'):.2f}"]
        for linha in self.db.execute("SELECT outro FROM relacoes ORDER BY outro"):
            r = self.relacao(linha["outro"])
            linhas.append(f"   com {linha['outro']}: confianca {r['confianca']:.2f} | "
                          f"medo {r['medo']:.2f} | desconfianca {r['desconfianca']:.2f} | "
                          f"deve favor {r['favor_devido']:.2f}")
        return "\n".join(linhas)


# Alias para compatibilidade entre nomenclatura 'Agente' e 'Ator'
Ator = Agente
