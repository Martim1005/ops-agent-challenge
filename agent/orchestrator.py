"""The agent loop: understand -> look up -> decide -> propose -> (confirm) -> act.

Perception (`nlu.classify`) and the final natural-language phrasing are the
only AI-shaped steps. Everything in between -- which employee is meant, what
the policy requires, what counts as "done" -- is deterministic lookup and
policy evaluation (data_store.py, policies.py, onboarding.py), because those
answers must be correct and repeatable, not merely plausible.

Nothing that changes state (creating a task, requesting access, logging an
expense, booking a meeting) ever executes directly from `handle()`. Every one
of those comes back as a `ProposedAction` that a human must explicitly
confirm via `confirm()`. See README.md "Ações que exigem validação humana"
for the reasoning per action type.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from . import nlu, policies, onboarding
from .data_store import DataStore, Employee

_action_id_counter = itertools.count(1)


@dataclass
class ProposedAction:
    id: str
    kind: str  # create_task | request_access | log_expense | schedule_meeting
    description: str
    payload: dict


@dataclass
class AgentResponse:
    text: str
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    intent: Optional[nlu.Intent] = None


class Agent:
    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()
        self._pending: dict[str, ProposedAction] = {}

    # ---------------------------------------------------------------- entry
    def handle_message(self, text: str, speaker_name: Optional[str] = None) -> AgentResponse:
        known_names = [e.name for e in self.store.employees]
        intent = nlu.classify(text, known_names)

        speaker = self.store.find_employees_by_name(speaker_name)[0] if speaker_name else None

        if intent.kind == "onboarding_status":
            resp = self._handle_onboarding(intent, text, speaker)
        elif intent.kind == "access_request":
            resp = self._handle_access(intent, text, speaker)
        elif intent.kind == "expense_request":
            resp = self._handle_expense(intent, text, speaker)
        elif intent.kind == "document_lookup":
            resp = self._handle_document(intent, text, speaker)
        else:
            resp = AgentResponse(
                text=(
                    "Não tenho a certeza do que está a ser pedido. Podes indicar se é sobre "
                    "onboarding, acesso, despesa ou um documento? (ex.: 'o que falta para o "
                    "onboarding do/a X?')"
                )
            )
        resp.intent = intent
        for action in resp.proposed_actions:
            self._pending[action.id] = action
        return resp

    def confirm(self, action_id: str, approved: bool, extra: Optional[dict] = None) -> str:
        action = self._pending.pop(action_id, None)
        if action is None:
            return f"Não encontrei nenhuma ação pendente com o id '{action_id}'."
        payload = {**action.payload, **(extra or {})}
        if not approved:
            self.store.log_pending_approval(action.kind, {**payload, "outcome": "rejeitado_pelo_humano"})
            return f"OK, ação '{action.description}' não foi executada."

        if action.kind == "create_task":
            task = self.store.create_task(**payload)
            return f"Tarefa criada: {task.id} — {task.title} (assignee: {task.assignee_id})."
        if action.kind in ("request_access", "log_expense", "schedule_meeting"):
            entry = self.store.log_executed(action.kind, payload)
            return f"Registado ({action.kind}) e encaminhado para aprovação de: {', '.join(payload.get('approvers', [])) or 'n/a'}."
        return "Tipo de ação desconhecido."

    def new_action_id(self) -> str:
        return f"act-{next(_action_id_counter)}"

    # ------------------------------------------------------------ onboarding
    def _handle_onboarding(self, intent: nlu.Intent, text: str, speaker: Optional[Employee]) -> AgentResponse:
        mentioned = intent.entities.get("mentioned_employee")
        target: Optional[Employee] = None
        if mentioned:
            matches = self.store.find_employees_by_name(mentioned)
            target = matches[0] if matches else None

        starters = self.store.upcoming_starters()

        if target is None and not starters:
            return AgentResponse(text="Não encontrei nenhum colaborador com onboarding em curso ou a começar em breve.")

        if target is None and len(starters) == 1:
            target = starters[0]

        if target is None and len(starters) > 1:
            names = ", ".join(s.name for s in starters)
            return AgentResponse(
                text=(
                    f"Encontrei {len(starters)} onboardings a decorrer: {names}. "
                    "Sobre qual queres o ponto de situação? (Podes perguntar por nome, ou pedir 'todos'.)"
                ),
                missing_info=["nome do colaborador"],
            )

        # heuristic: message implies more people than the system currently has
        plural_hint = any(k in text.lower() for k in ["duas pessoas", "mais pessoas", "dois novos", "duas novas", "várias pessoas"])
        note = ""
        if plural_hint and len(starters) < 2:
            note = (
                "\n\nNota: a mensagem sugere mais do que um novo colaborador, mas encontrei apenas "
                f"{len(starters)} registo(s) com status 'starting_soon' no diretório. Confirma se falta "
                "adicionar alguém ao sistema antes de eu avançar."
            )

        items = onboarding.build_checklist(self.store, target)
        summary = onboarding.summarize(items)
        header = f"Ponto de situação do onboarding de {target.name} ({target.role}, {target.team}):\n"
        response = AgentResponse(text=header + summary + note)

        for item in items:
            if item.status == "missing" and "boas-vindas" in item.step.lower():
                action_id = self.new_action_id()
                due = target.start_date or datetime.now().date().isoformat()
                response.proposed_actions.append(ProposedAction(
                    id=action_id, kind="create_task",
                    description=f"Criar tarefa: marcar sessão de boas-vindas para {target.name}",
                    payload=dict(title=f"Marcar sessão de boas-vindas com {target.name}", category="onboarding",
                                 assignee_id=self.store.find_role("people operations").id if self.store.find_role("people operations") else "",
                                 related_employee_id=target.id, due_date=due),
                ))
            if item.status == "missing" and "equipamento" in item.step.lower():
                action_id = self.new_action_id()
                response.proposed_actions.append(ProposedAction(
                    id=action_id, kind="create_task",
                    description=f"Criar tarefa: preparar equipamento para {target.name}",
                    payload=dict(title=f"Preparar equipamento para {target.name}", category="onboarding",
                                 assignee_id="E002", related_employee_id=target.id,
                                 due_date=target.start_date),
                ))

        if response.proposed_actions:
            response.text += "\n\nPosso criar as tarefas em falta listadas acima? Usa confirm(<id>, True/False)."
        return response

    # --------------------------------------------------------------- access
    def _handle_access(self, intent: nlu.Intent, text: str, speaker: Optional[Employee]) -> AgentResponse:
        if not speaker:
            return AgentResponse(text="Preciso de saber quem está a pedir o acesso para aplicar a política correta.",
                                  missing_info=["colaborador solicitante"])

        access_type = intent.entities.get("access_type")
        if access_type in (None, "unspecified"):
            return AgentResponse(
                text=f"{speaker.name}, que tipo de acesso precisas — Slack, Drive, GitHub, produção ou sistemas financeiros?",
                missing_info=["tipo de acesso"],
            )

        if intent.entities.get("revocation"):
            open_revocations = [t for t in self.store.find_tasks(category="access") if "revoga" in t.title.lower() and t.status != "done"]
            if open_revocations:
                lines = [f"- {t.id}: {t.title} [{t.status}]" + (f" — {t.notes}" if t.notes else "") for t in open_revocations]
                return AgentResponse(text="Pedidos de revogação em aberto:\n" + "\n".join(lines) +
                                      "\n\nPara concluir preciso de saber exatamente qual o acesso a revogar (sistema/repositório) — falta essa informação na tarefa.",
                                      missing_info=["qual acesso a revogar"])
            return AgentResponse(text="Não encontrei pedidos de revogação em aberto.")

        decision = policies.evaluate_access_request(
            self.store, speaker, access_type,
            target_repo=intent.entities.get("target_repo"),
            expiration_date=intent.entities.get("expiration_date"),
            reason=intent.entities.get("reason"),
        )

        lines = [f"Pedido de acesso ({access_type}) de {speaker.name} ({speaker.team}"
                 f"{', contractor' if speaker.is_contractor else ''}):"]
        if decision.approvers:
            lines.append("Aprovação necessária de: " + ", ".join(decision.approvers) + ".")
        for n in decision.notes:
            lines.append(f"- {n}")
        if decision.missing_info:
            lines.append("Informação em falta antes de avançar: " + ", ".join(decision.missing_info) + ".")

        response = AgentResponse(text="\n".join(lines), missing_info=decision.missing_info)

        if decision.is_ready:
            action_id = self.new_action_id()
            response.proposed_actions.append(ProposedAction(
                id=action_id, kind="request_access",
                description=f"Enviar pedido de acesso ({access_type}) de {speaker.name} para aprovação",
                payload=dict(employee_id=speaker.id, access_type=access_type,
                             approvers=decision.approvers, notes=decision.notes),
            ))
            response.text += "\n\nPosso registar este pedido e encaminhar para os aprovadores acima. Confirmas?"
        else:
            response.text += "\n\nAssim que tiver essa informação posso preparar o pedido para aprovação — não é possível avançar sem ela."
        return response

    # -------------------------------------------------------------- expense
    def _handle_expense(self, intent: nlu.Intent, text: str, speaker: Optional[Employee]) -> AgentResponse:
        if not speaker:
            return AgentResponse(text="Preciso de saber quem está a pedir a despesa.", missing_info=["colaborador solicitante"])

        amount = intent.entities.get("amount_eur")
        if amount is None:
            return AgentResponse(text=f"{speaker.name}, qual o valor estimado da despesa?", missing_info=["valor em EUR"])

        is_equipment = bool(intent.entities.get("is_equipment"))
        is_urgent = bool(intent.entities.get("urgent"))

        # avoid creating duplicate work: surface matching open tasks first
        existing = [t for t in self.store.tasks
                    if t.category == "expense" and t.amount_eur == amount
                    and (t.related_employee_id is None or t.related_employee_id == speaker.id)]

        decision = policies.evaluate_expense_request(self.store, speaker, amount, is_equipment, is_urgent)

        lines = [f"Pedido de despesa de {speaker.name}: {amount:.2f} EUR" + (" (equipamento)" if is_equipment else "") + "."]
        lines.append("Aprovação necessária de: " + ", ".join(decision.approvers) + ".")
        for n in decision.notes:
            lines.append(f"- {n}")

        if existing:
            lines.append("\nJá existem tarefas relacionadas com este pedido — a confirmar antes de criar uma nova:")
            for t in existing:
                lines.append(f"  - {t.id}: {t.title} (assignee: {t.assignee_id}, status: {t.status})")

        response = AgentResponse(text="\n".join(lines))

        if not existing:
            action_id = self.new_action_id()
            response.proposed_actions.append(ProposedAction(
                id=action_id, kind="log_expense",
                description=f"Registar pedido de despesa de {speaker.name} ({amount:.2f} EUR) para aprovação",
                payload=dict(employee_id=speaker.id, amount_eur=amount, is_equipment=is_equipment,
                             approvers=decision.approvers),
            ))
            response.text += "\n\nPosso registar este pedido e encaminhar para aprovação. Confirmas?"
        else:
            response.text += "\n\nComo já há tarefas em aberto para este pedido, não vou criar uma nova — só sinalizar."
        return response

    # ------------------------------------------------------------- document
    def _handle_document(self, intent: nlu.Intent, text: str, speaker: Optional[Employee]) -> AgentResponse:
        t_lower = text.lower()
        docs = {
            "onboarding": self.store.onboarding_text,
            "acesso": self.store.access_policy_text,
            "acessos": self.store.access_policy_text,
            "despesa": self.store.expenses_policy_text,
            "despesas": self.store.expenses_policy_text,
        }
        for kw, content in docs.items():
            if kw in t_lower:
                return AgentResponse(text=f"Encontrei o documento de processo interno sobre '{kw}':\n\n{content}")

        # not one of our known process docs -- check if the requester (or
        # anyone) already has a task about producing/updating it
        keywords = [w for w in ["template", "proposta", "modelo"] if w in t_lower]
        related_tasks = self.store.find_tasks(keyword=" ".join(keywords)) if keywords else []
        if not related_tasks and speaker:
            related_tasks = [t for t in self.store.tasks_assigned_to(speaker.id)
                              if any(k in t.title.lower() for k in ["template", "proposta", "modelo"])]

        if related_tasks:
            lines = ["Não tenho acesso direto a esse documento (fora dos processos internos que conheço), "
                     "mas encontrei tarefas relacionadas:"]
            for t in related_tasks:
                owner_note = " — atribuída a ti" if speaker and t.assignee_id == speaker.id else f" — assignee: {t.assignee_id}"
                lines.append(f"  - {t.id}: {t.title} [{t.status}]{owner_note}" + (f" (due {t.due_date})" if t.due_date else ""))
            lines.append("\nSe a tarefa acima ainda está aberta, é provável que a versão mais recente ainda não exista — "
                          "sugiro confirmar com o assignee antes de usar qualquer versão antiga.")
            return AgentResponse(text="\n".join(lines))

        return AgentResponse(
            text="Não encontrei esse documento nos processos internos que conheço (onboarding, acessos, despesas), "
                 "nem tarefas relacionadas. Sugiro perguntar diretamente à equipa responsável.",
            missing_info=["localização do documento"],
        )
