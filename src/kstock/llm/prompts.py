"""LLM 프롬프트 — 노드 채움(filler) 과 이벤트 분류(classifier).

원칙: 노드의 `question` 이 프롬프트의 본체다. 모델은 질문에만 답하고, 출력은 JSON 하나로 고정한다.
숫자는 숫자 타입으로, 출처 없는 사실은 confidence L + status unverified 로 돌려 보낸다.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import MindMap, Node

SYSTEM_FILLER = """당신은 주식 리서치 보조 분석가다. 사용자가 주는 마인드맵 노드 하나의 질문에만 답한다.
규칙:
1. 반드시 아래 JSON 한 개만 출력한다. 설명 문장을 덧붙이지 않는다.
   {"value": <value_type 에 맞는 값>, "confidence": "H"|"M"|"L", "rationale": "<근거 1~3문장>",
    "source": {"name": "<출처명>", "url": "<URL 또는 빈 문자열>"} | null, "as_of": "YYYY-MM-DD" | null}
2. value_type 이 number/percent/price/probability 이면 value 는 숫자. percent 는 소수(0.12) 가 아니라 퍼센트 단위 숫자(12.0) 로.
   enum 이면 enum_options 중 하나. bool 이면 true/false. text 면 2문장 이내.
3. 모르는 것은 추측하지 않는다. 근거가 없으면 value 를 null, confidence 를 "L" 로 두고 rationale 에 무엇이 필요한지 적는다.
4. 제공된 컨텍스트(논제, 다른 노드 값, 자료 발췌) 밖의 사실을 단정하지 않는다. 자료에 없으면 source 는 null.
5. 한국어로 쓴다."""

SYSTEM_CLASSIFIER = """당신은 공시·뉴스 분류기다. 한 건의 공시/기사와 보유 종목의 핵심 드라이버 목록을 받아 다음 JSON 한 개만 출력한다.
{"affects": ["<영향받는 드라이버 id>", ...], "direction": "강화"|"약화"|"중립", "priced_in": "예"|"부분"|"아니오",
 "materiality": "H"|"M"|"L", "summary": "<사실만 한 문장>"}
규칙: affects 는 주어진 드라이버 id 중에서만 고른다(없으면 빈 배열). direction 은 투자 논제 기준이다.
materiality 는 논제에 미치는 영향 크기다(가이던스·경영진·유상증자·M&A·지배구조는 보통 H).
priced_in 은 공시 후 주가 반응·컨센서스 변화 정보가 있을 때만 '예'/'부분', 없으면 '아니오'."""


def map_context(m: MindMap, *, max_chars: int = 2500) -> str:
    """노드 채움에 넣는 맵 요약 — 논제, 투자유형, 채워진 값들."""
    lines: list[str] = []
    root = m.root()
    lines.append(f"[맵] {m.kind.value} {m.name or ''} ({m.ticker or ''})")
    if root.value:
        lines.append(f"[논제] {root.value}")
    for n in m.nodes.values():
        if n.id == root.id or n.is_empty():
            continue
        if n.driver:
            d = n.driver
            lines.append(f"- {n.id} {n.label}: 현재 {d.current.value} / 컨센 {d.consensus.value} / 내 추정 {d.my_estimate.value}")
        elif n.scenario:
            lines.append(f"- {n.id} {n.scenario.name}: p={n.scenario.probability} TP={n.scenario.target_price}")
        elif n.value is not None and not isinstance(n.value, (dict, list)):
            lines.append(f"- {n.id} {n.label}: {n.value}")
    text = "\n".join(lines)
    return text[:max_chars]


def node_prompt(node: Node, m: MindMap, *, materials: str = "") -> str:
    spec = {
        "id": node.id, "label": node.label, "question": node.question,
        "node_type": node.node_type.value, "value_type": node.value_type.value,
        "unit": node.unit, "enum_options": node.enum_options,
    }
    parts = [
        "## 채울 노드", json.dumps(spec, ensure_ascii=False),
        "## 맵 컨텍스트", map_context(m),
    ]
    if materials:
        parts += ["## 자료 발췌", materials[:6000]]
    parts.append("위 노드의 question 에 답하는 JSON 하나만 출력하라.")
    return "\n".join(parts)


def classifier_prompt(title: str, body: str, drivers: list[tuple[str, str]], thesis: str | None) -> str:
    drv = "\n".join(f"- {i}: {label}" for i, label in drivers) or "- (없음)"
    return (
        f"## 투자 논제\n{thesis or '(없음)'}\n\n## 핵심 드라이버\n{drv}\n\n"
        f"## 공시/기사\n제목: {title}\n본문: {body[:4000]}\n\n분류 JSON 하나만 출력하라."
    )


def parse_json_block(text: str) -> dict[str, Any]:
    """모델 출력에서 첫 JSON 객체를 꺼낸다 (코드펜스 허용)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(t[start:end + 1])


__all__ = ["SYSTEM_FILLER", "SYSTEM_CLASSIFIER", "map_context", "node_prompt", "classifier_prompt", "parse_json_block"]
