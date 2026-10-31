"""투자 유형 프로파일 (템플릿 §8) 과 전역 임계값 설정."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .common import Action, Confidence, InvestType


class Settings(BaseModel):
    """맵·엔진 공통 임계값. 모두 조정 가능 (§3.4, §4.4, §5.2)."""

    model_config = ConfigDict(extra="forbid")

    # A7.1 비중 제안식
    w_base: float = 0.05
    w_max: float = 0.15
    f_conf: dict[str, float] = Field(default_factory=lambda: {"H": 1.5, "M": 1.0, "L": 0.5})
    f_rr_divisor: float = 2.0
    f_rr_clamp: tuple[float, float] = (0.5, 1.5)
    # B4.1 비중 밴드 — 둘 중 먼저 걸리는 쪽(min)
    dev_abs: float = 0.03
    dev_rel: float = 0.30
    # B3.6 밸류 상태 (역사 밴드 백분위)
    band_low: float = 30.0
    band_high: float = 70.0
    # B6 switch
    switch_threshold: float = 0.10
    # C
    hhi_warn: float = 0.10
    top3_warn: float = 0.50
    participation_rate: float = 0.10  # B4.5 분할 매도 참여율
    # 수익 분해
    decomp_metric: str = "eps"  # eps | sales | ebitda
    # 드라이버 방향 판정 허용 오차
    driver_tol_rel: float = 0.05
    # M6 레짐 변경 시 모든 B 맵 🟡
    regime_change_flags_yellow: bool = True

    def f_conf_of(self, c: Confidence | str | None) -> float:
        if c is None:
            return self.f_conf["M"]
        return self.f_conf.get(str(c), self.f_conf["M"])


class Profile(BaseModel):
    """투자 유형별 기본값 (§8)."""

    model_config = ConfigDict(extra="forbid")

    invest_type: InvestType
    driver_examples: list[str]
    w_flow_tech: float  # A6.2 수급/기술 weight
    w_evidence: float  # A9 근거 자료 weight
    review_cadence: str
    stale_days: int
    dd_threshold: float
    overvalued_default: Action
    rr_min: float
    rr_upside: str = Field(default="base", pattern="^(base|bull)$")
    flow_role: str = ""


PROFILES: dict[InvestType, Profile] = {
    InvestType.growth: Profile(
        invest_type=InvestType.growth,
        driver_examples=["매출성장률", "TAM 침투율", "마진 레버리지"],
        w_flow_tech=0.2, w_evidence=0.6, review_cadence="분기", stale_days=90,
        dd_threshold=0.25, overvalued_default=Action.trim, rr_min=2.0, rr_upside="base",
        flow_role="진입 타이밍",
    ),
    InvestType.value: Profile(
        invest_type=InvestType.value,
        driver_examples=["FCF 수익률", "자산가치 할인율", "주주환원"],
        w_flow_tech=0.1, w_evidence=0.8, review_cadence="반기", stale_days=180,
        dd_threshold=0.20, overvalued_default=Action.trim, rr_min=1.5, rr_upside="base",
        flow_role="거의 무시",
    ),
    InvestType.turnaround: Profile(
        invest_type=InvestType.turnaround,
        driver_examples=["마진 회복", "부채 감축", "가동률"],
        w_flow_tech=0.2, w_evidence=0.6, review_cadence="분기", stale_days=90,
        dd_threshold=0.30, overvalued_default=Action.hold, rr_min=2.5, rr_upside="bull",
        flow_role="진입 타이밍",
    ),
    InvestType.momentum: Profile(
        invest_type=InvestType.momentum,
        driver_examples=["이익 리비전", "상대강도", "수급"],
        w_flow_tech=0.6, w_evidence=0.2, review_cadence="주간", stale_days=14,
        dd_threshold=0.10, overvalued_default=Action.hold, rr_min=1.5, rr_upside="base",
        flow_role="논제 본체",
    ),
    InvestType.event: Profile(
        invest_type=InvestType.event,
        driver_examples=["이벤트 일정", "성공 확률", "결과별 가치"],
        w_flow_tech=0.3, w_evidence=0.4, review_cadence="이벤트 전후", stale_days=30,
        dd_threshold=0.15, overvalued_default=Action.hold, rr_min=2.0, rr_upside="base",
        flow_role="이벤트 전 포지셔닝",
    ),
}


def get_profile(invest_type: InvestType | str | None) -> Profile:
    if invest_type is None:
        return PROFILES[InvestType.growth]
    return PROFILES[InvestType(invest_type)]
