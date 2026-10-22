"""하위 스키마 (템플릿 §2.3): Driver, Scenario, Rule, Event, BiasCheck, DecisionLog."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import (
    Action,
    Direction,
    EventDirection,
    Light,
    Materiality,
    PricedIn,
    Source,
    ThesisState,
    ValuationState,
    WeightState,
)


class Measurement(BaseModel):
    """드라이버의 한 측정값 (현재값 / 컨센서스 / 내 추정)."""

    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    as_of: date | None = None
    source: Source | None = None
    rationale: str | None = None


class Driver(BaseModel):
    """핵심 드라이버 (A1.x / B2.1.x)."""

    model_config = ConfigDict(extra="forbid")

    current: Measurement = Field(default_factory=Measurement)
    consensus: Measurement = Field(default_factory=Measurement)
    my_estimate: Measurement = Field(default_factory=Measurement)
    check_indicator: str = ""
    next_check: date | None = None
    # 보유 맵 전용
    at_entry: float | None = None
    direction: Direction | None = None
    # 큰 값이 좋은 변수인가 (마진↑ 좋음=True, 부채비율↑ 좋음=False). 방향 판정에 사용.
    higher_is_better: bool = True

    def compute_direction(self, tol_rel: float = 0.05) -> Direction | None:
        """at_entry → current 가 my_estimate 쪽으로 움직였으면 강화, 멀어졌으면 약화."""
        if self.at_entry is None or self.current.value is None:
            return None
        cur, ent = self.current.value, self.at_entry
        target = self.my_estimate.value
        if target is None or target == ent:
            # 추정치가 없으면 단순 방향
            delta = (cur - ent) if self.higher_is_better else (ent - cur)
            scale = abs(ent) if ent else 1.0
        else:
            # 추정치까지의 거리가 줄었나
            delta = abs(target - ent) - abs(target - cur)
            scale = abs(target - ent)
        if scale == 0:
            return Direction.유지
        rel = delta / scale
        if rel > tol_rel:
            return Direction.강화
        if rel < -tol_rel:
            return Direction.약화
        return Direction.유지


class Scenario(BaseModel):
    """Bear / Base / Bull 시나리오 (A3.x / B3.x)."""

    model_config = ConfigDict(extra="forbid")

    name: str  # Bear | Base | Bull
    assumptions: list[str] = Field(default_factory=list)
    probability: float | None = Field(default=None, ge=0.0, le=1.0)   # None = 미입력 (자동 초안은 확률을 만들지 않는다 — 설계 v3 §2.5)
    target_price: float = Field(gt=0)
    horizon_months: int = 12
    valuation_method: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v2 = v.strip().capitalize()
        if v2 not in {"Bear", "Base", "Bull"}:
            raise ValueError("scenario name must be Bear/Base/Bull")
        return v2


class Rule(BaseModel):
    """If-Then 규칙 (A4.4 / A6.3~4 / B7.x)."""

    model_config = ConfigDict(extra="forbid")

    trigger_type: str = Field(pattern="^(fact|price|weight|time|event)$")
    condition: str
    action: Action
    size: str | None = None
    escalate_to: Light | None = None
    status: str = Field(default="armed", pattern="^(armed|fired|disabled)$")
    fired_at: date | None = None
    note: str | None = None


class Event(BaseModel):
    """날짜가 있는 사건 (A5.1.x 촉매 / B2.4 사실 로그)."""

    model_config = ConfigDict(extra="forbid")

    date: date
    fact: str
    affects: list[str] = Field(default_factory=list)
    direction: EventDirection = EventDirection.중립
    priced_in: PricedIn = PricedIn.아니오
    materiality: Materiality = Materiality.M
    source: Source | None = None

    def is_noise(self) -> bool:
        """affects 가 비어 있고 materiality 가 L 이면 로그만 남기고 알림하지 않는다."""
        return not self.affects and self.materiality == Materiality.L


class BiasCheck(BaseModel):
    """보유자 전용 편향 체크 (B5)."""

    model_config = ConfigDict(extra="forbid")

    anchoring: bool = False  # B5.1 매입가/본전 앵커링
    sunk_cost: bool = False  # B5.2 매몰비용
    disposition: bool = False  # B5.3 처분효과
    endowment: bool = False  # B5.4 보유효과
    confirmation: bool = False  # B5.5 확증편향
    breakeven_plan: bool = False  # B5.6 "본전 오면 판다"
    note: str | None = None

    def any(self) -> bool:
        return any([self.anchoring, self.sunk_cost, self.disposition,
                    self.endowment, self.confirmation, self.breakeven_plan])

    def flagged(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if k != "note" and v is True]


class DecisionLog(BaseModel):
    """B8.4 / C6.1 결정 로그 1건."""

    model_config = ConfigDict(extra="forbid")

    date: date
    decision: Action
    size: str | None = None
    reason: str = ""
    price: float | None = None
    thesis_state: ThesisState | None = None
    valuation_state: ValuationState | None = None
    weight_state: WeightState | None = None
    matrix_default: Action | None = None
    override: bool = False
    bias_check: BiasCheck | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
