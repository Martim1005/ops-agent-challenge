# Ops Agent - AI Agent para Operações Internas

Protótipo feito para o take-home challenge "AI Agent para Operações Internas". É um agente que recebe pedidos internos escritos em linguagem natural (pensa nisto ligado ao Slack) e responde com base no diretório de colaboradores, nas tarefas, no calendário e nas políticas internas fornecidas. Sempre que uma ação implica mudar alguma coisa no sistema, o agente pára e pede confirmação antes de avançar.

## 1. Como cheguei ao problema

Passei a maior parte do tempo inicial só a ler os dados fornecidos e as mensagens em `messages.json`, e rapidamente ficou claro um padrão: praticamente nenhum dos pedidos é difícil por si só. O que os torna lentos é a informação andar espalhada, uma tarefa aqui, uma regra num `.md` ali, uma reunião no calendário de outra pessoa, e alguém ter sempre de juntar tudo isso antes de conseguir responder ou agir.

Olhando para as 8 mensagens de exemplo, dá para ver três tipos de pedido a repetirem-se, o que faz sentido numa empresa de ~80 pessoas:

1. **"O que falta para o onboarding de X?"** É literalmente o exemplo dado no enunciado (aparece em M02 e M08). Para responder bem é preciso juntar o `employees.json` (quem é, quem é o manager), o `tasks.json` (o que já foi feito) e o `calendar.json` (o que já está marcado, mesmo que a tarefa correspondente ainda diga "open") contra os passos descritos no `onboarding.md`.
2. **"Posso ter acesso a X?"** (M03, M04, M06, M07). Aqui a resposta certa depende de regras específicas do `access_policy.md`, que mudam consoante a equipa, o tipo de contrato e se é ou não produção. São regras que raramente estão de cabeça de quem pergunta, e cujo incumprimento tem custo real, um acesso de escrita a produção dado sem aprovação, por exemplo.
3. **"Posso comprar X?"** (M01). O mesmo problema, mas com o `expenses_policy.md`, com o risco extra de se duplicar trabalho que já estava em curso (a tarefa de rever o pedido, nesse caso, já existia).

Decidi construir um único agente que cobrisse estes três fluxos, mais uma pesquisa simples de documentos (M05), em vez de me focar só no onboarding. O motivo é simples: o valor real não está em nenhum deles isolado, está em ter um ponto de entrada único que sabe a que sistema ou regra ir buscar a resposta, consoante o tipo de pedido. É basicamente o problema descrito no enunciado, "pedidos simples acabam por depender sempre das mesmas pessoas".

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

Sem dependências externas. Corre com Python 3.10+ da standard library, nada mais a instalar.

### Como correr

```bash
# ver o agente a processar as 8 mensagens de exemplo, incluindo o ciclo de confirmação
python run_demo.py

# chat interativo
python -m agent.cli

# testes
python -m unittest tests.py -v
```

No `cli.py`, define primeiro quem está a falar (é preciso para aplicar as políticas à pessoa certa) com `:quem <nome>`, e depois escreve o pedido como se fosses mesmo um colaborador. Exemplo, o do próprio enunciado:

```
[???] > :quem Gabriela Nunes
[Gabriela Nunes] > Vou receber um novo membro na equipa na próxima semana. O que falta preparar?
```

## 3. Onde faz sentido usar AI e onde faz sentido usar lógica tradicional

Esta acabou por ser a decisão de arquitetura mais importante do protótipo todo, por isso deixei-a explícita no próprio código (vê os comentários de topo de `nlu.py`, `policies.py` e `onboarding.py`, não é só este README a dizê-lo).

