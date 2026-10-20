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
    "evidence": "근거 자료 A8.1 (기업) 을 자료에서 요약한다.",
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
    for nid in ("A2.1", "A2.2", "A4.1", "A4.2", "A4.3", "A8.1.4"):
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
