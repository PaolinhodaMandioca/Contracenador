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
| `llm.py` | Cliente do llama-server (só biblioteca padrão): fala, gera cena e embeddings |
| `Ator.py` | Cérebro do Ator: banco, memória, crenças, emoções, decisões, prompts |
| `mundo.py` | O WorldState: locais, personagens, eventos e evidências (a verdade objetiva da cena) |
| `nomes.py` | Banco de nomes prontos; o código sorteia, o Roteirista só usa (não inventa nomes) |
| `roteirista.py` | O Roteirista: gera a cena e os 5 personagens via LLM (JSON estruturado) e materializa os `.db` |
| `main.py` | Terminal + orquestrador; sobe/derruba os servidores llama-server automaticamente |
| `atores/` | Criada na 1ª execução, um `.db` por ator (gerado pelo Roteirista ou por `/novo`) |
| `tests/` | Testes automatizados e a simulação em lote (ver seção "Testes" abaixo) |
| `cenario/` | Guarda `cena.json` (roteiro) e `mundo.db` (WorldState) da cena atual |

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
| `/cena [rodadas]` ou `/sala` | Encena a investigação do início ao fim, sem pausa: o investigador decide sozinho quem interrogar a cada rodada, até descobrir a verdade ou esgotar as rodadas (padrão 15) |
| `/roteiro [tema]` | Gera uma nova cena com 5 Atores via LLM (troca para o modelo do Roteirista e volta) |
| `/atores` | Lista os Atores carregados |
| `/falar <nome>` | Troca o Ator com quem você conversa |
| `/lembrar <texto>` | Ensina um fato ao Ator atual (pergunta sensibilidade e se pode ser contado a outros) |
| `/memorias` | Lista o que o Ator atual sabe (com origem e versão falsa) |
| `/falsa <id> <texto>` | Escreve à mão a versão falsa de uma memória |
| `/sobre <id> <nome>` | Diz de quem é o assunto de uma memória (sem nome remove) |
| `/estado` | Traços, emoções e relações do Ator atual |
| `/painel [outro]` | Painel de debug do Ator atual (emoções, traços, probabilidades, crenças/hipóteses) |
| `/debug` | Liga/desliga o painel automático após cada resposta |
| `/tracos <traço> <0 a 1>` | Muda um traço do Ator atual na hora |
| `/novo <nome>` | Cria um Ator novo avulso (um `.db` novo) |
| `/conversar <A> <B> <tópico>` | Faz A e B conversarem entre si sobre o tópico (nomes com espaço são aceitos, ex.: `Ana Carvalho`) |
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

## WorldState e crenças

Desde a Fase 1 do roadmap, a verdade de cada cena vive em `cenario/mundo.db` (módulo `mundo.py`):
locais, personagens, **eventos** (o que de fato aconteceu) e **evidências** (o que cada testemunha
forneceu, e sobre quem). É separado da memória de cada Ator — verdade ≠ conhecimento ≠ crença
(roadmap, seção 10).

Cada Ator também tem uma tabela de **crenças** (`Ator.py`): uma proposição com um nível de
confiança que evidências vão ajustando (`atualizar_crenca`). É o mesmo mecanismo tanto para crença
social quanto para hipótese de investigação — o investigador de `/cena` forma e reforça a hipótese
`"<suspeito> é o culpado"` a partir das evidências ligadas por quem ele interroga, e pode vencer por
**dedução** (confiança ≥ 75%) mesmo sem uma confissão. Veja com `/painel` (seção "HIPÓTESES").

## Nomes dos personagens vêm de um banco, não da criatividade do LLM

Escolher um nome não exige entendimento de contexto - é o tipo de decisão que cabe ao código.
`nomes.py` sorteia 5 nomes únicos ("Primeiro Sobrenome", sem repetir nem o primeiro nome nem o
sobrenome entre si) e o Roteirista recebe a ordem de **usar exatamente esses nomes** ao escrever
a trama - ele só decide personalidade, papel e quem viu o quê, os nomes já vêm prontos. Isso evita
nomes malformados, incompletos ou repetidos, e ainda economiza tokens do LLM com algo que não
precisa de criatividade nenhuma. Se o modelo ignorar algum nome sorteado, `/roteiro` avisa mas
segue em frente com o que ele escreveu (nunca trava a geração por isso).

## Quem interrogar é sempre decisão do investigador

Em `/cena`, ninguém escolhe por ele - nem o usuário (não há menu), nem um sorteio puro. A cada
rodada, o código monta a lista de suspeitos com o quanto já se suspeita de cada um (crença já
formada, desconfiança, quem já foi pressionado) e pede ao próprio LLM do investigador (o mesmo
7B usado para falar) que escolha um nome dessa lista (`Ator.escolher_investigado`). O código
sempre valida a resposta antes de agir: se o LLM não citar claramente um nome válido, quem
decide é o código, pela pontuação. O LLM nunca pode travar o jogo nem inventar um alvo
inexistente - a mesma filosofia de "LLM propõe, código decide" usada no resto do projeto.

## Objetivos e ações

Além de traços fixos, cada Ator pode ter **objetivos** estruturados (prioridade, progresso, risco,
status) - não é só uma memória de texto. O Roteirista já dá ao investigador o objetivo dele e ao
culpado o objetivo "Não ser descoberto".