| Onde | Lógica tradicional | AI / LLM |
|---|---|---|
| Entender o pedido | - | As mensagens são texto livre, em português, e cada pessoa escreve de forma diferente ("Preciso de...", "Alguém sabe...", "Conseguem confirmar?"). É exatamente o tipo de variação onde um classificador de regras rígido parte com facilidade, e onde um LLM generaliza bem. |
| Aplicar as políticas de acesso e despesas | Sim, sempre. `policies.py` é código determinístico e testado (`tests.py`). | Deixar um LLM "interpretar" a política a cada pedido é arriscado: pode parafrasear mal, esquecer uma exceção (testei isto de propósito com a mensagem do Diogo, M07, a exceção de urgência das despesas não se aplica a acessos) ou simplesmente inventar uma regra que não existe. Aprovações são compliance, têm de ser sempre consistentes e auditáveis, não apenas plausíveis. |
| Calcular o checklist de onboarding | Sim. `onboarding.py` cruza `tasks.json` e `calendar.json` com os passos do `onboarding.md`. | Os passos e a forma de os detetar são regra de negócio fixa, não há ambiguidade nenhuma a resolver aqui, só cruzamento de dados. |
| Escrever a resposta final ao utilizador | Por agora, templates simples. | É onde um LLM traria mais valor na próxima iteração: pegar na lista de decisões já estruturada e transformá-la num texto mais natural, sem nunca poder alterar o conteúdo da decisão em si (ver secção 6). |

Resumindo por palavras simples: o LLM nunca decide o que fazer, só ajuda a perceber o que está a ser pedido. Quem decide é sempre o motor de políticas determinístico, e isso foi intencional desde o início.

### Sobre o classificador de intenções (`nlu.py`)

Por omissão, o protótipo usa um classificador baseado em regras e palavras-chave, 100% offline, sem custo, sem precisar de chave de API nenhuma. Fiz esta escolha para cumprir à letra o "não é esperado qualquer custo" do enunciado, e também para o protótipo correr em qualquer máquina sem configuração prévia. Dito isto, a função `nlu.classify(text) -> Intent` já está pensada para o passo seguinte óbvio: se existir uma `ANTHROPIC_API_KEY` no ambiente e o pacote `anthropic` estiver instalado, o mesmo módulo tenta usar o Claude para a mesma tarefa, com o mesmo formato de saída, e se algo falhar cai automaticamente de volta para as regras, sem quebrar nada. O resto do agente nem sabe qual dos dois caminhos respondeu, e não precisa de saber.

## 4. Que ferramentas dei ao agente

Pensadas a partir das que o próprio enunciado sugere (documentos internos, calendário, sistema de tarefas, diretório, chat), mas simuladas localmente em cima dos ficheiros que recebi:

- **Leitura:** `get_employee`, `find_employees_by_name`, `manager_of`, `tasks_for_employee`, `tasks_assigned_to`, `find_tasks`, `events_for_employee`, `find_free_slot`, e os três documentos de processo (`onboarding.md`, `access_policy.md`, `expenses_policy.md`) como texto pesquisável.
- **Escrita**, todas a passar por confirmação humana (ver secção seguinte): `create_task`, `request_access` (regista o pedido e os aprovadores, nunca aprova nada sozinho), `log_expense` (o mesmo princípio), e o esqueleto de `schedule_meeting` (usa `find_free_slot`, que já respeita o calendário de todas as pessoas envolvidas).

Todas as escritas vão parar a `state/action_log.json`, um audit trail simulado. Nunca tocam nos ficheiros originais dentro de `ai_agent_operacoes_dados/`, que ficam como o "sistema de registo" só de leitura, tal como estavam quando os recebi.

## 5. Que ações exigem validação humana

A regra que segui foi esta: qualquer ação que mude estado real (criar algo, pedir um acesso, comprometer dinheiro, ocupar tempo de alguém no calendário) fica sempre proposta, nunca é executada diretamente, mesmo quando a política já deixa bem claro quem tem de aprovar. O agente não substitui o aprovador, só prepara o pedido para a pessoa certa decidir mais depressa.

| Ação | Porque precisa de um humano |
|---|---|
| Pedir acesso (GitHub, produção, financeiro) | O próprio `access_policy.md` exige aprovação nomeada (Engineering Manager, Finance Manager, Head of Operations, consoante o caso). O agente nunca pode contornar isso, e testei especificamente que um pedido "urgente" de acesso a produção continua a exigir aprovação normal (M07). |
| Registar despesa | Envolve dinheiro da empresa. A política pede aprovação do manager, e da Finance Manager acima de 500€, antes da compra na maioria dos casos. |
| Criar tarefa | Risco baixo, mas continua a atribuir trabalho a alguém. Por isso o agente propõe em vez de impor, para não gerar ruído nem duplicar coisas (é o que acontece em M01, onde o agente percebe que já existem as tarefas T106 e T107 em aberto e não propõe criar novas). |
| Marcar reunião | Ocupa tempo de outra pessoa no calendário dela. |

