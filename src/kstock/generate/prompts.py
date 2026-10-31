"""그룹별 LLM 프롬프트 (자동 생성 계획 §3b). 그룹마다 JSON 스키마를 강제한다.

공통 규칙은 `llm/prompts.py` 의 SYSTEM_FILLER 와 같다: 모르면 null, 자료에 없는 숫자는 쓰지 않는다, 한국어.
숫자 드라이버의 current 는 규칙 채움이 고정한다 — LLM 이 새로 제안한 드라이버는 `evidence`(리포트 인용) 가
자료 안에 있어야 받아들인다 (`llm_fill.py`).
"""

from __future__ import annotations

import json
from typing import Any

from ..models import NewMap
from ..reports.digest import ReportDigest
from .kpi_catalog import kpi_candidates, sector_name

SYSTEM_GROUP = """당신은 주식 리서치 보조 분석가다. 증권사 리포트 발췌(자료)와 현재 마인드맵 상태를 받아, 요청된 노드 그룹을 채우는 JSON 한 개만 출력한다.
규칙:
1. 반드시 요청된 스키마의 JSON 한 개만 출력한다. 설명 문장·코드펜스를 덧붙이지 않는다.
2. 자료에 없는 숫자를 만들지 않는다. 숫자를 쓸 때는 그 숫자가 적힌 자료 문장을 evidence 에 그대로 옮긴다. 근거가 없으면 null.
3. 리포트는 증권사의 뷰다. 그대로 베끼지 말고 '시장이 믿는 것(implied)' 과 '다르게 볼 여지' 를 구분한다. 매수 일색이면 그 편향을 적는다.
4. 투자 판단(BUY/WATCH/PASS) 은 쓰지 않는다. 그것은 사람이 한다.
5. 한국어로, 각 텍스트 필드는 2문장 이내로 쓴다."""

GROUPS = ("drivers", "thesis", "scenarios", "risks", "catalysts", "evidence")

SCHEMAS: dict[str, str] = {
    "drivers": """{"drivers": [{"label": "<변수명>", "unit": "<단위>", "higher_is_better": true|false,
   "current": <숫자|null>, "current_as_of": "YYYY-MM-DD"|null, "consensus": <숫자|null>, "my_estimate": <숫자|null>,
   "rationale": "<내 추정 근거 1~2문장>", "check_indicator": "<어디서 무엇을 확인>", "next_check": "YYYY-MM-DD"|null,
   "evidence": "<current/consensus 숫자가 적힌 자료 문장 그대로>", "keep_id": "<이미 있는 드라이버를 유지하려면 그 id (예 A1.1), 새 변수면 null>"}]}
 — 3~5개. 이 변수가 틀리면 논제가 무너지는 것만. 이미 있는 드라이버(규칙 채움)는 keep_id 로 유지하고 라벨·근거만 다듬어도 된다.""",
    "thesis": """{"thesis": "<왜·언제까지·얼마로, 시장이 아직 반영하지 않은 것 — 한 문장>", "horizon": "3M"|"1Y"|"3Y+",
   "confidence": "H"|"M"|"L", "implied": "<현재 주가가 성립하려면 시장이 믿고 있어야 하는 것>",
   "mine": "<리포트 논거와 컨센서스를 대조해 다르게 볼 여지 — 없으면 '리포트 논거와 같다' 고 쓴다>",
   "why_market_wrong": "시간축"|"정보"|"심리"|"구조"|null, "invest_type": "growth"|"value"|"turnaround"|"momentum"|"event"}""",
    "scenarios": """{"Bear": {"probability": <0~1>, "target_price": <원>, "assumptions": ["<가정>", ...], "method": "<밸류 방법>"},
 "Base": {...}, "Bull": {...}}
 — 확률 합 1.0. 목표가는 주어진 앵커의 ±25% 안에서만 조정한다. 가정에는 드라이버 값(예 2027E 영업이익)과 멀티플을 적는다.""",
    "risks": """{"risks": [{"text": "<무엇이>", "probability": <0~1>, "impact_pct": <음수 %>}] (3개),
 "kill": [{"driver_id": "<A1.x>", "op": "<"|"<="|">"|">=", "threshold": <숫자>, "note": "<이 사실이 확인되면 왜 논제가 끝나는가>"}] (1~2개),
 "early_warning": "<그 사실을 미리 알려주는 지표와 확인 주기>"}""",
    "catalysts": """{"events": [{"date": "YYYY-MM-DD", "fact": "<날짜 박힌 사건>", "affects": ["<A1.x>", ...],
   "direction": "강화"|"약화"|"중립", "materiality": "H"|"M"|"L", "estimated": true|false}]} — 최대 5개, 날짜 없는 것은 넣지 않는다.""",
    "evidence": """{"business_model": "<무엇을 팔아 어떻게 돈을 버나>", "moat": "<마진을 지키는 것, 5년 뒤에도 유효한가>",
 "governance_payout": "<자본배분·대주주·배당·자사주>", "peers": "<피어 대비 프리미엄/디스카운트와 정당성 — 자료에 없으면 null>"}""",
}

