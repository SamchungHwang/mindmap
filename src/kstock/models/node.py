"""공통 노드 (템플릿 §2.1·§2.2)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import Confidence, HistoryPoint, MapKind, NodeStatus, NodeType, Source, ValueType
from .sub import Driver, Event, Rule, Scenario


class Node(BaseModel):
    """모든 맵이 공유하는 노드. 필드는 템플릿 §2.1 표와 1:1."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    map: MapKind
    parent: str | None = None
    label: str
    question: str = ""
    node_type: NodeType
    value: Any = None
    value_type: ValueType = ValueType.text
    unit: str | None = None
    enum_options: list[str] | None = None
    source: Source | None = None
    as_of: date | None = None
    confidence: Confidence | None = None
    status: NodeStatus = NodeStatus.unverified
    next_check: date | None = None
    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    links: list[str] = Field(default_factory=list)
    history: list[HistoryPoint] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    formula: str | None = None
    # 판단 입력으로 쓰면 안 되는 메타데이터 (예: B0.5 평가손익) — validate.py 가 의존 그래프를 검사한다
    meta_only: bool = False
    # 하위 스키마 (해당 노드에만 붙는다)
    driver: Driver | None = None
    scenario: Scenario | None = None
    rule: Rule | None = None
    event: Event | None = None

    @model_validator(mode="after")
    def _coerce_status(self) -> Node:
        # fact 노드에 출처가 없으면 confirmed 를 허용하지 않는다 (§10)
        if self.node_type == NodeType.fact and self.source is None and self.status == NodeStatus.confirmed:
            object.__setattr__(self, "status", NodeStatus.unverified)
        if self.node_type == NodeType.estimate and self.status == NodeStatus.unverified and not self.is_empty():
            object.__setattr__(self, "status", NodeStatus.estimated)
        return self

    # ------------------------------------------------------------------ helpers
    def is_empty(self) -> bool:
        return (self.value is None and self.driver is None and self.scenario is None
                and self.rule is None and self.event is None)

    def set(self, value: Any, *, as_of: date | None = None, source: Source | None = None,
            confidence: Confidence | None = None, status: NodeStatus | None = None) -> Node:
        """값을 갱신하고 이전 값을 history 에 남긴다."""
        if self.value is not None and self.as_of is not None:
            self.history.append(HistoryPoint(as_of=self.as_of, value=self.value, source=self.source))
        self.value = value
        if as_of is not None:
            self.as_of = as_of
        if source is not None:
            self.source = source
        if confidence is not None:
            self.confidence = confidence
        if status is not None:
            self.status = status
        elif self.node_type == NodeType.fact:
            self.status = NodeStatus.confirmed if self.source else NodeStatus.unverified
        elif self.node_type == NodeType.estimate:
            self.status = NodeStatus.estimated
        elif self.node_type in (NodeType.judgment, NodeType.computed):
            self.status = NodeStatus.confirmed
        return self

    def is_stale(self, today: date, stale_days: int) -> bool:
        if self.as_of is None:
            return False
        return (today - self.as_of).days > stale_days


def map_prefix(node_id: str) -> str:
    """`A1.2` → `A`, `S.KR-SEMI.3` → `S`."""
    return node_id[0]


def parent_id_of(node_id: str) -> str | None:
    """`A1.2` → `A1`, `A1` → `A0`, `A0` → None. S/M 맵은 `S.KR-SEMI.3` → `S.KR-SEMI`."""
    if node_id.startswith(("S.", "M.")):
        parts = node_id.split(".")
        return ".".join(parts[:-1]) if len(parts) > 2 else None
    parts = node_id.split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1])
    head = parts[0]
    if len(head) >= 2 and head[1:].isdigit() and int(head[1:]) > 0:
        return f"{head[0]}0"
    return None
