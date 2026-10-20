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


class PricedIn(StrEnum):
    예 = "예"
    부분 = "부분"
    아니오 = "아니오"


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
