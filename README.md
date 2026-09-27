# Contracenador

Simulação de personagens de IA que interagem entre si com personalidade, memória, emoções e
relações. Cada Ator é **um arquivo `.db`** (SQLite) isolado. Um **Roteirista** (LLM maior) cria a
cena e os personagens; os **Atores** (LLM menor) interpretam. Quem decide se um Ator revela,
esconde, mente ou ameaça é o **código** (não o modelo); o LLM só escreve a fala.

Veja a visão completa e o roadmap do projeto em
[`docs/Contracenador_Roadmap.md`](docs/Contracenador_Roadmap.md).

## Arquivos

| Arquivo | Papel |
|---|---|
| `contracenador/agents/agent.py` | API `Actor`, personalidade verbal e memória do agente |
| `contracenador/agents/personality.py` | Nomes e perfis determinísticos de personalidade |
| `contracenador/agents/beliefs.py`, `emotions.py`, `relationships.py`, `actions.py` | Crenças, estado emocional, relações e políticas de decisão |
| `contracenador/memory/sqlite.py` / `embeddings.py` | Conexões de memória e operações vetoriais |
| `contracenador/world/world.py` | Esquema e abertura do WorldState |
| `contracenador/world/locations.py`, `events.py`, `evidence.py` | Locais, eventos e evidências do mundo |
| `contracenador/simulation/engine.py`, `scheduler.py`, `turn.py` | Execução de cena, seleção de turnos e troca privada |
| `contracenador/llm/client.py` / `model_manager.py` | Cliente e ciclo de vida dos servidores llama.cpp |
| `contracenador/scenarios/generator.py`, `validator.py`, `loader.py` | Geração, validação e materialização de cenas |
| `contracenador/cli/main.py` | CLI e orquestração do jogo |
| `main.py` | Entrada compatível: `python main.py` |
| `screenwriter.py` | Entrada compatível para o gerador de cenas |
| `atores/` | Criada na 1ª execução, um `.db` por ator (gerado pelo Roteirista ou por `/novo`) |
| `tests/` | Testes automatizados e a simulação em lote (ver seção "Testes" abaixo) |
| `cenario/` | Guarda `cena.json` (roteiro) e `world.db` (WorldState) da cena atual |

`TurnScheduler` em `contracenador/simulation/scheduler.py` separa execução da política de escolha:
ela recebe os agentes, o número do turno e o histórico, e devolve um par ou `None` para encerrar.
As regras de investigação continuam na CLI; outros tipos de cena podem fornecer políticas próprias.
Os comportamentos de crença, emoção, relação e ação ainda compartilham o estado do `Actor` em
`agents/agent.py`; os métodos públicos delegam a implementação aos módulos de domínio acima.

Não há dependências Python de runtime além da biblioteca padrão; é necessário Python 3.8+ e o
`llama-server` (llama.cpp). `main.py` sobe os
servidores sozinho — não é preciso rodar `llama-server` manualmente.

Todo o código (módulos, classes, funções, variáveis, colunas de banco) é em inglês; só os
comentários, docstrings, mensagens de terminal e o que é dito ao/pelo LLM ficam em português,
já que o próprio jogo (diálogos, prompts) é em português.

## Modelos

- **Atores**: `Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M` (padrão) — respostas rápidas, uma inferência
  por fala.
- **Roteirista**: `Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M` (padrão) — inicia primeiro em cada save novo
  e também é carregado temporariamente por `/roteiro` para gerar a cena e os personagens.

Os modelos padrão são baixados automaticamente do Hugging Face na primeira execução (flag `-hf`
do `llama-server`). Para usar um `.gguf` local, passe o caminho do arquivo em vez do repositório.

Como a troca de modelos consome RAM/VRAM, `main.py` **derruba o servidor dos Atores antes de subir
o do Roteirista** e o traz de volta assim que a cena é gerada — tudo automático, sem intervenção
manual.

## Como rodar

```bash
python main.py
```

