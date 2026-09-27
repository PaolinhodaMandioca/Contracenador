# Contracenador — Visão e Roadmap do Projeto

## 1. Ideia central

O **Contracenador** é um sistema de simulação de personagens controlados por IA.

A ideia não é simplesmente colocar vários LLMs para conversar.

O objetivo é criar um **mundo simulado**, no qual personagens possuem:

- personalidade;
- memória;
- emoções;
- relações;
- objetivos;
- crenças;
- conhecimento parcial;
- percepção limitada;
- capacidade de mentir;
- capacidade de tomar decisões;
- capacidade de agir sobre o mundo.

O LLM deve cuidar principalmente da **interpretação e expressão**, enquanto o código controla o estado real do mundo e dos personagens.

A inspiração conceitual vem do **teatro e do cinema**:

> O Cenógrafo cria o mundo.  
> O Roteirista cria as pessoas e suas histórias.  
> O Diretor coordena o que acontece.  
> Os Atores vivem os personagens.  
> O Contracenador coloca os Atores em cena para interagir.

---

# 2. Os papéis do Contracenador

## 2.1 Roteirista — cria as personalidades

**IA responsável por criar os personagens.**

O Roteirista não deve apenas gerar nome, idade e uma descrição.

Ele deve criar a base psicológica e narrativa do personagem.

### Responsabilidades

- criar personalidade;
- criar passado;
- criar objetivos;
- criar medos;
- criar desejos;
- criar valores;
- criar segredos;
- criar relações iniciais;
- definir características comportamentais;
- definir conhecimentos iniciais;
- criar possíveis conflitos;
- definir limites morais;
- definir maneira de falar.

### Exemplo

```text
Personagem: João

Personalidade:
- cauteloso
- desconfiado
- manipulador
- pouco empático
- inteligente
- evita confrontos físicos

Objetivo:
- esconder seu envolvimento no roubo

Segredo:
- ele realmente roubou o dinheiro

Medo:
- ser descoberto por Maria

Desejo:
- fugir da cidade

Relação:
- confia parcialmente em Carlos
- teme Maria
- despreza Ana
```

O Roteirista cria a **ficha psicológica**, mas não deve controlar diretamente as ações durante a simulação.

---

# 3. Ator — vive o personagem

## 3.1 IA que atua como Personagem

O **Ator** é a IA responsável por interpretar um personagem.

Ele recebe:

- personalidade;
- memória;
- emoções;
- crenças;
- objetivos;
- relações;
- percepção atual;
- situação;
- informações conhecidas pelo personagem.

O Ator não deve conhecer automaticamente tudo que existe no mundo.

### Regra fundamental

> O Ator sabe somente aquilo que o personagem sabe.

Isso permite criar:

- segredos;
- mal-entendidos;
- mentiras;
- suspeitas;
- investigações;
- conspirações;
- informações incompletas.

### O Ator possui

```text
Personalidade
Memória
Crenças
Emoções
Objetivos
Relações
Conhecimento
Percepção
Estado atual
```

---

# 4. Contracenador — coloca os Atores em cena

## 4.1 Comando que faz os atores conversar

O **Contracenador** é o mecanismo que coloca dois ou mais Atores para interagir.

Ele não é um personagem.

Ele é o equivalente ao mecanismo que organiza a **contracena**.

Exemplo:

```text
contracenador

João → Maria
Maria → João
João → Maria
Maria → João
```

Mas o Contracenador deve evoluir para controlar muito mais do que apenas turnos de fala.

### Responsabilidades

- iniciar uma cena;
- escolher quem participa;
- determinar ordem de interação;
- entregar aos Atores somente as informações disponíveis;
- registrar falas;
- registrar ações;
- atualizar o estado da cena;
- solicitar novas decisões;
- encerrar a cena;
- enviar acontecimentos relevantes ao Diretor.

### Futuramente

O Contracenador deve conseguir trabalhar com:

```text
2 atores
5 atores
20 atores
100 atores
```

sem precisar transformar todos os personagens em LLMs ativos ao mesmo tempo.

