"""
roteirista.py - O Roteirista: IA que gera a trama e personalidades dos atores.

O Roteirista é responsável por:
1) Criar um mistério fechado e coerente (cenário, culpado, investigador e testemunhas).
2) Definir traços psicológicos e jeitos de falar compatíveis com a história.
3) Gerar a 'verdade' e o 'álibi' do culpado, sem contradições com o que as testemunhas viram.
4) Materializar esses dados em arquivos .db SQLite isolados (um para cada ator).

Uso:
    python roteirista.py --tema "Uma xícara antiga foi quebrada durante o jantar"
    python roteirista.py --exemplo --pasta agentes
    python roteirista.py --carregar cena.json --pasta agentes
"""
import argparse
import json
import os
import re
import sys

from Ator import Ator, TRACOS_PADRAO, criar_ator, limitar
from llm import LLM
from mundo import abrir_mundo, posicionar, registrar_evento, registrar_evidencia, registrar_local

SCHEMA_CENA = {
    "type": "object",
    "properties": {
        "cena": {
            "type": "string",
            "description": "Descrição sucinta do incidente/crime que ocorreu na cena."
        },
        "atores": {
            "type": "array",
            "description": "Lista de exatamente 5 atores que participam da cena.",
            "items": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "descricao": {"type": "string"},
                    "exemplos": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "tracos": {
                        "type": "object",
                        "properties": {
                            "honestidade": {"type": "number"},
                            "dissimulacao": {"type": "number"},
                            "empatia": {"type": "number"},
                            "coragem": {"type": "number"},
                            "agressividade": {"type": "number"},
                            "ganancia": {"type": "number"}
                        },
                        "required": ["honestidade", "dissimulacao", "empatia", "coragem", "agressividade", "ganancia"]
                    },
                    "papel": {
                        "type": "string",
                        "enum": ["culpado", "investigador", "testemunha"]
                    },
                    "verdade": {
                        "type": ["string", "null"],
                        "description": "Preenchido apenas para o culpado: o que ele realmente fez."
                    },
                    "alibi": {
                        "type": ["string", "null"],
                        "description": "Preenchido apenas para o culpado: a versão mentirosa/álibi que ele usará."
                    },
                    "objetivo": {
                        "type": ["string", "null"],
                        "description": "Preenchido apenas para o investigador: o que ele deve descobrir."
                    },
                    "viu": {
                        "type": ["string", "null"],
                        "description": "Para testemunhas: fato observado sobre o culpado, ou null se não souber de nada."
                    }
                },
                "required": ["nome", "descricao", "exemplos", "tracos", "papel"]
            }
        }
    },
    "required": ["cena", "atores"]
}

PROMPT_SISTEMA_ROTEIRISTA = """Você é um roteirista especializado em simulações teatrais e jogos de investigação psicológica.
Sua missão é gerar um mistério completo com exatamente 5 personagens (atores), pronto para ser executado por modelos de linguagem.

Regras estruturais obrigatórias:
1. Deve haver exatamente 5 atores:
   - 1 "culpado": cometeu o ato. Deve ter 'verdade' (confissão detalhada do ato) e 'alibi' (versão falsa e plausível que ele contará).
   - 1 "investigador": encarregado de desvendar o mistério. Deve ter 'objetivo' claro relacionado ao tema (ex.: descobrir quem roubou a joia, quem causou o acidente, etc.).
   - 3 "testemunha": pessoas que estavam no local. O campo 'viu' deve conter um fato observado sobre o culpado (mencionando o nome do culpado), OU ser null caso a testemunha genuinamente não tenha visto nada. Pelo menos uma testemunha deve ter visto algo suspeito.
2. Coerência lógica absoluta:
   - O 'alibi' do culpado não pode entrar em contradição boba com o que as testemunhas viram, mas deve permitir brechas para dedução e pressão psicológica.
3. Traços numéricos (valores de 0.0 a 1.0 para honestidade, dissimulacao, empatia, coragem, agressividade, ganancia):
   - Ajuste os traços de acordo com o papel. Exemplo: um culpado frio deve ter alta dissimulacao e baixa honestidade; uma testemunha assustada deve ter baixa coragem; um investigador determinado deve ter coragem e agressividade moderadas.
4. Exemplos de fala:
   - Cada ator deve ter 2 frases de exemplo demonstrando seu estilo, vocabulário e temperamento.

A sua resposta deve ser EXCLUSIVAMENTE um objeto JSON válido, sem texto ou explicações antes ou depois.
"""


