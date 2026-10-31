"""성립 조건 (설계 v7 §3.1~3.3 — T5·T6): 가정 표 · 역산 표(한 변수 해 · 두 변수 조합) · 날짜별 필요 경로 · A2.4/A5.6 노드.

- 가정 표에는 **실제로 적힌 것**만: 증권사별 전망 셀(Claim, certainty=expected) × 목표가, 기준을 통일한 컨센, 내 가정(Base 시나리오). 역산 숫자는 들어오지 않는다.
- 역산은 수학적 해다. 통합 1차 모델에서 가격은 각 변수에 선형이므로 두 점을 재서 닫힌 식으로 풀고(이분 탐색 없음), 기울기 0 → "해 없음",
  허용 범위 밖 → "범위 밖". 환원(fcf) 변수는 가격에 닿지 않아 역산하지 않고, 수주(orders) 는 통합 모델이 아직 못 푼다.
- 필요 경로는 연간 한 점이 아니라 날짜별 점: ① Claim 의 분기 QoQ 셀을 이어 지수 경로(증권사/컨센) ② 사업부 표의 분기 열(컨센)
  ③ 연간만 있으면 등비 보간(표시). 숫자를 만들어 채우지 않는다 — 없으면 "경로 없음".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import DriverAssumption, MapKind, MindMap, NewMap, PathPoint, RequiredPath, Ruler, Scenario
from .evidence import normalize_period, period_key
from .scenario_model import evaluate_scenario
from .valuation import PER_SOURCE_LABEL, common_tail

V7_NODES = ("A2.4", "A5.6")
RANGE = {"asp": (-80.0, 200.0), "volume": (-80.0, 200.0), "cost": (-30.0, 30.0), "margin": (-30.0, 30.0), "op": (-500.0, 500.0)}
NOT_INVERTIBLE = {"fcf": "환원 변수는 가격에 닿지 않는다 (주당 환원만)", "orders": "수주 변수는 매출 전환 기간이 있어 통합 모델이 아직 풀지 않는다 (전환율·마진 입력 뒤 T10)",
                  "backlog": "수주잔고는 통합 모델이 아직 풀지 않는다"}
GRID_DEFAULT = (-60.0, -40.0, -20.0, -10.0, 0.0, 10.0, 20.0)


def ensure_v7_nodes(m: MindMap) -> list[str]:
    """A2.4 (성립 조건) · A5.6 (판단 수정 규칙) 이 없으면 만든다 (사용자 결정: 신설)."""
    if not isinstance(m, NewMap):
        return []
    from ..templates.questions import A_SPEC
    from ..templates.skeleton import _node_from_spec

    made: list[str] = []
    for nid in V7_NODES:
        if nid not in m.nodes and nid in A_SPEC and nid.rsplit(".", 1)[0] in m:
            m.add(_node_from_spec(MapKind.NEW, nid, A_SPEC[nid]))
            made.append(nid)
    return made


# --------------------------------------------------------------------------- 드라이버 메타
def _meta(m: MindMap, node_id: str) -> dict[str, Any]:
    return ((m.meta.get("draft") or {}).get("drivers") or {}).get(node_id) or {}


def _variable(m: MindMap, node_id: str) -> str | None:
    info = _meta(m, node_id)
    if info.get("variable"):
        return str(info["variable"])
    d = m[node_id].driver
    if d is not None and d.metric and ":" in d.metric:
        return d.metric.split(":")[1] or None
    return {"price_volume": "asp", "cost_ratio": "cost", "direct_op": "op", "payout": "fcf", "orders": "orders"}.get(info.get("template") or "", None)


def cause_driver_ids(m: NewMap) -> list[str]:
    return [n.id for n in m.drivers() if n.driver is not None and n.driver.role != "result"]


# --------------------------------------------------------------------------- 역산: 한 변수 해 · 두 변수 조합
@dataclass
class Inversion:
    node_id: str
    label: str
    variable: str | None
    scope: str = "single"                  # single | pair
    status: str = "미계산"                 # ok | no_solution | out_of_range | not_applicable | 미계산
    value: float | None = None             # 변수 변화 % (컨센 대비; 원가율은 %p; 손익 직접은 % of |OP|)
    unit: str = "%"
    price: float | None = None
    per: float | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _price_at(m: NewMap, node_id: str, x: float, *, per: float | None, today: date, fixed: dict[str, float] | None = None) -> float | None:
    das = [DriverAssumption(node_id=node_id, delta_pct=x)] + [DriverAssumption(node_id=k, delta_pct=v) for k, v in (fixed or {}).items()]
    sc = Scenario(name="Base", price_source="computed", per=per, driver_assumptions=das)
    r = evaluate_scenario(m, sc, today=today)
    return r.price


def solve_one(m: NewMap, node_id: str, price: float, *, per: float | None = None, today: date | None = None,
              fixed: dict[str, float] | None = None) -> Inversion:
    """다른 변수 전부 고정(fixed 로 몇 개 지정 가능) 하고 이 변수만 움직여 price 에 닿는 변화 %. 선형이므로 두 점으로 푼다."""
    today = today or date.today()
    n = m[node_id]
    var = _variable(m, node_id)
    inv = Inversion(node_id=node_id, label=n.label.split(" (")[0], variable=var, per=per)
    if var in NOT_INVERTIBLE:
        inv.status, inv.note = "not_applicable", NOT_INVERTIBLE[var]
        return inv
    p0 = _price_at(m, node_id, 0.0, per=per, today=today, fixed=fixed)
    p1 = _price_at(m, node_id, 10.0, per=per, today=today, fixed=fixed)
    if p0 is None or p1 is None:
        inv.status, inv.note = "no_solution", "통합 모델이 이 변수를 가격으로 잇지 못한다 — 필요 입력: 사업부 매출·OP (민감도 미계산)"
        return inv
    slope = (p1 - p0) / 10.0
    if abs(slope) < 1e-9:
        inv.status, inv.note = "no_solution", "기울기 0 — 이 변수는 가격을 움직이지 않는다"
        return inv
    x = (price - p0) / slope
    lo, hi = RANGE.get(var or "", (-500.0, 500.0))
    inv.value, inv.price = x, price
    inv.unit = "%p" if var in ("cost", "margin") else "%"
    inv.extra = {"price_at_0": p0, "slope_per_pct": slope, "range": [lo, hi]}
    if x < lo or x > hi:
        inv.status, inv.note = "out_of_range", f"해 {x:+.0f}{inv.unit} 는 허용 범위({lo:+.0f}~{hi:+.0f}) 밖 — 이 변수만으로는 설명 불가"
    else:
        inv.status, inv.note = "ok", f"다른 변수 고정 · 기울기 {slope:,.0f}원/{inv.unit}"
    return inv


def solve_pair(m: NewMap, x_node: str, price: float, *, y: str = "per", grid: tuple[float, ...] = GRID_DEFAULT,
               today: date | None = None) -> list[dict[str, Any]]:
    """두 변수 조합표: x 변수의 격자마다 y(적용 PER 또는 다른 드라이버) 를 풀어 같은 price 에 닿는 조합. 나머지 고정."""
    today = today or date.today()
    rows: list[dict[str, Any]] = []
    tail = common_tail(m)
    for gx in grid:
        row: dict[str, Any] = {"x": gx, "price": price}
        if y == "per":
            sc = Scenario(name="Base", price_source="computed", driver_assumptions=[DriverAssumption(node_id=x_node, delta_pct=gx)])
            r = evaluate_scenario(m, sc, today=today)
            if r.eps and r.eps > 0:
                row.update({"y": price / r.eps, "eps": r.eps, "status": "ok"})
            else:
                row.update({"y": None, "status": "no_solution"})
        else:
            inv = solve_one(m, y, price, per=tail.per, today=today, fixed={x_node: gx})
            row.update({"y": inv.value, "status": inv.status, "note": inv.note})
        rows.append(row)
    return rows


def _num(v: Any) -> str:
    """표 셀 숫자: 큰 수는 천 단위 구분, 작은 수는 유효숫자 4 — 1e+05 같은 지수 표기는 쓰지 않는다."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    return f"{x:,.4g}"


