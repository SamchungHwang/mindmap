"""공통 열거형·출처·이력 (템플릿 §2).

모든 맵(A/B/C/M/S)의 모든 노드는 `node.Node` 하나로 표현된다. 맵별 차이는
id·question·value_type 과 하위 스키마(`sub.py`)로만 드러난다.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # Python 3.10 호환 — StrEnum 은 3.11 에서 추가됨
    from enum import Enum

    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str(self.value)

        def _generate_next_value_(name, start, count, last_values):  # type: ignore[override]  # noqa: N805
            return name


# --------------------------------------------------------------------------- enums
class MapKind(StrEnum):
    NEW = "NEW"
    HOLD = "HOLD"
    PORT = "PORT"
    MACRO = "MACRO"
    SECTOR = "SECTOR"


class NodeType(StrEnum):
    fact = "fact"
    estimate = "estimate"
    question = "question"
    judgment = "judgment"
    rule = "rule"
    event = "event"
    link = "link"
    computed = "computed"


class ValueType(StrEnum):
    number = "number"
    percent = "percent"
    price = "price"
    probability = "probability"
    date = "date"
    text = "text"
    enum = "enum"
    range = "range"
    ref = "ref"
    bool = "bool"


class Confidence(StrEnum):
    H = "H"
    M = "M"
    L = "L"


class NodeStatus(StrEnum):
    confirmed = "confirmed"
    estimated = "estimated"
    unverified = "unverified"
    stale = "stale"


class InvestType(StrEnum):
    growth = "growth"
    value = "value"
    turnaround = "turnaround"
    momentum = "momentum"
    event = "event"


class Horizon(StrEnum):
    M3 = "3M"
    Y1 = "1Y"
    Y3 = "3Y+"


class ThesisState(StrEnum):
    강화 = "강화"
    유효 = "유효"
    약화 = "약화"
    무효 = "무효"
    변질 = "변질"


class ValuationState(StrEnum):
    저평가 = "저평가"
    적정 = "적정"
    고평가 = "고평가"


class WeightState(StrEnum):
    과소 = "과소"
    적정 = "적정"
    과다 = "과다"


class Action(StrEnum):
    add = "add"
    hold = "hold"
    trim = "trim"
    switch = "switch"
    exit = "exit"
    hedge = "hedge"
    review = "review"


class Decision(StrEnum):
    BUY = "BUY"
    WATCH = "WATCH"
    PASS = "PASS"


class Light(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class Direction(StrEnum):
    강화 = "강화"
    유지 = "유지"
    약화 = "약화"


class EventDirection(StrEnum):
    강화 = "강화"
    약화 = "약화"
    중립 = "중립"
    미정 = "미정"          # 사건 전에는 방향을 모른다 — 사건 뒤 outcome.verdict 로 정한다 (2026-10-30)


class PricedIn(StrEnum):
    예 = "예"
    부분 = "부분"
    아니오 = "아니오"
    미판단 = "미판단"      # 아직 판단하지 않았다 — 「아니오」(판단한 결과 선반영 안 됨) 와 다르다


class EventStatus(StrEnum):
    """사건의 진행 상태 (2026-10-31). 일정 확실성(`EventCertainty`) 과 다른 축 — 「공시로 확정된 일정」 이 「지연」 될 수 있다."""

    scheduled = "scheduled"    # 예정
    completed = "completed"    # 일어났다 (outcome 이 채워지면 코드가 바꾼다)
    delayed = "delayed"        # 미뤄졌다 (사람이 적는다 · 정정 공시로 날짜가 바뀌면 코드가 적는다)
    cancelled = "cancelled"


class EventCertainty(StrEnum):
    """촉매 일정의 확실성 — 출처가 곧 등급이다 (2026-10-30 A6 보강).

    공시확정    DART 공시 본문에 날짜가 있다 (납입일·IR 일시·주총일)
    공시상예정  공시가 「예정」 이라고 적은 날짜 (신주 상장예정일 등) — 바뀔 수 있다
    과거주기    그 회사의 과거 공시일(영업(잠정)실적) 로 추정한 날짜
    회사목표    회사가 발표·계획한 기간 목표 — **DART·회사 자료로 확인된 것** (또는 사람이 확인하고 올린 것)
    회사계획인용 증권사 리포트가 회사의 계획이라고 적은 것 (「기존 목표인 2027 상반기 55MW」) — 회사 원문은 아직 안 봤다 (2026-10-31)
    리포트전망  증권사가 쓴 일정 (회사 발표가 아니다)
    통상추정    일반 규칙 (법정 시한 등) — 회사별 근거가 없다
    미정        날짜·기간을 못 정했다
    """

    공시확정 = "공시확정"
    공시상예정 = "공시상 예정"
    과거주기 = "과거 주기 추정"
    회사목표 = "회사 목표"
    회사계획인용 = "회사 계획 (리포트 인용)"
    리포트전망 = "리포트 전망"
    통상추정 = "통상 일정 추정"
    미정 = "미정"


class Materiality(StrEnum):
    H = "H"
    M = "M"
    L = "L"


class ReconstructionQuality(StrEnum):
    기록 = "기록"
    재구성 = "재구성"
    불가 = "불가"


class HoldReason(StrEnum):
    세금 = "세금"
    유동성 = "유동성"
    촉매_임박 = "촉매 임박"
    없음 = "없음"


class WhyMarketWrong(StrEnum):
    시간축 = "시간축"
    정보 = "정보"
    심리 = "심리"
    구조 = "구조"


# --------------------------------------------------------------------------- source / history
class Source(BaseModel):
    """출처. fact 노드는 반드시 가진다."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str = ""
    retrieved_at: date | None = None


class HistoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    value: Any
    source: Source | None = None
