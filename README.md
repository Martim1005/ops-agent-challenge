# Ops Agent — AI Agent para Operações Internas

Protótipo para o take-home challenge "AI Agent para Operações Internas". Um agente
que responde a pedidos internos em linguagem natural (Slack/chat) — sobre
onboarding, acessos e despesas — cruzando o diretório de colaboradores, tarefas,
calendário e as políticas internas fornecidas, e que só executa ações depois de
validação humana explícita.

## 1. Como identifiquei o problema

Os dados fornecidos (`ai_agent_operacoes_dados/`) e as mensagens em
`messages.json` desenham um padrão muito claro: a maior parte dos pedidos
internos não são difíceis — são **repetitivos e dependem de uma pessoa ir
juntar informação espalhada por vários sítios** (uma tarefa aqui, uma regra
num `.md`, uma reunião no calendário de outra pessoa) antes de conseguir
responder ou agir.

Olhando para as 8 mensagens de exemplo, reconheço três padrões que se repetem
constantemente numa empresa de ~80 pessoas:

1. **"O que falta para o onboarding de X?"** — este é literalmente o exemplo
   dado no enunciado (M02, M08). Responder bem exige juntar `employees.json`
   (quem é, quem é o manager), `tasks.json` (o que já foi feito) e
   `calendar.json` (o que já está agendado, mesmo que a tarefa correspondente
   ainda não esteja marcada como concluída) contra os passos definidos em
   `onboarding.md`.
2. **"Posso ter acesso a X?"** (M03, M04, M06, M07) — a resposta certa depende
   de regras específicas em `access_policy.md` (equipa, tipo de contrato,
   se é produção) que raramente estão de cabeça na pessoa que pergunta, e
   cujo incumprimento tem custo real (acesso de escrita a produção dado sem
   aprovação, por exemplo).
3. **"Posso comprar X?"** (M01) — o mesmo problema com `expenses_policy.md`,
   com o risco adicional de duplicar trabalho (a tarefa de rever o pedido já
   podia existir).

Escolhi construir **um único agente que cobre estes três fluxos** (mais uma
pesquisa simples de documentos, M05) em vez de aprofundar só o onboarding,
porque o valor real não está em nenhum dos três isoladamente — está em ter
**um ponto de entrada único que sabe qual sistema/regra consultar para cada
tipo de pedido**, que é exactamente o problema descrito no enunciado
("pedidos simples acabam por depender sempre das mesmas pessoas").

## 2. O protótipo

```
agent/
  data_store.py    # carrega os dados; único sítio que sabe ler os ficheiros
  policies.py       # regras de acesso/despesas (lógica tradicional, testável)
  onboarding.py     # cruza tasks.json + calendar.json com onboarding.md
  nlu.py            # texto livre -> {intent, entidades} (regras, com LLM opcional)
  orchestrator.py   # o "agente": entender -> consultar -> decidir -> propor -> (confirmar) -> agir
  cli.py            # interface de chat interativa
run_demo.py         # corre as 8 mensagens de messages.json e mostra o ciclo completo
tests.py            # testes unitários à política e ao checklist de onboarding
state/action_log.json  # audit trail simulado (gerado ao correr; nunca escreve nos dados originais)
```

Zero dependências externas — corre com Python 3.10+ da standard library.

### Correr

```bash
# ver o agente a processar as 8 mensagens de exemplo, incluindo o ciclo de confirmação
python run_demo.py

# chat interativo
python -m agent.cli

# testes
python -m unittest tests.py -v
```

No `cli.py`, define primeiro quem está a falar (necessário para aplicar
políticas por pessoa) com `:quem <nome>`, depois escreve o pedido como se
fosses um colaborador. Exemplo (o do enunciado):

```
[???] > :quem Gabriela Nunes
[Gabriela Nunes] > Vou receber um novo membro na equipa na próxima semana. O que falta preparar?
```

## 3. Onde faz sentido usar AI vs. lógica tradicional

Esta foi a decisão de arquitetura mais importante do protótipo, por isso
separei-a explicitamente no código (ver os comentários de topo em
`nlu.py`, `policies.py` e `onboarding.py`):

