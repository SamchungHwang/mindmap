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


class SensitivityInput(BaseModel):
    """민감도 계산에 들어간 입력 하나 — 값과 **출처**(표·Claim·세그먼트 표·사용자·기본값·유도·없음) 를 함께 적는다 (설계 v5 §3)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None = None
    unit: str | None = None
    # header = 리포트 머리(발행주식수·시가총액), criteria = A3.8 평가 기준 (설계 v6)
    source: str = Field(default="missing", pattern="^(table|claim|segment|header|criteria|user|default|derived|missing)$")
    period: str | None = None
    note: str | None = None


class Sensitivity(BaseModel):
    """드라이버 변화 → 영업이익 → EPS → 적정주가 (1차 선형, ceteris paribus). 계산되지 않으면 status=미계산 + missing (설계 v5 §3).
    금액은 억원, EPS·주가는 원. 코드만 계산한다 — LLM 은 민감도를 만들지 않는다."""

    model_config = ConfigDict(extra="forbid")

    template: str = Field(default="price_volume", pattern="^(price_volume|cost_ratio|direct_op|payout|orders)$")
    input_delta_pct: float = 10.0
    op_delta: float | None = None
    np_delta: float | None = None
    eps_delta: float | None = None
    price_delta: float | None = None
    price_delta_pct: float | None = None
    inputs: list[SensitivityInput] = Field(default_factory=list)
    status: str = Field(default="미계산", pattern="^(계산됨|미계산)$")
    missing: list[str] = Field(default_factory=list)
    method: str = ""
    computed_at: date | None = None

    def n_by_source(self, source: str) -> int:
        return sum(1 for i in self.inputs if i.source == source)


class Driver(BaseModel):
    """핵심 드라이버 (A1.x / B2.1.x). 설계 v5: 원인 변수(role=cause) 와 결과 지표(role=result, A3.7.x) 가 같은 구조를 쓴다."""

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
    # ---- 설계 v5 (전부 기본값 — 옛 저장본 호환)
    metric: str | None = None                         # 관측하는 변수 id (원인: dram_asp·hbm_bg… / 결과: revenue·op·eps·opm)
    period: str | None = None                         # 관측·예측 기간 ("3Q26" · "2027E") — 현재값과 전망값은 같은 기간이어야 비교된다
    kind: str = Field(default="quant", pattern="^(quant|qualitative)$")   # 정성 드라이버는 숫자 대신 관측 지표·무효화 조건이 필수
    role: str | None = Field(default=None, pattern="^(cause|result)$")     # None = 옛 맵 (판정 전)
    proxies: list[str] = Field(default_factory=list)  # 공개 정보가 제한된 변수의 대체 관측 지표
    invalidation_condition: str | None = None         # 이 가설이 약화되는 조건 (문장)
    invalidation_rule: str | None = None              # 그 조건을 Kill 규칙으로 올렸으면 그 노드 id (A4.4.x)
    sensitivity: Sensitivity | None = None
    sensitivity_inputs: dict[str, float | None] = Field(default_factory=dict)   # 사용자가 준 입력 (segment_revenue 억원 · pass_through …)

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


class DriverAssumption(BaseModel):
    """시나리오 안의 드라이버 가정 하나 (설계 v6 §4.2) — "A1.1 DRAM ASP 가 컨센(또는 내 추정) 대비 −30%" 같은 것.
    delta_pct 와 value 는 택일: delta_pct 는 기준 경로 대비 변화(%, 원가율은 %p), value 는 절대값(현재 price_volume 에선 쓰지 않음)."""

    model_config = ConfigDict(extra="forbid")

    node_id: str                                  # A1.1
    driver_id: str | None = None                  # dram_asp (meta.draft.drivers 의 driver_id)
    variable: str | None = None                   # asp | volume | cost | margin | op | fcf … (None → 드라이버의 변수)
    delta_pct: float | None = None
    value: float | None = None
    unit: str | None = None
    note: str = ""


class ScenarioResult(BaseModel):
    """시나리오 가정 묶음을 통합 1차 모델로 돌린 결과 (설계 v6 §4.3). 금액 억원, EPS·주가 원. 코드만 계산한다."""

    model_config = ConfigDict(extra="forbid")

    op: float | None = None
    np: float | None = None
    eps: float | None = None
    per: float | None = None
    price: float | None = None
    price_vs_p0_pct: float | None = None
    eps_basis: str | None = None                  # consensus | my_estimate | normalized | user
    eps_year: str | None = None
    op_base: float | None = None                  # 가정 적용 전 OP (기준 경로)
    op_delta_by_node: dict[str, float] = Field(default_factory=dict)   # node_id → ΔOP (억원)
    inputs: list[SensitivityInput] = Field(default_factory=list)
    status: str = Field(default="미계산", pattern="^(계산됨|미계산)$")
    basis_status: str = "unchecked"      # 설계 v6 §11.1 — 평가 모델과 같은 상태
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    method: str = ""
    computed_at: date | None = None


class Scenario(BaseModel):
    """Bear / Base / Bull 시나리오 (A3.x / B3.x). 설계 v6: 목표가가 아니라 **드라이버 가정 묶음**이 1차 — price_source=computed 면
    target_price 는 통합 모델(engine/scenario_model.py) 의 결과이고, 가정이 모자라 계산이 안 되면 None(미계산) 일 수 있다."""

    model_config = ConfigDict(extra="forbid")

    name: str  # Bear | Base | Bull
    assumptions: list[str] = Field(default_factory=list)
    probability: float | None = Field(default=None, ge=0.0, le=1.0)   # None = 미입력 (자동 초안은 확률을 만들지 않는다 — 설계 v3 §2.5)
    target_price: float | None = Field(default=None, gt=0)            # None = 미계산 (computed 인데 입력이 모자람)
    horizon_months: int = 12
    valuation_method: str = ""
    # ---- 설계 v6 (전부 기본값 — 옛 저장본 호환)
    driver_assumptions: list[DriverAssumption] = Field(default_factory=list)
    eps_basis: str | None = Field(default=None, pattern="^(consensus|my_estimate|normalized|user)$")   # None → A3.8 평가 기준
    eps_year: str | None = None                   # "2027E" (None → A3.8 / 표의 차년도)
    per: float | None = Field(default=None, gt=0)  # 적용 PER (None → A3.8 / 목표 PER 기본값)
    conditions: list[str] = Field(default_factory=list)   # 이 시나리오가 성립하는 조건 (문장)
    indicators: list[str] = Field(default_factory=list)   # 확인 지표 (A1.x 관측 지표 참조)
    falsifier: str | None = None                  # 무엇이 보이면 이 시나리오를 버리나
    price_source: str = Field(default="user", pattern="^(computed|user|broker)$")
    model: ScenarioResult | None = None           # 계산 결과 스냅숏 (코드가 쓴다)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v2 = v.strip().capitalize()
        if v2 not in {"Bear", "Base", "Bull"}:
            raise ValueError("scenario name must be Bear/Base/Bull")
        return v2

    @property
    def priced(self) -> bool:
        return self.target_price is not None


class SotpPart(BaseModel):
    """SOTP 한 줄 — 사업부 영업이익 × (1−t) × 멀티플 (설계 v6 §3.2). 멀티플은 사용자 입력만(사용자 결정 ④)."""

    model_config = ConfigDict(extra="forbid")

    segment: str
    op: float | None = None            # 억원
    np: float | None = None            # 억원 = op × (1−t)
    multiple: float | None = None
    value: float | None = None         # 억원
    source: str = "missing"            # segment | user | missing
    n: int = 0


class ValuationModel(BaseModel):
    """A3.9 평가 모델 (설계 v6 §4.3) — PER 가격(기준 EPS × 적용 PER) 이 기본, SOTP 는 검증, 역산은 현재가가 내재하는 EPS·변수 경로.
    금액 억원, EPS·주가 원. 입력마다 출처. 계산이 안 되면 status=미계산 + missing."""

    model_config = ConfigDict(extra="forbid")

    eps_basis: str = "consensus"
    eps_year: str | None = None
    eps: float | None = None
    eps_consensus: float | None = None
    eps_my_estimate: float | None = None
    eps_normalized: float | None = None
    normalized_years: list[str] = Field(default_factory=list)
    per: float | None = None
    per_basis: str = "default"           # default(목표가 중앙값/EPS) | user
    basis_status: str = "unchecked"      # 설계 v6 §11.1: comparable(정규화 EPS 2곳 이상) | mixed(1곳 — 참고) | not_comparable(0곳 — 확정 표시 안 함) | unchecked(옛 맵)
    price_per: float | None = None
    price_normalized: float | None = None
    price_my_estimate: float | None = None
    sotp: list[SotpPart] = Field(default_factory=list)
    sotp_other_op: float | None = None   # 전사 OP − 사업부 합 (기타·연결조정)
    net_cash: float | None = None        # 억원 (순차입금 × −1)
    net_cash_year: str | None = None
    shares: float | None = None
    price_sotp: float | None = None
    gap_pct: float | None = None         # SOTP / PER − 1
    implied_eps: float | None = None     # P0 / PER
    implied_eps_vs_consensus_pct: float | None = None
    implied_path_pct: float | None = None    # 현재가에 닿는 가격 드라이버 경로 (컨센 대비 %)
    implied_path_label: str | None = None    # 어느 사업부 매출에 대한 경로인가
    inputs: list[SensitivityInput] = Field(default_factory=list)
    status: str = Field(default="미계산", pattern="^(계산됨|미계산)$")
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    method: str = ""
    computed_at: date | None = None


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
