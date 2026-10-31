"""하위 스키마 (템플릿 §2.3): Driver, Scenario, Rule, Event, BiasCheck, DecisionLog."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import (
    Action,
    Direction,
    EventCertainty,
    EventDirection,
    EventStatus,
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
    # header = 리포트 머리(발행주식수·시가총액), criteria = A3.2 평가 기준 (설계 v6)
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


# --------------------------------------------------------------------------- 설계 v7 (T4·T5): 관측값 데이터 계약 · 필요 경로
VALUE_TYPES = ("abs", "pct", "cum", "state")          # 절대값 · 증감률 · 누적 · 상태
BASES = ("none", "qoq", "yoy", "vs_consensus")        # 기준
VERIFICATIONS = ("confirmed", "reported", "forecast")  # 확인됨(공시·공식 통계·회사 발표) · 보도/추정 · 전망
PATH_SOURCES = ("broker", "consensus", "interpolated", "user")
THESIS_ROLES = ("required", "core", "secondary")      # 필수 조건 · 핵심 수익 변수 · 보조 변수 (Kill 은 A5.4)


class Ruler(BaseModel):
    """자(尺) — 무엇을 어떤 종류·기준으로 재는가. 관측값과 필요 경로의 자가 같을 때만 비교한다 (v7 §3.4 같은 자 규칙)."""

    model_config = ConfigDict(extra="forbid")

    metric_id: str                                     # dram_asp_index · hbm_stage · foundry_op …
    value_type: str = Field(default="abs", pattern="^(abs|pct|cum|state)$")
    basis: str = Field(default="none", pattern="^(none|qoq|yoy|vs_consensus)$")
    unit: str | None = None                            # 지수 · % · 조원 · 단계 …
    index_base: str | None = None                      # 지수면 기준 시점 ("26Q3 = 100")

    def same(self, other: Ruler | None) -> bool:
        return other is not None and self.metric_id == other.metric_id and self.value_type == other.value_type and self.basis == other.basis

    def label(self) -> str:
        return f"{self.metric_id} ({self.value_type}/{self.basis}" + (f", {self.unit}" if self.unit else "") + (f", {self.index_base}" if self.index_base else "") + ")"


class Observation(BaseModel):
    """관측값 한 점 — 여덟 칸이 다 있어야 저장된다 (v7 §3.4). 리포트의 전망 수치는 관측값이 아니다 (verification=forecast 는 신호등에 안 쓴다)."""

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    period: str                                        # 월 "2026-10" · 분기 "26Q4" · 연 "2027"
    value_type: str = Field(default="abs", pattern="^(abs|pct|cum|state)$")
    basis: str = Field(default="none", pattern="^(none|qoq|yoy|vs_consensus)$")
    value: float | str                                 # 숫자 또는 상태 문자열
    as_of: date                                        # 실제로 관측된 날
    compare_at: str                                    # 필요 경로의 어느 점과 비교하나 ("26Q4")
    source: str                                        # 어디서
    verification: str = Field(default="confirmed", pattern="^(confirmed|reported|forecast)$")
    note: str | None = None
    entered_by: str | None = None                      # user · collector · agent

    @property
    def ruler(self) -> Ruler:
        return Ruler(metric_id=self.metric_id, value_type=self.value_type, basis=self.basis)

    @property
    def numeric(self) -> float | None:
        return float(self.value) if isinstance(self.value, (int, float)) else None


class PathPoint(BaseModel):
    """필요 경로의 한 점 — 어느 시점에 어떤 값이어야 하나, 출처는 무엇인가."""

    model_config = ConfigDict(extra="forbid")

    at: str                                            # "26Q4" · "2027" · "2026-12"
    value: float | str
    source: str = Field(default="consensus", pattern="^(broker|consensus|interpolated|user)$")
    band: float | None = None                          # 허용 띠 (± 같은 단위; 상태형은 없음)
    note: str | None = None


class RequiredPath(BaseModel):
    """날짜별 필요 경로 (v7 §3.3) — 기준 가격이 성립하려면 이 변수가 언제까지 어디에 있어야 하나."""

    model_config = ConfigDict(extra="forbid")

    ruler: Ruler
    target_price: float | None = None
    target_label: str = "목표가 중앙값"
    points: list[PathPoint] = Field(default_factory=list)
    method: str = ""                                    # 어떻게 만들었나 (사업부 표 분기 열 / 등비 보간 / 사용자)
    computed_at: date | None = None

    def point(self, at: str) -> PathPoint | None:
        return next((p for p in self.points if p.at == at), None)

    @property
    def interpolated(self) -> bool:
        return any(p.source == "interpolated" for p in self.points)


class Driver(BaseModel):
    """핵심 드라이버 (A1.x / B2.1.x). 설계 v5: 원인 변수(role=cause) 와 결과 지표(role=result, A3.1.x) 가 같은 구조를 쓴다."""

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
    invalidation_rule: str | None = None              # 그 조건을 Kill 규칙으로 올렸으면 그 노드 id (A5.4.x)
    sensitivity: Sensitivity | None = None
    sensitivity_inputs: dict[str, float | None] = Field(default_factory=dict)   # 사용자가 준 입력 (segment_revenue 억원 · pass_through …)
    # ---- 설계 v7 (전부 기본값): 논제 역할 · 관측값 · 필요 경로 · 띠 · 경계 · 신호등
    thesis_role: str | None = Field(default=None, pattern="^(required|core|secondary)$")   # 필수 조건 · 핵심 수익 변수 · 보조 변수 (사람이 정한다)
    thesis_role_source: str | None = None             # user | proposed
    observations: list[Observation] = Field(default_factory=list)                        # 관측값 (데이터 계약 8칸)
    required: RequiredPath | None = None              # 날짜별 필요 경로 (코드가 만들고 사람이 고칠 수 있다)
    band: float | None = None                         # 허용 띠 (± 자의 단위) — 제안값은 band_source=proposed
    bound_bear: float | str | None = None             # Bear 쪽 경계 (숫자 또는 상태/문장)
    bound_bull: float | str | None = None
    bounds_source: str | None = None                  # user | proposed
    light: Light | None = None                        # 코드가 계산 (GREEN·YELLOW·RED; None = ⚪ 증거 없음)
    light_reason: str | None = None
    trend: str | None = None                          # 코드가 계산: on_path · drifting · off_path · None

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
    eps_basis: str | None = Field(default=None, pattern="^(consensus|my_estimate|normalized|user)$")   # None → A3.2 평가 기준
    eps_year: str | None = None                   # "2027E" (None → A3.2 / 표의 차년도)
    per: float | None = Field(default=None, gt=0)  # 적용 PER (None → A3.2 / 목표 PER 기본값)
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
    """A3.3 평가 모델 (설계 v6 §4.3) — PER 가격(기준 EPS × 적용 PER) 이 기본, SOTP 는 검증, 역산은 현재가가 내재하는 EPS·변수 경로.
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
    per_basis: str = "default"           # default(목표가 중앙값/EPS) | user  (옛 필드 — per_source 가 더 자세하다)
    per_source: str = "broker_target_implied"   # 설계 v6 §11.2 (v7 T2): broker_target_implied(목표가 중앙값÷컨센 EPS — 순환) | user | historical_normalized(보류) | peer(보류) | missing
    implied_status: str = "none"         # 역산 해석: assumption_dependent(PER 가 목표가 유도값 — 독립 근거 없음) | anchored(사용자·독립 PER) | none(미계산)
    implied_table: list[dict[str, Any]] = Field(default_factory=list)   # 여러 PER 에서의 역산 — {per, label, source, implied_eps, vs_consensus_pct, path_pct, path_label}
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
    """If-Then 규칙 (A5.4 / A7.3~4 / B7.x)."""

    model_config = ConfigDict(extra="forbid")

    trigger_type: str = Field(pattern="^(fact|price|weight|time|event|evidence)$")   # v7: evidence = 증거 계획의 지표·경계값 기반
    condition: str
    action: Action
    size: str | None = None
    escalate_to: Light | None = None
    status: str = Field(default="armed", pattern="^(armed|fired|disabled)$")
    fired_at: date | None = None
    note: str | None = None
    # ---- 설계 v7 (T8) 판단 수정 규칙 — 전부 기본값 (옛 규칙 호환)
    grade: str | None = Field(default=None, pattern="^(shift|review|warn|kill)$")   # ① 확률 이동 ② 논제 재검토 ③ 경고 · Kill
    driver_id: str | None = None                      # 대상 드라이버 노드 (A1.x)
    threshold: float | str | None = None              # 경계값 (숫자 또는 상태) — 드라이버의 bound_bear 와 같게 두는 것이 보통
    side: str = Field(default="bear", pattern="^(bear|bull)$")
    persistence: int = Field(default=2, ge=1)         # 연속 n 회 (검증 단계의 지속성)
    proposal: dict[str, Any] = Field(default_factory=dict)   # {kind: hold_new_buy|shift|review|warn, scenario_from, scenario_to, pct_points}


