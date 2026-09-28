"""Interactive CLI for the Ops Agent.

Run with:  python -m agent.cli
Type as if you were an employee posting in Slack. The agent will answer,
and if it proposes an action (creating a task, requesting access, logging
an expense) it will ask you to confirm before anything is "executed" —
executed here means appended to state/action_log.json, a simulated
system-of-record, never a write back into the original data files.
"""
from __future__ import annotations

from .orchestrator import Agent


BANNER = """\
Ops Agent — assistente de operações internas (protótipo)
Escreve uma mensagem como se fosses um colaborador. Comandos:
  :quem <nome>     -- define quem está a falar (para pedidos de acesso/despesa)
  :sair            -- termina
"""


def main():
    agent = Agent()
    speaker = None
    print(BANNER)
    while True:
        try:
            line = input(f"[{speaker or '???'}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":sair", ":quit", ":exit"):
            break
        if line.startswith(":quem"):
            speaker = line.split(maxsplit=1)[1].strip() if len(line.split(maxsplit=1)) > 1 else None
            print(f"(a falar como: {speaker})")
            continue

        response = agent.handle_message(line, speaker_name=speaker)
        print("\nAgente:")
        print(response.text)
        for action in response.proposed_actions:
            print(f"\n  [ação pendente {action.id}] {action.description}")
            ans = input(f"  Confirmar '{action.id}'? (s/n) > ").strip().lower()
            result = agent.confirm(action.id, approved=ans in ("s", "sim", "y", "yes"))
            print(f"  -> {result}")
        print()


if __name__ == "__main__":
    main()
