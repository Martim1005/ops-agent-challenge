"""Loads the simulated company data and gives the agent typed, queryable access to it.

This module is pure "traditional logic": deterministic loading, lookup and
filtering of the JSON files that stand in for the company's real systems
(HRIS, task tracker, calendar). Nothing here is AI — it doesn't need to be.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "ai_agent_operacoes_dados"
STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _norm(text: str) -> str:
    return _strip_accents(text or "").lower().strip()


@dataclass
class Employee:
    id: str
    name: str
    team: str
    role: str
    manager_id: Optional[str]
    location: str
    employment_type: str
    status: str
    start_date: Optional[str] = None

    @property
    def is_contractor(self) -> bool:
        return self.employment_type == "contractor"


@dataclass
class Task:
    id: str
    title: str
    category: str
    assignee_id: str
    status: str
    related_employee_id: Optional[str] = None
    due_date: Optional[str] = None
    completed_at: Optional[str] = None
    amount_eur: Optional[float] = None
    notes: Optional[str] = None
    source: str = "seed"  # "seed" = came from tasks.json, "agent" = created by the agent

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        return d


class DataStore:
    """Read-only access to the original files, plus an in-memory + on-disk
    log of anything the agent creates or changes, so the original seed data
    (tasks.json, calendar.json, ...) is never mutated on disk."""

    def __init__(self, data_dir: Path = DATA_DIR, state_dir: Path = STATE_DIR):
        self.data_dir = data_dir
        self.state_dir = state_dir
        self.state_dir.mkdir(exist_ok=True)

        self.employees: list[Employee] = [
            Employee(**e) for e in self._load_json("employees.json")
        ]
        self.tasks: list[Task] = [Task(**t) for t in self._load_json("tasks.json")]
        self.calendar_raw = self._load_json("calendar.json")
        self.messages = self._load_json("messages.json")

        self.access_policy_text = self._load_text("access_policy.md")
        self.expenses_policy_text = self._load_text("expenses_policy.md")
        self.onboarding_text = self._load_text("onboarding.md")

        self.action_log: list[dict] = self._load_action_log()
        self._next_task_seq = 200  # agent-created tasks get ids A200, A201, ...

    # ---------- loading ----------
    def _load_json(self, name: str):
        with open(self.data_dir / name, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_text(self, name: str) -> str:
        with open(self.data_dir / name, "r", encoding="utf-8") as f:
            return f.read()

    def _load_action_log(self) -> list[dict]:
        log_path = self.state_dir / "action_log.json"
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _persist_action_log(self):
        with open(self.state_dir / "action_log.json", "w", encoding="utf-8") as f:
            json.dump(self.action_log, f, ensure_ascii=False, indent=2)

    # ---------- employees ----------
    def get_employee(self, employee_id: str) -> Optional[Employee]:
        return next((e for e in self.employees if e.id == employee_id), None)

    def find_employees_by_name(self, name: str) -> list[Employee]:
        target = _norm(name)
        if not target:
            return []
        matches = []
        for e in self.employees:
            tokens = _norm(e.name).split()
            if target == _norm(e.name) or any(t.startswith(target) or target.startswith(t) for t in tokens):
                matches.append(e)
        return matches

    def manager_of(self, employee: Employee) -> Optional[Employee]:
        if not employee.manager_id:
            return None
        return self.get_employee(employee.manager_id)

    def find_role(self, name_contains: str, team: Optional[str] = None) -> Optional[Employee]:
        needle = _norm(name_contains)
        for e in self.employees:
            if needle in _norm(e.role) and (team is None or e.team == team):
                return e
        return None

    def engineering_manager(self) -> Optional[Employee]:
        return self.find_role("engineering manager")

    def finance_manager(self) -> Optional[Employee]:
        return self.find_role("finance manager")

    def head_of_operations(self) -> Optional[Employee]:
        return self.find_role("head of operations")

    def upcoming_starters(self) -> list[Employee]:
        return [e for e in self.employees if e.status == "starting_soon"]

    # ---------- tasks ----------
    def tasks_for_employee(self, employee_id: str) -> list[Task]:
        return [t for t in self.tasks if t.related_employee_id == employee_id]

    def tasks_assigned_to(self, employee_id: str) -> list[Task]:
        return [t for t in self.tasks if t.assignee_id == employee_id]

    def find_tasks(self, category: Optional[str] = None, keyword: Optional[str] = None) -> list[Task]:
        results = self.tasks
        if category:
            results = [t for t in results if t.category == category]
        if keyword:
            k = _norm(keyword)
            results = [t for t in results if k in _norm(t.title) or k in _norm(t.notes or "")]
        return results

    def create_task(self, *, title: str, category: str, assignee_id: str,
                     related_employee_id: Optional[str] = None,
                     due_date: Optional[str] = None, notes: Optional[str] = None,
                     amount_eur: Optional[float] = None) -> Task:
        task_id = f"A{self._next_task_seq}"
        self._next_task_seq += 1
        task = Task(
            id=task_id, title=title, category=category, assignee_id=assignee_id,
            status="open", related_employee_id=related_employee_id, due_date=due_date,
            notes=notes, amount_eur=amount_eur, source="agent",
        )
        self.tasks.append(task)
        self._append_log("create_task", task.to_dict())
        return task

    # ---------- calendar ----------
    def events_for_employee(self, employee_id: str) -> list[dict]:
        events = [e for e in self.calendar_raw.get("events", []) if e["employee_id"] == employee_id]
        return sorted(events, key=lambda e: e["start"])

    def find_free_slot(self, employee_ids: list[str], after: datetime, before: datetime,
                        duration_minutes: int = 30, work_start_hour: int = 9,
                        work_end_hour: int = 18) -> Optional[tuple[datetime, datetime]]:
        """Very small deterministic scheduler: walks half-hour steps in the
        window and returns the first slot with no conflicts for anyone."""
        busy = []
        for eid in employee_ids:
            for ev in self.events_for_employee(eid):
                busy.append((datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])))

        step = after
        from datetime import timedelta
        while step + timedelta(minutes=duration_minutes) <= before:
            if work_start_hour <= step.hour < work_end_hour and step.weekday() < 5:
                slot_end = step + timedelta(minutes=duration_minutes)
                conflict = any(s < slot_end and slot_end_b > step for s, slot_end_b in busy)
                if not conflict:
                    return step, slot_end
            step += timedelta(minutes=30)
        return None

    # ---------- action log (simulated pending approvals / audit trail) ----------
    def _append_log(self, action_type: str, payload: dict, status: str = "executed"):
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "type": action_type,
            "status": status,
            "payload": payload,
        }
        self.action_log.append(entry)
        self._persist_action_log()
        return entry

    def log_pending_approval(self, action_type: str, payload: dict) -> dict:
        return self._append_log(action_type, payload, status="pending_approval")

    def log_executed(self, action_type: str, payload: dict) -> dict:
        return self._append_log(action_type, payload, status="executed")