class EventOutcome(BaseModel):
    """사건 뒤에 채우는 칸 (2026-10-30 A6 보강). 숫자는 시세로 계산하고, verdict 는 사람이 정한다.

    reaction_d1 / reaction_d5: 사건 전 종가(D-1) 대비 D+1 · D+5 종가 수익률 (0.032 = +3.2%).
    vs_market_d5: 같은 시장(KOSPI/KOSDAQ) 종목들의 평균 수익률을 뺀 값 — 지수 자료가 없어 평균으로 대신한다.
    """

    model_config = ConfigDict(extra="forbid")

    actual: str | None = None
    reaction_d1: float | None = None
    reaction_d5: float | None = None
    vs_market_d5: float | None = None
    verdict: EventDirection | None = None
    as_of: date | None = None
    note: str | None = None
    measured_from: date | None = None    # 수익률의 기준 거래일 (D-1 종가의 날짜) — 사건일과 측정 기준일은 다르다


class Event(BaseModel):
    """날짜가 있는 사건 (A6.1.x 촉매 / B2.4 사실 로그).

    `date` 는 **기준일**이다 — 정렬·체크포인트(A8.1)·사후 수익률 계산에 쓴다. 날짜가 아니라
    기간인 사건(「2027 상반기 55MW 가동」) 은 `period` 에 기간을 적고 `date` 에는 그 기간의
    끝을 둔다. 화면은 `when_label()` 로 기간을 먼저 보여 준다.

    2026-10-30 보강 — 일정표가 아니라 **판단 항목**이 되게 칸을 더했다. 사건 전에 적는 것
    (precheck · bull · bear · rule) 과 사건 뒤에 채우는 것 (outcome) 을 가른다. 보강본이
    자유 문장(detail) 에 적었던 것을 칸으로 올린 것이라, 편집 폼이 만지고 다음 생성 때도 남는다.
    """

    model_config = ConfigDict(extra="forbid")

    date: date
    fact: str
    affects: list[str] = Field(default_factory=list)
    direction: EventDirection = EventDirection.중립
    priced_in: PricedIn = PricedIn.아니오
    materiality: Materiality = Materiality.M
    source: Source | None = None
    # ---- 2026-10-30 보강 (전부 기본값 — 옛 맵·편집 파일 그대로 읽힌다)
    period: str | None = None                                   # "2027H1" · "2027Q1" · "2028" · "2027-03"
    certainty: EventCertainty = EventCertainty.미정
    precheck: list[str] = Field(default_factory=list)           # 사전 확인 지표·질문
    bull: list[str] = Field(default_factory=list)               # 상방 증거 — 논제가 강해지는 조건
    bear: list[str] = Field(default_factory=list)               # 하방 증거 — 논제가 약해지는 조건
    rule: str | None = None                                     # 판단 규칙 — 결과에 따라 어느 노드를 어떻게 고치나
    next_check: date | None = None                              # 체크포인트 (없으면 date)
    outcome: EventOutcome | None = None                         # 사건 뒤
    # ---- 2026-10-31
    event_id: str | None = None                                 # 영속 식별자 — "earnings:3Q26" · "rights:납입일" · "claim:<id>". 노드 번호는 이것으로 고정된다
    status: EventStatus = EventStatus.scheduled                 # 예정 · 일어남 · 지연 · 취소
    evidence: list[str] = Field(default_factory=list)           # 관련 전망·근거 문장 (증권사 Claim). bull/bear 는 **판정 조건**이지 전망이 아니다
    project: str | None = None                                  # 같은 사업의 마일스톤을 묶는 열쇠 ("ai_dc") — 2027~2028 진척을 한 줄로 본다
    # ---- 2026-10-31 두산에너빌리티 검토 — 사건 시점과 납기·운전 시점은 다르고, 계약엔 진행 단계가 있고, 사업(고유명사) 은 드라이버보다 잘다
    delivery_period: str | None = None                          # 납기·공급·운전 시점 ("2030" 납기분의 본계약 전환은 2027 에 일어난다) — 사건 시점이 아니다
    stage: str | None = None                                    # 계약 진행 단계 — 협의 중 · 조건 합의 · 체결 대기 · 체결 · 불확실 (리포트·설명회가 적은 그대로)
    program: str | None = None                                  # 사업명 — "X-energy" · "NuScale" · "미국 AP1000" (project 는 드라이버 id, 이것은 고유명사)

    def is_noise(self) -> bool:
        """affects 가 비어 있고 materiality 가 L 이면 로그만 남기고 알림하지 않는다."""
        return not self.affects and self.materiality == Materiality.L

    def when_label(self) -> str:
        """화면에 보일 시점 — 기간이면 기간, 아니면 날짜."""
        return self.period or self.date.isoformat()

    def checkpoint(self) -> date:
        """A8.1 체크포인트에 넣을 날짜."""
        return self.next_check or self.date

    def is_dated(self) -> bool:
        """날짜가 박힌 사건인가 (기간 목표가 아닌가). 사후 수익률은 날짜가 박힌 것만 잰다."""
        return self.period is None

    def happened(self, today: date) -> bool:
        return self.date <= today

    def is_confirmed(self) -> bool:
        """공시로 확정된 일정인가 — LLM·규칙 채움이 지우면 안 된다."""
        return self.certainty in (EventCertainty.공시확정, EventCertainty.공시상예정)


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