---

# 5. Diretor — coordena o mundo

## 5.1 IA que coordena o mundo

O **Diretor** é responsável pela coordenação da simulação.

Ele não interpreta um personagem.

Ele observa a situação geral e decide **o que deve acontecer em seguida dentro da narrativa**, respeitando as regras do mundo.

### Responsabilidades

- coordenar eventos;
- iniciar acontecimentos;
- controlar ritmo da narrativa;
- determinar quando uma cena termina;
- provocar acontecimentos;
- controlar NPCs;
- coordenar mudanças de contexto;
- decidir quando uma informação deve se tornar relevante;
- supervisionar objetivos narrativos;
- coordenar transições entre cenas;
- impedir que a história fique parada.

### Importante

O Diretor não deve simplesmente escrever a história sozinho.

Ele deve trabalhar com o estado real da simulação.

```text
Mundo
  ↓
Diretor
  ↓
evento
  ↓
Contracenador
  ↓
Atores
  ↓
ações
  ↓
Mundo atualizado
```

---

# 6. Cenógrafo — cria o mundo

## 6.1 IA que cria o mundo

O **Cenógrafo** é responsável por criar a estrutura do mundo.

Ele é inspirado no papel do cenógrafo de teatro/cinema: definir o ambiente onde a história acontece.

### Responsabilidades

- criar locais;
- criar objetos;
- criar ambientes;
- criar cidades;
- criar prédios;
- criar salas;
- criar elementos do cenário;
- definir conexões entre locais;
- criar recursos;
- definir características físicas;
- criar condições ambientais;
- definir elementos importantes para a narrativa.

### Exemplo

```text
Casa
├── sala
├── cozinha
├── corredor
├── quarto
└── escritório

Objetos:
├── cofre
├── faca
├── telefone
└── chave

Conexões:
sala → corredor
corredor → cozinha
corredor → escritório
```

O Cenógrafo também pode definir:

```text
quem tem acesso a cada local
quem possui cada objeto
quais objetos podem ser utilizados
quais locais são públicos
quais locais são privados
```

---

# 7. Novo componente recomendado: Produtor

## 7.1 Por que existe?

No teatro e no cinema existe uma função que não é necessariamente artística: organizar produção, recursos e execução.

Para o Contracenador, vale criar um **Produtor** como componente técnico.

Ele não precisa ser uma IA.

Pode ser código.

### Responsabilidades

- carregar modelos;
- iniciar servidores;
- controlar recursos;
- controlar quantidade de agentes ativos;
- controlar memória;
- gerenciar filas de inferência;
- salvar estados;
- carregar checkpoints;
- controlar execução;
- pausar e continuar simulações.

Arquitetura:

```text
Produtor
   │
   ├── LLM
   ├── Banco de dados
   ├── Memória
   ├── Scheduler
   └── Simulação
```

---

# 8. Novo componente recomendado: Montador

## 8.1 Montador de cena

No cinema, existe uma etapa de montagem/edição.

No Contracenador, um **Montador** pode organizar o histórico da simulação.

Ele transforma:

```text
eventos
falas
ações
mudanças de estado
memórias
```

em:

```text
cena
capítulo
episódio
linha do tempo
resumo
```

### Exemplo

```text
CENA 12

21:30 — Maria entra na cozinha.
21:31 — João esconde a chave.
21:32 — Maria percebe que algo está errado.
21:33 — João mente.
21:35 — Carlos entra.
21:36 — Maria conta a Carlos o que viu.
```

Isso pode posteriormente gerar:

- roteiro;
- resumo;
- transcrição;
- livro;
- episódio;
- registro de investigação.

---

# 9. Arquitetura geral

A arquitetura desejada é:

```text
                         CONTRACENADOR
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
   Cenógrafo              Roteirista             Diretor
       │                      │                      │
       ▼                      ▼                      ▼
     MUNDO                PERSONAGENS             EVENTOS
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              │
                              ▼
                        CONTRACENADOR
                              │
                 ┌────────────┼────────────┐
                 │            │            │
                 ▼            ▼            ▼
               Ator         Ator         Ator
                 │            │            │
                 └────────────┼────────────┘
                              │
                              ▼
                       MUNDO ATUALIZADO
                              │
                              ▼
                           Diretor
```

