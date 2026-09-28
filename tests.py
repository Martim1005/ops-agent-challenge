"""Small sanity test-suite for the deterministic parts of the agent
(policy engine + onboarding checklist). Uses stdlib unittest only.

Run with:  python -m unittest tests.py -v
"""
from __future__ import annotations

import unittest

from agent.data_store import DataStore
from agent import policies, onboarding
from agent.orchestrator import Agent


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = DataStore()

    def test_github_access_engineering_requires_only_em(self):
        diogo = self.store.get_employee("E004")
        d = policies.evaluate_access_request(self.store, diogo, "github", target_repo="api")
        self.assertIn("Helena Duarte (Engineering Manager)", d.approvers)
        self.assertTrue(d.is_ready)

    def test_github_access_non_engineering_requires_team_lead_and_em(self):
        olivia = self.store.get_employee("E014")  # Design, contractor
        d = policies.evaluate_access_request(self.store, olivia, "github")
        self.assertTrue(any("responsável da equipa" in a for a in d.approvers))
        self.assertTrue(any("Engineering Manager" in a for a in d.approvers))
        self.assertFalse(d.is_ready)  # contractor missing repo + expiration

    def test_production_write_never_auto_and_requires_reason(self):
        diogo = self.store.get_employee("E004")
        d = policies.evaluate_access_request(self.store, diogo, "production_write")
        self.assertFalse(d.auto_actionable)
        self.assertIn("motivo do acesso (obrigatório registar)", d.missing_info)

    def test_production_write_contractor_needs_head_of_operations_too(self):
        olivia = self.store.get_employee("E014")
        d = policies.evaluate_access_request(self.store, olivia, "production_write", reason="debug")
        self.assertTrue(any("Head of Operations" in a for a in d.approvers))

    def test_expense_under_100_only_manager(self):
        bruno = self.store.get_employee("E002")
        d = policies.evaluate_expense_request(self.store, bruno, 45.0, is_equipment=False)
        self.assertEqual(len(d.approvers), 1)

    def test_expense_over_500_needs_finance_manager_before_purchase(self):
        carolina = self.store.get_employee("E003")
        d = policies.evaluate_expense_request(self.store, carolina, 600.0)
        self.assertTrue(any("Finance Manager" in a for a in d.approvers))

    def test_equipment_always_needs_operations_regardless_of_amount(self):
        carolina = self.store.get_employee("E003")
        d = policies.evaluate_expense_request(self.store, carolina, 30.0, is_equipment=True)
        self.assertTrue(any("Operations" in a for a in d.approvers))


class OnboardingChecklistTests(unittest.TestCase):
    def setUp(self):
        self.store = DataStore()

    def test_nuno_ribeiro_checklist_flags_welcome_session_from_calendar(self):
        nuno = self.store.get_employee("E013")
        items = onboarding.build_checklist(self.store, nuno)
        welcome = next(i for i in items if "boas-vindas" in i.step.lower())
        self.assertEqual(welcome.status, "done")
        self.assertIn("calendário", welcome.detail)

    def test_nuno_ribeiro_1on1_flagged_for_confirmation_not_silently_trusted(self):
        nuno = self.store.get_employee("E013")
        items = onboarding.build_checklist(self.store, nuno)
        oneonone = next(i for i in items if "1:1" in i.step)
        self.assertEqual(oneonone.status, "check")  # generic title, must be confirmed


class OrchestratorSmokeTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(DataStore())

    def test_example_from_brief_returns_checklist(self):
        resp = self.agent.handle_message(
            "Vou receber um novo membro na equipa na próxima semana. O que falta preparar?",
            speaker_name="Gabriela Nunes",
        )
        self.assertEqual(resp.intent.kind, "onboarding_status")
        self.assertIn("Nuno Ribeiro", resp.text)

    def test_access_request_never_auto_executes(self):
        resp = self.agent.handle_message(
            "Preciso de acesso de escrita a produção, é urgente.", speaker_name="Diogo Ferreira"
        )
        self.assertEqual(resp.intent.kind, "access_request")
        # missing "reason" -> no action proposed yet, must ask first
        self.assertTrue(resp.missing_info)
        self.assertFalse(resp.proposed_actions)

    def test_confirming_unknown_action_id_is_safe(self):
        result = self.agent.confirm("does-not-exist", approved=True)
        self.assertIn("não encontrei", result.lower())


if __name__ == "__main__":
    unittest.main()