Antes de iniciar o jogo, a aplicação verifica Python, `llama-server`, modelos, portas e
permissões das pastas. Se algum requisito falhar, a mensagem lista o problema e como agir;
essa etapa ocorre antes de apagar o save anterior. Para rodar apenas as verificações, sem
iniciar modelos, baixar arquivos ou alterar saves:

```bash
python main.py --verificar-ambiente
```

A primeira cena será solicitada durante a inicialização. Flags úteis:
Toda execução normal de `main.py` começa um save novo: limpa o conteúdo de `atores/` e `cenario/`,
pergunta o tema, inicia primeiro o modelo de 14B para gerar a cena e depois carrega o modelo de
7B para continuar o jogo. Os dados do save anterior são apagados permanentemente. Flags úteis:

```bash
python main.py --modelo-atores Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M \
                --modelo-roteirista Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M \
                --slots 2 --pasta atores --cenario cenario
```

Por padrão, o llama-server recebe `--fit on` para ajustar automaticamente o offload às GPUs e à
VRAM disponível, usando o máximo que couber. O backend depende de como o `llama-server` foi
compilado. Se quiser fixar manualmente o limite de camadas, use as flags abaixo; por exemplo,
`--camadas-gpu-atores 25 --camadas-gpu-roteirista 10`.

| Flag | Efeito |
|---|---|
| `--modelo-atores` / `--modelo-roteirista` | Repositório HF (`Org/Repo:arquivo.gguf`) ou caminho local `.gguf` |
| `--camadas-gpu-atores` / `--camadas-gpu-roteirista` | Limite manual de camadas de cada modelo na GPU (padrão: ajuste automático; use 0 para CPU) |
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
| `/cena [rodadas]` ou `/sala` | Investigação privada: interroga um suspeito e pode intercalar uma conversa do culpado com uma testemunha para plantar suspeita; até 10 trocas por linha ativa |
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
| `/tracos <traço> <0 a 1>` | Muda um traço do Ator atual na hora (nomes em inglês: honesty, deceit, empathy, courage, aggressiveness, greed) |
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

As linhas que começam com `.` mostram as decisões do código (`REVEAL`, `HIDE`, `LIE`, `DEFLECT`,
tática `ASK`/`THREATEN` e as chances). Para uma conversa livre entre dois Atores específicos, use
`/conversar <A> <B> <tópico>`.

## Como as decisões funcionam

| Parâmetro | Onde fica | Efeito |
|---|---|---|
| honesty, deceit | traços | Chance de revelar e de mentir em vez de só esconder |
| empathy | traço | Mentir gera mais culpa; ameaçar fica menos provável |
| courage | traço | Reduz o efeito do medo causado por ameaças |
| aggressiveness, greed | traços | Disposição para ameaçar |
| guilt | estado | Sobe ao mentir; empurra para confessar; esmaece com o tempo |
| frustration | estado | Sobe quando o outro não conta; amplifica a disposição de ameaçar |
| trust, fear, distrust, favor_owed | relação | Empurram a decisão de revelar a quem pergunta |
| sensitivity | fato | Quanto mais sensível, mais difícil de revelar |

Os pesos estão em `choose_action()` e `choose_tactic()` (em `contracenador/agents/agent.py`), e as meias-vidas em
`HALF_LIFE`. Regras que valem a pena conhecer:

- **Isolamento por construção:** nas conversas com outro Ator só entram memórias
  `shareable = 1`. O que é privado nem é lido do banco.
- **Mentir:** o prompt recebe só a `false_version`; a verdade não entra nele.
- **Postura persistente:** quem decidiu esconder ou mentir mantém a decisão até o placar mudar em
  0.5 ou mais (ameaça, culpa, confiança). Sem isso, qualquer um acabaria contando por sorteio.
- **Quem já conseguiu a informação** para de pressionar.

## WorldState e crenças

Desde a Fase 1 do roadmap, a verdade de cada cena vive em `cenario/world.db` (pacote `contracenador/world/`):
locais, personagens, **eventos** (o que de fato aconteceu) e **evidências** (o que cada testemunha
forneceu, e sobre quem). É separado da memória de cada Ator — verdade ≠ conhecimento ≠ crença
(roadmap, seção 10).