# --------------------------------------------------------------------------- 가정 표
def assumption_table(m: NewMap) -> dict[str, Any]:
    """증권사별 전망 셀 × 목표가 · 컨센 · 내 가정(Base). 역산 숫자는 없다. meta.draft.cause_cells(Claim 셀) 과 reports(목표가) 로 만든다."""
    draft = m.meta.get("draft") or {}
    cells = [c for c in (draft.get("cause_cells") or []) if c.get("certainty") == "expected"]
    reports = {r.get("broker"): r for r in (draft.get("reports") or []) if r.get("broker")}
    brokers = sorted({b for c in cells for b in (c.get("by_broker") or {})} | set(reports))
    base = m.scenarios().get("Base") or (m.scenario_nodes().get("Base").scenario if m.scenario_nodes().get("Base") else None)
    mine = {da.node_id: da for da in (base.driver_assumptions if base is not None else [])}
    rows: list[dict[str, Any]] = []
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result":
            continue
        did = (d.metric or "").split(":")[0]
        row: dict[str, Any] = {"node_id": n.id, "label": n.label.split(" (")[0], "driver_id": did, "by_broker": {}, "consensus": None, "mine": None}
        for b in brokers:
            vals = [f"{c['variable']} {c['measure']} {c['col']} {_num(c['by_broker'][b])}{c.get('unit') or ''}"
                    for c in cells if c.get("driver_id") == did and b in (c.get("by_broker") or {})]
            row["by_broker"][b] = " · ".join(vals[:3]) if vals else "말하지 않음"
        if d.consensus.value is not None:
            row["consensus"] = f"{_num(d.consensus.value)}{n.unit or ''} ({d.period or ''})"
        elif d.kind == "qualitative":
            row["consensus"] = "정성 (숫자 없음)"
        da = mine.get(n.id)
        if da is not None:
            row["mine"] = f"컨센 대비 {da.delta_pct:+.0f}%" if da.delta_pct is not None else (f"{_num(da.value)}{da.unit or ''}" if da.value is not None else "—")
        else:
            row["mine"] = "컨센 +0%" if base is not None else "—"
        rows.append(row)
    tail = common_tail(m)
    per_row = {"node_id": "PER", "label": "적용 PER", "driver_id": "per", "by_broker": {}, "consensus": None, "mine": None}
    fin = draft.get("financials") or {}
    per_cells = ((fin.get("years") or {}).get(tail.year or "") or {}).get("per") or {}
    for b in brokers:
        v = (per_cells.get("by_broker") or {}).get(b)
        per_row["by_broker"][b] = f"PER 행 {v:.1f} (리포트 가격 기준)" if v else "말하지 않음"
    per_row["consensus"] = (f"목표가 유도 {tail.per:.2f} (순환)" if tail.per_source == "default" and tail.per else (f"{tail.per:.2f} (사용자)" if tail.per else "—"))
    per_row["mine"] = f"{base.per:.2f}" if base is not None and base.per else ("목표가 유도 (순환)" if tail.per_source == "default" else "—")
    rows.append(per_row)
    head = {b: (f"TP {reports[b]['target_price']:,.0f}" if reports.get(b, {}).get("target_price") else "TP —") for b in brokers}
    return {"brokers": brokers, "broker_tp": head, "rows": rows, "note": "증권사 열 = Claim 전망 셀(리포트에 적힌 값), 컨센 = 기준 통일 집계, 내 가정 = Base 시나리오. 역산 숫자는 여기 없다"}