# ============================================================================
# 1) EXTRAÇÃO E VALIDAÇÃO DE JSON
# ============================================================================

def extrair_json(texto):
    """
    Extrai e decodifica um JSON a partir da resposta do modelo,
    mesmo que haja blocos ```json ... ``` ou texto ao redor.
    """
    texto = texto.strip()
    # Remove cercas de markdown
    if "```" in texto:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", texto)
        if match:
            texto = match.group(1).strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    # Tenta recortar do primeiro '{' até o último '}'
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio != -1 and fim != -1 and fim > inicio:
        sub = texto[inicio:fim + 1]
        try:
            return json.loads(sub)
        except json.JSONDecodeError as err:
            raise ValueError(f"Não foi possível decodificar o JSON retornado: {err}\nTexto bruto:\n{texto}")
    raise ValueError(f"Nenhum objeto JSON encontrado na resposta:\n{texto}")


def validar_dados_cena(dados):
    """Garante que o dicionário possui os campos obrigatórios e a estrutura esperada."""
    if not isinstance(dados, dict):
        raise ValueError("A raiz do JSON deve ser um objeto.")
    if "cena" not in dados or not isinstance(dados["cena"], str):
        raise ValueError("O campo 'cena' é obrigatório e deve ser uma string descritiva.")
    if "atores" not in dados or not isinstance(dados["atores"], list):
        raise ValueError("O campo 'atores' é obrigatório e deve ser uma lista.")

    atores = dados["atores"]
    if len(atores) != 5:
        print(f"   [aviso] A cena tem {len(atores)} atores (o padrão recomendado é 5).")

    papeis = [a.get("papel") for a in atores]
    if "culpado" not in papeis:
        raise ValueError("A cena precisa ter pelo menos 1 ator com papel 'culpado'.")
    if "investigador" not in papeis:
        raise ValueError("A cena precisa ter pelo menos 1 ator com papel 'investigador'.")

    for a in atores:
        nome = a.get("nome")
        if not nome or not isinstance(nome, str):
            raise ValueError("Cada ator deve ter um 'nome' válido.")
        papel = a.get("papel")
        if papel == "culpado":
            if not a.get("verdade"):
                raise ValueError(f"O culpado ({nome}) precisa ter o campo 'verdade' preenchido.")
            if not a.get("alibi"):
                raise ValueError(f"O culpado ({nome}) precisa ter o campo 'alibi' preenchido.")
        elif papel == "investigador":
            if not a.get("objetivo"):
                a["objetivo"] = f"Descobrir quem cometeu o ato em: {dados['cena']}"

        # Assegura que traços existem com valores numéricos limitados
        tracos = a.setdefault("tracos", {})
        for t in TRACOS_PADRAO:
            val = tracos.get(t, TRACOS_PADRAO[t])
            try:
                tracos[t] = limitar(float(val))
            except (ValueError, TypeError):
                tracos[t] = TRACOS_PADRAO[t]

        a.setdefault("exemplos", [])
        a.setdefault("descricao", f"Personagem {nome}")


# ============================================================================
# 2) GERAÇÃO DA CENA VIA LLM
# ============================================================================