Cada Ator também tem uma tabela de **crenças** (`contracenador/agents/agent.py`): uma proposição com um nível de
confiança que evidências vão ajustando (`update_belief`). É o mesmo mecanismo tanto para crença
social quanto para hipótese de investigação — o investigador de `/cena` forma e reforça a hipótese
`"<suspeito> é o culpado"` a partir das evidências ligadas por quem ele interroga, e pode vencer por
**dedução** (confiança ≥ 75%) mesmo sem uma confissão. Veja com `/painel` (seção "HIPÓTESES").

## Nomes dos personagens vêm de um banco, não da criatividade do LLM

Escolher um nome não exige entendimento de contexto - é o tipo de decisão que cabe ao código.
`contracenador/agents/personality.py` sorteia 5 nomes únicos ("Primeiro Sobrenome", sem repetir nem o primeiro nome nem o
sobrenome entre si) e o Roteirista recebe a ordem de **usar exatamente esses nomes** ao escrever
a trama - ele só decide personalidade, papel e quem viu o quê, os nomes já vêm prontos. Isso evita
nomes malformados, incompletos ou repetidos, e ainda economiza tokens do LLM com algo que não
precisa de criatividade nenhuma. Se o modelo ignorar algum nome sorteado, `/roteiro` avisa mas
segue em frente com o que ele escreveu (nunca trava a geração por isso).

## Quem interrogar é sempre decisão do investigador

Em `/cena`, ninguém escolhe por ele - nem o usuário (não há menu), nem um sorteio puro. A cada
rodada, o código monta a lista de suspeitos com o quanto já se suspeita de cada um (crença já
formada, desconfiança, quem já foi pressionado) e pede ao próprio LLM do investigador (o mesmo
7B usado para falar) que escolha um nome dessa lista (`Actor.choose_investigation_target`). O
código sempre valida a resposta antes de agir: se o LLM não citar claramente um nome válido,
quem decide é o código, pela pontuação. O LLM nunca pode travar o jogo nem inventar um alvo
inexistente - a mesma filosofia de "LLM propõe, código decide" usada no resto do projeto.

## Objetivos e ações

Além de traços fixos, cada Ator pode ter **objetivos** estruturados (prioridade, progresso, risco,
status) - não é só uma memória de texto. O Roteirista já dá ao investigador o objetivo dele e ao
culpado o objetivo "Não ser descoberto".

A antiga decisão REVEAL/HIDE/LIE (`choose_action()` em `contracenador/agents/agent.py`) ganhou uma quarta opção,
**DEFLECT**: quando um objetivo ativo de alta prioridade justifica o risco (e a personalidade
combina - deceit alto, empathy baixo), o código pode fazer o Ator insinuar que um terceiro
está envolvido, em vez de só mentir ou se esquivar. A acusação vira uma crença fraca em quem ouve
(inclusive no próprio investigador) - útil para incriminar um inocente, e visível em `/painel`.

## Contradições

Cada fato que um Ator recebe de outro carrega o id da memória ORIGINAL de quem contou
(`source_id`). Se a mesma origem contar algo DIFERENTE sobre o mesmo `source_id` depois (ex.:
mentiu e mais tarde, sob pressão, confessou), quem ouviu as duas versões percebe a contradição:
perde confiança nela, a desconfiança sobe, e as duas memórias ficam marcadas — visível em
`/painel` (seção "CONTRADIÇÕES PEGAS").

## Memória semântica

`recall()` busca por palavra em comum (rápido, mas não pega paráfrase). `recall_semantic()` usa o
embedding do próprio LLM já carregado (`LLM.embedding` em `contracenador/llm/client.py`, endpoint `/v1/embeddings` ou
`/embedding` do llama-server) para achar memórias parecidas por **significado** - "perto do
cofre" pode casar com "saindo do escritório" mesmo sem palavra igual. O embedding de cada memória
é calculado uma única vez e fica salvo no `.db`; buscas seguintes só gastam uma chamada ao LLM (a
da consulta).

