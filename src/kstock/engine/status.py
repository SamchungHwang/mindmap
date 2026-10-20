"""상태등 산출 (템플릿 §4.3). 규칙 기반 — LLM 이 아니라 코드가 결정한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..models import (
    Direction,
    HoldingMap,
    Light,
    NodeStatus,
    Profile,
    ReconstructionQuality,
    Settings,
    ThesisState,
    ValuationState,
    WeightState,
    get_profile,
)
from .rules import FiredRule, RuleContext, evaluate_rules


@dataclass
class LightResult:
    light: Light
    reasons: list[str] = field(default_factory=list)
    fired: list[FiredRule] = field(default_factory=list)

    def __str__(self) -> str:
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}[self.light.value]
        return f"{icon} {self.light.value}: " + ("; ".join(self.reasons) if self.reasons else "트리거 없음")


def mark_stale(m: HoldingMap, today: date, stale_days: int) -> list[str]:
    """as_of + stale_days < today 인 fact/estimate 노드를 stale 로 전환. 전환된 id 목록 반환."""
    out: list[str] = []
    for n in m.nodes.values():
        if n.node_type in ("fact", "estimate") and n.status != NodeStatus.stale and n.is_stale(today, stale_days):
            n.status = NodeStatus.stale
            out.append(n.id)
    return out


def compute_light(m: HoldingMap, ctx: RuleContext | None = None, *, today: date | None = None,
                  settings: Settings | None = None, profile: Profile | None = None,
                  regime_changed: bool = False, fire_rules: bool = True) -> LightResult:
    """§4.3 의 RED / YELLOW / GREEN 규칙을 순서대로 적용한다."""
    today = today or date.today()
    settings = settings or Settings()
    profile = profile or get_profile(m.profile)
    ctx = ctx or RuleContext.from_holding(m, today)

    red: list[str] = []
    yellow: list[str] = []

    # ---- 규칙 평가
    fired = evaluate_rules(m, ctx, fire=fire_rules)
    for f in fired:
        if f.reason.startswith("조건식 오류"):
            yellow.append(f"{f.node_id} {f.reason}")
        elif f.node_id.startswith("B1.4"):
            red.append(f"무효화 조건 충족: {f.node_id} ({f.rule.condition})")
        elif f.rule.escalate_to == Light.RED:
            red.append(f"규칙 발동(RED): {f.node_id} ({f.rule.condition})")
        elif f.rule.escalate_to == Light.YELLOW:
            yellow.append(f"규칙 발동: {f.node_id} ({f.rule.condition})")

    # ---- RED
    ts = m.thesis_state
    if ts in (ThesisState.무효, ThesisState.변질):
        red.append(f"논제 상태 {ts.value}")

    # ---- YELLOW
    if ts == ThesisState.약화:
        yellow.append("논제 약화")
    weak = [n.id for n in m.drivers() if n.driver and n.driver.direction == Direction.약화]
    if weak:
        yellow.append("드라이버 약화: " + ", ".join(weak))
    ws = m.value("B4.1")
    if ws in (WeightState.과소.value, WeightState.과다.value):
        yellow.append(f"비중 {ws}")
    vs = m.value("B3.6")
    if vs == ValuationState.고평가.value and ws == WeightState.과다.value:
        yellow.append("고평가 + 비중 과다")
    if ctx.drawdown is not None and ctx.drawdown > profile.dd_threshold:
        yellow.append(f"고점 대비 -{ctx.drawdown:.0%} (임계 {profile.dd_threshold:.0%})")
    stale = [n.id for n in m.nodes.values() if n.status == NodeStatus.stale]
    if stale:
        yellow.append(f"stale 노드 {len(stale)}개")
    if m.reconstruction == ReconstructionQuality.불가:
        yellow.append("논제 없는 보유")
    bias = [nid for nid in ("B5.1", "B5.2", "B5.3", "B5.4", "B5.5", "B5.6") if m.value(nid) is True]
    if bias:
        yellow.append("편향 체크 양성: " + ", ".join(bias))
    if regime_changed and settings.regime_change_flags_yellow:
        yellow.append("매크로 레짐 변경 이후 미검토")
    due = [n.id for n in m.drivers() if n.driver and n.driver.next_check and n.driver.next_check < today]
    if due:
        yellow.append("확인일 경과: " + ", ".join(due))

    if red:
        res = LightResult(Light.RED, red + yellow, fired)
    elif yellow:
        res = LightResult(Light.YELLOW, yellow, fired)
    else:
        res = LightResult(Light.GREEN, [], fired)
    m["B0"].set(res.light.value, as_of=today)
    return res


__all__ = ["LightResult", "compute_light", "mark_stale"]
