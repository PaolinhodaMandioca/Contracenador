# Contracenador

Simulação de personagens de IA que interagem entre si com personalidade, memória, emoções e
relações. Cada Ator é **um arquivo `.db`** (SQLite) isolado. Um **Roteirista** (LLM maior) cria a
cena e os personagens; os **Atores** (LLM menor) interpretam. Quem decide se um Ator revela,
esconde, mente ou ameaça é o **código** (não o modelo); o LLM só escreve a fala.

Veja a visão completa e o roadmap do projeto em
[`Contracenador_Roadmap.md`](Contracenador_Roadmap.md).

## Arquivos

| Arquivo | Papel |
|---|---|
| `llm.py` | Cliente do llama-server (só biblioteca padrão, com streaming no terminal) |
| `Ator.py` | Cérebro do Ator: banco, memória, emoções, decisões, prompts |
| `roteirista.py` | O Roteirista: gera a cena e os 5 personagens via LLM (JSON estruturado) e materializa os `.db` |
| `main.py` | Terminal + orquestrador; sobe/derruba os servidores llama-server automaticamente |
| `atores/` | Criada na 1ª execução, um `.db` por ator (gerado pelo Roteirista ou por `/novo`) |
| `cenario/` | Guarda `cena.json` com os metadados da cena atual (incidente, papéis, verdade) |

Não há nada para instalar além de Python 3.8+ e o `llama-server` (llama.cpp). `main.py` sobe os
servidores sozinho — não é preciso rodar `llama-server` manualmente.

## Modelos

- **Atores**: `Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M` (padrão) — respostas rápidas, uma inferência
  por fala.
- **Roteirista**: `Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M` (padrão) — só entra em ação em `/roteiro`,
  para gerar a cena e os personagens com mais qualidade.

Os modelos padrão são baixados automaticamente do Hugging Face na primeira execução (flag `-hf`
do `llama-server`). Para usar um `.gguf` local, passe o caminho do arquivo em vez do repositório.

Como a troca de modelos consome RAM/VRAM, `main.py` **derruba o servidor dos Atores antes de subir
o do Roteirista** e o traz de volta assim que a cena é gerada — tudo automático, sem intervenção
manual.

## Como rodar

```bash
python main.py
```

Se a pasta `atores/` estiver vazia, o programa já pergunta o tema e chama o Roteirista para criar
a primeira cena. Flags úteis:

```bash
python main.py --modelo-atores Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M \
                --modelo-roteirista Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M \
                --slots 2 --pasta atores --cenario cenario
```

| Flag | Efeito |
|---|---|
| `--modelo-atores` / `--modelo-roteirista` | Repositório HF (`Org/Repo:arquivo.gguf`) ou caminho local `.gguf` |
| `--porta-atores` / `--porta-roteirista` | Portas dos dois servidores (padrão 8080/8081) |
| `--slots` | Nº de slots KV do llama-server dos Atores (cada Ator sempre usa o mesmo slot) |
| `--threads` | Núcleos de CPU (padrão: automático) |
| `--pasta` / `--cenario` | Pastas dos `.db` dos Atores e do `cena.json` |
| `--semente` | Fixa o sorteio das decisões (útil para testes reprodutíveis) |

## Comandos

| Comando | O que faz |
|---|---|
| (texto normal) | Conversa com o Ator atual |
| `/cenario` | Mostra o incidente e os personagens da cena atual |
| `/cena [rodadas]` ou `/sala` | Encena a investigação: o investigador interroga até descobrir a verdade ou esgotar as rodadas (padrão 15) |
| `/roteiro [tema]` | Gera uma nova cena com 5 Atores via LLM (troca para o modelo do Roteirista e volta) |
| `/atores` | Lista os Atores carregados |
| `/falar <nome>` | Troca o Ator com quem você conversa |
| `/lembrar <texto>` | Ensina um fato ao Ator atual (pergunta sensibilidade e se pode ser contado a outros) |
| `/memorias` | Lista o que o Ator atual sabe (com origem e versão falsa) |
| `/falsa <id> <texto>` | Escreve à mão a versão falsa de uma memória |
| `/sobre <id> <nome>` | Diz de quem é o assunto de uma memória (sem nome remove) |
| `/estado` | Traços, emoções e relações do Ator atual |
| `/tracos <traço> <0 a 1>` | Muda um traço do Ator atual na hora |
| `/novo <nome>` | Cria um Ator novo avulso (um `.db` novo) |
| `/conversar <A> <B> <tópico>` | Faz A e B conversarem entre si sobre o tópico |
| `/turnos <N>` | Nº de falas de `/conversar` (padrão 4) |
| `/ajuda` / `/sair` | Ajuda / encerra o programa |

Enquanto o programa roda, o SQLite cria arquivos auxiliares `-wal` e `-shm` ao lado de cada `.db`
(normal, somem ao sair).

## Teste rápido

```
/cenario
/cena
```

As linhas que começam com `.` mostram as decisões do código (`REVELAR`, `ESCONDER`, `MENTIR`,
tática `PEDIR`/`AMEACAR` e as chances). Para uma conversa livre entre dois Atores específicos, use
`/conversar <A> <B> <tópico>`.

## Como as decisões funcionam

| Parâmetro | Onde fica | Efeito |
|---|---|---|
| honestidade, dissimulação | traços | Chance de revelar e de mentir em vez de só esconder |
| empatia | traço | Mentir gera mais culpa; ameaçar fica menos provável |
| coragem | traço | Reduz o efeito do medo causado por ameaças |
| agressividade, ganância | traços | Disposição para ameaçar |
| culpa | estado | Sobe ao mentir; empurra para confessar; esmaece com o tempo |
| frustração | estado | Sobe quando o outro não conta; amplifica a disposição de ameaçar |
| confiança, medo, desconfiança, favor devido | relação | Empurram a decisão de revelar a quem pergunta |
| sensibilidade | fato | Quanto mais sensível, mais difícil de revelar |

Os pesos estão em `decidir()` e `escolher_tatica()` (em `Ator.py`), e as meias-vidas em
`MEIA_VIDA`. Regras que valem a pena conhecer:

- **Isolamento por construção:** nas conversas com outro Ator só entram memórias
  `compartilhavel = 1`. O que é privado nem é lido do banco.
- **Mentir:** o prompt recebe só a `versao_falsa`; a verdade não entra nele.
- **Postura persistente:** quem decidiu esconder ou mentir mantém a decisão até o placar mudar em
  0.5 ou mais (ameaça, culpa, confiança). Sem isso, qualquer um acabaria contando por sorteio.
- **Quem já conseguiu a informação** para de pressionar.

## Limitações conhecidas

- Busca de memória por palavras em comum (sem embeddings): a pergunta precisa usar palavras
  parecidas com as do fato.
- Histórico, posturas e "quem já contou o quê" vivem só na RAM (somem ao fechar). Fatos, emoções e
  relações ficam no `.db`.
- Contradições não são detectadas (se um Ator mente e depois confessa, o outro fica com as duas
  versões).
- Não existe um estado do mundo (`WorldState`) central: a "verdade" de cada cena vive apenas dentro
  do `cena.json` gerado pelo Roteirista.
- Salvar fatos é sempre explícito (`/lembrar` ou gerado pelo Roteirista); o Ator não extrai fatos
  sozinho da conversa.

## Roadmap

O plano de evolução do projeto (WorldState, crenças vs. verdade, evidências, objetivos, Diretor,
Cenógrafo etc.) está em [`Contracenador_Roadmap.md`](Contracenador_Roadmap.md).
