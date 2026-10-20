"""검증 규칙 (템플릿 §3.3 완료 조건, §10 시스템이 강제할 것)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from ..models import HoldingMap, MindMap, NewMap, NodeStatus, NodeType, get_profile
from .rules import validate_condition

META_ONLY_IDS = {"B0.5"}


@dataclass(frozen=True)
class Issue:
    level: str  # error | warn
    node_id: str | None
    message: str

    def __str__(self) -> str:
        tag = "ERR " if self.level == "error" else "WARN"
        return f"[{tag}] {self.node_id or '-'}: {self.message}"


def _errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


# --------------------------------------------------------------------------- common
def validate_common(m: MindMap, today: date | None = None) -> list[Issue]:
    issues: list[Issue] = []
    today = today or date.today()
    prof = get_profile(m.profile) if m.profile else None
    for n in m.nodes.values():
        # 부모 존재
        if n.parent is not None and n.parent not in m.nodes:
            issues.append(Issue("error", n.id, f"parent {n.parent} 가 없다"))
        # fact 는 출처 필수 (없으면 unverified 강제 — 모델이 이미 강제하지만 값이 있는데 출처가 없으면 경고)
        if n.node_type == NodeType.fact and n.value is not None and n.source is None and not n.meta_only:
            issues.append(Issue("warn", n.id, "fact 노드에 출처가 없다 → unverified"))
        # computed 는 formula 필수
        if n.node_type == NodeType.computed and not n.formula:
            issues.append(Issue("error", n.id, "computed 노드에 formula 가 없다"))
        # meta_only 의존 금지: formula / links 에 메타 노드를 참조하면 오류
        if n.node_type in (NodeType.computed, NodeType.judgment):
            refs = set(n.links)
            if n.formula:
                refs |= {t for t in META_ONLY_IDS if t in n.formula}
            bad = [r for r in refs if r in META_ONLY_IDS or (r in m.nodes and m.nodes[r].meta_only)]
            if bad:
                issues.append(Issue("error", n.id, f"meta_only 노드 {bad} 를 판단 입력으로 참조"))
        # rule 조건식 문법
        if n.rule is not None:
            for e in validate_condition(n.rule.condition):
                issues.append(Issue("error", n.id, f"조건식 오류: {e}"))
        # stale 자동 전환 (프로파일 있을 때)
        if prof and n.node_type in (NodeType.fact, NodeType.estimate) and n.status != NodeStatus.stale \
                and n.is_stale(today, prof.stale_days):
            issues.append(Issue("warn", n.id, f"as_of {n.as_of} 가 {prof.stale_days}일을 넘어 stale"))
    return issues


# --------------------------------------------------------------------------- A
def validate_new_map(m: NewMap, today: date | None = None) -> list[Issue]:
    issues = validate_common(m, today)
    # A0 논제
    if not m.thesis:
        issues.append(Issue("error", "A0", "투자 논제가 비어 있다"))
    # A1 드라이버 3~5개, current & my_estimate
    drivers = m.drivers()
    if len(drivers) < 3:
        issues.append(Issue("error", "A1", f"드라이버가 {len(drivers)}개 — 3개 이상 필요"))
    if len(drivers) > 5:
        issues.append(Issue("warn", "A1", f"드라이버가 {len(drivers)}개 — 5개 이하 권장 (핵심만)"))
    for n in drivers:
        d = n.driver
        assert d is not None
        if d.current.value is None:
            issues.append(Issue("error", n.id, "driver.current 가 없다"))
        if d.my_estimate.value is None:
            issues.append(Issue("error", n.id, "driver.my_estimate 가 없다"))
    # A3 시나리오 확률 합
    sc = m.scenarios()
    missing = {"Bear", "Base", "Bull"} - set(sc)
    if missing:
        issues.append(Issue("error", "A3", f"시나리오 누락: {sorted(missing)}"))
    else:
        psum = sum(s.probability for s in sc.values())
        if abs(psum - 1.0) > 0.01:
            issues.append(Issue("error", "A3", f"시나리오 확률 합 {psum:.3f} ≠ 1.0"))
        if not (sc["Bear"].target_price < sc["Base"].target_price < sc["Bull"].target_price):
            issues.append(Issue("warn", "A3", "목표가 순서가 Bear < Base < Bull 이 아니다"))
    if m.current_price is None:
        issues.append(Issue("error", "A0.4", "현재가가 없다"))
    # A4.4 kill criteria ≥ 1 (fact 기준)
    kills = [r for r in m.kill_rules() if r.trigger_type == "fact"]
    if not kills:
        issues.append(Issue("error", "A4.4", "사실 기준 Kill criteria 가 1개 이상 필요"))
    # A6.5 판단
    if m.decision is None:
        issues.append(Issue("error", "A6.5", "판단(BUY/WATCH/PASS)이 없다"))
    return issues


def is_complete(m: NewMap, today: date | None = None) -> bool:
    return not _errors(validate_new_map(m, today))


# --------------------------------------------------------------------------- B
def snapshot_hash(m: HoldingMap) -> str:
    """B1.1~B1.4 (+하위) 의 내용 해시 — 불변성 검사."""
    payload = []
    for nid in sorted(m.nodes):
        if nid.startswith(("B1.1", "B1.2", "B1.3", "B1.4")):
            n = m.nodes[nid]
            payload.append(n.model_dump(mode="json", include={"id", "value", "rule", "links"}))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def validate_holding_map(m: HoldingMap, today: date | None = None) -> list[Issue]:
    issues = validate_common(m, today)
    # 스냅숏 불변성
    if m.snapshot_hash and snapshot_hash(m) != m.snapshot_hash:
        issues.append(Issue("error", "B1", "B1.1~B1.4 스냅숏이 수정되었다 (불변)"))
    if m.value("B1.5") is None:
        issues.append(Issue("error", "B1.5", "복원 품질이 없다"))
    # B7 사실 기준 규칙 ≥ 1
    fact_rules = [n for n in m.rules() + m.children("B1.4") if n.rule and n.rule.trigger_type == "fact"]
    if not fact_rules and not m.no_thesis:
        issues.append(Issue("warn", "B7", "사실 기준(fact) 규칙이 없다 → 상태등 최소 🟡"))
    # B6 오버라이드 사유
    last = m.decision_log[-1] if m.decision_log else None
    if last and last.matrix_default and last.decision != last.matrix_default and not (last.override and last.reason):
        issues.append(Issue("error", "B6", "매트릭스 기본값과 다른 결정에는 override=true 와 사유가 필요"))
    # 시나리오 확률 합 (있을 때만)
    sc = m.scenarios()
    if sc:
        psum = sum(s.probability for s in sc.values())
        if abs(psum - 1.0) > 0.01:
            issues.append(Issue("error", "B3", f"시나리오 확률 합 {psum:.3f} ≠ 1.0"))
    # 핵심 질문
    if m.value("B0.1") is None:
        issues.append(Issue("warn", "B0.1", "'오늘 현금으로 새로 사겠는가' 에 답하지 않았다"))
    elif m.value("B0.1") is False and m.value("B0.2") is None:
        issues.append(Issue("error", "B0.2", "새로 사지 않을 종목의 보유 이유가 없다"))
    return issues


def validate(m: MindMap, today: date | None = None) -> list[Issue]:
    if isinstance(m, NewMap):
        return validate_new_map(m, today)
    if isinstance(m, HoldingMap):
        return validate_holding_map(m, today)
    return validate_common(m, today)


def has_errors(issues: Iterable[Issue]) -> bool:
    return bool(_errors(issues))


__all__ = ["Issue", "validate", "validate_common", "validate_new_map", "validate_holding_map",
           "is_complete", "snapshot_hash", "has_errors", "META_ONLY_IDS"]
