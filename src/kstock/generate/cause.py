"""원인 변수 드라이버 (설계 v5 R2) — A1 을 **결과 지표가 아니라 원인 변수**로 채운다.

    Claim quant_refs ─┐
                      ├─ cause_cells(): (driver_id, variable, measure, unit, 기간) 셀 — 증권사별 값 (회사·섹터와 무관한 규칙)
    쟁점 표(issue_set) ┘
    섹터 원인 KPI (kpi_catalog.cause_kpis) ─ 있으면 그 순서로 A1.1~A1.5 (KR-SEMI: DRAM ASP · HBM · 메모리 원가 · 파운드리 · FCF·주주환원)
    없으면 쟁점 표의 원인 테마를 점수(증권사 수 × display 가중 + 메커니즘 수 + 관측 가능성) 로 골라 A1 에 — 최종 선택은 사람 (V12 편집)

규칙 (설계 v5 §1 원칙)
  · 결과 지표(revenue/op/eps/opm 과 RESULT_DRIVER_IDS 테마) 는 A1 후보에서 뺀다 — A3.7 로 간다 (rules.fill_result_metrics).
  · current 는 realized/announced Claim 만, consensus 는 expected Claim 들 — 같은 기간(col) 끼리만 한 셀.
  · 숫자가 없는 변수는 kind=qualitative + proxies — 숫자를 지어내지 않는다.
  · 선택 근거를 질문에 적는다 ("선택 근거: …"). 민감도는 engine/sensitivity 가 따로 계산한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import Driver, IssueDisplay, Measurement, NewMap, Node, Source
from ..models.claims import Certainty
from .kpi_catalog import RESULT_DRIVER_IDS, Kpi, cause_kpis, normalize_driver
from .rules import mark_draft, next_earnings
from .scenarios import PENDING_TAG

UNIT_TO_EOK = {"억원": 1.0, "십억원": 10.0, "조원": 10000.0, "백만원": 0.01, "bn": 10.0}
# 변수 축 — Claim 의 metric 이름과 명제 단어로 판정 (증권사마다 metric 이름이 달라도 같은 축으로 모은다)
_B = r"(?<![a-z0-9])"   # metric 이름은 'hbm_bg_qoq' 처럼 밑줄로 이어져 \b 가 안 먹는다
_E = r"(?![a-z0-9])"
VARIABLE_WORDS: list[tuple[str, str]] = [
    ("asp", r"asp|price|가격|판가|단가|계약가"),
    ("volume", rf"{_B}bg{_E}|b/g|bit|출하|shipment|volume|물량|판매량|가동률|utilization"),
    ("margin", r"margin|opm|이익률|마진"),
    ("cost", r"cost|비용|opex|원가|capex|감가"),
    ("op", rf"{_B}op{_E}|operating|영업이익|이익|loss|적자|profit"),
    ("revenue", r"revenue|매출|sales"),
    ("orders", r"order|수주|계약"),
    ("backlog", r"backlog|잔고"),
    ("capacity", r"capacity|capa|용량"),
    ("share", r"share|점유율|비중"),
    ("fcf", r"fcf|현금흐름|cash|배당|환원|payout|자사주"),
]
MEASURE_WORDS: list[tuple[str, str]] = [("qoq", r"qoq|q/q|전분기"), ("yoy", r"yoy|y/y|전년"), ("change", r"change|증감|증가율|성장률|상승률|변동|growth")]
DISPLAY_WEIGHT = {IssueDisplay.disagreement: 3.0, IssueDisplay.common: 2.0, IssueDisplay.unresolved: 1.5, IssueDisplay.independent: 1.0}


@dataclass
class CauseCell:
    driver_id: str
    variable: str
    measure: str                 # qoq | yoy | change | level
    unit: str                    # % | 억원 | 원 | 배 | …
    col: str                     # 기간 (접미 없이: 3Q26 · 2027)
    certainty: str               # realized | expected  (announced/planned → realized 쪽, conditional → expected 쪽)
    by_broker: dict[str, float] = field(default_factory=dict)
    claims: dict[str, str] = field(default_factory=dict)      # broker → claim_id
    metric_names: set[str] = field(default_factory=set)

    @property
    def values(self) -> list[float]:
        return list(self.by_broker.values())

    @property
    def mean(self) -> float:
        xs = self.values
        return sum(xs) / len(xs)

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.driver_id, self.variable, self.measure, self.unit, self.col, self.certainty)

    def label(self) -> str:
        meas = {"qoq": "QoQ", "yoy": "YoY", "change": "변화", "level": ""}[self.measure]
        return f"{self.variable} {meas} {self.col}".replace("  ", " ").strip()

    def brokers_text(self, fmt: str = "{:,.1f}") -> str:
        return " · ".join(f"{b} {fmt.format(v)}{'' if self.unit in ('억원',) else self.unit if self.unit == '%' else ''}" for b, v in self.by_broker.items())

    def to_dict(self) -> dict[str, Any]:
        return {"driver_id": self.driver_id, "variable": self.variable, "measure": self.measure, "unit": self.unit, "col": self.col,
                "certainty": self.certainty, "by_broker": dict(self.by_broker), "claims": dict(self.claims), "metric_names": sorted(self.metric_names)}


def _variable(metric: str, proposition: str) -> str:
    text = f"{metric} {proposition}".lower()
    for var, pat in VARIABLE_WORDS:
        if re.search(pat, metric.lower()):
            return var
    for var, pat in VARIABLE_WORDS:
        if re.search(pat, text):
            return var
    return metric.lower() or "value"


def _measure(metric: str, proposition: str, unit: str | None, col: str = "") -> str:
    """qoq | yoy | change | level. 'change'(상승·증가율…) 는 분기 기간이면 qoq, 연간이면 yoy 로 본다 — 증권사마다 'ASP 19% 상승' 과
    'ASP +21% qoq' 를 같은 셀에 모으기 위해 (설계 v5 §8 규칙 2)."""
    text = f"{metric} {proposition}".lower()
    found = None
    for meas, pat in MEASURE_WORDS:
        if re.search(pat, metric.lower()):
            found = meas
            break
    if found is None and (unit or "") == "%":
        for meas, pat in MEASURE_WORDS:
            if re.search(pat, text):
                found = meas
                break
    if found == "change":
        found = "qoq" if re.match(r"[1-4]Q", col or "") else ("yoy" if col else "change")
    return found or "level"


def _norm_value(value: float, unit: str | None) -> tuple[float, str]:
    u = (unit or "").strip()
    if u in UNIT_TO_EOK:
        return value * UNIT_TO_EOK[u], "억원"
    return value, (u or "")


def _norm_col(col: str | None) -> str:
    c = (col or "").strip()
    return c.rstrip("EPAF") if c else ""


def _certainty_bucket(c: Any) -> str:
    cert = getattr(c, "certainty", None)
    v = cert.value if hasattr(cert, "value") else str(cert or "")
    return "realized" if v in (Certainty.realized.value, Certainty.announced.value) else "expected"


def cause_cells(claimsets: list[Any], *, include_review: bool = False, sector: str | None = None) -> list[CauseCell]:
    """Claim 의 수치를 (driver_id, 변수, 측정, 단위, 기간, 확실성) 셀로 묶는다. 결과 지표(전사 op/revenue/eps…) 는 뺀다."""
    cells: dict[tuple, CauseCell] = {}
    for cs in claimsets:
        for c in cs.claims:
            if not include_review and not c.usable:
                continue
            did = c.driver_id or normalize_driver(c.driver_label, sector=sector) or normalize_driver(c.proposition, sector=sector)
            if not did or did in RESULT_DRIVER_IDS:
                continue
            for q in c.quant_refs:
                metric = (q.metric or "").strip()
                if metric in ("op", "revenue", "eps", "np", "target_price", "opm", "per", "pbr", "bps", "dps", "ebitda") and did in RESULT_DRIVER_IDS | {"earnings"}:
                    continue
                col = _norm_col(q.col) or _norm_col(c.horizon)
                var = _variable(metric, c.proposition)
                meas = _measure(metric, c.proposition, q.unit, col)
                val, unit = _norm_value(q.value, q.unit)
                cert = _certainty_bucket(c)
                key = (did, var, meas, unit, col, cert)
                cell = cells.get(key)
                if cell is None:
                    cell = cells[key] = CauseCell(did, var, meas, unit, col, cert)
                broker = c.broker or cs.broker or "?"
                if broker in cell.by_broker:
                    continue
                cell.by_broker[broker] = val
                cell.claims[broker] = c.claim_id
                cell.metric_names.add(metric)
    return sorted(cells.values(), key=lambda x: (-len(x.by_broker), x.driver_id, x.variable, x.col))


def pick_cell(cells: list[CauseCell], driver_id: str, variable: str | None, *, certainty: str = "expected",
              prefer_measure: tuple[str, ...] = ("qoq", "yoy", "change", "level"), period: str | None = None) -> CauseCell | None:
    """드라이버·변수에 맞는 셀 하나 — 증권사 수가 많은 것, 선호 측정 순, 기간이 맞는 것."""
    cands = [c for c in cells if c.driver_id == driver_id and c.certainty == certainty and (variable is None or c.variable == variable)]
    if period:
        exact = [c for c in cands if c.col == _norm_col(period)]
        cands = exact or cands
    if not cands:
        return None
    rank = {m: i for i, m in enumerate(prefer_measure)}
    return sorted(cands, key=lambda c: (-len(c.by_broker), rank.get(c.measure, 9), c.col))[0]


# ---------------------------------------------------------------------------- 선택
def _theme_candidates(issue_set: Any, cells: list[CauseCell], used: set[str]) -> list[tuple[float, Any, list[str]]]:
    """섹터 사전이 없을 때: 쟁점 표의 원인 테마를 점수로. (점수, 테마, 이유들)."""
    out: list[tuple[float, Any, list[str]]] = []
    if issue_set is None:
        return out
    for t in issue_set.issues:
        if t.level.value != "theme" or not t.driver_id or t.driver_id in RESULT_DRIVER_IDS or t.driver_id in used:
            continue
        w = DISPLAY_WEIGHT.get(t.display, 1.0) if t.display else 1.0
        score = t.n_brokers * w
        reasons = [f"쟁점 표 {t.display.value if t.display else ''} {t.n_brokers}곳"]
        n_mech = len(t.children)
        if n_mech:
            score += 0.5 * n_mech
            reasons.append(f"메커니즘 {n_mech}개")
        if any(c.driver_id == t.driver_id for c in cells):
            score += 1.0
            reasons.append("수치 있음")
        out.append((score, t, reasons))
    return sorted(out, key=lambda x: -x[0])


def _ensure_slot(m: NewMap, i: int) -> Node:
    nid = f"A1.{i}"
    if nid not in m.nodes:
        from ..templates.questions import A_SPEC
        from ..templates.skeleton import _node_from_spec

        m.add(_node_from_spec(m.kind, nid, A_SPEC["A1.1"], label=f"드라이버 {i}"))
    return m[nid]


def _period_key(col: str) -> tuple[int, int]:
    c = col.rstrip("EPAF")
    mq = re.match(r"([1-4])Q(\d{2})$", c)
    if mq:
        return (2000 + int(mq.group(2)), int(mq.group(1)))
    my = re.match(r"(20\d{2})$", c)
    return (int(my.group(1)), 5) if my else (0, 0)


def segment_measurements(digest: Any, segment: str, metric: str) -> tuple[Any, str | None, Any, str | None]:
    """사업부 표(R4) 에서 (현재 셀, 그 기간, 전망 셀, 그 기간). 현재 = 가장 최근 잠정/확정(…P/…A) 분기, 전망 = 그 다음 분기 E."""
    if digest is None or not getattr(digest, "segments", None):
        return None, None, None, None
    cols = sorted({k[2] for k in digest.segments if k[0] == segment and k[1] == metric}, key=_period_key)
    actual = [c for c in cols if c[-1:] in ("P", "A") and "Q" in c]
    cur_col = actual[-1] if actual else None
    exp = [c for c in cols if c[-1:] == "E" and "Q" in c and (cur_col is None or _period_key(c) > _period_key(cur_col))]
    cons_col = exp[0] if exp else next((c for c in cols if c[-1:] == "E" and "Q" not in c), None)
    cur = digest.segment_cell(segment, metric, cur_col.rstrip("EPAF")) if cur_col else None
    cons = digest.segment_cell(segment, metric, cons_col) if cons_col else None
    return cur, (cur_col.rstrip("EPAF") if cur_col else None), cons, cons_col


def fill_cause_drivers(m: NewMap, *, sector: str | None, claimsets: list[Any], issue_set: Any, today: date,
                       source: Source | None = None, n_max: int = 5, digest: Any = None) -> list[str]:
    """A1.1~A1.5 를 원인 변수로 채운다. 돌려주는 것은 메모 줄들. digest 가 있으면 사업부 표(R4) 의 손익·매출로 Claim 수치를 보강한다."""
    notes: list[str] = []
    cells = cause_cells(claimsets, sector=sector)
    draft = m.meta.setdefault("draft", {})
    draft["cause_cells"] = [c.to_dict() for c in cells[:60]]
    kpis = cause_kpis(sector)[:n_max]
    themes_by_id = {t.driver_id: t for t in (issue_set.issues if issue_set else []) if t.level.value == "theme" and t.driver_id}
    nxt = next_earnings(today)
    next_check = nxt[0][0] if nxt else None
    src = source or Source(name="리포트 Claim 종합", retrieved_at=today)
    plan: list[tuple[Kpi, list[str]]] = []
    used: set[str] = set()
    for i, k in enumerate(kpis, start=1):
        reasons = [f"섹터 원인 KPI #{i} ({sector})"]
        t = themes_by_id.get(k.driver_id or "")
        if t is not None:
            reasons.append(f"쟁점 표 {t.display.value if t.display else ''} {t.n_brokers}곳")
        if k.check_indicator:
            reasons.append("관측 지표 있음")
        plan.append((k, reasons))
        if k.driver_id:
            used.add(k.driver_id)
    if len(plan) < 3:
        # 섹터 사전이 없거나 모자라면 쟁점 테마에서 — 라벨은 리포트 표현 그대로
        for score, t, reasons in _theme_candidates(issue_set, cells, used):
            if len(plan) >= n_max:
                break
            cell = pick_cell(cells, t.driver_id, None)
            k = Kpi(t.driver_id, t.driver_label, cell.unit if cell else "", True,
                    ", ".join(mm.mechanism or "" for mm in _mechanisms(issue_set, t)[:3] if mm.mechanism) or "리포트 쟁점 참조", 90,
                    driver_id=t.driver_id, variable=cell.variable if cell else None, kind="quant" if cell else "qualitative")
            plan.append((k, [f"점수 {score:.1f}", *reasons]))
            used.add(t.driver_id)
        if not kpis and plan:
            notes.append("섹터 원인 KPI 사전 없음 — 드라이버 라벨은 리포트 표현 그대로 (그 종목을 만들 때 섹터 사전에 추가할 것)")
    filled: list[str] = []
    for i, (k, reasons) in enumerate(plan, start=1):
        n = _ensure_slot(m, i)
        cons = pick_cell(cells, k.driver_id, k.variable) if k.driver_id else None
        if cons is None and k.driver_id:
            cons = pick_cell(cells, k.driver_id, None)
        cur = pick_cell(cells, k.driver_id, cons.variable if cons else k.variable, certainty="realized", period=cons.col if cons else None) if k.driver_id else None
        unit = (cons.unit if cons else None) or k.unit or None
        period = cons.col if cons else None
        cur_m = (Measurement(value=round(cur.mean, 4), as_of=today, source=src, rationale=f"실적·발표 Claim: {cur.brokers_text()}") if cur else
                 Measurement(value=None, rationale="실적(realized) Claim 없음 — 발표 뒤 채운다" if cons else "리포트에 수치 없음 — 정성 드라이버"))
        cons_m = (Measurement(value=round(cons.mean, 4), as_of=today, source=src,
                              rationale=f"{cons.label()} Claim {len(cons.by_broker)}곳: {cons.brokers_text()} (metric {', '.join(sorted(cons.metric_names))})") if cons else
                  Measurement(value=None, rationale="증권사 전망 수치 없음"))
        seg_note = ""
        # 손익·매출 드라이버는 사업부 표가 있으면 **표를 우선**한다 — 증권사 수가 많고 기간(잠정 P / 전망 E) 구조가 있어 현재·전망이 갈린다.
        # Claim 수치는 표가 없을 때만 (PC 실측 2026-10-24: Claim 2곳 때문에 표를 안 써서 '−17,000억원(3Q26)' 로 나왔다)
        if k.segment and k.variable in ("op", "revenue"):
            scur, scur_col, scons, scons_col = segment_measurements(digest, k.segment, k.variable)
            if scons is not None:   # 표시는 조원 (민감도 입력은 meta.draft.segments 의 억원 값을 따로 쓴다)
                cons_m = Measurement(value=round(scons.mean / 10000, 2), as_of=today, source=src,
                                     rationale=f"사업부 표 {k.segment} {k.variable} {scons_col} 증권사 {scons.n}곳: " + " · ".join(f"{b} {v / 10000:,.1f}조" for b, v in scons.by_broker.items()))
                period, unit = scons_col.rstrip("EPAF"), "조원"
                seg_note = " · 사업부 표"
            if scur is not None:
                cur_m = Measurement(value=round(scur.mean / 10000, 2), as_of=today, source=src,
                                    rationale=f"사업부 표 {k.segment} {k.variable} {scur_col}(잠정) 증권사 {scur.n}곳: " + " · ".join(f"{b} {v / 10000:,.1f}조" for b, v in scur.by_broker.items()))
                unit = "조원"
        if not seg_note and unit == "억원" and cons_m.value is not None and abs(cons_m.value) >= 10000:
            # Claim 의 조원 단위 수치는 조원으로 보인다 (−17,000억원 → −1.7조원)
            cons_m.value = round(cons_m.value / 10000, 2)
            if cur_m.value is not None:
                cur_m.value = round(cur_m.value / 10000, 2)
            unit = "조원"
        kind = "quant" if cons_m.value is not None else "qualitative"
        label = k.label + (f" ({cons.label()})" if cons and not seg_note else (f" ({k.variable} {period})" if seg_note and period else ""))
        n.label, n.unit = label, unit
        n.driver = Driver(
            current=cur_m,
            consensus=cons_m,
            my_estimate=Measurement(value=None, as_of=None, rationale="내 추정은 논제의 주인이 쓴다 — 편집 폼의 「채우기」 로 컨센을 받아들일 수 있다"),
            check_indicator=k.check_indicator, next_check=next_check, higher_is_better=k.higher_is_better,
            metric=f"{k.driver_id or k.key}:{cons.variable if cons else (k.variable or '')}:{cons.measure if cons else ('level' if seg_note else '')}".rstrip(":"),
            period=period, kind=kind, role="cause", proxies=list(k.proxies), invalidation_condition=None)
        n.question = ("선택 근거: " + " · ".join(reasons) + ". " + ("정성 드라이버 — 관측 지표와 무효화 조건을 적는다. " if kind == "qualitative" else "")
                      + "이 변수가 틀리면 논제가 무너지는가?")
        t = themes_by_id.get(k.driver_id or "")
        n.links = [f"issue:{t.issue_id}"] if t is not None else []
        n.source, n.as_of = src, today
        mark_draft(n)
        n.tags = sorted(set(n.tags) | {PENDING_TAG})
        draft.setdefault("drivers", {})[n.id] = {
            "driver_id": k.driver_id, "kpi": k.key, "metric": n.driver.metric, "col": period, "variable": cons.variable if cons else k.variable,
            "measure": cons.measure if cons else None, "unit": unit, "by_broker": dict(cons.by_broker) if cons and not seg_note else {},
            "claims": dict(cons.claims) if cons else {}, "template": k.template, "segment": k.segment, "selection": {"reasons": reasons + ([seg_note.strip(" ·")] if seg_note else [])}}
        filled.append(n.id)
    # 남는 슬롯은 비운다 (옛 결과 지표가 들어 있던 자리)
    for n in m.children("A1"):
        if n.id not in filled and n.driver is not None and (n.driver.role in (None, "result")):
            n.driver = None
            n.label = f"드라이버 {n.id.split('.')[-1]}"
            n.value = None
    if filled:
        # A4.5 조기 경보 지표 = 원인 드라이버의 관측 지표 (rules 가 결과 지표 것을 임시로 넣었으면 바꾼다)
        indicators = [m[nid].driver.check_indicator for nid in filled if m[nid].driver.check_indicator]
        a45 = m.nodes.get("A4.5")
        if a45 is not None and indicators and (a45.value is None or (a45.source and (a45.source.name or "").startswith("섹터 KPI 사전"))):
            a45.set(" · ".join(dict.fromkeys(indicators)), as_of=today, source=Source(name="섹터 원인 KPI 사전 (자동)", retrieved_at=today))
            mark_draft(a45)
        q = sum(1 for nid in filled if m[nid].driver.kind == "quant")
        notes.append(f"A1 원인 드라이버 {len(filled)}개 (수치 {q} · 정성 {len(filled) - q}; 결과 지표는 A3.7) — " + ", ".join(m[nid].label.split(" (")[0] for nid in filled))
    else:
        m["A1"].tags = sorted(set(m["A1"].tags) | {PENDING_TAG})
        notes.append("A1 원인 드라이버를 고르지 못했다 — 섹터 사전도 쟁점 표도 없음 (사람이 적는다)")
    return notes


def _mechanisms(issue_set: Any, t: Any) -> list[Any]:
    by_id = {i.issue_id: i for i in issue_set.issues}
    return [by_id[c] for c in t.children if c in by_id]


def driver_node_for(m: NewMap, driver_id: str | None, label: str | None = None) -> str | None:
    """테마(driver_id) 에 해당하는 A1 노드 id — meta.draft.drivers 로 찾고, 없으면 라벨 일치."""
    if driver_id:
        for nid, info in ((m.meta.get("draft") or {}).get("drivers") or {}).items():
            if info.get("driver_id") == driver_id and nid in m.nodes:
                return nid
    if label:
        return next((n.id for n in m.children("A1") if n.label == label or n.label.split(" (")[0] == label), None)
    return None


def cells_table(cells: list[CauseCell]) -> str:
    rows = ["원인 변수 셀 (driver · 변수 · 측정 · 기간 · 확실성 → 증권사별 값)"]
    for c in cells:
        rows.append(f"  {c.driver_id:<18} {c.variable:<8} {c.measure:<6} {c.col:<6} {c.certainty:<8} {c.unit:<4} | {c.brokers_text()}")
    return "\n".join(rows)


__all__ = ["CauseCell", "cause_cells", "pick_cell", "fill_cause_drivers", "driver_node_for", "cells_table"]