---

# 10. Verdade do mundo

Uma das regras mais importantes do projeto:

> O mundo possui uma verdade objetiva independente do que os personagens acreditam.

Exemplo:

```text
VERDADE DO MUNDO

João pegou a chave às 21:30.
```

Maria pode acreditar:

```text
"João pegou a chave."
```

Carlos pode acreditar:

```text
"Maria pegou a chave."
```

João pode saber:

```text
"Eu peguei a chave."
```

Ana pode não saber nada.

Portanto:

```text
VERDADE
   ≠
CONHECIMENTO
   ≠
CRENÇA
```

Essa separação deve ser central no projeto.

---

# 11. WorldState

Criar um estado central do mundo.

```text
WorldState
├── tempo
├── locais
├── objetos
├── personagens
├── eventos
├── evidências
├── condições
└── regras
```

Exemplo:

```json
{
  "tempo": "21:43",
  "local": "cozinha",
  "objetos": {
    "copo_azul": "quebrado"
  }
}
```

O WorldState representa o que realmente existe.

---

# 12. Sistema de crenças

Cada Ator deve possuir suas próprias crenças.

```text
João

"Ana confia em mim"
confiança: 0.72

"Maria suspeita de mim"
confiança: 0.81

"Carlos sabe sobre o dinheiro"
confiança: 0.33
```

As crenças podem estar erradas.

Isso permite:

- paranoia;
- engano;
- manipulação;
- investigação;
- conspiração;
- mal-entendidos.

---

# 13. Evidências

Evidências devem ser entidades próprias.

```text
Evidência #42

tipo: testemunho
origem: Maria

conteúdo:
"Vi João perto do cofre."

confiabilidade:
0.72

hora:
21:35

local:
cozinha
```

Uma hipótese pode possuir várias evidências.

```text
Hipótese:
João roubou o dinheiro.

Evidências:
#42
#51
#63
```

---

# 14. Contradições

O sistema deve detectar contradições.

Exemplo:

```text
Maria disse:

21:30
"João estava na cozinha."

Depois:

21:30
"João estava no jardim."
```

O sistema deve registrar:

```text
CONTRADIÇÃO

Origem: Maria
Assunto: localização de João
Confiança reduzida.
```

Isso pode alterar a relação entre os personagens.

---

# 15. Memória

A memória deve ser dividida.

## Memória episódica

Eventos vivenciados.

```text
"Ontem João entrou na cozinha."
```

## Memória semântica

Conhecimento.

```text
"O cofre fica no escritório."
```

## Memória social

Conhecimento sobre outras pessoas.

```text
"Maria já mentiu para mim."
```

## Memória emocional

Associações emocionais.

```text
"Quando João gritou comigo, senti medo."
```

## Memória procedural

Comportamentos aprendidos.

```text
"Quando estou ameaçado, tento fugir."
```

---

# 16. Percepção

Um personagem não deve receber diretamente todos os eventos do mundo.

O fluxo deve ser:

```text
EVENTO DO MUNDO
      ↓
PERCEPÇÃO
      ↓
INTERPRETAÇÃO
      ↓
MEMÓRIA
      ↓
CRENÇA
```

Exemplo:

```text
João pega uma chave.

Maria está a 20 metros.

Resultado:

Maria percebe:
"Alguém pegou alguma coisa."

Não:
"Maria sabe que João pegou a chave."
```

A percepção deve depender de:

- distância;
- visibilidade;
- ruído;
- atenção;
- estado emocional;
- capacidade do personagem;
- ambiente.

---

# 17. Objetivos

Cada personagem deve possuir objetivos.

```text
João

Objetivo principal:
não ser descoberto

Subobjetivos:
- criar álibi
- convencer Maria
- descobrir o que o investigador sabe
- escapar
```