Tudo o que o agente faz sem pedir confirmação é só consulta: responder "o que falta no onboarding", explicar quem tem de aprovar um acesso, apontar para o documento certo. Isso é sempre seguro, porque não muda nada.

## 6. Como evoluiria isto num cenário real

Por ordem aproximada de prioridade:

1. **Integrações reais em vez de ficheiros JSON.** Google ou Microsoft Calendar, Slack de verdade (responder onde as pessoas já perguntam, em vez de uma CLI), um sistema de tarefas real (Jira, Linear, Asana), o HRIS ou diretório da empresa. A arquitetura já isola tudo isto em `data_store.py`, por isso trocar a fonte de dados não implica mexer no resto do agente.
2. **RAG a sério sobre a documentação interna**, para o dia em que os processos deixarem de caber em três `.md` (que é literalmente o cenário descrito no enunciado, "documentos espalhados"). Hoje o `_handle_document` faz um match direto por palavra-chave, porque só há 3 documentos, mas isso não escala.
3. **Geração da resposta final com um LLM**, usando os factos que o motor de políticas já produziu como contexto fechado, não como fonte da verdade, para respostas mais naturais sem abrir a porta a alucinações sobre a política em si.
4. **Identidade e permissões do próprio agente.** Hoje qualquer "speaker" pode perguntar o que quiser sobre qualquer pessoa. Num sistema real, o agente precisa de saber quem está autenticado e aplicar as mesmas regras de acesso à informação que se aplicariam a um humano (um colaborador comum não devia conseguir, por exemplo, consultar dados salariais de outra pessoa só porque perguntou ao agente).
5. **Aprovação humana como fluxo assíncrono a sério.** Hoje o `confirm()` acontece na mesma sessão de chat; num produto real seria antes uma notificação para o aprovador certo (Slack DM, email) com um aceitar/rejeitar próprio, e o agente só fecharia o ciclo quando essa resposta chegasse.
6. **Observabilidade e avaliação.** Já há um início disto em `state/action_log.json`, mas faltaria medir quantos pedidos o agente resolve sem escalar para um humano, e ter um conjunto de testes maior que o `tests.py` atual para apanhar regressões sempre que as regras mudassem.
7. **Mais do que um canal.** O `orchestrator.Agent` já não depende da CLI para nada; pô-lo atrás de um endpoint de Slack ou email seria só escrever um adapter fino, parecido com o `cli.py` de agora.

## 7. Notas sobre o dataset (coisas que o agente apanha de propósito)

Construí os handlers para reagir às inconsistências reais que encontrei no dataset, em vez de as ignorar, porque isso é, na prática, o que separa um agente útil de um agente perigoso:

- Em **M08**, a Gabriela fala em "mais duas pessoas" a começar, mas só há um colaborador com `status: starting_soon` no diretório. O agente aponta a discrepância em vez de inventar uma segunda pessoa que não existe nos dados.
- No onboarding do **Nuno Ribeiro (E013)**, a tarefa T103 (sessão de boas-vindas) continua marcada como `open`, mas o evento já está no calendário. O agente cruza as duas fontes, reporta o passo como feito, e ainda assim sinaliza que a tarefa ficou desatualizada, em vez de confiar cegamente numa só delas.
- O evento "1:1 reservado" no calendário da Helena Duarte (manager do Nuno) não identifica com quem é. Por isso o agente marca este passo como "a confirmar", nunca como "feito", porque não há garantia suficiente de que é mesmo o 1:1 com o novo colaborador.
- Em **M07**, o Diogo pede acesso de escrita a produção dizendo que é urgente. O agente não deixa a exceção de urgência da política de despesas "vazar" para acessos, porque essa exceção simplesmente não existe aí, e continua a exigir aprovação e motivo registado.
- Em **M01**, antes de propor a criação de uma tarefa nova para a despesa da Carolina, o agente verifica se já não há tarefas em aberto para o mesmo pedido, e encontra as T106 e T107, evitando assim duplicar trabalho que já estava em curso.
- Em **M05**, o Filipe pergunta por um documento que não existe nos processos internos que o agente conhece. Mas existe uma tarefa, a T109, atribuída ao próprio Filipe para atualizar esse template, e o agente prefere apontar isso a simplesmente responder "não encontrado".
