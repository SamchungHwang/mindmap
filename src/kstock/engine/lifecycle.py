"""라이프사이클 (템플릿 §7): A 평가 → 매수 스냅숏 → B 운영 루프 → 결정 로그 → 종료/복기, 논제 복원."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import (
    Action,
    BiasCheck,
    Decision,
    DecisionLog,
    Direction,
    Driver,
    Event,
    HistoryPoint,
    HoldingMap,
    Light,
    MapKind,
    MarketState,
    Measurement,
    NewMap,
    Node,
    Position,
    ReconstructionQuality,
    Rule,
    Scenario,
    Settings,
    Source,
    ThesisState,
    get_profile,
)
from ..templates.skeleton import hold_map_skeleton
from . import calc
from .matrix import MatrixResult, decide
from .rules import RuleContext
from .status import LightResult, compute_light, mark_stale
from .validate import snapshot_hash


# --------------------------------------------------------------------------- A 평가
@dataclass
class NewMapEval:
    expected_return: float | None = None
    risk_reward: float | None = None
    suggested_weight: float | None = None
    rr_gate_passed: bool | None = None
    checkpoints: list[date] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def evaluate_new_map(m: NewMap, *, settings: Settings | None = None, today: date | None = None,
                     enforce_gate: bool = True) -> NewMapEval:
    """A4.5 / A4.6 / A7.1(suggested) / A8.1 을 계산해 노드에 쓰고, rr_min 게이트를 적용한다."""
    settings = settings or Settings()
    today = today or date.today()
    prof = get_profile(m.profile)
    res = NewMapEval()
    p0 = m.current_price
    sc = m.scenarios()
    if p0 and len(sc) == 3:
        try:
            res.expected_return = calc.expected_return(sc, p0)
            m["A4.5"].set(round(res.expected_return, 4), as_of=today)
        except calc.CalcError as e:
            res.notes.append(f"ER 계산 불가: {e}")
        try:
            res.risk_reward = calc.risk_reward(sc, p0, prof.rr_upside)
            m["A4.6"].set(None if res.risk_reward is None else round(res.risk_reward, 2), as_of=today)
        except calc.CalcError as e:
            res.notes.append(f"RR 계산 불가: {e}")
    conf = m.value("A0.3")
    if res.risk_reward is not None or res.expected_return is not None:
        res.suggested_weight = calc.suggested_weight(conf, res.risk_reward, settings)
        cur = m["A7.1"].value if isinstance(m["A7.1"].value, dict) else {}
        cur = {**cur, "suggested": round(res.suggested_weight, 4)}
        m["A7.1"].value = cur
    # rr_min 게이트
    if res.risk_reward is not None:
        res.rr_gate_passed = res.risk_reward >= prof.rr_min
        if enforce_gate and not res.rr_gate_passed and m.decision == Decision.BUY:
            m["A7.5"].set(Decision.WATCH.value, as_of=today)
            res.notes.append(f"손익비 {res.risk_reward:.2f} < rr_min {prof.rr_min} → BUY 불허, WATCH 강제")
    # 설계 v7 §3.6: 판단 상태 플래그 — 신규 BUY 보류(필수 조건 위반 검증됨 ∨ 재검토 중) 이면 BUY 를 WATCH 로 (판단값은 사람이 바꾼다; 보류 중엔 BUY 로 저장되지 않음)
    flags = m.meta.get("flags") or {}
    if enforce_gate and (flags.get("hold_new_buy") or flags.get("review")) and m.decision == Decision.BUY:
        m["A7.5"].set(Decision.WATCH.value, as_of=today)
        res.notes.append("신규 BUY 보류 플래그(필수 조건 위반 검증 또는 재검토 중) → BUY 불허, WATCH 강제")
    # A8.1 체크포인트
    dates: list[date | None] = [n.driver.next_check for n in m.drivers() if n.driver]
    # 2026-10-31: A8.1 은 일정표의 복사본이 아니라 **판단을 고쳐야 하는 사건**의 목록 — 중요도 H 거나 바꾸는 노드가 있는 것만.
    # 체크포인트는 이벤트의 next_check 가 있으면 그것 (기간 사건은 기간 끝)
    from ..generate.catalysts import event_matters

    dates += [e.checkpoint() for e in m.catalysts() if event_matters(e)]
    res.checkpoints = calc.checkpoints(dates)
    m["A8.1"].set([d.isoformat() for d in res.checkpoints], as_of=today)
    from ..generate.catalysts import checkpoint_lines

    lines = checkpoint_lines(m, today=today)
    m["A8.1"].question = ("날짜 · 사건 — 검증할 것 → 후속 조치 [바꾸는 노드]\n" + "\n".join(lines)) if lines else ""
    m.touch(today)
    return res


# --------------------------------------------------------------------------- A → B 스냅숏
def snapshot_to_holding(a: NewMap, position: Position, *, today: date | None = None,
                        market: MarketState | None = None) -> HoldingMap:
    """매수 체결 시 A0 / A1(current→at_entry) / A4 / A5.4 / A7.1 / A9.2·A9.3 링크를 B1 에 복사한다 (§7)."""
    today = today or position.entry_date
    assert a.ticker and a.name
    b = hold_map_skeleton(a.ticker, a.name, a.profile, today, n_drivers=max(3, len(a.drivers())))
    b.source_map_id = a.map_id
    b.position = position
    b.market = market or MarketState(as_of=today, price=position.entry_price)
    b["B0.4"].set(position.w_target, as_of=today)

    # B1 스냅숏 (불변)
    b["B1.1"].set(a.thesis, as_of=today, source=Source(name=f"A map {a.map_id}", retrieved_at=today))
    drivers_at_entry: dict[str, Any] = {}
    for i, n in enumerate(a.drivers(), start=1):
        d = n.driver
        assert d is not None
        drivers_at_entry[n.id] = {"label": n.label, "at_entry": d.current.value, "my_estimate": d.my_estimate.value}
        bd = copy.deepcopy(d)
        bd.at_entry = d.current.value
        bd.direction = Direction.유지
        bn = b.get(f"B2.1.{i}") or b.add(Node(id=f"B2.1.{i}", map=MapKind.HOLD, parent="B2.1", label=n.label,
                                                node_type="estimate", value_type=n.value_type))
        bn.label = n.label
        bn.unit = n.unit
        bn.driver = bd
        bn.as_of = d.current.as_of or today
        bn.links = [n.id]
    b["B1.2"].set(drivers_at_entry, as_of=today, source=Source(name="A1 snapshot", retrieved_at=today))
    sc = a.scenarios()
    b["B1.3"].set({k: {"probability": v.probability, "target_price": v.target_price,
                       "horizon_months": v.horizon_months} for k, v in sc.items()},
                  as_of=today, source=Source(name="A4 snapshot", retrieved_at=today))
    # B3 초기값 = A4 (재추정 전까지)
    for k, nid in (("Bear", "B3.1"), ("Base", "B3.2"), ("Bull", "B3.3")):
        if k in sc:
            b[nid].scenario = sc[k].model_copy(deep=True)
            b[nid].as_of = today
    # B1.4 kill criteria → 그대로 복사 (RED 트리거)
    for j, r in enumerate(a.kill_rules(), start=1):
        rr = r.model_copy(deep=True)
        rr.escalate_to = rr.escalate_to or Light.RED
        b.add(Node(id=f"B1.4.{j}", map=MapKind.HOLD, parent="B1.4", label=f"무효화 조건 {j}",
                   node_type="rule", value_type="text", rule=rr, as_of=today))
    # B7 사전 약속 ← A7.3 / A7.4 + 비중 밴드 규칙 기본 1개
    j = 0
    for r in a.exec_rules():
        j += 1
        b.add(Node(id=f"B7.{j}", map=MapKind.HOLD, parent="B7", label=f"사전 약속 {j}", node_type="rule",
                   value_type="text", rule=r.model_copy(deep=True), as_of=today))
    j += 1
    band = calc.weight_band(position.w_target)
    b.add(Node(id=f"B7.{j}", map=MapKind.HOLD, parent="B7", label="비중 밴드 상한", node_type="rule",
               value_type="text", as_of=today,
               rule=Rule(trigger_type="weight", condition=f"w_now > {position.w_target + band:.4f}",
                         action=Action.trim, size="to_target", escalate_to="YELLOW",
                         note="템플릿 §4.4 비중 밴드 — 자동 생성")))
    # B1.5 / B1.6
    b["B1.5"].set(ReconstructionQuality.기록.value, as_of=today)
    links = [lk for nid in ("A9.2", "A9.3") if nid in a for lk in a[nid].links]
    b["B1.6"].links = links
    b["B1.6"].set(links or None, as_of=today)
    b["B8.1"].set(get_profile(a.profile).review_cadence, as_of=today)
    b.snapshot_hash = snapshot_hash(b)
    b.touch(today)
    return b


# --------------------------------------------------------------------------- 논제 복원 위자드
def reconstruct_holding(ticker: str, name: str, position: Position, *, invest_type: str | None,
                        thesis: str | None, drivers: list[dict[str, Any]] | None = None,
                        kill_conditions: list[str] | None = None, today: date | None = None,
                        market: MarketState | None = None) -> HoldingMap:
    """맵 없이 보유 중인 종목. thesis 가 없으면 B1.5 = 불가 ('논제 없는 보유' → 최소 🟡).

    drivers: [{"label", "current", "my_estimate", "at_entry"?, "check_indicator"?, "next_check"?}]
    """
    today = today or date.today()
    b = hold_map_skeleton(ticker, name, invest_type, today, n_drivers=max(3, len(drivers or [])))
    b.position = position
    b.market = market or MarketState(as_of=today)
    b["B0.4"].set(position.w_target, as_of=today)
    if thesis:
        b["B1.1"].set(thesis, as_of=today, source=Source(name="논제 복원 위자드", retrieved_at=today))
        b["B1.5"].set(ReconstructionQuality.재구성.value, as_of=today)
    else:
        b["B1.5"].set(ReconstructionQuality.불가.value, as_of=today)
        b.nodes["B0"].tags.append("논제 없는 보유")
    for i, d in enumerate(drivers or [], start=1):
        drv = Driver(current=Measurement(value=d.get("current"), as_of=today),
                     my_estimate=Measurement(value=d.get("my_estimate")),
                     check_indicator=d.get("check_indicator", ""), next_check=d.get("next_check"),
                     at_entry=d.get("at_entry", d.get("current")))
        drv.direction = drv.compute_direction() or Direction.유지
        n = b.get(f"B2.1.{i}") or b.add(Node(id=f"B2.1.{i}", map=MapKind.HOLD, parent="B2.1",
                                               label=d["label"], node_type="estimate", value_type="number"))
        n.label = d["label"]
        n.driver = drv
        n.as_of = today
    for j, cond in enumerate(kill_conditions or [], start=1):
        b.add(Node(id=f"B1.4.{j}", map=MapKind.HOLD, parent="B1.4", label=f"무효화 조건 {j}", node_type="rule",
                   value_type="text", as_of=today,
                   rule=Rule(trigger_type="fact", condition=cond, action=Action.exit, escalate_to="RED")))
    band = calc.weight_band(position.w_target)
    b.add(Node(id="B7.1", map=MapKind.HOLD, parent="B7", label="비중 밴드 상한", node_type="rule", value_type="text",
               as_of=today, rule=Rule(trigger_type="weight", condition=f"w_now > {position.w_target + band:.4f}",
                                      action=Action.trim, size="to_target", escalate_to="YELLOW")))
    b.snapshot_hash = snapshot_hash(b)
    b.touch(today)
    return b


# --------------------------------------------------------------------------- B 운영 루프
def update_market(b: HoldingMap, market: MarketState) -> None:
    b.market = market
    if b.position and market.price:
        b.position.peak_price = max(b.position.peak_price or 0.0, market.price)
    if market.w_now is not None:
        b["B0.3"].set(market.w_now, as_of=market.as_of, source=Source(name="positions", retrieved_at=market.as_of))
    # 평가손익은 메타데이터로만 기록 (B0.5, meta_only)
    if b.position and market.price:
        b["B0.5"].set(round(market.price / b.position.entry_price - 1, 4), as_of=market.as_of,
                      source=Source(name="market", retrieved_at=market.as_of))


def update_driver(b: HoldingMap, driver_id: str, value: float, *, as_of: date, source: Source | None = None,
                  settings: Settings | None = None) -> Direction | None:
    """드라이버 현재값 갱신 → history 적재 → direction 재계산."""
    s = settings or Settings()
    n = b[driver_id]
    d = n.driver
    assert d is not None, f"{driver_id} is not a driver node"
    if d.current.value is not None and d.current.as_of is not None:
        n.history.append(HistoryPoint(as_of=d.current.as_of, value=d.current.value, source=d.current.source))
    d.current.value = value
    d.current.as_of = as_of
    if source:
        d.current.source = source
    n.as_of = as_of
    n.status = "estimated" if source is None else "confirmed"
    d.direction = d.compute_direction(s.driver_tol_rel)
    return d.direction


def apply_event(b: HoldingMap, ev: Event, *, settings: Settings | None = None) -> Node:
    """사실 로그(B2.4) 에 적재. affects 의 드라이버 direction 은 갱신하지 않는다 — 값 갱신(update_driver)이 한다."""
    idx = len(b.children("B2.4")) + 1
    n = b.add(Node(id=f"B2.4.{idx:03d}", map=MapKind.HOLD, parent="B2.4", label=ev.fact[:40], node_type="event",
                   value_type="text", event=ev, as_of=ev.date, source=ev.source,
                   status="confirmed" if ev.source else "unverified",
                   tags=[] if not ev.is_noise() else ["noise"]))
    return n


@dataclass
class HoldingEval:
    light: LightResult
    matrix: MatrixResult
    expected_return: float | None = None
    risk_reward: float | None = None
    decomposition: calc.Decomposition | None = None
    valuation_state: str | None = None
    weight_state: str | None = None
    relative_edge: float | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [str(self.light), f"매트릭스 기본 대응: {self.matrix}"]
        if self.expected_return is not None:
            lines.append(f"기대수익률 {self.expected_return:+.1%} · 손익비 {self.risk_reward if self.risk_reward is None else round(self.risk_reward, 2)}")
        if self.decomposition and self.decomposition.consistent:
            d = self.decomposition
            lines.append(f"주가 분해: 총 {d.r_total:+.1%} = {d.metric} {d.d_metric:+.1%} × 멀티플 {d.d_multiple:+.1%} — {d.interpretation}")
        lines += self.notes
        return "\n".join(lines)


def evaluate_holding(b: HoldingMap, *, today: date | None = None, settings: Settings | None = None,
                     alternatives_er: list[float] | None = None, regime_changed: bool = False,
                     event_imminent: bool = False) -> HoldingEval:
    """B2.2 / B3.4~6 / B3.8 / B4.1 / B4.2 / B4.5 / B0 를 계산하고 매트릭스 기본 대응을 돌려준다. B6 은 쓰지 않는다."""
    today = today or date.today()
    s = settings or Settings()
    prof = get_profile(b.profile)
    notes: list[str] = []
    mk, pos = b.market, b.position

    # stale
    stale_ids = mark_stale(b, today, prof.stale_days)
    if stale_ids:
        notes.append(f"stale 전환: {', '.join(stale_ids)}")

    # B2.2 주가 분해
    decomp = None
    if pos and mk.price:
        decomp = calc.decompose_return(pos.entry_price, mk.price, pos.eps_fwd_entry, mk.eps_fwd, s.decomp_metric)
        b["B2.2"].set(decomp.as_dict(), as_of=today)

    # B3.4 / B3.5
    er = rr = None
    sc = b.scenarios()
    if mk.price and len(sc) == 3:
        try:
            er = calc.expected_return(sc, mk.price)
            rr = calc.risk_reward(sc, mk.price, prof.rr_upside)
            b["B3.4"].set(round(er, 4), as_of=today)
            b["B3.5"].set(None if rr is None else round(rr, 2), as_of=today)
        except calc.CalcError as e:
            notes.append(f"시나리오 계산 불가: {e}")

    # B3.6 밸류 상태
    vs = calc.valuation_state(mk.band_percentile, s)
    if vs:
        b["B3.6"].set(vs.value, as_of=today)

    # B3.8 상대 우위
    edge = None
    alts = list(alternatives_er or [])
    if mk.cash_yield is not None:
        alts.append(mk.cash_yield)
    if er is not None and alts:
        edge = calc.relative_edge(er, alts)
        b["B3.8"].set(round(edge, 4), as_of=today)

    # B4.1 비중 상태 / B4.2 / B4.5
    w_target = pos.w_target if pos else b.value("B0.4")
    ws = calc.weight_state(mk.w_now, w_target, s)
    if ws:
        b["B4.1"].set(ws.value, as_of=today)
    if pos and mk.w_now is not None and decomp is not None:
        share = calc.gain_driven_weight_share(w_target, mk.w_now, decomp.r_total)
        if share is not None:
            b["B4.2"].set(round(share, 2), as_of=today)
    if pos and mk.adv_shares:
        days = calc.liquidation_days(pos.quantity, mk.adv_shares, s.participation_rate)
        if days is not None:
            b["B4.5"].set(round(days, 1), as_of=today)

    # B0 상태등
    ctx = RuleContext.from_holding(b, today)
    light = compute_light(b, ctx, today=today, settings=s, profile=prof, regime_changed=regime_changed)

    # 매트릭스 기본 대응 (B6 은 사람이 확정)
    mres = decide(b.thesis_state, vs, ws, profile=prof, settings=s, no_thesis=b.no_thesis,
                  relative_edge=edge, event_imminent=event_imminent)
    b.meta["matrix_default"] = mres.action.value
    b.meta["matrix_rule"] = mres.rule
    b.touch(today)
    return HoldingEval(light, mres, er, rr, decomp, vs.value if vs else None, ws.value if ws else None, edge, notes)


def record_decision(b: HoldingMap, decision: Action | str, *, today: date, reason: str = "", size: str | None = None,
                    bias_check: BiasCheck | None = None, price: float | None = None) -> DecisionLog:
    """B6 확정 + 결정 로그 1건. 매트릭스 기본값과 다르면 override 로 표시하고 사유를 요구한다."""
    act = Action(decision)
    default = b.meta.get("matrix_default")
    default_act = Action(default) if default else None
    override = default_act is not None and act != default_act
    if override and not reason:
        raise ValueError(f"매트릭스 기본값 {default_act.value} 와 다른 결정 {act.value} 에는 사유가 필요하다")
    ts, vs, ws = b.thesis_state, b.value("B3.6"), b.value("B4.1")
    log = DecisionLog(date=today, decision=act, size=size, reason=reason,
                      price=price if price is not None else b.market.price,
                      thesis_state=ts, valuation_state=vs, weight_state=ws,
                      matrix_default=default_act, override=override, bias_check=bias_check)
    b.decision_log.append(log)
    b["B6"].set(act.value, as_of=today)
    if bias_check:
        for nid, flag in zip(("B5.1", "B5.2", "B5.3", "B5.4", "B5.5", "B5.6"),
                             (bias_check.anchoring, bias_check.sunk_cost, bias_check.disposition,
                              bias_check.endowment, bias_check.confirmation, bias_check.breakeven_plan), strict=True):
            b[nid].set(flag, as_of=today)
    b.touch(today)
    return log


def close_holding(b: HoldingMap, *, today: date, thesis_correct: bool | None, wrong_assumption: str = "",
                  biases: list[str] | None = None, lesson: str = "") -> dict[str, Any]:
    """exit 완료 → B 종료 + B8.5 사후 복기."""
    pm = {"thesis_correct": thesis_correct, "wrong_assumption": wrong_assumption,
          "biases": biases or [], "lesson": lesson, "closed_at": today.isoformat()}
    b["B8.5"].set(pm, as_of=today)
    b.closed_at = today
    b.touch(today)
    return pm


def set_thesis_state(b: HoldingMap, state: ThesisState | str, *, today: date, note: str = "") -> None:
    b["B2.3"].set(ThesisState(state).value, as_of=today)
    if note:
        b.meta.setdefault("thesis_notes", []).append({"date": today.isoformat(), "note": note})


__all__ = [
    "NewMapEval", "evaluate_new_map", "snapshot_to_holding", "reconstruct_holding", "update_market",
    "update_driver", "apply_event", "HoldingEval", "evaluate_holding", "record_decision", "close_holding",
    "set_thesis_state", "Scenario",
]
