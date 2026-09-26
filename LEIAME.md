# Agentes de IA isolados: versão simples

Cada agente é **um arquivo `.db`** (SQLite) com personalidade, memória, emoções e relações.
Um único `llama-server` com um modelo de 3B atende todos os agentes. Quem decide se um agente
revela, esconde, mente ou ameaça é o **código** (não o modelo); o LLM só escreve a fala.

## Arquivos

| Arquivo | Papel |
|---|---|
| `llm.py` | Cliente do llama-server (só biblioteca padrão, com streaming no terminal) |
| `agente.py` | Cérebro do agente: banco, memória, emoções, decisões, prompts |
| `main.py` | Terminal + orquestrador (o "carteiro" entre os agentes) |
| `agentes/` | Criada na 1ª execução com `bia.db` e `caio.db` (um arquivo por agente) |

Não há nada para instalar: Python 3.8+ e o `llama-server` (llama.cpp) com um modelo instruct de 3B em GGUF
(por exemplo, um Qwen2.5-3B-Instruct ou Llama-3.2-3B-Instruct em Q4_K_M).

## Como rodar

```bash
# terminal 1: servidor do modelo (-np = nº de slots; -t = núcleos FÍSICOS da CPU)
llama-server -m SEU-MODELO-3B-Q4_K_M.gguf -c 4096 -np 2 -t 8 --port 8080

# terminal 2, dentro desta pasta
python main.py --slots 2
```

`--slots` deve ser igual ao `-np`. Cada agente usa sempre o mesmo slot, então o cache da
personalidade dele não é perdido. Com `-c 4096` e `-np 2`, cada slot tem ~2048 tokens de contexto.

Enquanto o programa roda, o SQLite cria arquivos auxiliares `-wal` e `-shm` ao lado de cada `.db`.
Isso é normal; eles somem quando você sai com `/sair`.

## Comandos

| Comando | O que faz |
|---|---|
| (texto normal) | Conversa com o agente atual |
| `/falar <nome>` | Troca de agente |
| `/lembrar <texto>` | Ensina um fato (pergunta a sensibilidade e se pode ser contado a outros agentes) |
| `/memorias` | Lista o que o agente sabe (com origem e versão falsa) |
| `/falsa <id> <texto>` | Escreve à mão a versão falsa de uma memória |
| `/estado` | Traços, culpa, frustração e relações |
| `/tracos <traço> <0 a 1>` | Muda um traço na hora |
| `/novo <nome>` | Cria um agente novo (um `.db` novo) |
| `/conversar <A> <B> <tópico>` | Faz A e B conversarem entre si |
| `/turnos <N>` | Nº de falas da conversa entre agentes (padrão 4) |

## Teste rápido

```
/falar caio
/lembrar O cofre da empresa fica atrás do quadro da sala 3     (sensibilidade 0.9, compartilhável: s)
/conversar bia caio cofre da empresa
/estado
```

As linhas que começam com `.` mostram as decisões do código (`REVELAR`, `ESCONDER`, `MENTIR`, tática
`PEDIR`/`AMEACAR` e as chances). Para ver ameaças, inverta: dê o segredo à Bia e rode
`/conversar caio bia cofre`. O Caio é agressivo e vai ficando frustrado a cada recusa.

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

Os pesos estão em `decidir()` e `escolher_tatica()` (em `agente.py`), e as meias-vidas em `MEIA_VIDA`.
Regras que valem a pena conhecer:

- **Isolamento por construção:** nas conversas com outro agente só entram memórias `compartilhavel = 1`.
  O que é privado nem é lido do banco.
- **Mentir:** o prompt recebe só a `versao_falsa`; a verdade não entra nele.
- **Postura persistente:** quem decidiu esconder ou mentir mantém a decisão até o placar mudar
  em 0.5 ou mais (ameaça, culpa, confiança). Sem isso, qualquer um acabaria contando por sorteio.
- **Quem já conseguiu a informação** para de pressionar.

## Limitações conhecidas (versão simples)

- O fluxo foi testado com um servidor falso no lugar do llama-server, então o comportamento do seu
  modelo de 3B (seguir instruções, gerar boas versões falsas) ainda precisa ser visto na prática.
  Alguns modelos se recusam a escrever ameaças; se for o caso, suavize o texto em `instrucao_tatica()`
  ou escolha outro modelo.
- Busca de memória por palavras em comum (sem embeddings): a pergunta precisa usar palavras parecidas
  com as do fato.
- Histórico, posturas e "quem já contou o quê" vivem só na RAM (somem ao fechar). Fatos, emoções e
  relações ficam no `.db`.
- Contradições não são detectadas (se um agente mente e depois confessa, o outro fica com as duas versões).
- Salvar fatos é sempre explícito (`/lembrar`); o agente não extrai fatos sozinho da conversa.

## Próximos passos (fora do "simples")

1. FTS5 ou embeddings pequenos (`fastembed`, `sqlite-vec`) na busca de memória.
2. Mais táticas (barganhar, blefar) e "alavanca" para ameaças: coluna `sobre` em `memorias`.
3. Detectar contradições e subir a desconfiança.
4. Extração automática de fatos com saída estruturada (JSON schema / GBNF) e resumo do histórico.
5. Salvar/restaurar o cache do slot em disco por agente (`--slot-save-path`) para ter mais agentes que slots.
6. Medir com `llama-bench` (Q4_K_M vs Q4_0, threads, contexto) e testar decodificação especulativa.