Cada objetivo pode possuir:

```text
prioridade
urgência
progresso
risco
```

---

# 18. Sistema de ações

O personagem deve escolher entre ações possíveis.

```text
Situação:
Carlos está desconfiado.

Ações:

1. mentir
2. fugir
3. ameaçar
4. incriminar Maria
5. ficar calado
6. mudar de assunto
```

O código pode determinar as opções possíveis.

O Ator/LLM pode ajudar a escolher.

O sistema valida a ação antes de executá-la.

---

# 19. Regra importante: LLM não controla diretamente o mundo

O LLM deve pedir uma ação.

Exemplo:

```json
{
  "acao": "ACUSAR",
  "alvo": "Maria"
}
```

O motor verifica:

```text
A ação existe?
O personagem pode realizá-la?
Ele conhece o alvo?
A ação é possível no local?
Há algum impedimento?
```

Somente então o mundo é alterado.

Isso evita que uma alucinação do LLM quebre a simulação.

---

# 20. Sistema de relações

As relações devem possuir histórico.

Não apenas:

```text
confiança = 0.7
```

Mas:

```text
+ Maria me ajudou
+ Maria disse a verdade
- Maria mentiu
- Maria me ameaçou
+ Maria pediu desculpas
```

O valor atual pode ser calculado a partir do histórico.

Relações possíveis:

```text
confiança
amizade
ódio
medo
respeito
admiração
ciúme
rivalidade
dependência
lealdade
```

---

# 21. Sistema de fofoca e propagação de informação

Uma característica importante para um simulador social.

```text
Maria → Carlos

"João estava perto do cofre."
```

Carlos registra:

```text
origem: Maria
```

Depois:

```text
Carlos → Ana

"Maria disse que João estava perto do cofre."
```

A informação pode sofrer distorções.

```text
Maria:
"João estava perto do cofre."

↓

Carlos:
"Maria acha que João mexeu no cofre."

↓

Ana:
"Maria disse que João roubou."
```

Isso cria uma rede social dinâmica.

---

# 22. Investigação

O investigador deve possuir hipóteses.

```text
João:
0.64

Maria:
0.21

Carlos:
0.15
```

Novas evidências alteram as hipóteses.

```text
nova evidência
      ↓
atualizar hipóteses
      ↓
escolher próxima ação
```

O investigador pode:

- interrogar;
- observar;
- procurar objetos;
- procurar evidências;
- confrontar personagens;
- verificar álibis;
- seguir suspeitos.

---

# 23. Diretor como controlador narrativo

O Diretor deve observar:

```text
objetivos dos personagens
estado do mundo
ritmo da cena
conflitos
eventos pendentes
```

E criar acontecimentos quando necessário.

Exemplo:

```text
Cena parada.

Diretor:

Evento:
"Alguém bate à porta."
```

Mas o evento precisa ser compatível com o mundo.

O Diretor não deve simplesmente escrever:

> "De repente aparece uma explosão."

sem que exista uma razão ou possibilidade para isso.

---

# 24. Cenógrafo dinâmico

O Cenógrafo não precisa atuar somente no início.

Ele pode atualizar o mundo.

Exemplo:

```text
Cena:
uma porta foi quebrada.

Cenógrafo:

porta.estado = quebrada
porta.pode_ser_aberta = true
```

Ou:

```text
incêndio

sala:
temperatura ↑
fumaça ↑
visibilidade ↓
objetos danificados
```

Assim o cenário também reage aos acontecimentos.

---

# 25. Scheduler

Para suportar muitos personagens:

```text
Scheduler
```

deve decidir quem precisa executar.

Exemplo:

```text
100 personagens

Ativos:
João
Maria
Carlos

Dormindo:
97 personagens
```

Se um evento atingir Ana:

```text
Ana
 ↓
ativar
 ↓
processar reação
```

Isso reduz drasticamente o número de inferências.

---

# 26. Arquitetura de IA

Não usar um modelo grande para tudo.

### Modelo rápido

