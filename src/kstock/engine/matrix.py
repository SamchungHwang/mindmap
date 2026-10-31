"""대응 결정 매트릭스 (템플릿 §4.5).

입력은 세 가지뿐이다: 논제 상태, 밸류 상태, 비중 상태. 매입가·평가손익은 입력이 아니다.
모든 종목에 동일하게 적용되는 공통 규칙 테이블이며, 종목별로 바꾸려면 오버라이드 사유를 남겨야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    Action,
    InvestType,
    Profile,
    Settings,
    ThesisState,
    ValuationState,
    WeightState,
    get_profile,
)


@dataclass(frozen=True)
class MatrixResult:
    action: Action
    rule: str  # 적용된 매트릭스 행
    note: str = ""

    def __str__(self) -> str:
        return f"{self.action.value}  [{self.rule}]" + (f"  — {self.note}" if self.note else "")


def decide(
    thesis: ThesisState | str | None,
    valuation: ValuationState | str | None,
    weight: WeightState | str | None,
    *,
    profile: Profile | InvestType | str | None = None,
    settings: Settings | None = None,
    no_thesis: bool = False,
    relative_edge: float | None = None,
    switch_cost: float = 0.0,
    event_imminent: bool = False,
    trend_intact: bool = True,
) -> MatrixResult:
    """§4.5 표를 위에서부터 순서대로 적용한다."""
    s = settings or Settings()
    prof = profile if isinstance(profile, Profile) else get_profile(profile)
    ts = ThesisState(thesis) if thesis else None
    vs = ValuationState(valuation) if valuation else None
    ws = WeightState(weight) if weight else None

    # 논제 없는 보유
    if no_thesis:
        return MatrixResult(Action.review, "논제 없는 보유", "논제 복원 → 재판정. 복원 실패 시 exit 후보")

    # 무효 / 변질 → exit (손실 크기 무관)
    if ts in (ThesisState.무효, ThesisState.변질):
        return MatrixResult(Action.exit, f"논제 {ts.value}", "손실 크기 무관. 세금·유동성 사유로 분할 매도만 허용")

    # 약화 → trim 또는 hold + 조건
    if ts == ThesisState.약화:
        if ws == WeightState.과다 or vs == ValuationState.고평가:
            return MatrixResult(Action.trim, "논제 약화 × (과다|고평가)", "B7 에 확인 규칙 추가, next_check 단축")
        return MatrixResult(Action.hold, "논제 약화", "hold + 확인 조건: B7 에 사실 기준 규칙 추가, next_check 단축")

    # 이벤트 임박 → hedge (선택)
    if event_imminent:
        return MatrixResult(Action.hedge, "이벤트 임박", "이벤트 전 노출 조절 (선택)")

    # 강화 / 유효 (None 은 미판정 — 유효로 간주하되 결과에 표시)
    if ts in (ThesisState.강화, ThesisState.유효, None):
        if ts is None:
            r = decide(ThesisState.유효, vs, ws, profile=prof, settings=s, relative_edge=relative_edge,
                       switch_cost=switch_cost, event_imminent=event_imminent, trend_intact=trend_intact)
            return MatrixResult(r.action, r.rule + " (논제 상태 미판정)", "B2.3 논제 상태를 먼저 판정하라. " + r.note)
        # switch: 대안의 기대수익이 명확히 높고 비용을 넘을 때 (유효일 때만)
        if (ts == ThesisState.유효 and relative_edge is not None
                and relative_edge < -s.switch_threshold and abs(relative_edge) > switch_cost):
            return MatrixResult(Action.switch, "유효 × 대안 우위",
                                f"상대 우위 {relative_edge:+.1%} < -{s.switch_threshold:.0%}, 비용 {switch_cost:.1%}")
        if vs == ValuationState.저평가:
            if ws == WeightState.과다:
                return MatrixResult(Action.hold, "유효 × 저평가 × 과다", "비중 상한 유지, 추가 금지")
            return MatrixResult(Action.add, "유효 × 저평가 × (과소|적정)", "A7.1 재계산 비중까지")
        if vs == ValuationState.적정 or vs is None:
            if ws == WeightState.과소:
                return MatrixResult(Action.add, "유효 × 적정 × 과소", "소폭 add (프로파일 기본값)")
            if ws == WeightState.과다:
                return MatrixResult(Action.trim, "유효 × 적정 × 과다", "목표 비중까지")
            return MatrixResult(Action.hold, "유효 × 적정 × 적정", "next_check 반드시 지정")
        if vs == ValuationState.고평가:
            if ws == WeightState.과다:
                return MatrixResult(Action.trim, "유효 × 고평가 × 과다")
            if prof.invest_type == InvestType.momentum and trend_intact:
                return MatrixResult(Action.hold, "유효 × 고평가 (모멘텀, 추세 유지)", "추세 꺾이면 trim")
            return MatrixResult(prof.overvalued_default, "유효 × 고평가 × (과소|적정)",
                                f"프로파일 기본 대응 {prof.overvalued_default.value}")
    return MatrixResult(Action.review, "매트릭스 미정의", f"({thesis}, {valuation}, {weight})")


__all__ = ["MatrixResult", "decide"]