| Onde | Lógica tradicional | AI / LLM |
|---|---|---|
| **Entender o pedido** | — | Mensagens de texto livre, em português, com estilos diferentes por pessoa ("Preciso de...", "Alguém sabe...", "Conseguem confirmar?"). Isto é exatamente o tipo de variação onde um classificador de regras é frágil e um LLM generaliza bem. |
| **Aplicar as políticas de acesso/despesas** | **Sim, sempre.** `policies.py` é código determinístico, testado (`tests.py`). | Um LLM a "interpretar" a política em cada pedido arrisca parafrasear, esquecer uma exceção (ex.: a exceção de urgência em despesas *não* se aplica a acessos — testei isto explicitamente com a mensagem do Diogo, M07) ou simplesmente alucinar uma regra. Aprovações são compliance-sensitive: têm de ser **consistentes e auditáveis**, não apenas "plausíveis". |
| **Calcular o checklist de onboarding** | **Sim.** `onboarding.py` cruza `tasks.json`/`calendar.json` com os passos de `onboarding.md`. | Os passos e como os detetar são regra de negócio fixa; não há ambiguidade a resolver aqui, só cruzamento de dados. |
| **Escrever a resposta final ao utilizador** | Templates simples por agora | Onde um LLM acrescentaria mais valor real na próxima iteração: transformar a lista estruturada de decisões num texto mais natural e adaptado ao tom do canal, sem nunca poder alterar o conteúdo da decisão (ver secção 6). |

Na prática, isto significa: **o LLM nunca decide o quê fazer — só ajuda a
entender o pedido e (numa próxima iteração) a formular a resposta.** A decisão
em si passa sempre pelo motor de políticas determinístico.

### Sobre o classificador de intents (`nlu.py`)

Por omissão o protótipo usa um classificador **baseado em regras/keywords**,
100% offline, sem custo, sem chave de API — para cumprir literalmente o
requisito de "não é esperado qualquer custo" e para o protótipo ser
demonstrável em qualquer máquina sem configuração. Mas a interface
`nlu.classify(text) -> Intent` já está pensada para o passo seguinte óbvio:
se existir `ANTHROPIC_API_KEY` no ambiente e o pacote `anthropic` instalado,
o mesmo módulo tenta usar o Claude para a mesma tarefa (mesmo formato de
saída), com fallback automático e silencioso para as regras em caso de
qualquer falha. O resto do agente não sabe (nem precisa de saber) qual dos
dois caminhos respondeu.

## 4. Que ferramentas dei ao agente

Pensadas como as que o enunciado sugere (documentos internos, calendário,
sistema de tarefas, diretório, chat), mas simuladas localmente sobre os
ficheiros fornecidos:

- **Leitura**: `get_employee`, `find_employees_by_name`, `manager_of`,
  `tasks_for_employee`, `tasks_assigned_to`, `find_tasks`,
  `events_for_employee`, `find_free_slot`, mais os três documentos de
  processo (`onboarding.md`, `access_policy.md`, `expenses_policy.md`) como
  texto pesquisável.
- **Escrita (todas passam por confirmação humana — ver secção 5)**:
  `create_task`, `request_access` (regista pedido + aprovadores, nunca
  aprova), `log_expense` (idem), e o esqueleto de `schedule_meeting`
  (via `find_free_slot`, que já respeita o calendário existente de todos os
  participantes).

Todas as escritas vão para `state/action_log.json` — um audit trail simulado
— e **nunca** para os ficheiros originais em `ai_agent_operacoes_dados/`, que
se mantêm como "sistema de registo" só de leitura.

## 5. Que ações exigem validação humana

Regra que segui: **qualquer ação que mude estado real (crie algo, peça
acesso, comprometa dinheiro, ocupe tempo de alguém no calendário) é sempre
proposta, nunca executada diretamente** — mesmo quando a política já diz
claramente quem tem de aprovar. O agente não substitui o aprovador; só
prepara o pedido para a pessoa certa decidir mais depressa.

| Ação | Por que precisa de humano |
|---|---|
| Pedir acesso (GitHub, produção, financeiro) | O próprio `access_policy.md` exige aprovação nomeada (EM, Finance Manager, Head of Operations); o agente nunca deve poder contornar isso — testei explicitamente que um pedido "urgente" de acesso a produção continua a exigir aprovação (M07). |
| Registar despesa | Envolve dinheiro da empresa; a política exige aprovação do manager (e da Finance Manager acima de 500€) *antes* da compra na generalidade dos casos. |
| Criar tarefa | Baixo risco, mas ainda assim atribui trabalho a alguém — o agente propõe, não impõe, para evitar ruído/duplicação (ver M01, onde o agente deteta que já existem tarefas T106/T107 e **não** propõe criar novas). |
| Marcar reunião | Ocupa tempo de outra pessoa no calendário. |

