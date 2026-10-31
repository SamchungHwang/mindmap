"""규칙 채움 — 리포트 합산(ReportDigest)에서 숫자로 결정되는 노드를 채운다 (자동 생성 계획 §3a).

LLM 없이 돌아간다. 채운 노드에는 전부 `draft` 태그가 붙고, 추정(my_estimate)에는 '초안' 이 적힌다.
판단(A7.5)은 항상 WATCH. 사실(fact) 은 리포트를 Source 로 달아 confirmed, 판단(judgment) 은 unverified 로 둔다 —
사람이 저장할 때 확정한다.
"""

from __future__ import annotations

import statistics
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
from .scenarios import PENDING_TAG

DRAFT = "초안 — 논제의 주인이 확정할 것"
DRAFT_TAG = "draft"
SCENARIO_P = {"Bear": 0.30, "Base": 0.45, "Bull": 0.25}
BEAR_FLOOR = 0.60          # Bear 앵커는 현재가의 60% 아래로 두지 않는다
DRIVER_METRIC_ORDER = {"op": 0, "revenue": 1, "eps": 2, "opm": 3, "roe": 4}  # 표에서 채우는 드라이버 순서: 영업이익이 먼저


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


def next_earnings(today: date, history: list[date] | None = None) -> list[tuple[date, str, str]]:
    """다음 실적 일정 (예정일, 라벨, 종류). 2026-10-30 부터 `generate.catalysts.earnings_calendar` 가 계산한다 —
    회사별 과거 공시일이 있으면 그것으로, 없으면 통상 규칙(잠정 +8일 · 분기보고서 45일 · 사업보고서 90일)."""
    from .catalysts import next_earnings as _next

    return _next(today, history)


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


RESULT_SLOTS = {"revenue": "A3.1.1", "op": "A3.1.2", "eps": "A3.1.3", "opm": "A3.1.4"}   # 설계 v5: 결과 지표 자리 (A1 → A3.1)


def ensure_result_nodes(m: NewMap) -> None:
    """A3.1 재무 전망 묶음이 없는 옛 스켈레톤에 노드를 만든다."""
    from ..templates.questions import A_SPEC
    from ..templates.skeleton import _node_from_spec

    for nid in ("A3.1", *RESULT_SLOTS.values()):
        if nid not in m.nodes and nid in A_SPEC:
            m.add(_node_from_spec(MapKind.NEW, nid, A_SPEC[nid]))