def gerar_cena_llm(llm, tema, max_tokens=1800, temperatura=0.7):
    """
    Solicita ao modelo a criação da cena e dos 5 atores a partir do tema proposto.
    Tenta usar response_format para forçar JSON e trata possíveis incompatibilidades.
    """
    mensagens = [
        {"role": "system", "content": PROMPT_SISTEMA_ROTEIRISTA},
        {"role": "user", "content": f"Crie um mistério completo com 5 personagens sobre o seguinte tema:\n\"{tema}\""}
    ]

    print(f"\n[Roteirista] Conectando a {llm.url} para criar a cena...")
    print(f"[Roteirista] Tema: \"{tema}\"")

    # Tentativa 1: com schema estruturado
    formato_schema = {
        "type": "json_object",
        "schema": SCHEMA_CENA
    }

    resposta_texto = ""
    try:
        resposta_texto = llm.gerar(
            mensagens,
            max_tokens=max_tokens,
            temperatura=temperatura,
            ao_vivo=True,
            response_format=formato_schema
        )
    except RuntimeError as err:
        # Se o servidor rejeitar o formato avançado de schema, tenta json_object genérico
        print(f"   [aviso] Falha com schema avançado ({err}). Tentando formato JSON simples...")
        try:
            resposta_texto = llm.gerar(
                mensagens,
                max_tokens=max_tokens,
                temperatura=temperatura,
                ao_vivo=True,
                response_format={"type": "json_object"}
            )
        except RuntimeError:
            print("   [aviso] Tentando chamada sem parâmetro response_format...")
            resposta_texto = llm.gerar(
                mensagens,
                max_tokens=max_tokens,
                temperatura=temperatura,
                ao_vivo=True
            )

    dados = extrair_json(resposta_texto)
    validar_dados_cena(dados)
    return dados


# ============================================================================
# 3) MATERIALIZAÇÃO DOS ATORES NO DISCO (.db)
# ============================================================================

def materializar_cena(dados_cena, pasta_atores="atores", pasta_cenario="cenario", slots=2):
    """
    Transforma a cena JSON em arquivos .db SQLite reais na pasta de atores
    e salva os metadados cena.json na pasta do cenário.
    """
    validar_dados_cena(dados_cena)
    os.makedirs(pasta_atores, exist_ok=True)
    os.makedirs(pasta_cenario, exist_ok=True)

    atores_dados = dados_cena["atores"]
    culpado_nome = next((a["nome"] for a in atores_dados if a["papel"] == "culpado"), None)
    investigador_nome = next((a["nome"] for a in atores_dados if a["papel"] == "investigador"), None)

    print(f"\n=== Materializando cena ===")
    print(f"Cenário salvo em: '{pasta_cenario}/cena.json'")
    print(f"Atores salvos em: '{pasta_atores}/'")
    print(f"Cena: {dados_cena['cena']}")
    print(f"Culpado: {culpado_nome} | Investigador: {investigador_nome}")

    caminho_meta = os.path.join(pasta_cenario, "cena.json")
    with open(caminho_meta, "w", encoding="utf-8") as f:
        json.dump(dados_cena, f, ensure_ascii=False, indent=2)
    print(f"Metadados do cenário gravados em: {caminho_meta}")

    # WorldState: a verdade objetiva do crime vira um EVENTO em mundo.db, não só uma string
    # solta no cena.json. Hoje só existe 1 local (o Roteirista ainda não gera múltiplos - ver
    # nota de escopo em mundo.py); por isso o evento é publico=False e cada testemunha que
    # "viu" algo é ligada explicitamente como evidência, em vez de por percepção automática.
    caminho_mundo = os.path.join(pasta_cenario, "mundo.db")
    mundo = abrir_mundo(caminho_mundo)
    registrar_local(mundo, "cena", descricao=dados_cena["cena"], publico=False)
    for a_info in atores_dados:
        posicionar(mundo, a_info["nome"], "cena", papel=a_info["papel"])

    id_evento_crime = None
    if culpado_nome:
        culpado_info = next(a for a in atores_dados if a["papel"] == "culpado")
        id_evento_crime, _ = registrar_evento(
            mundo, "crime", ator=culpado_nome, local="cena",
            dados={"proposicao": culpado_info["verdade"]}, publico=False)
    print(f"Mundo (verdade objetiva) gravado em: {caminho_mundo}")

    atores_criados = {}
    for i, a_info in enumerate(atores_dados):
        nome = a_info["nome"]
        papel = a_info["papel"]
        caminho_db = os.path.join(pasta_atores, f"{nome.lower()}.db")

        # Se já existia um .db com esse nome, remove para começar a cena limpa
        if os.path.exists(caminho_db):
            for sufixo in ("", "-wal", "-shm"):
                arq = caminho_db + sufixo
                if os.path.exists(arq):
                    try:
                        os.remove(arq)
                    except OSError:
                        pass

        # 1) Cria o banco com a personalidade
        criar_ator(
            caminho_db,
            nome=nome,
            descricao=a_info["descricao"],
            exemplos=a_info.get("exemplos", []),
            tracos=a_info["tracos"]
        )

        # 2) Abre o ator para gravar as memórias iniciais
        ator = Ator(caminho_db, slot=i % slots)

        if papel == "culpado":
            verdade = a_info["verdade"]
            alibi = a_info["alibi"]
            # Sensibilidade alta (0.9): é o segredo do crime
            id_mem = ator.lembrar(verdade, origem="sistema", sensibilidade=0.9, compartilhavel=1)
            ator.definir_versao_falsa(id_mem, alibi)
            # Objetivo estruturado (prioridade/risco altos): é o que libera a ação DESVIAR em
            # escolher_acao(), não só a personalidade dele - ver Ator.py, seção 4.4.
            ator.formar_objetivo("Não ser descoberto", prioridade=0.9, risco=0.9)
            print(f"   [Culpado] {nome}: gravada a verdade (memória #{id_mem}) e o álibi pré-gerado.")

        elif papel == "investigador":
            objetivo = a_info.get("objetivo")
            if objetivo:
                # O objetivo serve como memória inicial para guiar a atenção do investigador
                id_mem = ator.lembrar(f"Objetivo da investigação: {objetivo}",
                                      origem="sistema", sensibilidade=0.2, compartilhavel=1)
                ator.formar_objetivo(objetivo, prioridade=0.9)
                print(f"   [Investigador] {nome}: objetivo definido (memória #{id_mem}).")

        elif papel == "testemunha":
            viu = a_info.get("viu")
            if viu:
                # Se viu algo, vincula ao culpado usando o campo 'sobre'
                id_mem = ator.lembrar(viu, origem="observacao", sensibilidade=0.6,
                                      compartilhavel=1, sobre=culpado_nome)
                print(f"   [Testemunha] {nome}: gravado fato observado sobre {culpado_nome} (memória #{id_mem}).")
                if id_evento_crime is not None:
                    registrar_evidencia(mundo, id_evento_crime, viu, origem=nome, assunto=culpado_nome)
            else:
                print(f"   [Testemunha] {nome}: não presenciou nada relevante (sem memórias iniciais).")

        ator.db.close()
        atores_criados[nome.lower()] = caminho_db

    mundo.close()
    print(f"\nSucesso! {len(atores_criados)} atores materializados prontos para a cena.")
    return atores_criados


