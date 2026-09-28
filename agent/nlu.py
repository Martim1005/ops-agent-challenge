"""Turns a free-text message into a structured Intent the orchestrator can
act on.

This is the one place where using an LLM genuinely earns its keep: incoming
requests are unstructured Portuguese sentences written by different people in
different styles ("Preciso de um monitor...", "Alguém sabe onde está...",
"Conseguem confirmar?"). Mapping that into {intent, entities} is exactly the
kind of fuzzy, high-variance task LLMs are good at and rule-matching is
brittle at.

By default this runs on a small deterministic keyword/regex classifier so the
whole prototype works offline with zero API cost (per the brief). If an
ANTHROPIC_API_KEY is present in the environment AND the `anthropic` package
is installed, `classify()` instead asks Claude to return the same structured
shape -- same contract, better generalization to phrasing the keyword rules
don't cover. The rest of the agent never knows which path produced the
Intent.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

INTENTS = ["onboarding_status", "access_request", "expense_request", "document_lookup", "unknown"]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.lower()


@dataclass
class Intent:
    kind: str
    confidence: float
    entities: dict = field(default_factory=dict)
    source: str = "rules"  # "rules" | "llm"


_ACCESS_TYPE_KEYWORDS = {
    "github": ["github", "repositorio", "repo"],
    "production_write": ["producao", "escrita em producao", "producao urgente"],
    "finance": ["financeiro", "sistema financeiro", "financas"],
    "drive": ["drive"],
    "slack": ["slack"],
}

_EQUIPMENT_KEYWORDS = ["monitor", "portatil", "computador", "teclado", "rato", "ecra", "equipamento"]

_AMOUNT_RE = re.compile(r"(\d+[.,]?\d*)\s*(eur|€|euros)", re.IGNORECASE)


def _extract_amount(text: str) -> Optional[float]:
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _extract_access_type(norm_text: str) -> Optional[str]:
    if "producao" in norm_text and ("escrita" in norm_text or "acesso" in norm_text):
        return "production_write"
    for atype, kws in _ACCESS_TYPE_KEYWORDS.items():
        if any(kw in norm_text for kw in kws):
            return atype
    if "acesso" in norm_text:
        return "unspecified"
    return None


def _rule_based_classify(text: str, known_names: list[str]) -> Intent:
    n = _norm(text)
    scores = {k: 0 for k in INTENTS}
    entities: dict = {}

    if any(k in n for k in ["falta", "onboarding", "novo membro", "comeca", "começa", "entrada", "starting_soon", "entra ", "entram"]):
        scores["onboarding_status"] += 2
    if any(k in n for k in ["novo membro", "nova pessoa", "entram", "entra na equipa", "proxima semana"]) and "onboarding" not in n:
        scores["onboarding_status"] += 1

    if any(k in n for k in ["acesso", "github", "repositorio", "producao", "permissao", "permissoes"]):
        scores["access_request"] += 2
    if "revoga" in n:
        scores["access_request"] += 2
        entities["revocation"] = True

    if any(k in n for k in ["eur", "€", "despesa", "comprar", "compra", "orcamento", "reembolso"]) or _extract_amount(text):
        scores["expense_request"] += 2

    if any(k in n for k in ["template", "documento", "onde esta", "onde está", "modelo"]):
        scores["document_lookup"] += 2

    best_kind = max(scores, key=lambda k: scores[k])
    if scores[best_kind] == 0:
        best_kind = "unknown"
    confidence = min(0.55 + 0.15 * scores.get(best_kind, 0), 0.95) if best_kind != "unknown" else 0.2

    amount = _extract_amount(text)
    if amount is not None:
        entities["amount_eur"] = amount
    entities["is_equipment"] = any(k in n for k in _EQUIPMENT_KEYWORDS)
    if best_kind == "access_request":
        entities["access_type"] = _extract_access_type(n)
        entities["urgent"] = "urgente" in n
    if best_kind == "expense_request":
        entities["urgent"] = "urgente" in n or "risco" in n

    # is this about someone other than the sender? look for a known employee
    # first name mentioned in the text (e.g. "O Nuno começa segunda-feira").
    for name in known_names:
        first = name.split()[0]
        if re.search(rf"\b{re.escape(_norm(first))}\b", n):
            entities.setdefault("mentioned_employee", name)
            break

    return Intent(kind=best_kind, confidence=confidence, entities=entities, source="rules")


def _llm_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _llm_classify(text: str, known_names: list[str]) -> Optional[Intent]:
    """Optional enhancement path. Falls back to None (caller uses rules) on
    any failure -- the agent must never break just because an external API
    call failed."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        prompt = (
            "Classifica a seguinte mensagem interna de uma empresa numa destas intents: "
            f"{', '.join(INTENTS)}.\n"
            "Extrai também entidades quando existirem: amount_eur (numero), is_equipment (bool), "
            "access_type (um de slack/drive/github/production_write/finance/unspecified), "
            "mentioned_employee (nome de colaborador mencionado, se houver), urgent (bool), "
            "revocation (bool, se pedido de revogar acesso).\n"
            f"Colaboradores conhecidos: {', '.join(known_names)}.\n"
            'Responde APENAS com JSON no formato: {"kind": "...", "confidence": 0.0-1.0, "entities": {...}}\n\n'
            f"Mensagem: {text!r}"
        )
        resp = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(raw)
        if data.get("kind") not in INTENTS:
            return None
        return Intent(kind=data["kind"], confidence=float(data.get("confidence", 0.7)),
                      entities=data.get("entities", {}), source="llm")
    except Exception:
        return None


def classify(text: str, known_names: list[str]) -> Intent:
    if _llm_available():
        llm_result = _llm_classify(text, known_names)
        if llm_result is not None:
            return llm_result
    return _rule_based_classify(text, known_names)
