"""템플릿 스켈레톤 생성기 — 투자유형을 받아 질문만 들어 있는 빈 맵을 만든다 (§11 로드맵 2)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import (
    HoldingMap,
    InvestType,
    MacroMap,
    MapKind,
    NewMap,
    Node,
    Portfolio,
    SectorMap,
    get_profile,
    parent_id_of,
)
from .questions import A_SPEC, B_SPEC, C_SPEC, M_SPEC, S_SPEC, NodeSpec


def _node_from_spec(map_kind: MapKind, node_id: str, spec: NodeSpec, *, parent: str | None = None,
                    profile_weights: dict[str, float] | None = None, **override: Any) -> Node:
    kw: dict[str, Any] = {
        "id": node_id,
        "map": map_kind,
        "parent": parent if parent is not None else parent_id_of(node_id),
        "label": spec["label"],
        "node_type": spec["node_type"],
        "value_type": spec.get("value_type", "text"),
        "question": spec.get("question", ""),
    }
    for k in ("enum_options", "unit", "formula", "meta_only"):
        if k in spec:
            kw[k] = spec[k]
    wk = spec.get("weight_key")
    if wk and profile_weights and wk in profile_weights:
        kw["weight"] = profile_weights[wk]
    kw.update(override)
    return Node(**kw)


def new_map_skeleton(ticker: str, name: str, invest_type: InvestType | str, today: date | None = None,
                     n_drivers: int = 3) -> NewMap:
    """A 맵 빈 스켈레톤. 드라이버 슬롯 수와 프로파일 가중치를 반영한다."""
    today = today or date.today()
    it = InvestType(invest_type)
    prof = get_profile(it)
    weights = {"w_flow_tech": prof.w_flow_tech, "w_evidence": prof.w_evidence}
    m = NewMap(map_id=f"A:{ticker}:{today.isoformat()}", ticker=ticker, name=name, profile=it,
               created_at=today, updated_at=today)
    for nid, spec in A_SPEC.items():
        if nid.startswith("A1.") and int(nid.split(".")[1]) > n_drivers:
            continue
        m.add(_node_from_spec(MapKind.NEW, nid, spec, profile_weights=weights))
    # 드라이버 슬롯 확장 (n_drivers > 3)
    for i in range(4, n_drivers + 1):
        m.add(_node_from_spec(MapKind.NEW, f"A1.{i}", A_SPEC["A1.1"], label=f"드라이버 {i}"))
    # 고정값
    m["A0.1"].set(it.value)
    m["A0"].label = f"{name} 투자 논제"
    # 시나리오 이름을 미리 박아 둔다 (확률·목표가는 비움 → 검증에서 걸림)
    for nid, nm in (("A4.2", "Bear"), ("A4.3", "Base"), ("A4.4", "Bull")):
        m[nid].label = nm
    # 프로파일 힌트
    m["A1"].question += f"  예시({it.value}): " + " / ".join(prof.driver_examples)
    return m


def hold_map_skeleton(ticker: str, name: str, invest_type: InvestType | str | None, today: date | None = None,
                      n_drivers: int = 3) -> HoldingMap:
    """B 맵 빈 스켈레톤. 보통은 lifecycle.snapshot_to_holding 이 A 맵에서 만들지만,
    맵 없이 보유 중인 종목(논제 복원 위자드)에서는 여기서 시작한다."""
    today = today or date.today()
    it = InvestType(invest_type) if invest_type else None
    m = HoldingMap(map_id=f"B:{ticker}:{today.isoformat()}", ticker=ticker, name=name, profile=it,
                   created_at=today, updated_at=today)
    for nid, spec in B_SPEC.items():
        m.add(_node_from_spec(MapKind.HOLD, nid, spec))
    for i in range(1, n_drivers + 1):
        m.add(Node(id=f"B2.1.{i}", map=MapKind.HOLD, parent="B2.1", label=f"드라이버 {i}",
                   node_type="estimate", value_type="number",
                   question="at_entry 대비 now 가 내 추정에 가까워졌나 멀어졌나"))
    for nid, nm in (("B3.1", "Bear"), ("B3.2", "Base"), ("B3.3", "Bull")):
        m[nid].label = nm
    m["B0"].label = f"{name} 보유"
    return m


def portfolio_skeleton(today: date | None = None, name: str = "포트폴리오") -> Portfolio:
    today = today or date.today()
    m = Portfolio(map_id="C:main", name=name, created_at=today, updated_at=today)
    for nid, spec in C_SPEC.items():
        m.add(_node_from_spec(MapKind.PORT, nid, spec))
    return m


def macro_skeleton(today: date | None = None) -> MacroMap:
    today = today or date.today()
    m = MacroMap(map_id="M", name="매크로", created_at=today, updated_at=today)
    for nid, spec in M_SPEC.items():
        m.add(_node_from_spec(MapKind.MACRO, nid, spec))
    return m


def sector_skeleton(sector_code: str, name: str, today: date | None = None) -> SectorMap:
    """S.<코드> 루트 아래 S.<코드>.1 ~ .6"""
    today = today or date.today()
    m = SectorMap(map_id=f"S.{sector_code}", sector_code=sector_code, name=name,
                  created_at=today, updated_at=today)
    root = f"S.{sector_code}"
    for suffix, spec in S_SPEC.items():
        nid = root if suffix == "" else f"{root}.{suffix}"
        parent = None if suffix == "" else root
        n = _node_from_spec(MapKind.SECTOR, nid, spec, parent=parent)
        if suffix == "":
            n.label = f"{name} 섹터"
        m.add(n)
    return m


__all__ = ["new_map_skeleton", "hold_map_skeleton", "portfolio_skeleton", "macro_skeleton", "sector_skeleton"]