def fill_drivers(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, sector: str | None) -> list[str]:
    """**결과 지표** (매출·영업이익·EPS·영업이익률) 를 추정치 표에서 A3.1.x 에 채운다 (설계 v5 — 전에는 A1 이었다).
    current = 최근 실적(A), consensus = 차년도 E 평균, my_estimate = 비움. Driver 구조를 그대로 쓰되 role=result."""
    src = d.source_all()
    ensure_result_nodes(m)
    cands = sorted((k for k in kpi_candidates(sector) if k.metric and k.metric in RESULT_SLOTS), key=lambda k: DRIVER_METRIC_ORDER.get(k.metric or "", 9))
    filled: list[str] = []
    used_metrics: set[str] = set()
    nxt_dates = next_earnings(today)
    next_check = nxt_dates[0][0] if nxt_dates else today + timedelta(days=90)
    amt_unit, amt_scale = d.amount_unit
    for kpi in cands:
        if kpi.metric in used_metrics:
            continue
        metric = kpi.metric
        scale = amt_scale if metric in AMOUNT_METRICS else 1.0
        unit = amt_unit if metric in AMOUNT_METRICS else kpi.unit
        cur_val: float | None = None
        cur_note = ""
        cons_val: float | None = None
        cons_note = ""
        label = kpi.label
        cell = None   # 컨센서스 셀 — 증권사별 값을 meta.draft.drivers 에 남긴다 (제안값 label 용, 설계 v4 §6)
        col = ""
        # 영업이익은 분기 표가 있으면 분기 단위가 더 날카롭다 (다음 확인 시점이 가까움)
        q = d.latest_actual_quarter(metric) if metric == "op" else None
        nq = d.next_estimate_quarter(metric) if q is not None else None
        if q is not None and nq is not None:
            qlab, qval = q
            cur_val, cur_note = qval / scale, f"{qlab} 실적 (리포트 표)"
            cons_val, cons_note = nq[1].mean / scale, f"{nq[0]} 컨센서스 — 리포트 {nq[1].n}건 평균"
            label = f"분기 영업이익 ({nq[0][:4]})"
            cell, col = nq[1], nq[0]
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
            cell, col = c, ye
            lo, hi = min(c.values) / scale, max(c.values) / scale
            cons_note = f"{ye} 컨센서스 — 리포트 {c.n}건 평균" + (f" (범위 {lo:,.1f}~{hi:,.1f})" if c.n > 1 else "")
            label = f"{kpi.label} ({ye})"
        if cur_val is None or cons_val is None:
            continue
        nid = RESULT_SLOTS[metric]
        n = m[nid]
        n.label, n.unit = label, unit
        n.driver = Driver(
            current=Measurement(value=round(cur_val, 2), as_of=d.as_of, source=src, rationale=cur_note),
            consensus=Measurement(value=round(cons_val, 2), as_of=d.as_of, source=src, rationale=cons_note),
            my_estimate=Measurement(value=None, as_of=None, rationale="내 추정은 논제의 주인이 쓴다 — 비워 두면 계산은 컨센서스 기준(임시)"),
            check_indicator=kpi.check_indicator, next_check=next_check, higher_is_better=kpi.higher_is_better,
            metric=metric, period=col or None, role="result", kind="quant")
        n.as_of = today
        mark_draft(n)
        n.tags = sorted(set(n.tags) | {PENDING_TAG})
        if cell is not None:
            m.meta.setdefault("draft", {}).setdefault("drivers", {})[nid] = {
                "metric": metric, "col": col, "role": "result", "by_broker": {b: round(v / scale, 2) for b, v in cell.by_broker.items()}}
        res.filled.append(nid)
        filled.append(nid)
        used_metrics.add(metric)
    if filled:
        res.notes.append(f"결과 지표 {len(filled)}개를 표에서 A3.1 에 채움 ({', '.join(m[x].label for x in filled)}) — 원인 드라이버(A1) 는 Claim 에서")
    else:
        res.notes.append("추정치 표에서 결과 지표를 채우지 못했다 (A3.1 비움)")
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
        elif bear < p0 * BEAR_FLOOR:
            notes.append(f"Bear 앵커 {bear:,.0f} 가 현재가 대비 {bear / p0 - 1:.0%} — 1년 전 저가는 하락 시나리오로 과하다. 현재가 −{1 - BEAR_FLOOR:.0%} 로 제한")
            bear = p0 * BEAR_FLOOR
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
    for nid, nm in (("A4.2", "Bear"), ("A4.3", "Base"), ("A4.4", "Bull")):
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
        # 증권사 컨센서스 ≠ 주가에 내재된 기대 (설계 v3 §2.4). 값은 비워 두고 관측치는 질문에 붙인다; 자식 A2.1.x 는 쟁점(Issue).
        m["A2.1"].question = (m["A2.1"].question.split(" | 리포트 관측:")[0] + " | 리포트 관측: " + ". ".join(parts))[:600]
        m["A2.1"].tags = sorted(set(m["A2.1"].tags) | {PENDING_TAG})
        m["A2.1"].source = src
        res.notes.append("A2.1 내재 가정은 비워 둔다 — 증권사 전망(관측) 은 A2.1 의 자식 쟁점과 질문에만 적는다")
    # A2.2 · A2.3 은 사람 몫 — 비운다
    for nid in ("A2.2", "A2.3"):
        m[nid].tags = sorted(set(m[nid].tags) | {PENDING_TAG})
    if d.revision:
        _set(m, "A3.5.3", d.revision, today=today, source=Source(name=f"직전 목표가 대비 상향/하향 건수 (리포트 {d.n_tp}건)", url=src.url, retrieved_at=d.as_of), res=res)
    m["A3.5"].question = d.consensus_line()


