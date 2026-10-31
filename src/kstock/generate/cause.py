"""원인 변수 드라이버 (설계 v5 R2) — A1 을 **결과 지표가 아니라 원인 변수**로 채운다.

    Claim quant_refs ─┐
                      ├─ cause_cells(): (driver_id, variable, measure, unit, 기간) 셀 — 증권사별 값 (회사·섹터와 무관한 규칙)
    쟁점 표(issue_set) ┘
    섹터 원인 KPI (kpi_catalog.cause_kpis) ─ 있으면 그 순서로 A1.1~A1.5 (KR-SEMI: DRAM ASP · HBM · 메모리 원가 · 파운드리 · FCF·주주환원)
    없으면 쟁점 표의 원인 테마를 점수(증권사 수 × display 가중 + 메커니즘 수 + 관측 가능성) 로 골라 A1 에 — 최종 선택은 사람 (V12 편집)

규칙 (설계 v5 §1 원칙)
  · 결과 지표(revenue/op/eps/opm 과 RESULT_DRIVER_IDS 테마) 는 A1 후보에서 뺀다 — A3.1 로 간다 (rules.fill_result_metrics).
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
from ..units import UNIT_TO_EOK, convert, fmt_amount, is_past_period, to_eok
from .kpi_catalog import RESULT_DRIVER_IDS, Kpi, cause_kpis, normalize_driver
from .rules import mark_draft, next_earnings
from .scenarios import PENDING_TAG

# 변수 축 — Claim 의 metric 이름과 명제 단어로 판정 (증권사마다 metric 이름이 달라도 같은 축으로 모은다)
_B = r"(?<![a-z0-9])"   # metric 이름은 'hbm_bg_qoq' 처럼 밑줄로 이어져 \b 가 안 먹는다
_E = r"(?![a-z0-9])"
VARIABLE_WORDS: list[tuple[str, str]] = [
    ("commitment", r"guarantee|지급보증|보증|commitment|약정|contract_value|계약\s*총액|계약\s*규모"),   # 보증·약정·계약 총액 — 매출도 비용도 아니다
    ("asp", r"asp|price|가격|판가|단가|계약가"),
    ("volume", rf"{_B}bg{_E}|b/g|bit|출하|shipment|volume|물량|판매량|가동률|utilization"),
    ("margin", r"margin|opm|이익률|마진"),
    ("cost", r"cost|비용|opex|원가|capex|감가"),
    ("op", rf"{_B}op{_E}|operating|영업이익|이익|loss|적자|profit"),
    ("revenue", r"revenue|매출|sales"),
    ("orders", r"order|수주|계약"),
    ("backlog", r"backlog|잔고"),
    ("capacity", r"capacity|capa|용량"),
    # 주주환원(배당·자사주·환원) 과 FCF(현금 창출) 는 다른 축 — 2026-10-31 삼성전자: 특별배당 30조원이 「FCF 컨센」 이 됐다. share(점유율) 보다 먼저 (shareholder_return)
    ("payout", r"dividend|배당|dps|buyback|자사주|소각|환원|payout|shareholder_return"),
    ("share", r"share|점유율|비중"),
    ("fcf", r"fcf|현금흐름|free\s*cash|cash\s*flow|ocf|cash"),
]
MEASURE_WORDS: list[tuple[str, str]] = [("qoq", r"qoq|q/q|전분기"), ("yoy", r"yoy|y/y|전년"), ("change", r"change|증감|증가율|성장률|상승률|변동|growth")]
DISPLAY_WEIGHT = {IssueDisplay.disagreement: 3.0, IssueDisplay.common: 2.0, IssueDisplay.unresolved: 1.5, IssueDisplay.independent: 1.0}
#: 값이 아닌 변수 — 보증·약정·계약 총액은 뜻은 알지만 드라이버의 **값**(매출·비용·물량…) 이 아니다. 가정 메모로만
NON_VALUE_VARIABLES = frozenset({"commitment"})
#: 드라이버 값이 될 수 있는 변수 축 — 여기 없는 변수(metric 이름 그대로 · 보증·약정) 는 드라이버 값으로 쓰지 않는다
KNOWN_VARIABLES = frozenset(v for v, _ in VARIABLE_WORDS) - NON_VALUE_VARIABLES
# 돈의 방향 — 「지급할 GPUaaS 이용료 연 3조원」 은 비용이고 「GPUaaS 매출」 은 매출이다. 이름(fee·이용료·수수료) 만으로는 못 정한다
# (2026-10-31 NAVER 실측: annual_gpuaas_fee 3조원이 AI 데이터센터 매출 드라이버의 컨센이 됐다 — 지급보증 18.7조원에서 역산한 **지급** 금액이다)
_OUTFLOW_WORDS = ("지급", "지불", "납부", "부담", "임차", "리스", "지출", "보증", "payable", "lease", "rent", "paid", "payment", "spend")
_INFLOW_WORDS = ("수취", "받는", "받을", "매출", "수익", "인식", "receiv", "revenue", "sales", "earn")
_FEE_WORDS = ("fee", "이용료", "수수료", "사용료", "임대료", "royalty", "charge")


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
        """증권사별 값 — 단위를 **셀 단위로** 적는다 (억원은 1조 이상이면 조원). 2026-10-31 전엔 억원을 빼고 적어 뒤에서 노드 단위(조원) 가 잘못 붙었다."""
        if self.unit in UNIT_TO_EOK or self.unit in ("%", "%p", "원", "주"):
            return " · ".join(f"{b} {fmt_amount(v, self.unit)}" for b, v in self.by_broker.items())
        return " · ".join(f"{b} {fmt.format(v)}{self.unit}" for b, v in self.by_broker.items())

    def is_classified(self) -> bool:
        return is_classified(self.variable)

    def to_dict(self) -> dict[str, Any]:
        return {"driver_id": self.driver_id, "variable": self.variable, "measure": self.measure, "unit": self.unit, "col": self.col,
                "certainty": self.certainty, "by_broker": dict(self.by_broker), "claims": dict(self.claims), "metric_names": sorted(self.metric_names)}


def flow_direction(metric: str, proposition: str) -> str | None:
    """돈이 나가는 수치(cost) 인가 들어오는 수치(revenue) 인가 — 지급·부담·임차 ↔ 수취·매출·수익. 둘 다거나 둘 다 아니면 None(못 정함)."""
    text = f"{metric} {proposition}".lower()
    out = any(w in text for w in _OUTFLOW_WORDS)
    inn = any(w in text for w in _INFLOW_WORDS)
    if out and not inn:
        return "cost"
    if inn and not out:
        return "revenue"
    return None


def _variable(metric: str, proposition: str) -> str:
    """변수 축. 요금·수수료(fee·이용료) 는 이름이 아니라 **돈의 방향**으로 정한다 — 못 정하면 metric 이름 그대로(뜻 미정) 남겨 값으로 안 쓰게."""
    m = metric.lower()
    if any(w in m for w in _FEE_WORDS):
        return flow_direction(metric, proposition) or (m or "fee")
    for var, pat in VARIABLE_WORDS:
        if re.search(pat, m):
            return var
    text = f"{m} {proposition}".lower()
    for var, pat in VARIABLE_WORDS:
        if re.search(pat, text):
            return var
    d = flow_direction(metric, proposition)
    if d:
        return d
    return m or "value"


def is_classified(variable: str | None) -> bool:
    """뜻이 정해진 변수인가 (매출·비용·물량·…). metric 이름 그대로인 변수는 드라이버 값으로 쓰지 않는다."""
    return bool(variable) and variable in KNOWN_VARIABLES


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
    return to_eok(value, unit)


def _norm_col(col: str | None) -> str:
    c = (col or "").strip()
    return c.rstrip("EPAF") if c else ""


def _certainty_bucket(c: Any) -> str:
    cert = getattr(c, "certainty", None)
    v = cert.value if hasattr(cert, "value") else str(cert or "")
    return "realized" if v in (Certainty.realized.value, Certainty.announced.value) else "expected"


#: 드라이버의 **수준값이 아닌** 비율·상대·평가 파라미터 — 「온디맨드 가격의 45% 할인」 「WACC 9%」 「PER 17배」 는 변수의 값이 아니라
#: 가정이다. 2026-10-31 NAVER 실측: discount 45% 가 「AI 데이터센터 ASP 45%」 로 들어갔다. 이런 수치는 셀이 되지 않고 가정 메모로만 남는다
RELATIVE_METRICS = frozenset({"discount", "discount_rate", "haircut", "premium", "wacc", "cost_of_equity", "beta", "per", "pbr", "psr", "ev_ebitda",
                              "ev_sales", "multiple", "target_multiple", "target_price", "upside", "downside", "probability", "weight"})
_RELATIVE_WORDS = ("할인", "프리미엄", "discount", "premium", "wacc", "배수", "멀티플", "목표주가", "적용 per", "적용 pbr")


def is_relative_metric(metric: str, proposition: str = "") -> bool:
    """셀로 쓰면 안 되는 수치인가 — 지표 이름이 비율·평가 파라미터이거나, 이름이 비어 있는데 문장이 할인·배수를 말한다."""
    m = (metric or "").strip().lower()
    if m in RELATIVE_METRICS or m.endswith(("_discount", "_premium", "_multiple", "_per", "_wacc")):
        return True
    return not m and any(w in (proposition or "").lower() for w in _RELATIVE_WORDS)


def cause_cells(claimsets: list[Any], *, include_review: bool = False, sector: str | None = None,
                skipped: list[dict] | None = None) -> list[CauseCell]:
    """Claim 의 수치를 (driver_id, 변수, 측정, 단위, 기간, 확실성) 셀로 묶는다. 결과 지표(전사 op/revenue/eps…) 와
    비율·평가 파라미터(할인율·WACC·배수) 는 뺀다 — 뺀 것은 `skipped` 에 (driver_id · broker · 문장 · 지표) 로 남겨 가정 메모가 된다."""
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
                if is_relative_metric(metric, c.proposition):
                    if skipped is not None:
                        skipped.append({"driver_id": did, "broker": c.broker or cs.broker or "?", "metric": metric, "value": q.value, "unit": q.unit,
                                        "claim_id": c.claim_id, "text": " ".join(c.proposition.split())[:140], "reason": "relative",
                                        "why": "비율·평가 파라미터 — 변수의 값이 아니다"})
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


#: 서로 대신할 수 없는 변수 — 비용 자리에 이익·매출·마진·현금흐름 셀이 들어오면 부호와 뜻이 뒤집힌다
#: 비용은 다른 어떤 변수(매출·이익·마진·물량·단가·용량…) 자리에도 못 들어가고, 그 자리들도 비용 셀을 못 받는다.
#: 2026-10-31 네 번째 검토: ai_dc(volume) 자리에 「지급할 GPUaaS 이용료」(cost) 가 들어갔다 — 전엔 cost ↔ 이익·매출·마진만 막았다
_INCOMPATIBLE: dict[str, frozenset[str]] = {
    "cost": KNOWN_VARIABLES - {"cost"},
    **{v: frozenset({"cost"}) for v in KNOWN_VARIABLES - {"cost"}},
}
# FCF(현금 창출) ↔ 주주환원(현금 배분) 은 서로 대신 못 한다 — 환원 여력과 환원 정책을 섞지 않는다 (2026-10-31 삼성전자)
_INCOMPATIBLE["fcf"] = _INCOMPATIBLE["fcf"] | {"payout"}
_INCOMPATIBLE["payout"] = _INCOMPATIBLE["payout"] | {"fcf"}


def variables_compatible(kpi_variable: str | None, cell_variable: str | None) -> bool:
    """KPI 변수 자리에 그 셀 변수를 써도 되나. 변수가 없으면(정성 KPI) 무엇이든 된다 — 단 보증·약정 금액은 어느 자리의 값도 아니다."""
    if cell_variable in NON_VALUE_VARIABLES and kpi_variable != cell_variable:
        return False
    if not kpi_variable or not cell_variable or kpi_variable == cell_variable:
        return True
    return cell_variable not in _INCOMPATIBLE.get(kpi_variable, frozenset())


def pick_cell(cells: list[CauseCell], driver_id: str, variable: str | None, *, certainty: str = "expected",
              prefer_measure: tuple[str, ...] = ("qoq", "yoy", "change", "level"), period: str | None = None,
              today: date | None = None) -> CauseCell | None:
    """드라이버·변수에 맞는 셀 하나 — 증권사 수가 많은 것, 선호 측정 순, 기간이 가까운 것.

    안 고르는 것 (2026-10-31 네 번째 검토):
      · 뜻 미정 변수(metric 이름 그대로) — `variable` 로 그 이름을 꼭 집어 달라고 하지 않는 한. 「annual_gpuaas_fee 3조원」 이 매출 자리에 들어갔다
      · 전망(expected) 인데 **지난 기간** 열 — 2025 비용은 2026-10 의 컨센이 아니다 (`today` 를 주면 거른다)
    """
    cands = [c for c in cells if c.driver_id == driver_id and c.certainty == certainty
             and (c.variable == variable if variable is not None else c.is_classified())]
    if certainty == "expected" and today is not None:
        cands = [c for c in cands if not is_past_period(c.col, today)]
    if period:
        exact = [c for c in cands if c.col == _norm_col(period)]
        cands = exact or cands
    if not cands:
        return None
    rank = {m: i for i, m in enumerate(prefer_measure)}
    return sorted(cands, key=lambda c: (-len(c.by_broker), rank.get(c.measure, 9), c.col))[0]


def _unused_cell_memos(cells: list[CauseCell], k: Kpi, cons: CauseCell | None, *, today: date | None,
                       text_of: dict[str, str] | None = None, cur: CauseCell | None = None) -> list[dict]:
    """드라이버의 셀 가운데 값으로 쓰지 않은 것을 메모로 — reason: incompatible(반대 성질) · unclassified(뜻 미정) · past_period(지난 기간)."""
    out: list[dict] = []
    text_of = text_of or {}
    for c in cells:
        if c.driver_id != k.driver_id or c is cons:
            continue
        if cur is not None and c is cur:
            continue
        if c.variable in NON_VALUE_VARIABLES:
            reason, why = "commitment", "보증·약정·계약 총액 — 매출도 비용도 아니다"
        elif not c.is_classified():
            reason, why = "unclassified", "매출·비용·계약 가운데 무엇인지 못 정함"
        elif k.variable and c.variable != k.variable and not variables_compatible(k.variable, c.variable):
            tag = {"cost": " (비용, 매출 아님)", "payout": " (주주환원 — 현금 배분이지 FCF 가 아니다)", "fcf": " (FCF — 현금 창출이지 환원이 아니다)"}.get(c.variable, "")
            reason, why = "incompatible", f"{c.variable} — {k.variable} 자리의 값이 아니다" + tag
        elif c.certainty == "expected" and is_past_period(c.col, today):
            reason, why = "past_period", f"{c.col} 열은 지난 기간 — 확정 실적 확인 전, 컨센·내 추정으로 안 씀"
        else:
            continue
        for broker, v in c.by_broker.items():
            cid = c.claims.get(broker, "")
            out.append({"driver_id": c.driver_id, "broker": broker, "metric": ", ".join(sorted(c.metric_names)) or c.variable, "variable": c.variable,
                        "col": c.col, "value": v, "unit": c.unit, "claim_id": cid, "reason": reason, "why": why, "certainty": c.certainty,
                        "text": " ".join((text_of.get(cid) or "").split())[:140]})
    return out


#: 가정 메모의 순서 — 판단을 뒤집을 수 있는 것(반대 성질 · 뜻 미정) 이 먼저, 그다음 지난 기간 · 보증 · 비율
_MEMO_ORDER = {"incompatible": 0, "unclassified": 1, "past_period": 2, "commitment": 3, "relative": 4}


def _memo_sort(memos: list[dict]) -> list[dict]:
    return sorted(memos, key=lambda x: _MEMO_ORDER.get(x.get("reason") or "", 9))


def _memo_text(x: dict) -> str:
    """가정 메모 한 조각 — 「[하나증권] cost 3조원 (비용, 매출 아님) — 네이버클라우드가 연간 지급할 …」."""
    amount = fmt_amount(x["value"], x.get("unit")) if isinstance(x.get("value"), (int, float)) else str(x.get("value"))
    head = " ".join(p for p in (f"[{x['broker']}]", x.get("variable") or x.get("metric"), x.get("col") or "", amount,
                                "(발표)" if x.get("certainty") == "realized" else "") if p)
    why = x.get("why") or ""
    tail = (x.get("text") or "")[:90]
    return head + (f" ({why})" if why else "") + (f" — {tail}" if tail else "")


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
    skipped: list[dict] = []
    cells = cause_cells(claimsets, sector=sector, skipped=skipped)
    draft = m.meta.setdefault("draft", {})
    draft["cause_cells"] = [c.to_dict() for c in cells[:60]]
    draft["skipped_cells"] = skipped[:40]
    text_of = {c.claim_id: c.proposition for cs in claimsets for c in cs.claims}
    if skipped:
        notes.append(f"비율·평가 파라미터 {len(skipped)}건은 드라이버 값으로 안 쓴다 (가정 메모로): " + "; ".join(f"{x['driver_id']} {x['metric']} {x['value']}{x['unit'] or ''}" for x in skipped[:4]))
    unused_all: list[dict] = []
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
        memos: list[dict] = [x for x in skipped if x["driver_id"] == k.driver_id]
        cons = pick_cell(cells, k.driver_id, k.variable, today=today) if k.driver_id else None
        if cons is None and k.driver_id and k.kind != "qualitative":
            # 정성으로 설계한 KPI(ai_dc · 원가) 는 꼭 맞는 변수 셀만 받는다 — 다른 변수(용량 MW) 로 떨어져 수치 드라이버가 되지 않게
            # 변수가 안 맞으면 다른 변수의 셀로 **떨어지되, 반대 성질의 변수로는 안 간다.** 2026-10-31 NAVER 실측: 「마케팅·인프라
            # 비용」(cost) 에 셀이 없자 같은 driver_id 의 영업이익(op) 4,789억 셀을 집어 와서, 비용 드라이버가 영업이익을 컨센으로
            # 들고 higher_is_better=False 라 「영업이익이 늘면 나쁘다」 가 됐다. 비용 ↔ 이익·매출·마진은 서로 대신 못 한다
            fallback = pick_cell(cells, k.driver_id, None, today=today)
            if fallback is not None and not variables_compatible(k.variable, fallback.variable):
                notes.append(f"{k.label}: {fallback.variable} 셀({fallback.label()}) 은 {k.variable} 변수가 아니라 쓰지 않는다 — 정성 드라이버로")
                fallback = None
            cons = fallback
        cur = pick_cell(cells, k.driver_id, cons.variable if cons else k.variable, certainty="realized", period=cons.col if cons else None) if k.driver_id else None
        if k.driver_id:
            # 값으로 안 쓴 수치도 버리지 않는다 — 반대 성질(비용 ↔ 매출 · 배당 ↔ FCF) · 뜻 미정(fee 가 매출인지 비용인지) · 지난 기간. 사람이 보는 가정 메모로
            # (2026-10-31 네 번째 검토: 「지급할 GPUaaS 이용료 연 3조원」 이 AI DC 매출 컨센이 됐고, 2025 비용이 2026-10 의 컨센이 됐다)
            unused = _unused_cell_memos(cells, k, cons, today=today, text_of=text_of, cur=cur)
            memos += unused
            unused_all += unused
            for x in unused:
                what = " ".join(p for p in (x["variable"], x["col"] or "", fmt_amount(x["value"], x["unit"])) if p)
                notes.append(f"{k.label}: {what} ({x['broker']}) 은 값으로 안 쓴다 — {x['why']}")
        unit = (cons.unit if cons else None) or k.unit or None
        period = cons.col if cons else None
        past = [x for x in memos if x.get("reason") == "past_period" and x.get("variable") == (cons.variable if cons else k.variable)]
        past_note = (" · 리포트가 적은 지난 기간 수치 " + " · ".join(f"{x['col']} {fmt_amount(x['value'], x['unit'])} ({x['broker']})" for x in past[:2])
                     + " — 확정 실적(공시) 확인 전이라 값으로 안 씀") if past else ""
        cur_m = (Measurement(value=round(cur.mean, 4), as_of=today, source=src, rationale=f"실적·발표 Claim: {cur.brokers_text()}") if cur else
                 Measurement(value=None, rationale=("실적(realized) Claim 없음 — 발표 뒤 채운다" if cons else "리포트에 수치 없음 — 정성 드라이버") + past_note))
        cons_m = (Measurement(value=round(cons.mean, 4), as_of=today, source=src,
                              rationale=f"{cons.label()} Claim {len(cons.by_broker)}곳: {cons.brokers_text()} (metric {', '.join(sorted(cons.metric_names))})") if cons else
                  Measurement(value=None, rationale="증권사 전망 수치 없음"))
        seg_note = ""
        cons_from_table = False
        # 손익·매출 드라이버는 사업부 표가 있으면 **표를 우선**한다 — 증권사 수가 많고 기간(잠정 P / 전망 E) 구조가 있어 현재·전망이 갈린다.
        # Claim 수치는 표가 없을 때만 (PC 실측 2026-10-24: Claim 2곳 때문에 표를 안 써서 '−17,000억원(3Q26)' 로 나왔다)
        if k.segment and k.variable in ("op", "revenue"):
            scur, scur_col, scons, scons_col = segment_measurements(digest, k.segment, k.variable)
            # 표시 단위는 크기로 정한다 — 둘 다 1,000억 이상이면 조원, 아니면 억원. 파운드리 적자 −300억이 「−0.0조」 로 보이지 않게 (2026-10-31 삼성전자)
            seg_unit = "조원" if all(abs(c.mean) >= 1000 for c in (scur, scons) if c is not None) else "억원"
            seg_div = 10000.0 if seg_unit == "조원" else 1.0
            seg_nd = 2 if seg_unit == "조원" else 0
            if scons is not None:   # 민감도 입력은 meta.draft.segments 의 억원 값을 따로 쓴다
                cons_m = Measurement(value=round(scons.mean / seg_div, seg_nd), as_of=today, source=src,
                                     rationale=f"사업부 표 {k.segment} {k.variable} {scons_col} 증권사 {scons.n}곳: " + " · ".join(f"{b} {fmt_amount(v, '억원')}" for b, v in scons.by_broker.items()))
                period, unit = scons_col.rstrip("EPAF"), seg_unit
                seg_note = " · 사업부 표"
                cons_from_table = True
            if scur is not None:
                cur_m = Measurement(value=round(scur.mean / seg_div, seg_nd), as_of=today, source=src,
                                    rationale=f"사업부 표 {k.segment} {k.variable} {scur_col}(잠정) 증권사 {scur.n}곳: " + " · ".join(f"{b} {fmt_amount(v, '억원')}" for b, v in scur.by_broker.items()))
                if scons is None and cons is not None and cons_m.value is not None and cons.unit in UNIT_TO_EOK:
                    # 현재는 사업부 표, 컨센은 Claim 셀 — 한 노드의 두 값은 같은 단위여야 한다
                    cons_m.value = round(convert(cons_m.value, cons.unit, seg_unit), seg_nd)
                unit = seg_unit
                if not seg_note:
                    seg_note = " · 사업부 표"
        if not seg_note and unit == "억원" and cons_m.value is not None and abs(cons_m.value) >= 10000:
            # Claim 의 조원 단위 수치는 조원으로 보인다 (−17,000억원 → −1.7조원)
            cons_m.value = round(cons_m.value / 10000, 2)
            if cur_m.value is not None:
                cur_m.value = round(cur_m.value / 10000, 2)
            unit = "조원"
        # 증권사별 값은 **노드 단위로 맞춰** 적는다 — 원천(억원) 을 그대로 두면 뒤에서 「77,599조원」 이 된다 (제안 라벨 · 편집 폼)
        by_broker = ({b: round(convert(v, cons.unit, unit), 4) for b, v in cons.by_broker.items()} if cons and not cons_from_table else {})
        kind = "quant" if cons_m.value is not None else "qualitative"
        label = k.label + (f" ({cons.label()})" if cons and not cons_from_table else (f" ({k.variable} {period})" if seg_note and period else ""))
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
        if memos:
            # 수치로는 안 쓰지만 버리지도 않는다 — 「온디맨드 가격 대비 −45% 장기계약 단가」 같은 가정, 「지급할 이용료 3조원」(비용), 지난 기간 수치는 사람이 봐야 한다
            n.question += " | 가정 메모(수치 아님): " + " / ".join(_memo_text(x) for x in _memo_sort(memos)[:3])
        t = themes_by_id.get(k.driver_id or "")
        n.links = [f"issue:{t.issue_id}"] if t is not None else []
        n.source, n.as_of = src, today
        mark_draft(n)
        n.tags = sorted(set(n.tags) | {PENDING_TAG})
        draft.setdefault("drivers", {})[n.id] = {
            "driver_id": k.driver_id, "kpi": k.key, "metric": n.driver.metric, "col": period, "variable": cons.variable if cons else k.variable,
            "measure": cons.measure if cons else None, "unit": unit, "by_broker": by_broker, "by_broker_unit": unit if by_broker else None,
            "claims": dict(cons.claims) if cons and not cons_from_table else {}, "template": k.template, "segment": k.segment,
            "memos": _memo_sort(memos)[:6],
            "selection": {"reasons": reasons + ([seg_note.strip(" ·")] if seg_note else [])}}
        filled.append(n.id)
    if unused_all:
        draft["skipped_cells"] = (skipped + unused_all)[:60]
    # 남는 슬롯은 비운다 (옛 결과 지표가 들어 있던 자리)
    for n in m.children("A1"):
        if n.id not in filled and n.driver is not None and (n.driver.role in (None, "result")):
            n.driver = None
            n.label = f"드라이버 {n.id.split('.')[-1]}"
            n.value = None
    if filled:
        # A5.5 조기 경보 지표 = 원인 드라이버의 관측 지표 (rules 가 결과 지표 것을 임시로 넣었으면 바꾼다)
        indicators = [m[nid].driver.check_indicator for nid in filled if m[nid].driver.check_indicator]
        a45 = m.nodes.get("A5.5")
        if a45 is not None and indicators and (a45.value is None or (a45.source and (a45.source.name or "").startswith("섹터 KPI 사전"))):
            a45.set(" · ".join(dict.fromkeys(indicators)), as_of=today, source=Source(name="섹터 원인 KPI 사전 (자동)", retrieved_at=today))
            mark_draft(a45)
        q = sum(1 for nid in filled if m[nid].driver.kind == "quant")
        notes.append(f"A1 원인 드라이버 {len(filled)}개 (수치 {q} · 정성 {len(filled) - q}; 결과 지표는 A3.1) — " + ", ".join(m[nid].label.split(" (")[0] for nid in filled))
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


__all__ = ["CauseCell", "cause_cells", "pick_cell", "fill_cause_drivers", "driver_node_for", "cells_table", "variables_compatible",
           "is_relative_metric", "RELATIVE_METRICS", "flow_direction", "is_classified", "KNOWN_VARIABLES", "UNIT_TO_EOK"]
