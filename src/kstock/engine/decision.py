"""설계 v7 묶음 실행 — 성립 조건(T6) · 필요 경로(T5) · 역할·띠·경계 제안(T5·T7) · 증거 계획·일정·신호등(T7) · 규칙 평가·플래그(T8).
draft · 편집 저장 · CLI · 콘솔이 같이 부른다 (compute_valuation_all 뒤에)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import MindMap, NewMap
from .evidence import (
    apply_proposed_bounds,
    evidence_plan,
    evidence_schedule,
    light_counts_text,
    propose_roles,
)
from .path import ensure_v7_nodes, required_paths, write_conditions
from .revision import compute_flags, evaluate_rules


def compute_v7_all(m: MindMap, *, today: date | None = None, sector: str | None = None, price: float | None = None) -> list[str]:
    """돌려주는 것은 메모 줄들 (초안 요약·편집 메모에 쓴다)."""
    if not isinstance(m, NewMap):
        return []
    today = today or date.today()
    notes: list[str] = []
    made = ensure_v7_nodes(m)
    if made:
        notes.append("v7 노드 생성: " + ", ".join(made))
    sector = sector or (m.meta.get("draft") or {}).get("sector") or m.meta.get("sector")
    notes += propose_roles(m, sector)
    notes += required_paths(m, today=today)
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result":
            continue
        if d.bound_bear is None and d.band is None:
            pr = apply_proposed_bounds(m, n.id)
            if pr.get("status") == "제안":
                notes.append(f"{n.id} 띠·경계 제안: {pr['note']}")
    write_conditions(m, today=today, price=price)
    rows = evidence_plan(m, today=today, write=True)
    evidence_schedule(m, today=today)
    changed = evaluate_rules(m, today=today)
    for e in changed:
        notes.append(f"규칙 이벤트 {e['id']} {e['rule_label']}: {e['status']}")
    flags = compute_flags(m, rows)
    notes.append("신호등: " + light_counts_text((m.meta.get("draft") or {}).get("lights") or {}) + (" · 증거 없는 판단" if flags.get("no_evidence") else "")
                 + (" · 신규 BUY 보류" if flags.get("hold_new_buy") else ""))
    return notes


def v7_summary(m: NewMap) -> dict[str, Any]:
    draft = m.meta.get("draft") or {}
    return {"lights": draft.get("lights") or {}, "flags": m.meta.get("flags") or {}, "schedule": (draft.get("evidence_schedule") or [])[:5],
            "pending": [e for e in (draft.get("rule_events") or []) if e.get("status") == "proposed"]}


__all__ = ["compute_v7_all", "v7_summary"]