Usado para:

- respostas simples;
- decisões;
- classificação;
- pequenas reações;
- diálogos comuns.

### Modelo grande

Usado para:

- planejamento complexo;
- criação de cenários;
- criação de personagens;
- resolução de conflitos;
- eventos importantes;
- situações ambíguas.

Arquitetura:

```text
                 Evento
                   ↓
              Scheduler
                   ↓
            precisa de IA?
              /         \
            não          sim
            ↓             ↓
          código       modelo rápido
                          │
                    decisão simples
                          │
                    complexo?
                       /     \
                     não      sim
                     ↓         ↓
                   ação      modelo grande
```

---

# 27. Uso de modelos grandes com pouca RAM/VRAM

O projeto deve manter o sistema de LLM desacoplado.

A interface deve permitir futuramente:

```text
llama.cpp
Ollama
API local
modelo remoto
outro backend
```

Também pode ser estudado o uso de:

- GGUF;
- quantização;
- mmap;
- GPU offload;
- CPU offload;
- streaming de camadas;
- streaming de experts para modelos MoE;
- cache de modelos;
- cache de KV.

Isso permite experimentar modelos grandes sem tornar o Contracenador dependente de uma máquina específica.

---

# 28. Banco de dados

SQLite continua sendo uma boa escolha inicial.

Uma possível estrutura:

```text
database
├── world
├── locations
├── objects
├── agents
├── personalities
├── memories
├── beliefs
├── emotions
├── relationships
├── goals
├── actions
├── events
├── evidence
├── hypotheses
├── conversations
└── scenes
```

---

# 29. Arquitetura de diretórios sugerida

```text
Contracenador/
│
├── contracenador/
│   │
│   ├── agents/
│   │   ├── ator.py
│   │   ├── personalidade.py
│   │   ├── memoria.py
│   │   ├── crencas.py
│   │   ├── emocoes.py
│   │   ├── relacoes.py
│   │   ├── objetivos.py
│   │   └── acoes.py
│   │
│   ├── world/
│   │   ├── mundo.py
│   │   ├── eventos.py
│   │   ├── evidencias.py
│   │   ├── locais.py
│   │   └── objetos.py
│   │
│   ├── simulation/
│   │   ├── contracenador.py
│   │   ├── diretor.py
│   │   ├── scheduler.py
│   │   └── engine.py
│   │
│   ├── ia/
│   │   ├── llm.py
│   │   ├── prompts.py
│   │   └── model_manager.py
│   │
│   ├── roteirista/
│   │   └── roteirista.py
│   │
│   ├── cenografo/
│   │   └── cenografo.py
│   │
│   └── montador/
│       └── montador.py
│
├── scenarios/
├── tests/
├── docs/
├── data/
├── README.md
└── pyproject.toml
```

---

# 30. Roadmap

## Fase 1 — Fundamentos

- [ ] separar melhor Ator e mundo;
- [ ] criar `WorldState`;
- [ ] criar sistema de eventos;
- [ ] separar verdade do mundo de memória;
- [ ] melhorar estrutura do banco;
- [ ] atualizar README;
- [ ] criar testes básicos.

## Fase 2 — Inteligência dos personagens

- [ ] memória episódica;
- [ ] memória semântica;
- [ ] crenças;
- [ ] emoções;
- [ ] relações históricas;
- [ ] objetivos;
- [ ] sistema de ações;
- [ ] percepção limitada.

## Fase 3 — Mundo

- [ ] locais;
- [ ] objetos;
- [ ] propriedades físicas;
- [ ] eventos;
- [ ] mudanças ambientais;
- [ ] Cenógrafo;
- [ ] regras do mundo.

## Fase 4 — Investigação e interação

- [ ] evidências;
- [ ] contradições;
- [ ] hipóteses;
- [ ] interrogatórios;
- [ ] investigação;
- [ ] fofoca;
- [ ] propagação de informação;
- [ ] informação de segunda ordem.

## Fase 5 — Diretor