def fill_thesis(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, p0: float | None) -> None:
    """A0 — 사실 요약 + 빈칸. 규칙으로는 '시장이 아직 반영하지 않은 것' 을 쓸 수 없다고 적어 둔다."""
    name = m.name or d.name or ""
    ye = d.next_estimate_year("op")
    bits: list[str] = []
    if ye and d.value("op", ye):
        bits.append(f"{ye} 영업이익 컨센서스 {d.fmt_amt('op', ye)}")
    if d.n_tp:
        bits.append(f"증권사 {d.n_tp}곳 평균 목표가 {d.tp_mean:,.0f}원")
    body = (f"[초안 · 증권사 종합] {name}: " + (", ".join(bits) if bits else "리포트 요약") + "."
            + " 시장이 아직 반영하지 않은 것이 무엇인지(A2.2)는 논제의 주인이 쓴다.")
    _set(m, "A0", body, today=today, status=NodeStatus.unverified, res=res)
    _set(m, "A0.2", "1Y", today=today, status=NodeStatus.unverified, res=res)
    conf = "M" if (d.n_tp >= 3 and d.estimates) else "L"
    _set(m, "A0.3", conf, today=today, status=NodeStatus.unverified, res=res)


def fill_risks(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult) -> None:
    risks = d.top("risk", 3)
    # 설계 v4 V8: 키워드로 고른 문장은 '리스크' 로 확정하지 않는다 — 값은 비우고(pending) 질문에 후보로만 적는다.
    # LLM Claim 이 있으면 mapping 이 인용 근거가 있는 리스크로 채운다. 확률·영향(0.25 / -25%) 같은 기본값도 더 넣지 않는다.
    for nid, text in zip(("A5.1", "A5.2", "A5.3"), risks, strict=False):
        n = m[nid]
        base_q = (n.question or "").split(" | 키워드 후보")[0]
        n.question = (base_q + f" | 키워드 후보(미검증): {_trim(text, 120)}")[:400]
        n.tags = sorted(set(n.tags) | {PENDING_TAG})
        res.notes.append(f"{nid}: 키워드 문장은 후보로만 (질문) — 리스크 확정은 Claim 또는 사람")
    # 원인 드라이버(A1) 는 뒤에 cause 모듈이 채우므로 여기서는 표의 결과 지표(A3.1) 를 예시·경보 지표로 쓴다
    drivers = m.drivers() or [n for n in (m.children("A3.1") if "A3.1" in m else []) if n.driver is not None]
    # Kill 기준은 자동으로 만들지 않는다 (설계 v3): 원문 근거(조건부 리스크 주장) 가 있으면 mapping 이 제안하고, 없으면 질문으로 남긴다
    m["A5.4.1"].tags = sorted(set(m["A5.4.1"].tags) | {PENDING_TAG})
    if drivers and drivers[0].driver and drivers[0].driver.current.value:
        n = drivers[0]
        m["A5.4.1"].question = (m["A5.4.1"].question + f" | 예시(미검증): {n.label} 이 지금 수준 {n.driver.current.value:,.2f}{n.unit or ''} 에서 20% 악화되면 폐기").strip(" |")
    indicators = sorted({n.driver.check_indicator for n in drivers if n.driver and n.driver.check_indicator})
    if indicators:
        _set(m, "A5.5", " · ".join(indicators), today=today, source=Source(name="섹터 KPI 사전 (자동)", retrieved_at=today), res=res)