O que o agente faz **sem pedir confirmação** é só consulta/leitura: responder
"o que falta no onboarding", explicar quem tem de aprovar um acesso, apontar
para o documento certo. Isso é sempre seguro porque não altera nada.

## 6. Como evoluiria isto num contexto real

Por ordem aproximada de prioridade:

1. **Integrações reais** em vez de ficheiros JSON: Google/Microsoft Calendar,
   Slack (ler e responder onde as pessoas já perguntam, em vez de CLI), um
   sistema de tarefas real (Jira/Linear/Asana), o HRIS/diretório real. A
   arquitetura já isola isto em `data_store.py`, por isso trocar a fonte de
   dados não implica tocar no resto do agente.
2. **RAG real sobre a documentação interna**, para quando os processos não
   couberem em três `.md` (que é o caso do enunciado: "documentos
   espalhados"). Hoje `_handle_document` faz um match direto por palavra-chave
   porque só há 3 documentos — não escala.
3. **Geração da resposta final via LLM**, usando os factos estruturados que o
   motor de políticas já produziu como *contexto fechado* (não como fonte da
   verdade) — para respostas mais naturais sem abrir a porta a alucinação da
   política em si.
4. **Identidade e permissões do próprio agente**: hoje qualquer "speaker" pode
   perguntar qualquer coisa sobre qualquer pessoa. Num sistema real, o agente
   precisa de saber quem está autenticado e aplicar as mesmas regras de
   acesso à informação que aplicaria a um humano (ex.: um colaborador comum
   não deveria conseguir consultar detalhes salariais de outra pessoa via o
   agente).
5. **Aprovação humana como fluxo assíncrono real**: hoje `confirm()` é
   síncrono na mesma sessão de chat; num produto real seria uma notificação
   para o aprovador certo (Slack DM, email) com aceitar/rejeitar, e o agente
   só fecha o ciclo quando essa resposta chega.
6. **Observabilidade e avaliação**: logging estruturado de cada decisão
   (já começado em `state/action_log.json`), métricas de quantos pedidos o
   agente resolveu sem escalar para um humano, e um conjunto de casos de
   teste (como `tests.py`, mas maior) para apanhar regressões de política
   quando as regras mudarem.
7. **Multi-canal**, não só chat: o mesmo `orchestrator.Agent` já não depende
   de CLI — reutilizá-lo atrás de um endpoint Slack/email é só escrever um
   novo "adapter" fino, equivalente ao `cli.py` atual.

## 7. Notas sobre o dataset (casos que o agente apanhou de propósito)

Construí os handlers para reagir a inconsistências reais no dataset em vez de
as ignorar — porque isso é, na prática, o que torna um agente destes útil
(ou perigoso, se ignorar):

- **M08** (Gabriela): a mensagem fala de "mais duas pessoas", mas só há um
  colaborador com `status: starting_soon` no diretório. O agente sinaliza a
  discrepância em vez de inventar uma segunda pessoa.
- **Nuno Ribeiro (E013)**: a tarefa T103 (sessão de boas-vindas) continua
  `open`, mas o evento já existe no calendário — o agente cruza as duas
  fontes e reporta o passo como feito, sinalizando a tarefa desatualizada,
  em vez de confiar cegamente numa só fonte.
- O evento "1:1 reservado" no calendário da manager (Helena Duarte) não
  identifica a pessoa — o agente marca este passo como "a confirmar", não
  como "feito", porque não há garantia suficiente de que é o 1:1 com o Nuno.
- **M07** (Diogo): pedido de acesso de escrita a produção "urgente" — o
  agente não aplica a exceção de urgência da política de despesas (que não
  existe para acessos) e continua a exigir aprovação + motivo registado.
- **M01** (Carolina): antes de propor criar uma tarefa nova de despesa, o
  agente verifica se já existem tarefas em aberto para o mesmo pedido
  (T106/T107) e evita duplicar trabalho.
- **M05** (Filipe): o documento pedido não existe nos processos internos que
  o agente conhece, mas existe uma tarefa (T109) atribuída ao próprio Filipe
  para atualizar esse template — o agente aponta isso em vez de responder
  "não encontrado".