- [ ] Diretor;
- [ ] eventos narrativos;
- [ ] controle de cenas;
- [ ] ritmo;
- [ ] conflitos;
- [ ] objetivos narrativos;
- [ ] transição entre cenas.

## Fase 6 — Escala

- [ ] Scheduler;
- [ ] agentes inativos;
- [ ] processamento por evento;
- [ ] cache;
- [ ] filas de inferência;
- [ ] múltiplos modelos;
- [ ] métricas de desempenho.

## Fase 7 — IA avançada

- [ ] embeddings;
- [ ] memória semântica;
- [ ] modelo rápido para decisões;
- [ ] modelo grande para planejamento;
- [ ] saída estruturada;
- [ ] seleção por logits;
- [ ] modelos MoE;
- [ ] streaming de experts;
- [ ] suporte a diferentes backends.

## Fase 8 — Montagem

- [ ] Montador;
- [ ] histórico de cenas;
- [ ] linha do tempo;
- [ ] resumo automático;
- [ ] exportação de roteiro;
- [ ] exportação de livro;
- [ ] reprodução da simulação.

---

# 31. Primeiro grande objetivo

Não tentar criar imediatamente uma cidade com 100 personagens.

Criar uma simulação pequena e extremamente consistente:

```text
1 cenário
5 personagens
1 objetivo
1 segredo
1 conflito
1 investigação
```

Exemplo:

```text
Cenógrafo
    ↓
Casa

Roteirista
    ↓
5 personagens

Diretor
    ↓
evento inicial

Contracenador
    ↓
interações

Atores
    ↓
ações

WorldState
    ↓
mundo atualizado

Diretor
    ↓
próximo evento
```

Se isso funcionar bem, aumentar para:

```text
5 → 10 → 20 → 50 → 100 personagens
```

---

# 32. Princípio central do projeto

O Contracenador não deve ser apenas:

```text
LLM + LLM + LLM
```

Deve ser:

```text
SIMULAÇÃO
    +
ESTADO
    +
MEMÓRIA
    +
CRENÇAS
    +
OBJETIVOS
    +
RELAÇÕES
    +
MUNDO
    +
IA
```

O LLM é o **Ator**, não o mundo inteiro.

---

# 33. Visão final

A arquitetura conceitual do Contracenador:

```text
                         🎬 CONTRACENADOR
                              │
                 ┌────────────┼────────────┐
                 │            │            │
                 ▼            ▼            ▼
             🎨 CENÓGRAFO  ✍️ ROTEIRISTA  🎬 DIRETOR
                 │            │            │
                 │            │            │
                 ▼            ▼            ▼
               MUNDO      PERSONAGENS    EVENTOS
                 │            │            │
                 └────────────┼────────────┘
                              │
                              ▼
                        🎭 CONTRACENA
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
          🎭 ATOR          🎭 ATOR          🎭 ATOR
             │                │                │
             └────────────────┼────────────────┘
                              │
                              ▼
                         🌎 MUNDO
                              │
                              ▼
                           🎬 DIRETOR
                              │
                              ▼
                         nova cena
```

### Filosofia

**Roteirista cria quem são as pessoas.**

**Cenógrafo cria onde elas vivem.**

**Diretor coordena o que acontece.**

**Ator decide e interpreta o personagem.**

**Contracenador coloca os personagens juntos.**

**Montador registra e organiza o que aconteceu.**

**Produtor mantém toda a máquina funcionando.**

O objetivo final é que uma história possa surgir da interação entre essas partes, em vez de ser simplesmente escrita antecipadamente por um único LLM.

---

# 34. Próximo passo recomendado

Antes de adicionar novas funcionalidades, implementar:

```text
1. WorldState
2. Event System
3. Beliefs
4. Evidence
5. Goals
6. Actions
7. Perception
8. Director
```

Depois reorganizar o código atual para que:

```text
Ator
Roteirista
Cenógrafo
Diretor
Contracenador
```

sejam componentes independentes.

A partir daí, novas capacidades podem ser adicionadas sem precisar reescrever o núcleo do projeto.
