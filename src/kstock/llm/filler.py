"""노드 채움 (NodeFiller) 과 LLM 이벤트 분류기.

- `MockFiller`   : 테스트용. 미리 준 답을 돌려준다.
- `ClaudeFiller` : anthropic SDK (선택 의존성 `pip install kstock[llm]`), `ANTHROPIC_API_KEY`.
- `fill_map()`   : 비어 있는 노드(또는 지정 노드) 를 순서대로 채우고 결과를 돌려준다.

LLM 은 노드를 '채우기'만 한다. 상태등·매트릭스·검증은 코드가 한다 (§11).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from ..collect.base import EventClassifier, RawDisclosure
from ..env import describe, load_dotenv
from ..models import Confidence, Event, HoldingMap, MindMap, Node, NodeStatus, NodeType, Source
from .prompts import SYSTEM_CLASSIFIER, SYSTEM_FILLER, classifier_prompt, node_prompt, parse_json_block


@dataclass
class FillResult:
    node_id: str
    value: Any
    confidence: Confidence | None = None
    rationale: str = ""
    source: Source | None = None
    as_of: date | None = None
    raw: str = ""

    def apply(self, node: Node) -> None:
        if self.value is None:
            node.status = NodeStatus.unverified
            if self.rationale:
                node.tags = sorted(set(node.tags) | {"needs_data"})
            return
        value = _coerce(self.value, node)
        node.set(value, as_of=self.as_of, source=self.source, confidence=self.confidence)
        if node.node_type == NodeType.fact and self.source is None:
            node.status = NodeStatus.unverified


def _coerce(value: Any, node: Node) -> Any:
    vt = node.value_type.value
    if vt in ("number", "percent", "price", "probability") and isinstance(value, str):
        try:
            return float(value.replace(",", "").rstrip("%"))
        except ValueError:
            return value
    if vt == "bool" and isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "예", "y", "1")
    if vt == "enum" and node.enum_options and value not in node.enum_options:
        # 느슨한 매칭
        for opt in node.enum_options:
            if str(value).strip().lower() == opt.lower():
                return opt
    return value


class NodeFiller(Protocol):
    def fill(self, node: Node, m: MindMap, *, materials: str = "") -> FillResult: ...


class MockFiller:
    """answers: {node_id: value | {"value":..., "confidence":..., "rationale":..., "source": {...}}}"""

    def __init__(self, answers: dict[str, Any] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def fill(self, node: Node, m: MindMap, *, materials: str = "") -> FillResult:
        self.calls.append(node.id)
        a = self.answers.get(node.id)
        if a is None:
            return FillResult(node.id, None, Confidence.L, "mock: 답 없음")
        if isinstance(a, dict) and "value" in a:
            src = a.get("source")
            return FillResult(node.id, a["value"], Confidence(a.get("confidence", "M")), a.get("rationale", ""),
                              Source(**src) if src else None,
                              date.fromisoformat(a["as_of"]) if a.get("as_of") else None)
        return FillResult(node.id, a, Confidence.M, "mock")


class ClaudeFiller:
    """Claude API 로 노드를 채운다. anthropic 패키지와 ANTHROPIC_API_KEY 필요."""

    def __init__(self, model: str | None = None, *, api_key: str | None = None, max_tokens: int = 800,
                 temperature: float = 0.0):
        load_dotenv()  # 프로젝트 루트 .env 의 ANTHROPIC_API_KEY · KSTOCK_LLM_MODEL (셸 환경변수가 있으면 그것이 우선)
        self.model = model or os.environ.get("KSTOCK_LLM_MODEL", "claude-sonnet-4-5")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None

    def _client_or_raise(self):
        if self._client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("pip install 'kstock[llm]' (anthropic) 가 필요합니다") from e
            if not self.api_key:
                raise RuntimeError(describe("ANTHROPIC_API_KEY") + " — 프로젝트 루트 .env 에 ANTHROPIC_API_KEY=… 를 적으세요 (.env.example 참고)")
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _complete(self, system: str, user: str) -> str:
        client = self._client_or_raise()
        msg = client.messages.create(model=self.model, max_tokens=self.max_tokens, temperature=self.temperature,
                                     system=system, messages=[{"role": "user", "content": user}])
        return "".join(getattr(b, "text", "") for b in msg.content)

    def fill(self, node: Node, m: MindMap, *, materials: str = "") -> FillResult:
        raw = self._complete(SYSTEM_FILLER, node_prompt(node, m, materials=materials))
        data = parse_json_block(raw)
        src = data.get("source")
        return FillResult(
            node_id=node.id, value=data.get("value"),
            confidence=Confidence(data["confidence"]) if data.get("confidence") in ("H", "M", "L") else None,
            rationale=data.get("rationale", ""),
            source=Source(name=src.get("name", "LLM"), url=src.get("url", "")) if isinstance(src, dict) and src.get("name") else None,
            as_of=date.fromisoformat(data["as_of"]) if data.get("as_of") else None,
            raw=raw,
        )


FILLABLE_TYPES = {NodeType.fact, NodeType.estimate, NodeType.judgment}


def fill_map(m: MindMap, filler: NodeFiller, *, node_ids: list[str] | None = None, materials: str = "",
             only_empty: bool = True, skip_sub_schema: bool = True) -> list[FillResult]:
    """맵의 노드를 채운다. 기본: 값이 비어 있는 fact/estimate/judgment 노드만. driver/scenario/rule 노드는
    구조화 값이 필요하므로 기본 제외(skip_sub_schema) — 전용 프롬프트는 다음 단계."""
    targets: list[Node] = []
    for n in m.nodes.values():
        if node_ids is not None and n.id not in node_ids:
            continue
        if node_ids is None:
            if n.node_type not in FILLABLE_TYPES or n.meta_only:
                continue
            if only_empty and not n.is_empty():
                continue
            if skip_sub_schema and (n.id.startswith(("A1.", "A3.", "B2.1.", "B3.")) and n.id.count(".") >= 1
                                    and n.value_type.value in ("number", "price")):
                continue
        targets.append(n)
    results: list[FillResult] = []
    for n in targets:
        r = filler.fill(n, m, materials=materials)
        r.apply(n)
        results.append(r)
    return results


class ClassifierLLM(EventClassifier):
    """LLM 이벤트 분류기 — RuleBasedClassifier 를 대체. filler 와 같은 클라이언트를 쓴다."""

    def __init__(self, filler: ClaudeFiller):
        self.filler = filler

    def classify(self, raw: RawDisclosure, holding: HoldingMap) -> Event:
        drivers = [(n.id, n.label) for n in holding.drivers()]
        out = self.filler._complete(SYSTEM_CLASSIFIER, classifier_prompt(raw.title, raw.body, drivers,
                                                                         holding.value("B1.1")))
        data = parse_json_block(out)
        valid_ids = {i for i, _ in drivers}
        return Event(
            date=raw.date, fact=data.get("summary") or raw.title,
            affects=[a for a in data.get("affects", []) if a in valid_ids],
            direction=data.get("direction", "중립"), priced_in=data.get("priced_in", "아니오"),
            materiality=data.get("materiality", "M"),
            source=Source(name=raw.source or "disclosure", url=raw.url, retrieved_at=raw.date),
        )


__all__ = ["FillResult", "NodeFiller", "MockFiller", "ClaudeFiller", "fill_map", "ClassifierLLM", "FILLABLE_TYPES"]