def fill_catalysts(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult,
                   earnings_history: list[date] | None = None, sector: str | None = None, texts: dict[str, str] | None = None) -> None:
    """A6.1 규칙 채움 — 실적 일정(회사별 과거 공시일 우선) + 리포트 문장·설명회 Q&A 의 촉매 (`event_text.report_text_events`).

    여기서 만든 것은 전부 추정이다. 공시(DART) 와 Claim 은 뒤에서 `catalysts.merge_events` 로 합쳐지며,
    같은 사건이면 확실성이 높은 출처가 남는다. (2026-10-31 전엔 촉매 문장 상위 3개만 하나씩 이벤트로 — 두산에너빌리티 검토 뒤 교체)
    """
    from .catalysts import earnings_calendar, merge_events, rule_event, set_catalysts
    from .event_text import report_text_events

    drivers = m.drivers()
    op_driver = next((n.id for n in drivers if "영업이익" in n.label), None)
    events: list[Event] = [rule_event(e, today=today, affects=[op_driver] if op_driver else [])
                           for e in earnings_calendar(today, earnings_history)[:2]]
    text_events, text_notes = report_text_events(d, today=today, sector=sector, texts=texts)
    events += text_events
    res.notes += [f"A6.1 문장: {x}" for x in text_notes]
    merged, notes = merge_events([], events)
    res.notes += [f"A6.1: {x}" for x in notes]
    res.filled += set_catalysts(m, merged, today=today)
    dropped = (m.meta.get("draft") or {}).get("a6_1_dropped") or []
    if dropped:
        res.notes.append(f"A6.1 상한 {len(merged) - len(dropped)}건 — 생략 {len(dropped)}건: " + " / ".join(dropped))
    if earnings_history:
        res.notes.append(f"A6.1 실적 일정: 과거 공시일 {len(earnings_history)}건으로 추정")


def _date_in_sentence(text: str, today: date) -> date | None:
    """옛 이름 호환 — `catalysts.parse_when` 의 기준일."""
    from .catalysts import date_in_sentence

    return date_in_sentence(text, today)


def _safe_date(y: int, mth: int, d: int) -> date | None:
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def fill_exec(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult) -> None:
    base = None   # 시나리오를 자동으로 만들지 않으므로 익절 기준도 비워 둔다 (사람이 Base 를 정한 뒤)
    m["A7.3"].rule = Rule(trigger_type="price", condition="drawdown > 0.25", action=Action.review, escalate_to="YELLOW",
                          note=f"고점 대비 −25% 면 논제 재검토 — 기본값 ({DRAFT})")
    m["A7.3"].as_of = today
    mark_draft(m["A7.3"])
    res.filled.append("A7.3")
    if base:
        m["A7.4"].rule = Rule(trigger_type="price", condition=f"price > {base:g}", action=Action.trim, size="50%",
                              note="Base 목표가 도달 시 절반 축소 — 기본값")
        m["A7.4"].as_of = today
        mark_draft(m["A7.4"])
        res.filled.append("A7.4")
    _set(m, "A7.5", "WATCH", today=today, status=NodeStatus.unverified, res=res)


def fill_evidence(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, sector: str | None) -> None:
    src = d.source_all()
    biz = d.top("business", 1)
    if biz:
        _set(m, "A9.1.1", _trim(biz[0], 200), today=today, source=src, res=res)
    sh = d.top("shareholder", 1)
    if sh:
        _set(m, "A9.1.3", _trim(sh[0], 200), today=today, source=src, res=res)
    val = d.top("valuation", 1)
    if val:
        _set(m, "A3.5.2", _trim(val[0], 200), today=today, source=src, res=res)
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
            _set(m, "A9.1.4", " / ".join(bits) + f" — 리포트 {len(d.reports)}건 평균 (표 원단위 {d.unit_note})", today=today, source=src, res=res)
    if sector:
        m["A9.2"].links = [f"S.{sector}"]
    m["A9.3"].links = ["M0"]
    m["A9"].question = "리포트: " + " / ".join(f.label + (f" ({f.path.name})" if f.path else "") for f in d.reports)
    m["A9.1"].question = f"리포트 {len(d.reports)}건에서 규칙으로 뽑은 문장 — 원문 확인 필요"


