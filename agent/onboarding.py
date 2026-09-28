"""Builds an onboarding checklist for a given new hire by cross-referencing
tasks.json and calendar.json against the steps in onboarding.md.

This is the core of the example scenario from the brief ("o que falta
preparar?"). It is deliberately plain code: the checklist steps and how to
detect them are fixed business logic, not something we want an LLM
re-deriving (and possibly getting wrong) on every call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .data_store import DataStore, Employee, Task


@dataclass
class ChecklistItem:
    step: str
    owner: str
    status: str  # "done" | "open" | "missing" | "check"
    detail: str = ""


def _has_task_matching(tasks: list[Task], *keywords: str) -> Task | None:
    for t in tasks:
        title = t.title.lower()
        if any(k.lower() in title for k in keywords):
            return t
    return None


def build_checklist(store: DataStore, employee: Employee) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    related_tasks = store.tasks_for_employee(employee.id)
    manager = store.manager_of(employee)
    people_ops = store.find_role("people operations")

    # 1. People Ops confirms core data
    items.append(ChecklistItem(
        step="Confirmar nome, função, equipa, manager e data de entrada",
        owner=people_ops.name if people_ops else "People Operations",
        status="done" if employee.name and employee.manager_id and employee.start_date else "check",
        detail=f"{employee.name} · {employee.role} · {employee.team} · manager: {manager.name if manager else '—'} · entrada: {employee.start_date or '—'}",
    ))

    # 2. Operations creates email/Slack/Drive
    t = _has_task_matching(related_tasks, "email", "slack")
    drive_t = _has_task_matching(related_tasks, "drive")
    if t or drive_t:
        parts = [x for x in (t, drive_t) if x]
        status = "done" if all(p.status == "done" for p in parts) else "open"
        detail = "; ".join(f"{p.title} [{p.status}]" for p in parts)
        items.append(ChecklistItem("Criar conta de email, Slack e acesso a Drive", "Operations", status, detail))
    else:
        items.append(ChecklistItem("Criar conta de email, Slack e acesso a Drive", "Operations", "missing",
                                    "Nenhuma tarefa encontrada — sugerir criação."))

    # 3. Manager indicates specific access needs
    manager_access_tasks = [t for t in related_tasks if t.category == "access" and t.assignee_id == (manager.id if manager else None)]
    if manager_access_tasks:
        items.append(ChecklistItem("Manager define acessos específicos da função", manager.name if manager else "Manager",
                                    "open" if any(x.status == "open" for x in manager_access_tasks) else "done",
                                    "; ".join(f"{x.title} [{x.status}]" for x in manager_access_tasks)))
    else:
        items.append(ChecklistItem("Manager define acessos específicos da função", manager.name if manager else "Manager",
                                    "check", "Sem tarefa dedicada encontrada — confirmar diretamente com o manager."))

    # 4. GitHub access if Engineering
    if employee.team == "Engineering":
        gh = _has_task_matching(related_tasks, "github")
        if gh:
            items.append(ChecklistItem("Pedir acesso GitHub (política de acessos)", "Engineering Manager", gh.status, f"{gh.title} [{gh.status}]" + (f" — nota: {gh.notes}" if gh.notes else "")))
        else:
            items.append(ChecklistItem("Pedir acesso GitHub (política de acessos)", "Engineering Manager", "missing", "Nenhum pedido encontrado."))

    # 5. Equipment
    eq = _has_task_matching(related_tasks, "portátil", "computador", "equipamento", "laptop")
    if eq:
        items.append(ChecklistItem("Preparar equipamento (se aplicável à função)", "Operations", eq.status, f"{eq.title} [{eq.status}]"))
    else:
        items.append(ChecklistItem("Preparar equipamento (se aplicável à função)", "Operations", "check", "Sem tarefa — confirmar se a função exige equipamento próprio."))

    # 6. Welcome session (30 min) — check both tasks AND calendar, they can disagree
    welcome_task = _has_task_matching(related_tasks, "boas-vindas", "welcome")
    welcome_events = [e for e in store.events_for_employee(employee.id) if "boas-vindas" in e["title"].lower() or "welcome" in e["title"].lower()]
    if welcome_events:
        ev = welcome_events[0]
        detail = f"Agendada no calendário: {ev['title']} em {ev['start']}"
        if welcome_task and welcome_task.status != "done":
            detail += f" (tarefa '{welcome_task.title}' ainda marcada como '{welcome_task.status}' — atualizar)"
        items.append(ChecklistItem("Marcar sessão de boas-vindas (30min, 1º dia)", "People Operations", "done", detail))
    elif welcome_task:
        items.append(ChecklistItem("Marcar sessão de boas-vindas (30min, 1º dia)", "People Operations", welcome_task.status, welcome_task.title))
    else:
        items.append(ChecklistItem("Marcar sessão de boas-vindas (30min, 1º dia)", "People Operations", "missing", "Não encontrada em tarefas nem calendário."))

    # 7. Manager 1:1 within first 3 days
    if manager and employee.start_date:
        try:
            start = datetime.fromisoformat(employee.start_date)
            window_end = start + timedelta(days=3)
            mgr_events = store.events_for_employee(manager.id)
            one_on_ones = [
                e for e in mgr_events
                if "1:1" in e["title"].lower()
                and start <= datetime.fromisoformat(e["start"]).replace(tzinfo=None) <= window_end
            ]
            if one_on_ones:
                ev = one_on_ones[0]
                ambiguous = employee.name.split()[0].lower() not in ev["title"].lower()
                detail = f"{ev['title']} em {ev['start']}"
                if ambiguous:
                    detail += " — título genérico, confirmar que é com este novo colaborador."
                items.append(ChecklistItem("Manager marca 1:1 nos primeiros 3 dias", manager.name, "check" if ambiguous else "done", detail))
            else:
                items.append(ChecklistItem("Manager marca 1:1 nos primeiros 3 dias", manager.name, "missing",
                                            "Nenhuma reunião 1:1 encontrada na janela dos primeiros 3 dias."))
        except ValueError:
            items.append(ChecklistItem("Manager marca 1:1 nos primeiros 3 dias", manager.name, "check", "Data de entrada inválida."))
    else:
        items.append(ChecklistItem("Manager marca 1:1 nos primeiros 3 dias", manager.name if manager else "Manager", "check", "Falta manager ou data de entrada."))

    # 8. End-of-first-week confirmation by People Ops
    items.append(ChecklistItem(
        "People Operations confirma conclusão de todas as tarefas obrigatórias",
        people_ops.name if people_ops else "People Operations",
        "done" if all(i.status == "done" for i in items) else "open",
        "Depende da conclusão dos passos anteriores.",
    ))

    if employee.is_contractor:
        items.insert(1, ChecklistItem(
            "Contractor: validar acessos com o responsável do projeto e definir data de expiração",
            "Responsável do projeto",
            "check",
            "Contractors não recebem email/equipamento automaticamente; acessos limitados ao necessário.",
        ))

    return items


def summarize(items: list[ChecklistItem]) -> str:
    done = sum(1 for i in items if i.status == "done")
    total = len(items)
    lines = [f"Progresso: {done}/{total} passos concluídos.\n"]
    icons = {"done": "[OK]", "open": "[EM CURSO]", "missing": "[EM FALTA]", "check": "[VERIFICAR]"}
    for i in items:
        lines.append(f"{icons.get(i.status, '[?]')} {i.step} (owner: {i.owner})")
        if i.detail:
            lines.append(f"      {i.detail}")
    return "\n".join(lines)
