"""규칙 채움 — 리포트 합산(ReportDigest)에서 숫자로 결정되는 노드를 채운다 (자동 생성 계획 §3a).

LLM 없이 돌아간다. 채운 노드에는 전부 `draft` 태그가 붙고, 추정(my_estimate)에는 '초안' 이 적힌다.
판단(A6.5)은 항상 WATCH. 사실(fact) 은 리포트를 Source 로 달아 confirmed, 판단(judgment) 은 unverified 로 둔다 —
사람이 저장할 때 확정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..models import (
    Action,
    Driver,
    Event,
    MapKind,
    Measurement,
    NewMap,
    Node,
    NodeStatus,
    Rule,
    Scenario,
    Source,
)
from ..reports.digest import AMOUNT_METRICS, ReportDigest
from .kpi_catalog import kpi_candidates

DRAFT = "초안 — 논제의 주인이 확정할 것"
DRAFT_TAG = "draft"
SCENARIO_P = {"Bear": 0.30, "Base": 0.45, "Bull": 0.25}


@dataclass
class RuleFillResult:
    filled: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    anchors: dict[str, float] = field(default_factory=dict)
    sector: str | None = None
    price_source: str = ""


# ---------------------------------------------------------------------------- 도우미
def mark_draft(n: Node) -> None:
    if DRAFT_TAG not in n.tags:
        n.tags = sorted(set(n.tags) | {DRAFT_TAG})


def _set(m: NewMap, nid: str, value: Any, *, today: date, source: Source | None = None,
         status: NodeStatus | None = None, res: RuleFillResult | None = None) -> Node:
    n = m[nid]
    n.set(value, as_of=today, source=source, status=status)
    mark_draft(n)
    if res is not None:
        res.filled.append(nid)
    return n


def _trim(s: str, n: int = 140) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def next_earnings(today: date) -> list[tuple[date, str, str]]:
    """다음 두 분기의 (예정일 추정, 라벨, 종류). 잠정 = 분기 말 +8일, 확정 = +45일 (법정 시한) — 통상 일정 기준 추정."""
    out: list[tuple[date, str, str]] = []
    q_end_month = ((today.month - 1) // 3 + 1) * 3
    y, mth = today.year, q_end_month
    for _ in range(3):
        last_day = (date(y + (mth // 12), (mth % 12) + 1, 1) - timedelta(days=1))
        q = mth // 3
        yy = str(y)[2:]
        prov, final = last_day + timedelta(days=8), last_day + timedelta(days=45)
        if prov > today:
            out.append((prov, f"{q}Q{yy} 잠정실적 발표 (예정일 추정)", "잠정"))
        if final > today:
            out.append((final, f"{q}Q{yy} 확정실적·사업보고서 (예정일 추정)", "확정"))
        mth += 3
        if mth > 12:
            mth, y = 3, y + 1
        if len(out) >= 4:
            break
    return out[:4]


def _year_num(label: str) -> int:
    return int(label[:4])


# ---------------------------------------------------------------------------- 채움
def fill_price(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult,
               price: tuple[float, date | None, Source] | None = None) -> float | None:
    """A0.4 현재가. 외부(리서치 DB) 값이 있으면 그것, 없으면 가장 최근 리포트의 현재주가."""
    if price is not None:
        p, as_of, src = price
        _set(m, "A0.4", p, today=as_of or today, source=src, res=res)
        res.price_source = src.name
        return p
    rp = d.price
    if rp is None:
        res.notes.append("현재가를 찾지 못했다 — A0.4 를 직접 적어야 한다 (리서치 DB 연결 시 자동)")
        return None
    p, as_of, f = rp
    src = Source(name=f"{f.broker or '리포트'} {f.date or ''} 리포트 현재주가 ({as_of or '기준일 미상'})".strip(), url=str(f.path), retrieved_at=f.date)
    _set(m, "A0.4", p, today=as_of or today, source=src, res=res)
    res.price_source = src.name
    return p


def fill_drivers(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, sector: str | None) -> list[str]:
    """A1.x — 추정치 표에서 바로 채울 수 있는 KPI 만. current = 최근 실적(A), consensus = 차년도 E 평균, my_estimate = 컨센(초안)."""
    src = d.source_all()
    slots = [n.id for n in m.children("A1")]
    cands = [k for k in kpi_candidates(sector) if k.metric]
    filled: list[str] = []
    used_metrics: set[str] = set()
    nxt_dates = next_earnings(today)
    next_check = nxt_dates[0][0] if nxt_dates else today + timedelta(days=90)
    amt_unit, amt_scale = d.amount_unit
    for kpi in cands:
        if len(filled) >= len(slots) or kpi.metric in used_metrics:
            continue
        metric = kpi.metric
        scale = amt_scale if metric in AMOUNT_METRICS else 1.0
        unit = amt_unit if metric in AMOUNT_METRICS else kpi.unit
        cur_val: float | None = None
        cur_note = ""
        cons_val: float | None = None
        cons_note = ""
        label = kpi.label
        # 영업이익은 분기 표가 있으면 분기 단위가 더 날카롭다 (다음 확인 시점이 가까움)
        q = d.latest_actual_quarter(metric) if metric == "op" else None
        nq = d.next_estimate_quarter(metric) if q is not None else None
        if q is not None and nq is not None:
            qlab, qval = q
            cur_val, cur_note = qval / scale, f"{qlab} 실적 (리포트 표)"
            cons_val, cons_note = nq[1].mean / scale, f"{nq[0]} 컨센서스 — 리포트 {nq[1].n}건 평균"
            label = f"분기 영업이익 ({nq[0][:4]})"
        if cur_val is None:
            ya = d.latest_actual_year(metric)
            ye = d.next_estimate_year(metric)
            if ya is None or ye is None:
                continue
            c = d.cell(metric, ye)
            va = d.value(metric, ya)
            if c is None or va is None:
                continue
            cur_val, cur_note = va / scale, f"{ya} 실적 (리포트 표)"
            cons_val = c.mean / scale
            lo, hi = min(c.values) / scale, max(c.values) / scale
            cons_note = f"{ye} 컨센서스 — 리포트 {c.n}건 평균" + (f" (범위 {lo:,.1f}~{hi:,.1f})" if c.n > 1 else "")
            label = f"{kpi.label} ({ye})"
        if cur_val is None or cons_val is None:
            continue
        nid = slots[len(filled)]
        n = m[nid]
        n.label, n.unit = label, unit
        n.driver = Driver(
            current=Measurement(value=round(cur_val, 2), as_of=d.as_of, source=src, rationale=cur_note),
            consensus=Measurement(value=round(cons_val, 2), as_of=d.as_of, source=src, rationale=cons_note),
            my_estimate=Measurement(value=round(cons_val, 2), as_of=today, rationale=f"컨센서스와 동일하게 둔 값. {DRAFT}"),
            check_indicator=kpi.check_indicator, next_check=next_check, higher_is_better=kpi.higher_is_better)
        n.as_of = today
        mark_draft(n)
        res.filled.append(nid)
        filled.append(nid)
        used_metrics.add(metric)
    if len(filled) < 3:
        want = [k.label for k in kpi_candidates(sector) if not k.metric][:4]
        res.notes.append(f"드라이버 {len(filled)}개만 표에서 채움 — 3개 이상 필요. 섹터 KPI 후보: {', '.join(want) or '없음'} (수동 입력 또는 LLM)")
    return filled


def scenario_anchors(d: ReportDigest, p0: float | None) -> tuple[dict[str, float], list[str]]:
    """컨센서스 분포 → Bear/Base/Bull 앵커. Base 는 분포 하위 1/3, Bull 은 평균, Bear 는 최저×0.9 와 52주 저가 중 낮은 값."""
    notes: list[str] = []
    anchors: dict[str, float] = {}
    if d.n_tp == 0:
        if p0 is None:
            return {}, ["목표주가·현재가 모두 없어 시나리오 앵커를 만들 수 없다"]
        anchors = {"Bear": p0 * 0.8, "Base": p0 * 1.2, "Bull": p0 * 1.5}
        notes.append("리포트에 목표주가가 없어 현재가 ±(−20%/+20%/+50%) 임시 앵커를 썼다")
        return anchors, notes
    bear_cands = [d.tp_min * 0.9]
    if d.low_52w:
        bear_cands.append(d.low_52w)
    bear = min(bear_cands)
    base = d.tp_percentile(0.33) or d.tp_median
    bull = d.tp_mean if d.n_tp >= 3 else d.tp_max
    if p0 is not None:
        if bear >= p0:
            bear = p0 * 0.85
            notes.append("최저 목표가가 현재가 위에 있어 Bear 를 현재가 −15% 로 두었다 (컨센서스가 전부 위쪽)")
        if base <= p0:
            notes.append(f"컨센서스 하위 1/3 목표가 {base:,.0f} 가 현재가 아래 — 시장이 목표가를 넘어섰다. 사람이 봐야 한다")
    if base <= bear:
        base = bear * 1.15
    if bull <= base:
        bull = max(base * 1.15, d.tp_max or 0)
    anchors = {"Bear": bear, "Base": base, "Bull": bull}
    return anchors, notes


def fill_scenarios(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, p0: float | None) -> None:
    anchors, notes = scenario_anchors(d, p0)
    res.notes += notes
    if not anchors:
        return
    res.anchors = anchors
    ye = d.next_estimate_year("op") or d.next_estimate_year("eps")
    op_e = d.value("op", ye) if ye else None
    per_e = d.value("per", ye) if ye else None
    eps_e = d.value("eps", ye) if ye else None
    risk_top = d.top("risk", 1)
    thesis_top = d.top("thesis", 1)
    common = [f"현재가 {p0:,.0f} 기준" if p0 else "현재가 미상"]
    assumptions = {
        "Bear": [f"증권사 최저 목표가 {d.tp_min:,.0f}×0.9" + (f" / 52주 저가 {d.low_52w:,.0f}" if d.low_52w else "") + " 중 낮은 값"
                 if d.n_tp else "임시 앵커 (현재가 −20%)"] + ([_trim(risk_top[0], 90)] if risk_top else []),
        "Base": [f"증권사 목표가 하위 1/3 분위 ({d.n_tp}건)" if d.n_tp else "임시 앵커 (현재가 +20%)"]
                + ([f"{ye} 영업이익 컨센서스 {d.fmt_amt('op', ye)}"] if op_e else [])
                + ([f"{ye} 컨센서스 PER {per_e:.1f}배 · EPS {eps_e:,.0f}원"] if per_e and eps_e else []),
        "Bull": [f"증권사 평균 목표가 ({d.tp_mean:,.0f})" if d.n_tp >= 3 else f"증권사 최고 목표가 ({d.tp_max:,.0f})" if d.n_tp else "임시 앵커 (현재가 +50%)"]
                + ([_trim(thesis_top[0], 90)] if thesis_top else []),
    }
    methods = {"Bear": "컨센서스 분포 하단 앵커 (초안)", "Base": "컨센서스 분포 하위 1/3 앵커 (초안)", "Bull": "컨센서스 평균 앵커 (초안)"}
    for nid, nm in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
        n = m[nid]
        n.scenario = Scenario(name=nm, probability=SCENARIO_P[nm], target_price=round(anchors[nm], -2) if anchors[nm] >= 10000 else round(anchors[nm]),
                              assumptions=assumptions[nm] + common, horizon_months=12, valuation_method=methods[nm])
        n.as_of = today
        mark_draft(n)
        res.filled.append(nid)
    res.notes.append(f"시나리오 확률은 기본값 Bear {SCENARIO_P['Bear']:.0%} / Base {SCENARIO_P['Base']:.0%} / Bull {SCENARIO_P['Bull']:.0%} — 사람이 조정")


def fill_view(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, p0: float | None) -> None:
    src = d.source_all()
    ye = d.next_estimate_year("op")
    parts: list[str] = []
    if d.n_tp:
        up = f" (현재가 대비 {d.tp_mean / p0 - 1:+.0%})" if p0 else ""
        dist = ", ".join(f"{k} {v}" for k, v in d.opinion_dist.most_common())
        parts.append(f"증권사 {d.n_tp}곳 목표가 평균 {d.tp_mean:,.0f}원{up}, 범위 {d.tp_min:,.0f}~{d.tp_max:,.0f}. 의견 {dist}")
    if ye and d.value("op", ye):
        g = ""
        ya = d.latest_actual_year("op")
        if ya and d.value("op", ya):
            g = f" ({ya} 실적 {d.fmt_amt('op', ya)} 대비 {d.value('op', ye) / d.value('op', ya) - 1:+.0%})"
        parts.append(f"{ye} 영업이익 컨센서스 {d.fmt_amt('op', ye)}{g}")
        if d.value("per", ye):
            parts.append(f"현재가는 {ye} PER {d.value('per', ye):.1f}배")
    dist = d.opinion_dist
    if d.reports and dist.get("BUY", 0) == sum(v for k, v in dist.items() if k != "미상") and dist.get("BUY", 0) >= 2:
        parts.append("컨센서스 편향: 매도·중립 의견 0건 (매수 일색) — 리포트만으로는 반대 논리를 볼 수 없다")
    if parts:
        _set(m, "A2.1", ". ".join(parts), today=today, source=src, res=res)
    mine = d.top("thesis", 2)
    if mine:
        _set(m, "A2.2", "[초안: 리포트 논거를 옮긴 것 — 내가 다르게 보는 것은 논제의 주인이 쓴다] " + " / ".join(_trim(s, 120) for s in mine),
             today=today, status=NodeStatus.unverified, res=res)
    if d.revision:
        _set(m, "A3.6.3", d.revision, today=today, source=Source(name=f"직전 목표가 대비 상향/하향 건수 (리포트 {d.n_tp}건)", url=src.url, retrieved_at=d.as_of), res=res)
    m["A3.6"].question = d.consensus_line()


def fill_thesis(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, p0: float | None) -> None:
    """A0 — 사실 요약 + 빈칸. 규칙으로는 '시장이 아직 반영하지 않은 것' 을 쓸 수 없다고 적어 둔다."""
    name = m.name or d.name or ""
    ye = d.next_estimate_year("op")
    bits: list[str] = []
    if ye and d.value("op", ye):
        bits.append(f"{ye} 영업이익 컨센서스 {d.fmt_amt('op', ye)}")
    if d.n_tp:
        bits.append(f"증권사 {d.n_tp}곳 평균 목표가 {d.tp_mean:,.0f}원")
    base = res.anchors.get("Base")
    tail = f" 이 맵의 Base 목표가 {base:,.0f}원은 그 분포의 하위 1/3 이다." if base else ""
    body = (f"[초안] {name}: " + (", ".join(bits) if bits else "리포트 요약") + "." + tail
            + " 시장이 아직 반영하지 않은 것이 무엇인지(A2.2)는 논제의 주인이 쓴다.")
    _set(m, "A0", body, today=today, status=NodeStatus.unverified, res=res)
    _set(m, "A0.2", "1Y", today=today, status=NodeStatus.unverified, res=res)
    conf = "M" if (d.n_tp >= 3 and d.estimates) else "L"
    _set(m, "A0.3", conf, today=today, status=NodeStatus.unverified, res=res)


def fill_risks(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult) -> None:
    src = d.source_all()
    risks = d.top("risk", 3)
    impacts = (-25, -20, -15)
    for i, (nid, text) in enumerate(zip(("A4.1", "A4.2", "A4.3"), risks, strict=False)):
        _set(m, nid, {"probability": 0.25, "impact_pct": impacts[i], "text": _trim(text, 160) + " (초안: 확률·영향은 기본값)"},
             today=today, source=src, res=res)
    drivers = m.drivers()
    if drivers:
        n = drivers[0]
        dr = n.driver
        assert dr is not None
        base = dr.consensus.value if dr.consensus.value is not None else dr.current.value
        if base is not None:
            thr = base * (0.8 if dr.higher_is_better else 1.2)
            op = "<" if dr.higher_is_better else ">"
            m["A4.4.1"].rule = Rule(trigger_type="fact", condition=f"driver[{n.id}].current {op} {thr:g}", action=Action.exit, escalate_to="RED",
                                    note=f"{n.label} 이 컨센서스({base:,.0f}{n.unit or ''}) 대비 20% {'미달' if dr.higher_is_better else '초과'}이면 논제 폐기 — {DRAFT} (임계값)")
            m["A4.4.1"].as_of = today
            mark_draft(m["A4.4.1"])
            res.filled.append("A4.4.1")
    indicators = sorted({n.driver.check_indicator for n in drivers if n.driver and n.driver.check_indicator})
    if indicators:
        _set(m, "A4.5", " · ".join(indicators), today=today, source=Source(name="섹터 KPI 사전 (자동)", retrieved_at=today), res=res)


def fill_catalysts(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult) -> None:
    drivers = m.drivers()
    op_driver = next((n.id for n in drivers if "영업이익" in n.label), None)
    events: list[Event] = []
    for dt, label, kind in next_earnings(today)[:2]:
        events.append(Event(date=dt, fact=label, affects=[op_driver] if op_driver else [], direction="중립", priced_in="아니오",
                            materiality="H" if kind == "잠정" else "M", source=Source(name="통상 일정 기준 추정", retrieved_at=today)))
    src = d.source_all()
    for text in d.top("catalyst", 3):
        dt = _date_in_sentence(text, today)
        if dt is None or dt <= today:
            continue
        events.append(Event(date=dt, fact=_trim(text, 110) + " (리포트)", affects=[], direction="중립", priced_in="아니오", materiality="M", source=src))
    events.sort(key=lambda e: e.date)
    seen: set[date] = set()
    i = 0
    for ev in events:
        if ev.date in seen and "리포트" not in ev.fact:
            continue
        seen.add(ev.date)
        i += 1
        nid = f"A5.1.{i}"
        node = Node(id=nid, map=MapKind.NEW, parent="A5.1", label=_trim(ev.fact, 30), node_type="event", value_type="text", as_of=today, event=ev)
        mark_draft(node)
        m.add(node)
        res.filled.append(nid)
        if i >= 5:
            break


def _date_in_sentence(text: str, today: date) -> date | None:
    import re

    m = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
    if m:
        y = today.year if int(m[1]) >= today.month else today.year + 1
        return _safe_date(y, int(m[1]), int(m[2]))
    m = re.search(r"(20\d{2})\s*년\s*(\d{1,2})\s*월", text)
    if m:
        return _safe_date(int(m[1]), int(m[2]), 15)
    m = re.search(r"([1-4])Q\s?(\d{2})", text)
    if m:
        y, q = 2000 + int(m[2]), int(m[1])
        return _safe_date(y, q * 3, 28) + timedelta(days=8)
    m = re.search(r"(20\d{2})\s*년\s*([1-4])\s*분기", text)
    if m:
        return _safe_date(int(m[1]), int(m[2]) * 3, 28) + timedelta(days=8)
    return None


def _safe_date(y: int, mth: int, d: int) -> date | None:
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def fill_exec(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult) -> None:
    base = res.anchors.get("Base")
    m["A6.3"].rule = Rule(trigger_type="price", condition="drawdown > 0.25", action=Action.review, escalate_to="YELLOW",
                          note=f"고점 대비 −25% 면 논제 재검토 — 기본값 ({DRAFT})")
    m["A6.3"].as_of = today
    mark_draft(m["A6.3"])
    res.filled.append("A6.3")
    if base:
        m["A6.4"].rule = Rule(trigger_type="price", condition=f"price > {base:g}", action=Action.trim, size="50%",
                              note="Base 목표가 도달 시 절반 축소 — 기본값")
        m["A6.4"].as_of = today
        mark_draft(m["A6.4"])
        res.filled.append("A6.4")
    _set(m, "A6.5", "WATCH", today=today, status=NodeStatus.unverified, res=res)


def fill_evidence(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, sector: str | None) -> None:
    src = d.source_all()
    biz = d.top("business", 1)
    if biz:
        _set(m, "A8.1.1", _trim(biz[0], 200), today=today, source=src, res=res)
    sh = d.top("shareholder", 1)
    if sh:
        _set(m, "A8.1.3", _trim(sh[0], 200), today=today, source=src, res=res)
    val = d.top("valuation", 1)
    if val:
        _set(m, "A3.6.2", _trim(val[0], 200), today=today, source=src, res=res)
    ya, ye = d.latest_actual_year("op"), d.next_estimate_year("op")
    if ya and ye:
        rev_a, op_a = d.value("revenue", ya), d.value("op", ya)
        rev_e, op_e = d.value("revenue", ye), d.value("op", ye)
        bits = []
        if rev_a and op_a:
            bits.append(f"{ya} 매출 {d.fmt_amt('revenue', ya)} · 영업이익 {d.fmt_amt('op', ya)} (OPM {op_a / rev_a:.1%})")
        if rev_e and op_e:
            g = f" ({op_e / op_a - 1:+.0%})" if op_a else ""
            bits.append(f"{ye} 컨센서스 매출 {d.fmt_amt('revenue', ye)} · 영업이익 {d.fmt_amt('op', ye)}{g}")
        per, pbr, roe = d.value("per", ye), d.value("pbr", ye), d.value("roe", ye)
        v = [f"PER {per:.1f}배" if per else "", f"PBR {pbr:.2f}배" if pbr else "", f"ROE {roe:.1f}%" if roe else ""]
        if any(v):
            bits.append(f"{ye} " + " · ".join(x for x in v if x))
        if bits:
            _set(m, "A8.1.4", " / ".join(bits) + f" — 리포트 {len(d.reports)}건 평균 (표 원단위 {d.unit_note})", today=today, source=src, res=res)
    if sector:
        m["A8.2"].links = [f"S.{sector}"]
    m["A8.3"].links = ["M0"]
    m["A8"].question = "리포트: " + " / ".join(f.label + (f" ({f.path.name})" if f.path else "") for f in d.reports)
    m["A8.1"].question = f"리포트 {len(d.reports)}건에서 규칙으로 뽑은 문장 — 원문 확인 필요"


# ---------------------------------------------------------------------------- 진입점
def fill_from_digest(m: NewMap, d: ReportDigest, *, today: date, sector: str | None = None,
                     price: tuple[float, date | None, Source] | None = None) -> RuleFillResult:
    res = RuleFillResult(sector=sector)
    p0 = fill_price(m, d, today=today, res=res, price=price)
    fill_drivers(m, d, today=today, res=res, sector=sector)
    fill_scenarios(m, d, today=today, res=res, p0=p0)
    fill_view(m, d, today=today, res=res, p0=p0)
    fill_thesis(m, d, today=today, res=res, p0=p0)
    fill_risks(m, d, today=today, res=res)
    fill_catalysts(m, d, today=today, res=res)
    fill_exec(m, d, today=today, res=res)
    fill_evidence(m, d, today=today, res=res, sector=sector)
    m.meta.setdefault("draft", {})
    m.meta["draft"].update({
        "method": "reports+rules", "generated_at": today.isoformat(), "report_as_of": d.as_of.isoformat(),
        "reports": [{"file": f.path.name, "broker": f.broker, "date": f.date.isoformat() if f.date else None,
                     "opinion": f.opinion, "target_price": f.target_price, "prev_target_price": f.prev_target_price} for f in d.reports],
        "consensus": {"n": d.n_tp, "mean": d.tp_mean, "median": d.tp_median, "min": d.tp_min, "max": d.tp_max, "revision": d.revision},
        "anchors": res.anchors, "sector": sector, "price_source": res.price_source,
        "notes": list(res.notes), "warnings": list(d.warnings),
    })
    return res


__all__ = ["DRAFT", "DRAFT_TAG", "RuleFillResult", "fill_from_digest", "scenario_anchors", "next_earnings", "mark_draft"]
