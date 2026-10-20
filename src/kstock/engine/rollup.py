"""C 포트폴리오 롤업 (템플릿 §5). 직접 채우는 노드가 거의 없다 — B 맵들과 포지션에서 계산된다."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import Action, HoldingMap, Light, NodeStatus, Portfolio, ReconstructionQuality, Settings
from ..templates.skeleton import portfolio_skeleton
from . import calc


@dataclass
class HoldingRow:
    map_id: str
    ticker: str
    name: str
    w_now: float
    w_target: float
    light: str | None
    thesis_state: str | None
    decision: str | None
    matrix_default: str | None
    expected_return: float | None
    risk_reward: float | None
    sector: str | None
    no_thesis: bool
    value: float  # 평가금액


def _row(b: HoldingMap, total_assets: float) -> HoldingRow:
    mk, pos = b.market, b.position
    value = (pos.quantity * mk.price) if (pos and mk.price) else 0.0
    w_now = mk.w_now if mk.w_now is not None else (value / total_assets if total_assets else 0.0)
    sectors = b.sector_links()
    return HoldingRow(
        map_id=b.map_id, ticker=b.ticker or "", name=b.name or "", w_now=w_now,
        w_target=pos.w_target if pos else (b.value("B0.4") or 0.0),
        light=b.value("B0"), thesis_state=b.value("B2.3"), decision=b.value("B6"),
        matrix_default=b.meta.get("matrix_default"), expected_return=b.value("B3.4"),
        risk_reward=b.value("B3.5"), sector=sectors[0] if sectors else None,
        no_thesis=b.reconstruction == ReconstructionQuality.불가, value=value,
    )


def rollup(holdings: list[HoldingMap], *, cash: float, total_assets: float | None = None,
           cash_yield: float = 0.03, today: date | None = None, settings: Settings | None = None,
           portfolio: Portfolio | None = None) -> Portfolio:
    """B 맵 목록 + 현금 → C 맵. total_assets 를 주지 않으면 평가금액 합 + 현금으로 계산."""
    today = today or date.today()
    s = settings or Settings()
    open_maps = [b for b in holdings if b.is_open]
    if total_assets is None:
        total_assets = cash + sum((b.position.quantity * b.market.price) for b in open_maps
                                  if b.position and b.market.price)
    rows = [_row(b, total_assets) for b in open_maps]
    c = portfolio or portfolio_skeleton(today)
    c.holding_ids = [r.map_id for r in rows]
    c.touch(today)

    # C0
    lights = Counter(r.light or "UNKNOWN" for r in rows)
    cash_w = cash / total_assets if total_assets else 0.0
    c["C0"].set({"total_assets": total_assets, "cash_weight": round(cash_w, 4), "n_holdings": len(rows),
                 "status_dist": {k.value: lights.get(k.value, 0) for k in Light}, "as_of": today.isoformat()},
                as_of=today)

    # C1
    rows_sorted = sorted(rows, key=lambda r: r.w_now, reverse=True)
    c["C1.1"].set([r.__dict__ for r in rows_sorted], as_of=today)
    top3 = sum(r.w_now for r in rows_sorted[:3])
    c["C1.2"].set(round(top3, 4), as_of=today)
    h = calc.hhi(r.w_now for r in rows)
    c["C1.3"].set(round(h, 4), as_of=today)
    c["C1.4"].set({"max_weight": round(rows_sorted[0].w_now, 4) if rows_sorted else 0.0, "w_max": s.w_max,
                   "breach": bool(rows_sorted and rows_sorted[0].w_now > s.w_max)}, as_of=today)
    warnings: list[str] = []
    if h > s.hhi_warn:
        warnings.append(f"HHI {h:.3f} > {s.hhi_warn} 집중 경고")
    if top3 > s.top3_warn:
        warnings.append(f"상위 3종목 {top3:.0%} > {s.top3_warn:.0%}")

    # C2 노출
    by_sector: dict[str, float] = defaultdict(float)
    for r in rows:
        by_sector[r.sector or "미분류"] += r.w_now
    c["C2.1"].set({k: round(v, 4) for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])}, as_of=today)
    by_factor: dict[str, float] = defaultdict(float)
    by_ccy: dict[str, float] = defaultdict(float)
    for b, r in zip(open_maps, rows, strict=True):
        tags = set(b.root().tags) | set(b["B0"].tags)
        for n in b.nodes.values():
            tags |= {t for t in n.tags if t.startswith(("팩터:", "통화:"))}
        for t in tags:
            if t.startswith("팩터:"):
                by_factor[t[3:]] += r.w_now
            elif t.startswith("통화:"):
                by_ccy[t[3:]] += r.w_now
    c["C2.2"].set({k: round(v, 4) for k, v in by_factor.items()}, as_of=today)
    c["C2.3"].set({k: round(v, 4) for k, v in by_ccy.items()} or {"KRW": round(1 - cash_w, 4)}, as_of=today)

    # C3 대응 집계: B6 (확정) 우선, 없으면 매트릭스 기본값
    net_buy = net_sell = 0.0
    orders: list[dict[str, Any]] = []
    for r in rows:
        act = r.decision or r.matrix_default
        if act in (Action.trim.value, Action.exit.value, Action.switch.value) and r.w_now > 0:
            target = r.w_target if act == Action.trim.value else 0.0
            amt = max(0.0, (r.w_now - target) * total_assets)
            net_sell += amt
            orders.append({"ticker": r.ticker, "action": act, "amount": round(amt), "from_w": r.w_now, "to_w": target})
        if act in (Action.add.value, Action.switch.value):
            target = r.w_target
            amt = max(0.0, (target - r.w_now) * total_assets) if act == Action.add.value else 0.0
            if amt > 0:
                net_buy += amt
                orders.append({"ticker": r.ticker, "action": "add", "amount": round(amt), "from_w": r.w_now, "to_w": target})
    c["C3.1"].set({"net_buy": round(net_buy), "net_sell": round(net_sell), "orders": orders}, as_of=today)
    cash_after = (cash + net_sell - net_buy) / total_assets if total_assets else 0.0
    c["C3.2"].set(round(cash_after, 4), as_of=today)
    order_rank = {"exit": 0, "trim": 1, "switch": 2, "add": 3}
    c["C3.3"].set([o["ticker"] + ":" + o["action"] for o in sorted(orders, key=lambda o: order_rank.get(o["action"], 9))],
                  as_of=today)
    conflicts: list[str] = []
    if cash + net_sell - net_buy < 0:
        conflicts.append(f"현금 부족: {cash + net_sell - net_buy:,.0f}")
    sector_actions: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r.sector and (r.decision or r.matrix_default):
            sector_actions[r.sector].add(r.decision or r.matrix_default)
    for sec, acts in sector_actions.items():
        if "add" in acts and ("exit" in acts or "trim" in acts):
            conflicts.append(f"{sec}: 같은 섹터에서 add 와 exit/trim 동시 — 교체 후보로 묶어 검토")
    c["C3.4"].set(conflicts, as_of=today)

    # C4 drift
    c["C4.1"].set({r.ticker: round(r.w_now - r.w_target, 4) for r in rows}, as_of=today)
    gains = [r.ticker for b, r in zip(open_maps, rows, strict=True)
             if (r.decision or r.matrix_default) in ("exit", "trim") and (b.value("B0.5") or 0) > 0]
    losses = [r.ticker for b, r in zip(open_maps, rows, strict=True)
              if (r.decision or r.matrix_default) == "exit" and (b.value("B0.5") or 0) < 0]
    c["C4.4"].set({"gain_candidates": gains, "loss_candidates": losses,
                   "note": "해외주식·대주주 양도세 대상에만 적용. 평가손익은 여기(세금)와 복기에서만 쓴다"}, as_of=today)

    # C5 품질
    nt = [r for r in rows if r.no_thesis]
    c["C5.1"].set({"count": len(nt), "weight": round(sum(r.w_now for r in nt), 4),
                   "tickers": [r.ticker for r in nt]}, as_of=today)
    n_nodes = sum(len(b.nodes) for b in open_maps) or 1
    n_stale = sum(1 for b in open_maps for n in b.nodes.values() if n.status == NodeStatus.stale)
    c["C5.2"].set(round(n_stale / n_nodes, 4), as_of=today)
    covered = sum(1 for b in open_maps
                  if any(n.rule and n.rule.trigger_type == "fact" for n in b.rules() + b.children("B1.4")))
    c["C5.3"].set(round(covered / len(open_maps), 4) if open_maps else None, as_of=today)
    port_er = sum(r.w_now * r.expected_return for r in rows if r.expected_return is not None) + cash_w * cash_yield
    c["C5.4"].set(round(port_er, 4), as_of=today)
    rrs = [r.risk_reward for r in rows if r.risk_reward is not None]
    c["C5.5"].set(round(sum(rrs) / len(rrs), 2) if rrs else None, as_of=today)
    logs = [lg for b in open_maps for lg in b.decision_log]
    c["C5.6"].set(round(sum(1 for lg in logs if lg.override) / len(logs), 4) if logs else None, as_of=today)

    # C6 복기 통합
    c["C6.1"].set([{"ticker": b.ticker, **lg.model_dump(mode="json", exclude={"bias_check"})}
                   for b in holdings for lg in b.decision_log], as_of=today)
    bias_counter: Counter[str] = Counter()
    for b in holdings:
        for lg in b.decision_log:
            if lg.bias_check:
                bias_counter.update(lg.bias_check.flagged())
    c["C6.3"].set(dict(bias_counter), as_of=today)
    c.meta["warnings"] = warnings
    return c


__all__ = ["rollup", "HoldingRow"]