INSTRUCTIONS: dict[str, str] = {
    "drivers": "핵심 드라이버 A1 을 고른다. 섹터 KPI 후보와 자료의 숫자를 참고한다.",
    "thesis": "투자 논제 A0 과 컨센서스 vs 내 뷰 A2 를 쓴다. 현재 규칙 채움 초안은 사실 요약일 뿐이다.",
    "scenarios": "Bear/Base/Bull 의 가정·확률·목표가를 쓴다. 앵커는 컨센서스 분포에서 나왔다.",
    "risks": "하락 리스크 3개와 사실 기준 무효화 조건(Kill), 조기 경보 지표를 쓴다. 가격이 아니라 사실(드라이버 값) 기준이어야 한다.",
    "catalysts": "날짜가 박힌 촉매 이벤트를 쓴다. 자료에 날짜가 있는 것, 그리고 통상 실적 일정(추정은 estimated=true).",
    "evidence": "근거 자료 A9.1 (기업) 을 자료에서 요약한다.",
}


def map_state(m: NewMap) -> str:
    lines = [f"[맵] {m.name} ({m.ticker}) 프로파일 {m.profile.value if m.profile else '-'} · 현재가 {m.current_price}"]
    if m.thesis:
        lines.append(f"[A0 초안] {m.thesis}")
    for n in m.drivers():
        d = n.driver
        assert d is not None
        lines.append(f"- {n.id} {n.label} [{n.unit or ''}]: 현재 {d.current.value} ({d.current.rationale or ''}) / 컨센 {d.consensus.value} / 내 추정 {d.my_estimate.value}")
    for nm, sc in m.scenarios().items():
        lines.append(f"- {nm}: p={sc.probability} TP={sc.target_price:,.0f} · {'; '.join(sc.assumptions[:3])}")
    for nid in ("A2.1", "A2.2", "A5.1", "A5.2", "A5.3", "A9.1.4"):
        v = m.value(nid)
        if v:
            lines.append(f"- {nid} {m[nid].label}: {v if not isinstance(v, dict) else v.get('text')}")
    return "\n".join(lines)


def group_prompt(group: str, m: NewMap, d: ReportDigest, *, sector: str | None, anchors: dict[str, float] | None = None,
                 max_materials: int = 9000) -> str:
    extra: list[str] = []
    if group == "drivers":
        cands = kpi_candidates(sector)
        extra.append(f"## 섹터 KPI 후보 ({sector_name(sector)})")
        extra += [f"- {k.label} [{k.unit}] 확인: {k.check_indicator}" + (" (표에서 채움 가능)" if k.metric else "") for k in cands]
    if group == "scenarios" and anchors:
        extra.append("## 앵커 (컨센서스 분포)")
        extra += [f"- {k}: {v:,.0f}원" for k, v in anchors.items()]
    parts = [f"## 그룹: {group}", INSTRUCTIONS[group], "## 출력 스키마", SCHEMAS[group], "## 현재 맵 상태", map_state(m)]
    parts += extra
    parts += ["## 자료 (리포트 발췌)", d.materials(max_materials), f"위 스키마의 JSON 하나만 출력하라 (그룹 {group})."]
    return "\n".join(parts)


def parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"JSON 객체가 없다: {text[:160]!r}")
    return json.loads(t[s:e + 1])


__all__ = ["SYSTEM_GROUP", "GROUPS", "SCHEMAS", "INSTRUCTIONS", "group_prompt", "map_state", "parse_json"]


