"""Non-interactive walkthrough of every scenario in
ai_agent_operacoes_dados/messages.json, run through the real agent
(agent/orchestrator.py) end to end — including the confirm step for
proposed actions, so the full "propose -> human confirms -> execute" loop
is visible without needing a live terminal session.

Run with:  python run_demo.py
"""
from __future__ import annotations

from agent.data_store import DataStore
from agent.orchestrator import Agent

# For messages where a proposed action would make sense to accept in this
# scripted walkthrough, list the message id here. Everything else is left
# pending on purpose, to show the agent does NOT act without a human.
AUTO_CONFIRM_FOR = {"M08"}  # demonstrated explicitly below as well


def main():
    store = DataStore()
    agent = Agent(store)

    print("=" * 78)
    print("OPS AGENT — DEMO SOBRE ai_agent_operacoes_dados/messages.json")
    print("=" * 78)

    for msg in store.messages:
        print(f"\n--- {msg['id']} | #{msg['channel']} | {msg['from']} ---")
        print(f"> {msg['message']}")
        resp = agent.handle_message(msg["message"], speaker_name=msg["from"])
        print(f"\n[intent detectada: {resp.intent.kind} | confiança: {resp.intent.confidence:.2f} | via: {resp.intent.source}]")
        print(resp.text)

        for action in resp.proposed_actions:
            print(f"\n  >> ação proposta [{action.id}]: {action.description}")
            print(f"     payload: {action.payload}")

    print("\n" + "=" * 78)
    print("EXEMPLO DO CICLO DE CONFIRMAÇÃO (exemplo do enunciado)")
    print("=" * 78)
    example = "Vou receber um novo membro na equipa na próxima semana. O que falta preparar?"
    print(f"> {example}")
    resp = agent.handle_message(example, speaker_name="Gabriela Nunes")
    print(resp.text)
    if resp.proposed_actions:
        action = resp.proposed_actions[0]
        print(f"\n  >> ação proposta [{action.id}]: {action.description}")
        print("  >> humano confirma: SIM")
        result = agent.confirm(action.id, approved=True)
        print(f"  -> {result}")
    else:
        print("\n(Sem tarefas em falta a propor para este colaborador neste momento — tudo já "
              "rastreado em tasks.json/calendar.json.)")

    print("\n" + "-" * 78)
    print("EXEMPLO: pedido de acesso standard (pronto de imediato) -> confirmado")
    print("-" * 78)
    example2 = "Preciso de acesso ao Slack, ainda não me chegou nada."
    print(f"[Nuno Ribeiro] > {example2}")
    resp2 = agent.handle_message(example2, speaker_name="Nuno Ribeiro")
    print(resp2.text)
    if resp2.proposed_actions:
        action = resp2.proposed_actions[0]
        print(f"\n  >> ação proposta [{action.id}]: {action.description}")
        print("  >> humano (Operations) confirma: SIM")
        print(f"  -> {agent.confirm(action.id, approved=True)}")

    print("\n" + "-" * 78)
    print("EXEMPLO: pedido de despesa -> humano REJEITA (nada é executado)")
    print("-" * 78)
    example3 = "Posso comprar um teclado novo, uns 45 EUR?"
    print(f"[Bruno Costa] > {example3}")
    resp3 = agent.handle_message(example3, speaker_name="Bruno Costa")
    print(resp3.text)
    if resp3.proposed_actions:
        action = resp3.proposed_actions[0]
        print(f"\n  >> ação proposta [{action.id}]: {action.description}")
        print("  >> humano (manager) confirma: NÃO")
        print(f"  -> {agent.confirm(action.id, approved=False)}")

    print("\n" + "=" * 78)
    print(f"Registo de ações (audit trail) gravado em: state/action_log.json "
          f"({len(store.action_log)} entradas)")
    print("=" * 78)


if __name__ == "__main__":
    main()