A antiga decisão REVELAR/ESCONDER/MENTIR (`escolher_acao()` em `Ator.py`) ganhou uma quarta opção,
**DESVIAR**: quando um objetivo ativo de alta prioridade justifica o risco (e a personalidade
combina - dissimulação alta, empatia baixa), o código pode fazer o Ator insinuar que um terceiro
está envolvido, em vez de só mentir ou se esquivar. A acusação vira uma crença fraca em quem ouve
(inclusive no próprio investigador) - útil para incriminar um inocente, e visível em `/painel`.

## Contradições

Cada fato que um Ator recebe de outro carrega o id da memória ORIGINAL de quem contou
(`origem_id`). Se a mesma origem contar algo DIFERENTE sobre o mesmo `origem_id` depois (ex.:
mentiu e mais tarde, sob pressão, confessou), quem ouviu as duas versões percebe a contradição:
perde confiança nela, a desconfiança sobe, e as duas memórias ficam marcadas — visível em
`/painel` (seção "CONTRADIÇÕES PEGAS").

## Memória semântica

`recordar()` busca por palavra em comum (rápido, mas não pega paráfrase). `recordar_semantico()`
usa o embedding do próprio LLM já carregado (`LLM.embedding` em `llm.py`, endpoint
`/v1/embeddings` ou `/embedding` do llama-server) para achar memórias parecidas por
**significado** - "perto do cofre" pode casar com "saindo do escritório" mesmo sem palavra
igual. O embedding de cada memória é calculado uma única vez e fica salvo no `.db`; buscas
seguintes só gastam uma chamada ao LLM (a da consulta).

Isso exige o servidor iniciado com `--embeddings` (o `main.py` já liga isso sozinho no servidor
dos Atores). Se o servidor não suportar - build antiga, ou rodando sem a flag -, o Ator detecta
o erro na primeira tentativa e volta a usar `recordar()` pelo resto da sessão, sem travar nada.

**Honestidade sobre a qualidade**: testado ao vivo contra um llama-server real, o embedding de um
modelo de chat comum (não treinado com objetivo de embedding) é um sinal **ruidoso** - às vezes
rankeia uma memória aleatória acima da que realmente importa. Por isso `_recordar_melhor()` (usada
internamente por `responder()`/`falar_com_usuario`) **combina** as duas buscas em vez de a
semântica substituir a lexical: o que a busca por palavra já encontra sempre vem primeiro,
garantido; a busca semântica só ACRESCENTA candidatos que a lexical não acharia (paráfrases sem
nenhuma palavra em comum), mesmo que o ranking dela sozinha não seja confiável.

## Limitações conhecidas

- Memória semântica (embeddings) só funciona se o llama-server tiver sido iniciado com
  `--embeddings` e a build suportar o endpoint; sem isso, a busca cai para palavra em comum
  (a pergunta precisa usar palavras parecidas com as do fato).
- Histórico, posturas e "quem já contou o quê" vivem só na RAM (somem ao fechar). Fatos, emoções,
  relações e crenças ficam no `.db`.
- Contradição só é detectada quando é a MESMA origem mudando de versão sobre o MESMO fato (ex.:
  mentiu e depois confessou) - a confiança nela cai e a memória fica marcada (ver `/painel`). Duas
  testemunhas diferentes discordando uma da outra sobre o mesmo assunto não é pego ainda: exigiria
  comparar texto livre semanticamente (memória semântica, ainda não implementada).
- Só existe 1 local por cenário ainda: a percepção automática por local (em `mundo.py`) só é usada
  para eventos durante a cena, não para decidir quem viu o crime inicial (isso continua vindo do
  campo `viu` gerado pelo Roteirista).
- Salvar fatos é sempre explícito (`/lembrar` ou gerado pelo Roteirista); o Ator não extrai fatos
  sozinho da conversa.

## Testes

```bash
python -m unittest discover -s tests -v
```

Testes determinísticos (`tests/test_ator.py`, `tests/test_mundo.py`, `tests/test_roteirista.py`,
`tests/test_main.py`, `tests/test_nomes.py`, `tests/test_memoria_semantica.py`): memória e
isolamento, mentira não vaza a verdade, fofoca relatada em terceira pessoa (não primeira),
DESVIAR exige objetivo + candidato, contradição, crenças, objetivos, decaimento de emoção,
escolha autônoma de quem interrogar, WorldState, materialização de cena, reconhecimento de nomes
com espaço em `/conversar` e memória semântica (similaridade de cosseno, cache de embedding,
fallback para busca lexical).
Nenhum chama um LLM de verdade (usam um `FakeLLM` em `tests/apoio.py`) - o que é testado é a
lógica em CÓDIGO, não a qualidade do texto gerado.

```bash
python tests/simular_lote.py --n 500
```

Roda muitas cenas de investigação "de cabeça" (sem terminal, sem LLM) e imprime estatísticas
agregadas (taxa de vitória por confissão/dedução, rodadas médias, mentiras, contradições) - serve
para calibrar os pesos de `escolher_acao()`/`escolher_tatica()` por número em vez de só no olho.
Não é um teste automatizado (não entra no `unittest discover`): é uma ferramenta de calibração, e
reimplementa uma versão simplificada e não-interativa do loop de `/cena` (ver aviso no topo do
arquivo). Com os pesos padrão atuais, por exemplo, contradições praticamente não ocorrem - o
culpado raramente muda de postura (mentir → confessar) sob a pressão simulada, o que é um
candidato a ajuste de pesos futuro, não um bug.

## Roadmap

O plano de evolução do projeto (WorldState, crenças vs. verdade, evidências, objetivos, Diretor,
Cenógrafo etc.) está em [`Contracenador_Roadmap.md`](Contracenador_Roadmap.md).