Isso exige o servidor iniciado com `--embeddings` (o `main.py` já liga isso sozinho no servidor
dos Atores). Se o servidor não suportar - build antiga, ou rodando sem a flag -, o Ator detecta
o erro na primeira tentativa e volta a usar `recall()` pelo resto da sessão, sem travar nada.

**Honestidade sobre a qualidade**: testado ao vivo contra um llama-server real, o embedding de um
modelo de chat comum (não treinado com objetivo de embedding) é um sinal **ruidoso** - às vezes
rankeia uma memória aleatória acima da que realmente importa. Por isso `_recall_best()` (usada
internamente por `respond()`/`speak_with_user`) **combina** as duas buscas em vez de a semântica
substituir a lexical: o que a busca por palavra já encontra sempre vem primeiro, garantido; a
busca semântica só ACRESCENTA candidatos que a lexical não acharia (paráfrases sem nenhuma
palavra em comum), mesmo que o ranking dela sozinha não seja confiável.

## Limitações conhecidas

- Memória semântica (embeddings) só funciona se o llama-server tiver sido iniciado com
  `--embeddings` e a build suportar o endpoint; sem isso, a busca cai para palavra em comum
  (a pergunta precisa usar palavras parecidas com as do fato).
- Histórico, posturas e "quem já contou o quê" vivem só na RAM (somem ao fechar). Fatos, emoções,
  relações e crenças ficam no `.db`.
- Contradição só é detectada quando é a MESMA origem mudando de versão sobre o MESMO fato (ex.:
  mentiu e depois confessou) - a confiança nela cai e a memória fica marcada (ver `/painel`). Duas
  testemunhas diferentes discordando uma da outra sobre o mesmo assunto não é pego ainda: exigiria
  comparar texto livre semanticamente.
- Só existe 1 local por cenário ainda: a percepção automática por local (em `contracenador/world/`) só é usada
  para eventos durante a cena, não para decidir quem viu o crime inicial (isso continua vindo do
  campo `saw` gerado pelo Roteirista).
- Salvar fatos é sempre explícito (`/lembrar` ou gerado pelo Roteirista); o Ator não extrai fatos
  sozinho da conversa.

## Testes

```bash
python -m unittest discover -s tests -v
```

Testes determinísticos (`tests/test_actor.py`, `tests/test_world.py`, `tests/test_screenwriter.py`,
`tests/test_main.py`, `tests/test_names.py`, `tests/test_semantic_memory.py`): memória e
isolamento, mentira não vaza a verdade, fofoca relatada em terceira pessoa (não primeira),
DEFLECT exige objetivo + candidato, contradição, crenças, objetivos, decaimento de emoção,
escolha autônoma de quem interrogar, WorldState, materialização de cena, reconhecimento de nomes
com espaço em `/conversar` e memória semântica (similaridade de cosseno, cache de embedding,
fallback para busca lexical).
Nenhum chama um LLM de verdade (usam um `FakeLLM` em `tests/support.py`) - o que é testado é a
lógica em CÓDIGO, não a qualidade do texto gerado.

```bash
python tests/batch_simulation.py --n 500
```

Roda muitas cenas de investigação "de cabeça" (sem terminal, sem LLM) e imprime estatísticas
agregadas (taxa de vitória por confissão/dedução, rodadas médias, mentiras, contradições) - serve
para calibrar os pesos de `choose_action()`/`choose_tactic()` por número em vez de só no olho.
Não é um teste automatizado (não entra no `unittest discover`): é uma ferramenta de calibração.
Ela reutiliza o scheduler e o diálogo privado, mas simplifica a política de seleção e as condições
de vitória de `/cena` (ver aviso no topo do arquivo). Com os pesos padrão atuais, por exemplo, contradições praticamente não ocorrem - o
culpado raramente muda de postura (mentir → confessar) sob a pressão simulada, o que é um
candidato a ajuste de pesos futuro, não um bug.

## Roadmap

O plano de evolução do projeto (WorldState, crenças vs. verdade, evidências, objetivos, Diretor,
Cenógrafo etc.) está em [`docs/Contracenador_Roadmap.md`](docs/Contracenador_Roadmap.md).
