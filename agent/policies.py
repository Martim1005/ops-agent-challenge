"""Deterministic policy rules, hand-encoded from access_policy.md and
expenses_policy.md.

Why this is plain code and not an LLM call: approvals and compliance rules
must be consistent, auditable and impossible to talk a model out of. An LLM
reading the policy text on every request could paraphrase, drift, or miss an
edge case; a hard-coded rule always applies the same way and is trivial to
unit-test. The LLM's job (see nlu.py) is to get the request INTO structured
form and turn the decision back OUT into natural language -- never to decide
what the policy says.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data_store import DataStore, Employee


@dataclass
class Decision:
    action: str
    approvers: list[str] = field(default_factory=list)  # human-readable names/roles
    missing_info: list[str] = field(default_factory=list)
    auto_actionable: bool = False  # can Operations just do it, or does it need named sign-off first
    notes: list[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return not self.missing_info


def evaluate_access_request(
    store: DataStore,
    requester: Employee,
    access_type: str,  # "slack" | "drive" | "github" | "production_write" | "finance"
    target_repo: str | None = None,
    expiration_date: str | None = None,
    reason: str | None = None,
) -> Decision:
    d = Decision(action=f"access:{access_type}")

    if access_type in ("slack", "drive"):
        if requester.is_contractor:
            d.notes.append(
                "Contractors não recebem acessos standard automaticamente; "
                "definir apenas o que for necessário para o projeto, com data de expiração."
            )
            d.missing_info.append("data de expiração do acesso")
        else:
            d.approvers = ["Operations"]
            d.auto_actionable = True
            d.notes.append("Acesso standard para full-time, disponível após confirmação de onboarding por People Operations.")
        return d

    if access_type == "github":
        em = store.engineering_manager()
        if requester.team == "Engineering":
            d.approvers = [f"{em.name} (Engineering Manager)"] if em else ["Engineering Manager"]
        else:
            team_lead = store.manager_of(requester)
            approvers = []
            if team_lead:
                approvers.append(f"{team_lead.name} (responsável da equipa)")
            if em:
                approvers.append(f"{em.name} (Engineering Manager)")
            d.approvers = approvers or ["responsável da equipa", "Engineering Manager"]
        if requester.is_contractor:
            if not target_repo:
                d.missing_info.append("repositório específico necessário")
            if not expiration_date:
                d.missing_info.append("data de expiração do acesso")
        elif not target_repo:
            # Not a hard requirement for employees per policy, but a repo-less
            # "general GitHub access" request has no clear scope to approve.
            d.notes.append("Pedido não especifica repositório/necessidade de projeto concreta.")
            d.missing_info.append("repositório ou necessidade de projeto específica")
        return d

    if access_type == "production_write":
        em = store.engineering_manager()
        d.approvers = [f"{em.name} (Engineering Manager)"] if em else ["Engineering Manager"]
        if requester.is_contractor:
            hoo = store.head_of_operations()
            d.approvers.append(f"{hoo.name} (Head of Operations)" if hoo else "Head of Operations")
        if not reason:
            d.missing_info.append("motivo do acesso (obrigatório registar)")
        d.notes.append("Acesso de escrita a produção nunca é automático, mesmo em pedidos urgentes.")
        return d

    if access_type == "finance":
        fm = store.finance_manager()
        d.approvers = [f"{fm.name} (Finance Manager)"] if fm else ["Finance Manager"]
        return d

    d.notes.append(f"Tipo de acesso desconhecido: {access_type}")
    d.missing_info.append("tipo de acesso reconhecido")
    return d


def evaluate_expense_request(
    store: DataStore,
    requester: Employee,
    amount_eur: float,
    is_equipment: bool = False,
    is_urgent_operational_risk: bool = False,
) -> Decision:
    d = Decision(action="expense")
    manager = store.manager_of(requester)
    manager_label = f"{manager.name} (manager)" if manager else "manager"

    if amount_eur <= 100:
        d.approvers = [manager_label]
    elif amount_eur <= 500:
        d.approvers = [manager_label]
        d.notes.append("Deve ficar registada no sistema de despesas.")
    else:
        fm = store.finance_manager()
        d.approvers = [manager_label, f"{fm.name} (Finance Manager)" if fm else "Finance Manager"]
        d.notes.append("Aprovação necessária ANTES da compra (valor acima de 500 EUR).")

    if is_equipment:
        d.approvers.append("Operations (validação de equipamento, obrigatória independentemente do valor)")

    if is_urgent_operational_risk:
        d.notes.append(
            "Exceção de urgência: pode ser realizada antes da aprovação por risco operacional imediato, "
            "mas deve ser justificada e regularizada em 2 dias úteis."
        )
    else:
        d.notes.append("Sem indicação de risco operacional imediato: seguir aprovação normal antes da compra.")

    return d
