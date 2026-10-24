"""맵 컨테이너: 노드 저장소 + 맵별 편의 접근자.

노드 자체는 모두 `Node` 하나로 표현되고, 맵은 `nodes: dict[id, Node]` 를 들고 있다.
`NewMap`(A) / `HoldingMap`(B) / `Portfolio`(C) / `MacroMap`(M) / `SectorMap`(S) 는
같은 저장소 위에 자주 쓰는 노드에 대한 접근자만 덧붙인 얇은 래퍼다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .common import (
    Decision,
    InvestType,
    Light,
    MapKind,
    ReconstructionQuality,
    Source,
    ThesisState,
)
from .node import Node, parent_id_of
from .sub import DecisionLog, Driver, Event, Rule, Scenario

KIND_PREFIX: dict[MapKind, str] = {
    MapKind.NEW: "A", MapKind.HOLD: "B", MapKind.PORT: "C", MapKind.MACRO: "M", MapKind.SECTOR: "S",
}


# --------------------------------------------------------------------------- base
class MindMap(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: MapKind
    map_id: str
    ticker: str | None = None
    name: str | None = None
    profile: InvestType | None = None
    created_at: date
    updated_at: date
    schema_version: str = "1.0"
    nodes: dict[str, Node] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ node access
    def get(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def __getitem__(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError as e:
            raise KeyError(f"{self.map_id}: node {node_id} not found") from e

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def value(self, node_id: str, default: Any = None) -> Any:
        n = self.nodes.get(node_id)
        return default if n is None or n.value is None else n.value

    def add(self, node: Node) -> Node:
        if node.parent is None and node.id != self.root_id:
            node.parent = parent_id_of(node.id)
        self.nodes[node.id] = node
        return node

    def children(self, node_id: str) -> list[Node]:
        kids = [n for n in self.nodes.values() if n.parent == node_id]
        return sorted(kids, key=lambda n: _sort_key(n.id))

    def descendants(self, node_id: str) -> Iterator[Node]:
        for c in self.children(node_id):
            yield c
            yield from self.descendants(c.id)

    @property
    def prefix(self) -> str:
        return KIND_PREFIX[self.kind]

    @property
    def root_id(self) -> str:
        if self.kind in (MapKind.MACRO, MapKind.SECTOR):
            return next((i for i, n in self.nodes.items() if n.parent is None), f"{self.prefix}0")
        return f"{self.prefix}0"

    def root(self) -> Node:
        return self[self.root_id]

    def by_type(self, node_type: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.node_type == node_type]

    def touch(self, today: date) -> None:
        self.updated_at = today

    # ------------------------------------------------------------------ io
    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent, exclude_none=True)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> MindMap:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return load_map(data)


def _sort_key(node_id: str) -> tuple:
    out: list[Any] = []
    for part in node_id.split("."):
        if part.isdigit():
            out.append((0, int(part)))
        elif len(part) >= 2 and part[1:].isdigit():
            out.append((0, int(part[1:])))
        else:
            out.append((1, part))
    return tuple(out)


# --------------------------------------------------------------------------- A
class NewMap(MindMap):
    """A 신규 투자 맵 — "살 것인가"."""

    kind: MapKind = MapKind.NEW

    @property
    def thesis(self) -> str | None:
        return self.value("A0")

    @property
    def current_price(self) -> float | None:
        return self.value("A0.4")

    def drivers(self) -> list[Node]:
        return [n for n in self.children("A1") if n.driver is not None]

    def scenarios(self) -> dict[str, Scenario]:
        """목표가가 있는 시나리오만 (설계 v6: computed 인데 미계산이면 target_price=None — ER·손익비 계산에서 뺀다; `scenario_nodes` 가 전부)."""
        out: dict[str, Scenario] = {}
        for n in self.children("A3"):
            if n.scenario is not None and n.scenario.target_price is not None:
                out[n.scenario.name] = n.scenario
        return out

    def scenario_nodes(self) -> dict[str, Node]:
        """Bear/Base/Bull 노드 전부 (미계산 포함)."""
        return {n.scenario.name: n for n in self.children("A3") if n.scenario is not None}

    def kill_rules(self) -> list[Rule]:
        return [n.rule for n in self.children("A4.4") if n.rule is not None] + \
               ([self["A4.4"].rule] if "A4.4" in self and self["A4.4"].rule is not None else [])

    def exec_rules(self) -> list[Rule]:
        out: list[Rule] = []
        for nid in ("A6.3", "A6.4"):
            if nid in self and self[nid].rule is not None:
                out.append(self[nid].rule)
            out += [n.rule for n in self.children(nid) if n.rule is not None]
        return out

    def catalysts(self) -> list[Event]:
        return [n.event for n in self.children("A5.1") if n.event is not None]

    @property
    def decision(self) -> Decision | None:
        v = self.value("A6.5")
        return Decision(v) if v else None


# --------------------------------------------------------------------------- B
class Position(BaseModel):
    """보유 포지션. avg_cost / unrealized_pnl 은 메타데이터이며 판단 입력이 아니다."""

    model_config = ConfigDict(extra="forbid")

    entry_date: date
    entry_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    w_target: float = Field(ge=0, le=1)
    eps_fwd_entry: float | None = None
    peak_price: float | None = None
    avg_cost: float | None = None  # meta_only


class MarketState(BaseModel):
    """수집기가 갱신하는 현재 시장 상태 (B 맵 계산 입력)."""

    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None
    price: float | None = None
    eps_fwd: float | None = None
    band_percentile: float | None = None  # 역사 밸류 밴드 백분위 (0~100)
    w_now: float | None = None
    total_assets: float | None = None
    adv_shares: float | None = None  # 일평균 거래량 (주)
    cash_yield: float | None = None  # 현금 대안 수익률


class HoldingMap(MindMap):
    """B 보유 종목 맵 — "계속 들고 있을 자격이 있는가"."""

    kind: MapKind = MapKind.HOLD
    position: Position | None = None
    market: MarketState = Field(default_factory=MarketState)
    source_map_id: str | None = None  # 스냅숏을 뜬 A 맵
    snapshot_hash: str | None = None  # B1.1~B1.4 불변성 검사용
    closed_at: date | None = None
    decision_log: list[DecisionLog] = Field(default_factory=list)

    @property
    def light(self) -> Light | None:
        v = self.value("B0")
        return Light(v) if v else None

    @property
    def thesis_state(self) -> ThesisState | None:
        v = self.value("B2.3")
        return ThesisState(v) if v else None

    @property
    def reconstruction(self) -> ReconstructionQuality | None:
        v = self.value("B1.5")
        return ReconstructionQuality(v) if v else None

    @property
    def no_thesis(self) -> bool:
        return self.reconstruction == ReconstructionQuality.불가

    def drivers(self) -> list[Node]:
        return [n for n in self.children("B2.1") if n.driver is not None]

    def scenarios(self) -> dict[str, Scenario]:
        out: dict[str, Scenario] = {}
        for n in self.children("B3"):
            if n.scenario is not None and n.scenario.target_price is not None:
                out[n.scenario.name] = n.scenario
        return out

    def rules(self) -> list[Node]:
        return [n for n in self.children("B7") if n.rule is not None]

    def kill_rules(self) -> list[Rule]:
        return [n.rule for n in self.children("B1.4") if n.rule is not None]

    def fact_log(self) -> list[Event]:
        return [n.event for n in self.children("B2.4") if n.event is not None]

    def sector_links(self) -> list[str]:
        n = self.get("B1.6")
        return [] if n is None else [lk for lk in n.links if lk.startswith("S.")]

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


# --------------------------------------------------------------------------- C
class Portfolio(MindMap):
    """C 포트폴리오 롤업 — 직접 채우는 노드가 거의 없고 B 맵들에서 계산된다."""

    kind: MapKind = MapKind.PORT
    holding_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- M / S
class MacroMap(MindMap):
    kind: MapKind = MapKind.MACRO


class SectorMap(MindMap):
    kind: MapKind = MapKind.SECTOR
    sector_code: str = ""  # 예: KR-SEMI

    @property
    def sector_prefix(self) -> str:
        return f"S.{self.sector_code}"


# --------------------------------------------------------------------------- factory
_KIND_TO_CLS: dict[MapKind, type[MindMap]] = {
    MapKind.NEW: NewMap,
    MapKind.HOLD: HoldingMap,
    MapKind.PORT: Portfolio,
    MapKind.MACRO: MacroMap,
    MapKind.SECTOR: SectorMap,
}


def load_map(data: dict[str, Any]) -> MindMap:
    kind = MapKind(data["kind"])
    return _KIND_TO_CLS[kind].model_validate(data)


def load_map_file(path: str | Path) -> MindMap:
    return load_map(json.loads(Path(path).read_text(encoding="utf-8")))


def new_node(map_kind: MapKind, node_id: str, label: str, node_type: str, *, question: str = "",
             value_type: str = "text", value: Any = None, parent: str | None = None, **kw: Any) -> Node:
    """노드 생성 축약. parent 를 주지 않으면 id 규칙으로 유도한다."""
    return Node(id=node_id, map=map_kind, parent=parent if parent is not None else parent_id_of(node_id),
                label=label, node_type=node_type, question=question, value_type=value_type, value=value, **kw)


__all__ = [
    "MindMap", "NewMap", "HoldingMap", "Portfolio", "MacroMap", "SectorMap",
    "Position", "MarketState", "load_map", "load_map_file", "new_node", "Source",
    "Driver", "Scenario", "Rule", "Event", "DecisionLog",
]