# --------------------------------------------------------------------------- Claim 추출 (설계 v3 §2.2)
CLAIM_PROMPT_VERSION = "c3"   # c1 → c2 (2026-10-21): 인용 1~3개(quotes) · 명제의 숫자는 전부 인용 안에 · 한 명제 한 방향 · 추정치 변경 필수
                              # c2 → c3 (2026-10-22, 설계 v4 V1): asserted_by(누구의 주장) · certainty(확정 수준) · as_of(기준일)
CLAIM_SYSTEM = (
    "너는 증권사 기업분석 리포트에서 '투자 주장(Claim)' 을 뽑는 분석가다. 주장은 검증 가능한 명제 하나다. "
    "리포트에 적힌 것만 뽑고, 리포트에 없는 인과·숫자·결론을 만들지 않는다. "
    "모든 주장에는 원문에서 그대로 복사한 인용(quotes, 1~3개, 각 20~200자) 을 붙인다 — 인용이 원문에 없으면 그 주장은 버려진다. "
    "명제에 쓴 숫자와 사실은 **모두 인용 안에 있어야 한다**: 인용 밖의 내용을 명제에 넣지 말고, 필요하면 인용을 하나 더 붙이거나 "
    "명제를 인용이 말하는 범위로 줄인다 (명제가 인용을 넘어서면 그 주장은 검토함으로 밀려 맵에 못 들어간다). "
    "한 명제 = 한 방향: 긍정과 부정이 섞인 문장(예: 'DS 는 상회, MX 는 적자 확대') 은 둘로 나눈다. neutral 은 방향이 정말 없을 때만. "
    "누구의 주장인지(asserted_by) 와 확정 수준(certainty) 을 구분한다 — '설비투자 계획을 발표했다' 는 company+announced, "
    "'2027년 공급을 30% 늘릴 전망이다' 는 analyst+expected, '공급을 30% 늘릴 계획이라고 밝혔다' 는 company+planned(공급 증가가 일어난 것이 아니다), "
    "'공급이 늘면 영업이익이 개선될 것이다' 는 analyst+conditional(condition 필수), 발표된 분기 실적 수치는 company+realized. "
    "답은 JSON 객체 하나, 설명 없이."
)
CLAIM_SCHEMA = (
    '{"claims": [{"proposition": "명제 한 문장", "epistemic_type": "fact|estimate|opinion", '
    '"roles": ["thesis|risk|catalyst|valuation|shareholder|business|revision_reason|scenario"], '
    '"driver_id": "사전 id 또는 null", "driver_label": "변수 이름(자유)", "target_metric_id": "op|revenue|eps|opm|target_price|per|… 또는 null", '
    '"mechanism": "원인 → 결과 경로 (예: 인증 완료 → 출하)" , "direction": "positive|negative|neutral", '
    '"condition": "조건부면 조건문, 아니면 null", "horizon": "2027E|4Q26|2026-10-30|null", '
    '"asserted_by": "analyst|company|market|authority|other", "certainty": "realized|announced|planned|expected|conditional", '
    '"as_of": "정보 기준일 YYYY-MM-DD (실적은 분기 말, 발표는 발표일, 전망은 리포트 날짜) 또는 null", '
    '"quant": [{"metric": "op|revenue|eps|…", "col": "2027E|3Q26E|null", "value": 123.4, "unit": "십억원|억원|조원|원|%|배"}], '
    '"quotes": ["원문 그대로", "…(필요하면 2~3번째 인용)"], "inferred": false}]}'
)


