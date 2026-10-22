"""시나리오 (설계 v3 §2.5) — 서로 다른 증권사의 가정을 섞지 않는다.

우선순위 (위에서부터 하나만):
1. 리포트가 Bull/Base/Bear(낙관/중립/비관) 케이스와 목표가를 명시 → 그대로 구조화 (출처: 증권사). 여러 리포트가 제시하면 증권사별 후보.
2. 리포트별 가치평가 산식(valuation_method · 배수 · 기준 EPS/BPS) 이 읽히면 **그 리포트 안의 값만으로** 재계산한 '자체 시나리오 후보'.
3. 목표가만 있음 → A3.6.4 분포·상승여력만. A3.1~3 은 공란(pending).
4. 근거 부족 → 공란.
확률은 어떤 경우에도 자동으로 넣지 않는다. 목표가 분포→시나리오 폴백·min(EPS)×min(PER) 교차 조합·Bear 하한은 없다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from ..models import HistoryPoint, NewMap, Scenario, Source
from ..reports.digest import ReportDigest
from ..reports.extract import ReportFacts

PENDING_TAG = "pending"      # 의도적으로 비워 둔 노드 (초안 검증에서 '오류' 가 아니라 '미확정' 으로 센다)

_NUM = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
RE_CASE = re.compile(r"(?P<name>Bull|Bear|Base|낙관|비관|보수|중립|기본)\s*(?:case|케이스|시나리오|Case|Scenario)?[^\n\d]{0,40}?(?P<tp>" + _NUM + r")\s*(?:만\s*원|원)", re.I)
RE_METHOD = re.compile(
    r"(?P<per>(?:목표|Target|적정)\s*(?:PER|P/E)\s*[^\n\d]{0,12}(?P<per_x>\d+(?:\.\d+)?)\s*(?:배|x)|(?:PER|P/E)\s*(?P<per_x2>\d+(?:\.\d+)?)\s*(?:배|x)\s*(?:를|을)?\s*(?:적용|부여))"
    r"|(?P<pbr>(?:목표|Target|적정)\s*(?:PBR|P/B)\s*[^\n\d]{0,12}(?P<pbr_x>\d+(?:\.\d+)?)\s*(?:배|x)|(?:PBR|P/B)\s*(?P<pbr_x2>\d+(?:\.\d+)?)\s*(?:배|x)\s*(?:를|을)?\s*(?:적용|부여))"
    r"|(?P<ev>EV/EBITDA\s*[^\n\d]{0,12}(?P<ev_x>\d+(?:\.\d+)?)\s*(?:배|x))"
    r"|(?P<dcf>DCF|현금흐름할인)|(?P<sotp>SOTP|Sum[- ]of[- ]the[- ]parts|부문별\s*가치|사업부별\s*가치)|(?P<rim>RIM|잔여이익)", re.I)
RE_BASE_YEAR = re.compile(r"(?P<y>20\d{2})\s*(?:년|E|F)?\s*(?:예상|추정)?\s*(?:EPS|BPS|주당순이익|주당순자산)", re.I)
RE_EPS_BASIS = re.compile(r"(?:EPS|주당순이익)\s*[^\n\d]{0,12}(?P<eps>" + _NUM + r")\s*원")


@dataclass
class ValuationSpec:
    method: str                       # PER | PBR | EV/EBITDA | DCF | SOTP | RIM | ?
    multiple: float | None = None
    base_year: str | None = None      # 2027E
    base_value: float | None = None   # 적용 EPS/BPS (리포트 본문 또는 표)
    note: str = ""


@dataclass
class ScenarioCandidate:
    """증권사 한 곳의 **자체** 가정 묶음 — 사용자가 Bear/Base/Bull 로 골라 넣는 재료."""

    broker: str
    report_date: date | None
    kind: str                         # 리포트 제시 | 산식 재계산 | 목표가
    name: str | None                  # Bull | Base | Bear (리포트 제시일 때)
    target_price: float
    method: str = ""
    multiple: float | None = None
    base_year: str | None = None
    base_value: float | None = None
    assumptions: list[str] = field(default_factory=list)
    source_file: str = ""

    @property
    def origin(self) -> str:
        """v4 V5: broker_case(리포트가 명시한 Bear/Base/Bull) · broker_recalc(리포트 내부 가정 재계산) · broker_tp(증권사 기준 목표가)."""
        return {"리포트 제시": "broker_case", "산식 재계산": "broker_recalc", "목표가": "broker_tp"}.get(self.kind, "user")

    def is_reinterpretation(self, name: str) -> bool:
        """사용자가 이 후보를 name(Bear/Base/Bull) 에 넣는 것이 증권사의 뜻과 다른 재해석인가.
        리포트가 명시한 같은 이름의 케이스만 그대로이고, 기준 목표가·재계산값을 Bear/Bull 로 두는 것은 재해석이다 (Base 도 '증권사의 기준값' 이라는 표시만)."""
        return not (self.kind == "리포트 제시" and self.name == name)

    def label(self) -> str:
        core = f"{self.broker} {self.report_date or ''}".strip()
        if self.kind == "리포트 제시":
            return f"{core} {self.name} 케이스 {self.target_price:,.0f}원"
        if self.kind == "산식 재계산":
            return f"{core} {self.method} {self.multiple:g}배 × {self.base_year} {self.base_value:,.0f} = {self.target_price:,.0f}원 (시스템 계산)"
        return f"{core} 목표주가 {self.target_price:,.0f}원"

    def to_scenario(self, name: str) -> Scenario:
        vm = f"{self.kind}: {self.broker}" + (f" {self.method} {self.multiple:g}배 × {self.base_year} {'EPS' if self.method == 'PER' else 'BPS' if self.method == 'PBR' else ''} {self.base_value:,.0f}"
                                              if self.kind == "산식 재계산" and self.multiple and self.base_value else "")
        return Scenario(name=name, assumptions=list(self.assumptions), probability=None, target_price=float(self.target_price),
                        horizon_months=12, valuation_method=vm)


# --------------------------------------------------------------------------- 리포트 읽기
def find_report_cases(text: str) -> dict[str, float]:
    """'Bull case 600,000원 / Bear 300,000원' 같은 명시 케이스. 이름 → 목표가."""
    out: dict[str, float] = {}
    for m in RE_CASE.finditer(text[:30000]):
        nm = m["name"].lower()
        name = "Bull" if nm in ("bull", "낙관") else "Bear" if nm in ("bear", "비관", "보수") else "Base"
        try:
            v = float(m["tp"].replace(",", ""))
        except ValueError:
            continue
        if "만" in m.group(0)[m.end("tp") - m.start():]:
            v *= 10000
        if v < 100:   # '만원' 단위가 빠진 조각 등
            continue
        out.setdefault(name, v)
    return out


def find_valuation(text: str, facts: ReportFacts | None = None) -> ValuationSpec | None:
    head = text[:30000]
    m = RE_METHOD.search(head)
    if m is None:
        return None
    if m["per"]:
        x = m["per_x"] or m["per_x2"]
        spec = ValuationSpec("PER", float(x) if x else None)
    elif m["pbr"]:
        x = m["pbr_x"] or m["pbr_x2"]
        spec = ValuationSpec("PBR", float(x) if x else None)
    elif m["ev"]:
        spec = ValuationSpec("EV/EBITDA", float(m["ev_x"]) if m["ev_x"] else None)
    elif m["dcf"]:
        spec = ValuationSpec("DCF")
    elif m["sotp"]:
        spec = ValuationSpec("SOTP")
    else:
        spec = ValuationSpec("RIM")
    y = RE_BASE_YEAR.search(head[max(0, m.start() - 300):m.end() + 300])
    if y:
        spec.base_year = f"{y['y']}E"
    if spec.method in ("PER", "PBR") and facts is not None:
        metric = "eps" if spec.method == "PER" else "bps"
        cols = facts.estimates.get(metric, {})
        if spec.base_year and spec.base_year in cols:
            spec.base_value = cols[spec.base_year]
        elif cols:
            # 기준 연도를 못 읽었으면 다음 해 E
            es = sorted(c for c in cols if c.endswith("E"))
            if es:
                spec.base_year, spec.base_value = es[min(1, len(es) - 1)], cols[es[min(1, len(es) - 1)]]
        if spec.base_value is None:
            e = RE_EPS_BASIS.search(head[max(0, m.start() - 300):m.end() + 300])
            if e:
                spec.base_value = float(e["eps"].replace(",", ""))
    return spec


def candidates_from(facts: ReportFacts, text: str) -> list[ScenarioCandidate]:
    """리포트 한 건 → 후보들 (제시 케이스 · 산식 재계산 · 목표가). 모두 **그 리포트 안의 값** 만 쓴다."""
    out: list[ScenarioCandidate] = []
    broker = facts.broker or facts.path.name
    cases = find_report_cases(text)
    for name, tp in cases.items():
        out.append(ScenarioCandidate(broker, facts.date, "리포트 제시", name, tp, assumptions=[f"{broker} {name} 케이스 (리포트 명시)"], source_file=facts.path.name))
    spec = find_valuation(text, facts)
    if spec and spec.multiple and spec.base_value and spec.method in ("PER", "PBR"):
        recon = spec.multiple * spec.base_value
        if facts.target_price and abs(recon - facts.target_price) / facts.target_price > 0.35:
            # 산식이 목표가와 너무 다르면 잘못 읽은 것 — 후보로 올리지 않고 메모만
            spec.note = f"재계산 {recon:,.0f} 이 목표가 {facts.target_price:,.0f} 와 35% 넘게 다르다 — 배수/기준값 확인"
        else:
            out.append(ScenarioCandidate(broker, facts.date, "산식 재계산", None, round(recon, -2) if recon >= 10000 else round(recon), spec.method,
                                         spec.multiple, spec.base_year, spec.base_value,
                                         assumptions=[f"{spec.method} {spec.multiple:g}배 × {spec.base_year} {'EPS' if spec.method == 'PER' else 'BPS'} {spec.base_value:,.0f}"],
                                         source_file=facts.path.name))
    if facts.target_price:
        out.append(ScenarioCandidate(broker, facts.date, "목표가", None, facts.target_price, spec.method if spec else "",
                                     spec.multiple if spec else None, spec.base_year if spec else None, spec.base_value if spec else None,
                                     assumptions=[f"{broker} 목표주가" + (f" ({spec.method})" if spec else "")], source_file=facts.path.name))
    return out


# --------------------------------------------------------------------------- 맵에 넣기
def fill_scenarios_v3(m: NewMap, d: ReportDigest, texts: dict[str, str], *, today: date, p0: float | None) -> tuple[list[str], list[dict]]:
    """A3.1~3: 리포트 제시 케이스가 있으면 채우고(확률 없음), 아니면 **비워 둔다**(pending). 후보는 meta 로 돌려준다.
    texts: path.name → 정규화 텍스트. 돌려주는 것: (메모, 후보 dict 목록)."""
    notes: list[str] = []
    cands: list[ScenarioCandidate] = []
    for f in d.aggregated:
        cands += candidates_from(f, texts.get(f.path.name, ""))
    src = d.source_all()
    stated = [c for c in cands if c.kind == "리포트 제시"]
    by_name: dict[str, ScenarioCandidate] = {}
    if stated:
        # 가장 최근 리포트의 케이스 묶음 하나만 (증권사 섞지 않는다)
        latest = max(stated, key=lambda c: c.report_date or date.min)
        for c in stated:
            if c.broker == latest.broker and c.name and c.name not in by_name:
                by_name[c.name] = c
    filled = 0
    for nid, name in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
        n = m[nid]
        c = by_name.get(name)
        if c is not None:
            n.scenario = c.to_scenario(name)
            n.as_of, n.source = today, src
            n.tags = sorted((set(n.tags) | {"draft"}) - {PENDING_TAG})
            filled += 1
        else:
            n.scenario = None
            n.value = None
            n.tags = sorted(set(n.tags) | {PENDING_TAG})
            n.question = (n.question + " — 자동 초안은 시나리오를 만들지 않는다: 콘솔 「시나리오 후보 넣기」 로 증권사 자체 후보를 고르거나 직접 입력").strip(" —")
    if filled == 3:
        notes.append(f"시나리오: {by_name['Base'].broker} 리포트가 제시한 Bull/Base/Bear 케이스를 그대로 (확률은 미입력)")
    elif filled:
        notes.append(f"시나리오: 리포트 제시 케이스 {filled}개만 채움 — 나머지는 공란")
    else:
        notes.append("시나리오: 리포트에 Bull/Base/Bear 제시가 없어 A3.1~3 을 비워 둔다 (증권사 자체 후보 "
                     f"{sum(1 for c in cands if c.kind != '리포트 제시')}개는 meta.draft.scenario_candidates)")
    # 확률·ER·RR 은 사람 몫
    m["A3.4"].set(None, as_of=today)
    m["A3.5"].set(None, as_of=today)
    m["A3.4"].tags = sorted(set(m["A3.4"].tags) | {PENDING_TAG})
    m["A3.5"].tags = sorted(set(m["A3.5"].tags) | {PENDING_TAG})
    return notes, [asdict(c) for c in cands]


def apply_candidate(m: NewMap, cand: dict, name: str, *, today: date) -> Scenario:
    """사용자가 고른 후보를 A3.x 에 넣는다 (확률은 그대로 None). cand 는 meta.draft.scenario_candidates 의 항목."""
    c = ScenarioCandidate(**{k: v for k, v in cand.items() if k in ScenarioCandidate.__dataclass_fields__})
    if isinstance(c.report_date, str):
        c.report_date = date.fromisoformat(c.report_date)
    nid = {"Bear": "A3.1", "Base": "A3.2", "Bull": "A3.3"}[name]
    n = m[nid]
    sc = c.to_scenario(name)
    reinterp = c.is_reinterpretation(name)
    if reinterp:
        # v4 V5: 증권사가 Bear/Bull 로 제시한 것이 아니다 — 사용자가 그 증권사의 기준값을 이 시나리오에 '배치' 한 것임을 남긴다
        what = "기준 목표가" if c.origin == "broker_tp" else ("산식 재계산값" if c.origin == "broker_recalc" else f"{c.name} 케이스")
        sc.valuation_method = f"[사용자 재해석] {sc.valuation_method}"
        sc.assumptions = list(sc.assumptions) + [f"사용자 재해석: {c.broker} 의 {what} 을 {name} 시나리오에 배치 — 증권사가 {name} 로 제시한 값이 아님"]
    n.scenario = sc
    n.as_of = today
    n.source = Source(name=f"{c.broker} 리포트 ({c.kind})" + (" · 사용자 재해석" if reinterp else ""), url=c.source_file, retrieved_at=today)
    n.tags = sorted((set(n.tags) | {"draft"}) - {PENDING_TAG})
    n.history.append(HistoryPoint(as_of=today, value=c.target_price,
                                  source=Source(name=("사용자 재해석 — " if reinterp else "사용자 선택: ") + c.label(), retrieved_at=today)))
    draft = m.meta.setdefault("draft", {})
    draft.setdefault("scenario_origin", {})[name] = {"origin": c.origin, "broker": c.broker, "kind": c.kind, "case_name": c.name,
                                                       "target_price": c.target_price, "reinterpretation": reinterp, "as_of": str(today)}
    return n.scenario


EXAMPLE_PROBS = {"Bear": 0.30, "Base": 0.45, "Bull": 0.25}


def apply_example_probs(m: NewMap, *, today: date, probs: dict[str, float] | None = None) -> list[str]:
    """「예시 확률 입력」 — 통계적 근거 없는 미검증 가정임을 history 에 남기고 넣는다. 세 시나리오가 다 있어야 한다."""
    probs = probs or EXAMPLE_PROBS
    done: list[str] = []
    for nid, name in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
        n = m[nid]
        if n.scenario is None:
            raise ValueError(f"{name} 시나리오가 비어 있어 확률을 넣을 수 없다")
    for nid, name in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
        n = m[nid]
        n.scenario.probability = probs[name]
        n.history.append(HistoryPoint(as_of=today, value=probs[name], source=Source(name="예시 확률 — 통계적 근거 없는 미검증 가정 (사용자 입력)", retrieved_at=today)))
        done.append(f"{name} {probs[name]:.2f}")
    for nid in ("A3.4", "A3.5"):
        m[nid].tags = sorted(set(m[nid].tags) - {PENDING_TAG})
    return done


__all__ = ["PENDING_TAG", "ValuationSpec", "ScenarioCandidate", "find_report_cases", "find_valuation", "candidates_from", "fill_scenarios_v3",
           "apply_candidate", "apply_example_probs", "EXAMPLE_PROBS"]