# ---------------------------------------------------------------------------- 진입점
def fill_tp_distribution(m: NewMap, d: ReportDigest, *, today: date, res: RuleFillResult, p0: float | None) -> None:
    """A3.5.4 증권사 목표가 분포 (fact) — min/median/mean/max/n/분산 + 목표가별 상승여력. 시나리오가 아니라 분포다."""
    if not d.n_tp:
        return
    nid = "A3.5.4"
    if nid not in m.nodes:
        m.add(Node(id=nid, map=MapKind.NEW, parent="A3.5", label="증권사 목표가 분포", node_type="fact", value_type="text",
                   question="목표가 분포는 시나리오가 아니다 — 가장 보수적/낙관적 증권사와 그 간격"))
    rows = []
    for t in sorted(d.targets, key=lambda t: t.tp):
        up = f" ({t.tp / p0 - 1:+.0%})" if p0 else ""
        rows.append(f"{t.broker or '?'} {t.tp:,.0f}{up}")
    xs = d.tp_values
    spread = (max(xs) - min(xs)) / (statistics.median(xs) or 1) if xs else 0.0
    ref = ""
    if p0 and d.low_52w:
        ref = f" · 참고: 52주 저가 {d.low_52w:,.0f} ({d.low_52w / p0 - 1:+.0%}), 현재가 −40% {p0 * 0.6:,.0f}"
    val = (f"목표가 {d.n_tp}건 {d.tp_min:,.0f}~{d.tp_max:,.0f} · 중앙 {d.tp_median:,.0f} · 평균 {d.tp_mean:,.0f} · 분산 {spread:.0%}"
           + (f" · 현재가 대비 평균 {d.tp_mean / p0 - 1:+.0%}" if p0 else "") + " | " + ", ".join(rows) + ref)
    _set(m, nid, val, today=today, source=d.source_all(), res=res)
    m.meta.setdefault("draft", {})["tp_distribution"] = {"n": d.n_tp, "min": d.tp_min, "max": d.tp_max, "median": d.tp_median, "mean": d.tp_mean,
                                                        "spread": round(spread, 4), "by_broker": {(t.broker or "?"): t.tp for t in d.targets}}


def fill_from_digest(m: NewMap, d: ReportDigest, *, today: date, sector: str | None = None,
                     price: tuple[float, date | None, Source] | None = None, texts: dict[str, str] | None = None,
                     earnings_history: list[date] | None = None) -> RuleFillResult:
    res = RuleFillResult(sector=sector)
    p0 = fill_price(m, d, today=today, res=res, price=price)
    fill_drivers(m, d, today=today, res=res, sector=sector)
    from .scenarios import fill_scenarios_v3

    sc_notes, cands = fill_scenarios_v3(m, d, texts or {}, today=today, p0=p0)
    res.notes += sc_notes
    res.anchors = {}
    fill_tp_distribution(m, d, today=today, res=res, p0=p0)
    fill_view(m, d, today=today, res=res, p0=p0)
    fill_thesis(m, d, today=today, res=res, p0=p0)
    fill_risks(m, d, today=today, res=res)
    fill_catalysts(m, d, today=today, res=res, earnings_history=earnings_history, sector=sector, texts=texts)
    fill_exec(m, d, today=today, res=res)
    fill_evidence(m, d, today=today, res=res, sector=sector)
    m.meta.setdefault("draft", {})
    m.meta["draft"].update({
        "method": "reports+rules", "generated_at": today.isoformat(), "report_as_of": d.as_of.isoformat(),
        "reports": [{"file": f.path.name, "broker": f.broker, "date": f.date.isoformat() if f.date else None,
                     "opinion": f.opinion, "target_price": f.target_price, "prev_target_price": f.prev_target_price} for f in d.reports],
        "consensus": {"n": d.n_tp, "mean": d.tp_mean, "median": d.tp_median, "min": d.tp_min, "max": d.tp_max, "revision": d.revision},
        "anchors": res.anchors, "sector": sector, "price_source": res.price_source,
        "notes": list(res.notes), "warnings": list(d.warnings), "scenario_candidates": cands,
        "draft_status": "미완성", "approval_status": "미승인", "quality": "rules",
        "excluded_reports": dict(d.excluded), "pre_earnings": dict(d.pre_earnings),
    })
    return res


__all__ = ["DRAFT", "DRAFT_TAG", "RuleFillResult", "fill_from_digest", "scenario_anchors", "next_earnings", "mark_draft", "RESULT_SLOTS", "ensure_result_nodes"]