def claim_prompt(chunk_text: str, *, broker: str, report_date: str, name: str, ticker: str, section: str,
                 catalog_text: str, facts_summary: str) -> str:
    return (
        "## 그룹: claims\n"
        f"리포트: {broker} {report_date} · {name} ({ticker}) · 구간: {section}\n"
        f"리포트에서 읽은 사실 (참고): {facts_summary}\n\n"
        "### 드라이버 사전 (driver_id 는 이 목록에서 고른다; 없으면 null 로 두고 driver_label 에 자유롭게)\n"
        f"{catalog_text}\n\n"
        "### 규칙\n"
        "- 주장 1개 = 명제 1개 = 방향 1개. 리스크·촉매·논거·밸류·주주환원·사업·변경 사유·시나리오 역할은 복수 가능.\n"
        "- quotes: 명제의 숫자·사실을 전부 덮는 인용 1~3개 (원문 그대로, 줄바꿈은 공백으로). quant 에는 명제에 적힌 숫자만.\n"
        "- 반드시 뽑을 것: 실적 추정치 변경(이전 → 현재, 변경률; roles 에 revision_reason) · 분기/연간 전망 수치(estimate) · "
        "목표주가와 산식(valuation) · 사업부별 실적 방향(business) · 리스크(risk) · 일정이 있는 촉매(catalyst) · 주주환원(shareholder).\n"
        "- epistemic_type: 실적·계약·공시 같은 사실=fact, 전망·추정=estimate, 애널리스트 판단=opinion. certainty=realized 면 fact.\n"
        "- asserted_by: 기업 발표·가이던스·IR 인용은 company, 애널리스트 추정·판단은 analyst, 컨센서스·시장 기대는 market, 정부·규제는 authority.\n"
        "- inferred: 리포트가 말하지 않은 인과를 네가 보탰으면 true (그 주장은 맵에 들어가지 않고 검토함에만 남는다).\n"
        "- 이 구간에서 중요한 주장 5~15개. 사소한 서술·면책 문구·표 설명은 제외.\n"
        f"### 출력 형식\n{CLAIM_SCHEMA}\n\n"
        "### 리포트 구간\n"
        f"{chunk_text}"
    )


SUPPORT_SYSTEM = (
    "너는 근거 검증자다. 각 항목의 인용(quote; ' … ' 로 이어진 여러 구절일 수 있다) 이 명제(proposition) 를 실제로 지지하는지 판정한다. "
    "supported = 인용이 명제의 서술(무엇이·어느 방향으로·왜) 을 직접 뒷받침; contradicted = 인용이 명제와 반대이거나 명제가 인용을 왜곡(예: '미확인' 을 '확정' 으로); "
    "insufficient = 인용만으로는 명제의 서술을 판단할 수 없다. "
    "숫자의 존재와 일치는 별도 코드가 리포트 표·본문까지 대조하므로, **인용에 숫자가 없다는 이유만으로 insufficient 를 주지 않는다** — "
    "서술이 맞으면 supported 로 하고 why 에 '수치 ○○는 인용 밖' 이라고만 적는다. 설명 없이 JSON 객체 하나."
)


def support_prompt(items: list[tuple[str, str, str]]) -> str:
    """items: (id, proposition, quote)."""
    rows = "\n".join(f'- id: {i}\n  명제: {p}\n  인용: "{q}"' for i, p, q in items)
    return ("## 그룹: support\n"
            '출력: {"verdicts": [{"id": "…", "verdict": "supported|contradicted|insufficient", "why": "한 줄"}]}\n\n'
            f"{rows}")


# --------------------------------------------------------------------------- 쟁점 기준 명제 판정 (설계 v4 V3)
ISSUE_SYSTEM = (
    "너는 증권사 리포트 비교 분석가다. 쟁점마다 '기준 명제' 하나와 증권사별 주장 목록이 주어진다. "
    "증권사마다 그 기준 명제에 대해 agree(동의: 같은 방향·같은 대상) / disagree(반대: 방향이 다르거나 효과를 부정) / "
    "partial(부분 동의: 원인에는 동의하지만 결과·대상·크기·시점이 다름 — 예: 공급 증가에는 동의하되 이익 효과는 부정) / "
    "unrelated(기준 명제와 무관한 언급) 중 하나로 판정한다. 주장이 없는 증권사는 적지 않는다. "
    "리포트에 적힌 주장만 보고 판정하고 네 의견을 넣지 않는다. 설명 없이 JSON 객체 하나."
)


def issue_prompt(items: list[tuple[str, str, dict[str, list[str]]]]) -> str:
    """items: (issue_id, reference_proposition, {broker: [tagged propositions]})."""
    blocks = []
    for iid, ref, by in items:
        rows = "\n".join(f"  - {b}:\n" + "\n".join(f"      · {p}" for p in props) for b, props in by.items())
        blocks.append(f"- id: {iid}\n  기준 명제: {ref}\n  증권사별 주장:\n{rows}")
    return ("## 그룹: issues\n"
            '출력: {"verdicts": [{"id": "…", "broker": "…", "verdict": "agree|disagree|partial|unrelated", "why": "한 줄"}]}\n\n'
            + "\n".join(blocks))


__all__ += ["CLAIM_PROMPT_VERSION", "CLAIM_SYSTEM", "CLAIM_SCHEMA", "claim_prompt", "SUPPORT_SYSTEM", "support_prompt",
            "ISSUE_SYSTEM", "issue_prompt"]