# ============================================================================
# 4) INTERFACE CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="O Roteirista - Gerador de tramas e atores de IA (100% via LLM)")
    parser.add_argument("--tema", type=str, help="Tema ou incidente central para o Roteirista inventar a cena")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Endereço do llama-server (ex: 8080 ou 8081)")
    parser.add_argument("--pasta", default="atores", help="Pasta onde os arquivos .db dos atores serão criados")
    parser.add_argument("--cenario", default="cenario", help="Pasta onde o arquivo cena.json será salvo")
    parser.add_argument("--slots", type=int, default=2, help="Número de slots no llama-server")
    parser.add_argument("--carregar", type=str, help="Carrega e materializa uma cena a partir de um arquivo JSON")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.carregar:
        print(f"[Roteirista] Carregando cena de {args.carregar}...")
        with open(args.carregar, "r", encoding="utf-8") as f:
            dados = json.load(f)
        materializar_cena(dados, pasta_atores=args.pasta, pasta_cenario=args.cenario, slots=args.slots)
        return

    tema = args.tema
    while not tema:
        print("\n=== O Roteirista de Cenas (100% IA) ===")
        print("Digite o tema ou incidente da cena a ser criada pelo modelo:")
        tema = input("> ").strip()
        if not tema:
            print("Por favor, digite um tema para que a IA possa criar a história.")

    llm = LLM(url=args.url, timeout=300)
    try:
        dados_cena = gerar_cena_llm(llm, tema)
        materializar_cena(dados_cena, pasta_atores=args.pasta, pasta_cenario=args.cenario, slots=args.slots)
    except Exception as e:
        print(f"\n[Erro do Roteirista] {e}")
        print("Verifique se o llama-server está em execução com o modelo carregado.")
        sys.exit(1)


if __name__ == "__main__":
    main()