def assumption_table_text(t: dict[str, Any]) -> str:
    if not t.get("rows"):
        return "가정 표 없음"
    brokers = t["brokers"]
    lines = [f"가정 표 — {t['note']}"]
    lines.append("  " + f"{'드라이버':<12}" + "".join(f"{(b[:6] + ' ' + t['broker_tp'][b])[:18]:<19}" for b in brokers) + f"{'컨센':<22}내 가정(Base)")
    for r in t["rows"]:
        lines.append("  " + f"{r['label'][:11]:<12}" + "".join(f"{(r['by_broker'].get(b) or '—')[:18]:<19}" for b in brokers) + f"{(r['consensus'] or '—')[:21]:<22}{r['mine'] or '—'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 역산 표 (단일 · 조합)
def inversion_table(m: NewMap, *, price: float | None = None, pair_x: str | None = None, pair_y: str = "per",
                    grid: tuple[float, ...] = GRID_DEFAULT, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    p = price or m.current_price
    tail = common_tail(m)
    out: dict[str, Any] = {"price": p, "per": tail.per, "per_source": PER_SOURCE_LABEL.get({"default": "broker_target_implied"}.get(tail.per_source, tail.per_source), tail.per_source),
                           "single": [], "pair": None, "scope_labels": {"single": "한 변수 해 — 나머지 변수 고정", "pair": "두 변수 조합 — 사용자가 고른 쌍, 나머지 고정",
                                                                          "broker": "증권사 경로 — 리포트에서 확인된 전망", "consensus": "컨센 경로 — 여러 리포트를 같은 기준으로 집계"}}
    if not p:
        out["note"] = "현재가 없음"
        return out
    ids = cause_driver_ids(m)
    for nid in ids:
        inv = solve_one(m, nid, p, per=tail.per, today=today)
        out["single"].append(inv.__dict__)
    # PER 만으로
    if tail.eps:
        out["single"].append({"node_id": "PER", "label": "적용 PER", "variable": "per", "scope": "single", "status": "ok", "value": p / tail.eps, "unit": "배",
                              "price": p, "per": None, "note": "실적 컨센 고정 — 현재가 ÷ 컨센 EPS", "extra": {}})
    x = pair_x or next((nid for nid in ids if (m[nid].driver and m[nid].driver.thesis_role == "core")), None) or (ids[0] if ids else None)
    if x:
        rows = solve_pair(m, x, p, y=pair_y, grid=grid, today=today)
        out["pair"] = {"x": x, "x_label": m[x].label.split(" (")[0], "y": pair_y, "y_label": ("적용 PER" if pair_y == "per" else m[pair_y].label.split(" (")[0]),
                       "rows": rows}
    return out


def inversion_table_text(t: dict[str, Any]) -> str:
    if not t.get("price"):
        return "역산 표 없음 — " + (t.get("note") or "")
    per_txt = f"{t['per']:.2f} ({t['per_source']})" if t.get("per") is not None else "없음 — 목표가·EPS 또는 A3.2 per 입력 전"
    lines = [f"역산 표 — 현재가 {t['price']:,.0f} 를 설명하는 조합 · 적용 PER {per_txt} · 모든 줄은 '나머지 고정' 의 수학적 해 (시장의 기대가 아니다)"]
    lines.append("  ① 한 변수 해")
    for r in t["single"]:
        if r["status"] == "ok":
            v = f"{r['value']:+.1f}{r['unit']}" if r["unit"] != "배" else f"{r['value']:.2f}배"
            lines.append(f"    {r['label'][:14]:<14} {v:>10}  가능   {r['note']}")
        else:
            v = f"{r['value']:+.0f}{r['unit']}" if r.get("value") is not None else "—"
            tag = {"out_of_range": "범위 밖", "no_solution": "해 없음", "not_applicable": "역산 없음"}.get(r["status"], r["status"])
            lines.append(f"    {r['label'][:14]:<14} {v:>10}  {tag:<5} {r['note']}")
    pr = t.get("pair")
    if pr:
        lines.append(f"  ② 두 변수 조합 — {pr['x_label']} × {pr['y_label']} (같은 가격 {t['price']:,.0f})")
        lines.append(f"    {pr['x_label'][:12] + ' (컨센 대비)':<22} {pr['y_label']:>10}")
        for r in pr["rows"]:
            y = (f"{r['y']:.2f}" if pr["y"] == "per" else f"{r['y']:+.1f}%") if r.get("y") is not None else "—"
            lines.append(f"    {r['x']:>+8.0f}%             {y:>10}" + (f"  {r.get('note', '')}" if r.get("status") not in ("ok", None) else ""))
    lines.append("  해석 범위: " + " / ".join(f"{k}={v}" for k, v in t["scope_labels"].items()))
    return "\n".join(lines)


# --------------------------------------------------------------------------- 필요 경로 (날짜별)
def _geo_interp(a: tuple[int, int], va: float, b: tuple[int, int], vb: float) -> list[tuple[str, float]]:
    """연간 두 점 사이를 분기마다 등비 보간. a·b 는 (연, 월=12)."""
    ya, yb = a[0], b[0]
    nq = (yb - ya) * 4
    if nq <= 1 or va <= 0 or vb <= 0:
        return []
    ratio = (vb / va) ** (1.0 / nq)
    out: list[tuple[str, float]] = []
    v = va
    for i in range(1, nq):
        v *= ratio
        q = (i - 1) % 4 + 1
        yr = ya + 1 + (i - 1) // 4          # 연간 점은 연말(Q4) 이므로 보간 분기는 다음 해 Q1 부터
        out.append((f"{yr % 100:02d}Q{q}", v))
    return out


def build_required_path(m: NewMap, node_id: str, *, price_label: str = "목표가 중앙값", today: date | None = None) -> RequiredPath | None:
    """드라이버 하나의 필요 경로. ① Claim 분기 QoQ 셀 → 지수 경로 ② 사업부 표 분기 열 ③ 연간 등비 보간(표시). 없으면 None."""
    today = today or date.today()
    n = m[node_id]
    d = n.driver
    if d is None:
        return None
    draft = m.meta.get("draft") or {}
    info = _meta(m, node_id)
    did = (d.metric or "").split(":")[0]
    var = _variable(m, node_id)
    tp = (draft.get("financials") or {}).get("tp_median")
    # ① Claim 의 분기 QoQ 셀 (expected) — 기준 100 지수로 잇는다
    # 전망(expected) 셀만 — 실현(realized) 셀은 current 용이지 경로가 아니다. 같은 분기의 셀은 증권사별 값을 합쳐 한 점으로
    by_q: dict[str, dict[str, float]] = {}
    for c in draft.get("cause_cells") or []:
        if c.get("driver_id") != did or c.get("variable") != var or c.get("measure") != "qoq" or c.get("unit") != "%" or c.get("certainty") != "expected":
            continue
        if period_key(c.get("col") or "") is None or "Q" not in normalize_period(c["col"]):
            continue
        by_q.setdefault(normalize_period(c["col"]), {}).update(c.get("by_broker") or {})
    if len(by_q) >= 2:
        pts: list[PathPoint] = []
        idx = 100.0
        qs = sorted(by_q, key=lambda q: period_key(q) or (0, 0))
        for q in qs:
            vals = list(by_q[q].values())
            if not vals:
                continue
            mean = sum(vals) / len(vals)
            idx *= 1.0 + mean / 100.0
            pts.append(PathPoint(at=q, value=round(idx, 1), source="consensus" if len(vals) >= 2 else "broker", note=f"QoQ {mean:+.1f}% ({len(vals)}곳)"))
        ruler = Ruler(metric_id=f"{did}_index", value_type="abs", basis="none", unit="지수", index_base=f"{qs[0]} 직전 = 100")
        return RequiredPath(ruler=ruler, target_price=tp, target_label=price_label, points=pts, method="Claim 분기 QoQ 셀을 이은 지수 경로", computed_at=today)
    # ② 사업부 표 분기 열 (매출 또는 OP)
    seg = info.get("segment")
    if var not in ("asp", "volume", "op"):
        return None          # 원가율·환원·수주 변수는 사업부 매출 지수로 잴 수 없다 — Claim 분기 셀이나 사람 입력만 (숫자를 만들지 않는다)
    metric = "op" if var == "op" else "revenue"
    segs = (draft.get("segments") or {}).get(seg or "", {}) or {}
    # 같은 분기가 '3Q26' · '3Q26E' · '3Q26P' 로 따로 있으면 증권사 수(n) 가 가장 많은 셀을 쓴다 (같으면 접미 없는 셀)
    cols: dict[str, Any] = {}
    for c, v in (segs.get(metric) or {}).items():
        if period_key(c) is None:
            continue
        key = normalize_period(c)
        cur = cols.get(key)
        n_new, n_cur = (v.get("n", 1) if isinstance(v, dict) else 1), ((cur.get("n", 1) if isinstance(cur, dict) else 1) if cur is not None else -1)
        if cur is None or n_new > n_cur or (n_new == n_cur and c[-1:] not in "EPFA"):
            cols[key] = v
    quarters = {c: v for c, v in cols.items() if "Q" in c}
    years = {c: v for c, v in cols.items() if "Q" not in c}
    # 기준 시점 = 오늘 이전에 끝난 마지막 분기 (최근 실적) — 그 이후의 점만 경로다
    from .evidence import period_end

    past = [c for c in quarters if (period_end(c) or today) < today]
    base_at = max(past, key=lambda x: period_key(x) or (0, 0)) if past else None
    if base_at is not None:
        quarters = {c: v for c, v in quarters.items() if (period_key(c) or (0, 0)) >= (period_key(base_at) or (0, 0))}
    if len(quarters) >= 2:
        pts = []
        base_v = None
        for c in sorted(quarters, key=lambda x: period_key(x) or (0, 0)):
            v = quarters[c].get("mean") if isinstance(quarters[c], dict) else quarters[c]
            if v is None:
                continue
            if metric == "op":
                pts.append(PathPoint(at=c, value=round(v / 1e4, 2), source="consensus" if (quarters[c].get("n") or 1) >= 2 else "broker", note=f"{quarters[c].get('n', 1)}곳"))
            else:
                if base_v is None:
                    base_v = v
                pts.append(PathPoint(at=c, value=round(v / base_v * 100.0, 1), source="consensus" if (quarters[c].get("n") or 1) >= 2 else "broker", note=f"매출 {v / 1e4:,.1f}조 ({quarters[c].get('n', 1)}곳)"))
        if pts:
            if metric == "op":
                ruler = Ruler(metric_id=f"{seg}_op", value_type="abs", basis="none", unit="조원")
            else:
                ruler = Ruler(metric_id=f"{seg}_revenue_index", value_type="abs", basis="none", unit="지수", index_base=f"{pts[0].at} = 100")
            return RequiredPath(ruler=ruler, target_price=tp, target_label=price_label, points=pts, method=f"사업부 표 분기 열 ({seg} {metric})", computed_at=today)
    # ③ 연간 두 점 이상 → 등비 보간 (표시)
    ys = sorted(((period_key(c) or (0, 0)), c, (v.get("mean") if isinstance(v, dict) else v)) for c, v in years.items())
    ys = [(k, c, v) for k, c, v in ys if v]
    if len(ys) >= 2 and metric == "revenue":
        pts = []
        base_v = ys[0][2]
        for i in range(len(ys)):
            k, c, v = ys[i]
            pts.append(PathPoint(at=c, value=round(v / base_v * 100.0, 1), source="consensus", note=f"연간 매출 {v / 1e4:,.1f}조"))
            if i + 1 < len(ys):
                for at, iv in _geo_interp(k, v, ys[i + 1][0], ys[i + 1][2]):
                    pts.append(PathPoint(at=at, value=round(iv / base_v * 100.0, 1), source="interpolated", note="등비 보간"))
        pts.sort(key=lambda p: period_key(p.at) or (0, 0))
        ruler = Ruler(metric_id=f"{seg}_revenue_index", value_type="abs", basis="none", unit="지수", index_base=f"{ys[0][1]} = 100")
        return RequiredPath(ruler=ruler, target_price=tp, target_label=price_label, points=pts, method=f"연간 {seg} 매출 사이 등비 보간 (보간 표시)", computed_at=today)
    return None


def required_paths(m: NewMap, *, today: date | None = None, overwrite: bool = False) -> list[str]:
    """모든 원인 드라이버의 필요 경로를 만든다 (사람이 고친 경로(points 에 user 가 있음) 는 overwrite 가 아니면 두고 본다)."""
    today = today or date.today()
    notes: list[str] = []
    for nid in cause_driver_ids(m):
        d = m[nid].driver
        assert d is not None
        if d.required is not None and not overwrite and any(p.source == "user" for p in d.required.points):
            notes.append(f"{nid} 필요 경로: 사용자 점이 있어 유지")
            continue
        rp = build_required_path(m, nid, today=today)
        if d.required is not None and not overwrite and rp is not None and d.required.ruler.metric_id == rp.ruler.metric_id and d.observations:
            # 관측값이 쌓인 경로는 기준 시점(지수 100) 을 옮기지 않는다 — 자가 바뀌면 관측값과 비교할 수 없다. 새 리포트로 다시 만들 때(overwrite) 만 바꾼다
            notes.append(f"{nid} 필요 경로: 관측값이 있어 기준 유지 ({d.required.ruler.index_base or d.required.ruler.unit})")
            continue
        d.required = rp
        notes.append(f"{nid} 필요 경로: " + (f"{rp.method} · {len(rp.points)}점" + (" (보간 포함)" if rp.interpolated else "") if rp else "없음 — 분기 추정치 또는 사람 입력 필요"))
    return notes


def required_path_text(m: NewMap) -> str:
    lines = ["필요 경로 — 기준 가격이 성립하려면 언제까지 무엇이 어디에 있어야 하나 (날짜별 · 출처 표시 · 보간은 항상 표시)"]
    any_ = False
    for nid in cause_driver_ids(m):
        d = m[nid].driver
        assert d is not None
        lab = m[nid].label.split(" (")[0]
        if d.required is None:
            lines.append(f"  {lab[:14]:<14} 경로 없음 — 분기 추정치(Claim·사업부 표) 또는 사람 입력 필요")
            continue
        any_ = True
        rp = d.required
        pts = " · ".join(f"{p.at} {p.value}{'*' if p.source == 'interpolated' else ''}" for p in rp.points[:8])
        lines.append(f"  {lab[:14]:<14} [{rp.ruler.label()}] {pts}" + (" …" if len(rp.points) > 8 else "") + f"  ← {rp.method}")
    if any_:
        lines.append("  (* = 보간 · 지수는 기준 시점 100)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- A2.4 쓰기
def write_conditions(m: NewMap, *, today: date | None = None, price: float | None = None) -> dict[str, Any]:
    """가정 표 · 역산 표 · 필요 경로를 계산해 meta.draft.conditions 와 A2.4 값에 쓴다."""
    today = today or date.today()
    ensure_v7_nodes(m)
    at = assumption_table(m)
    inv = inversion_table(m, price=price, today=today)
    notes = required_paths(m, today=today)
    cond = {"assumptions": at, "inversion": inv, "path_notes": notes, "computed_at": today.isoformat()}
    draft = m.meta.setdefault("draft", {})
    draft["conditions"] = cond
    if "A2.4" in m:
        n = m["A2.4"]
        n.value = conditions_text(m, cond)
        n.as_of = today
    return cond


def conditions_text(m: NewMap, cond: dict[str, Any] | None = None) -> str:
    cond = cond or ((m.meta.get("draft") or {}).get("conditions") or {})
    if not cond:
        return "성립 조건 미계산 — `mindmap path` 또는 콘솔 「D」"
    return "\n".join([assumption_table_text(cond.get("assumptions") or {}), "", inversion_table_text(cond.get("inversion") or {}), "", required_path_text(m)])


__all__ = ["ensure_v7_nodes", "solve_one", "solve_pair", "Inversion", "assumption_table", "assumption_table_text", "inversion_table", "inversion_table_text",
           "build_required_path", "required_paths", "required_path_text", "write_conditions", "conditions_text", "cause_driver_ids", "V7_NODES", "NOT_INVERTIBLE"]
