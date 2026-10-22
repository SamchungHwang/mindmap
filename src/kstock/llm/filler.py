"""노드 채움 (NodeFiller) 과 LLM 이벤트 분류기.

- `MockFiller`   : 테스트용. 미리 준 답을 돌려준다.
- `ClaudeFiller` : anthropic SDK (선택 의존성 `pip install kstock[llm]`), `ANTHROPIC_API_KEY`.
- `fill_map()`   : 비어 있는 노드(또는 지정 노드) 를 순서대로 채우고 결과를 돌려준다.

LLM 은 노드를 '채우기'만 한다. 상태등·매트릭스·검증은 코드가 한다 (§11).
"""

from __future__ import annotations

import os
import re
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


_UNEXPECTED_KW = re.compile(r"unexpected keyword argument '([^']+)'")


def _create_tolerant(client, kw: dict, dropped: set[str]):  # noqa: ANN001
    """client.messages.create(**kw) — 설치된 anthropic SDK 버전이 모르는 인자(예: temperature 를 뺀 새 SDK) 는
    TypeError 메시지에서 이름을 읽어 빼고 다시 부른다. 필수 인자(model/max_tokens/messages) 는 빼지 않는다."""
    for _ in range(4):
        try:
            return client.messages.create(**kw)
        except TypeError as e:
            m = _UNEXPECTED_KW.search(str(e))
            name = m.group(1) if m else None
            if not name or name not in kw or name in ("model", "max_tokens", "messages"):
                raise
            kw.pop(name)
            dropped.add(name)
    return client.messages.create(**kw)


# 사고(thinking) 끄기 — 모델마다 받는 값이 다르다. Sonnet 5.5: between_tools 만 (disabled 는 400) · 4.x: disabled ·
# 그 밖/옛 모델: thinking 필드 자체를 모를 수 있다 → 없음. 통한 값은 모델별로 기억한다 (None = 필드 없이).
_THINKING_OFF_CANDIDATES: tuple[dict | None, ...] = ({"type": "between_tools"}, {"type": "disabled"}, None)
_THINKING_MODE_OK: dict[str, dict | None] = {}
_UNSET = object()


def _thinking_modes(model: str | None) -> list[dict | None]:
    """시도 순서. KSTOCK_LLM_THINKING=adaptive|on 이면 모델 기본(사고 켜짐) 그대로 — 필드를 보내지 않는다."""
    pref = os.environ.get("KSTOCK_LLM_THINKING", "off").strip().lower()
    if pref in ("adaptive", "on", "default"):
        return [None]
    known = _THINKING_MODE_OK.get(model or "", _UNSET)
    if known is not _UNSET:
        return [known]  # type: ignore[list-item]
    return list(_THINKING_OFF_CANDIDATES)


def _is_bad_request(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 400 or type(e).__name__ == "BadRequestError"


class ClaudeFiller:
    """Claude API 로 노드를 채운다. anthropic 패키지와 ANTHROPIC_API_KEY 필요."""

    def __init__(self, model: str | None = None, *, api_key: str | None = None, max_tokens: int = 800,
                 temperature: float | None = 0.0):
        load_dotenv()  # 프로젝트 루트 .env 의 ANTHROPIC_API_KEY · KSTOCK_LLM_MODEL (셸 환경변수가 있으면 그것이 우선)
        # 모델: 인자 > KSTOCK_LLM_MODEL > (첫 호출 때 /v1/models 목록에서 최신 Sonnet 자동 선택)
        self.model = model or os.environ.get("KSTOCK_LLM_MODEL", "").strip() or None
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # 조직 범위(워크스페이스 미지정) 키는 anthropic-workspace-id 헤더가 있어야 한다 — .env 의 ANTHROPIC_WORKSPACE_ID
        self.workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip() or None
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
            headers = {"anthropic-workspace-id": self.workspace_id} if self.workspace_id else None
            self._client = anthropic.Anthropic(api_key=self.api_key, default_headers=headers)
            if self.model is None:
                self.model = self._resolve_model(self._client)
        return self._client

    @staticmethod
    def _resolve_model(client) -> str:
        """KSTOCK_LLM_MODEL 이 없을 때: 계정에서 쓸 수 있는 모델 목록에서 고른다. 목록을 못 받으면 FALLBACK_MODEL."""
        from .models import FALLBACK_MODEL, pick_model

        try:
            page = client.models.list(limit=100)
            ids = [getattr(m, "id", "") for m in getattr(page, "data", [])]
        except Exception:
            return FALLBACK_MODEL
        return pick_model(ids) or FALLBACK_MODEL

    _dropped_kwargs: set[str] = set()   # 설치된 SDK 가 거부한 인자 (프로세스 안에서 한 번만 배운다)
    MAX_TOKENS_CAP = 32000

    def _complete(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        """한 번 묻고 텍스트 블록만 돌려준다.

        - Claude 5 계열(Sonnet 5.5 등) 은 기본으로 '적응형 사고' 가 켜져 있어 사고 토큰이 max_tokens 를 먼저 먹는다 →
          텍스트가 빈 채로 끝난다('JSON 객체가 없다'). 그래서 사고를 끄는 값을 모델이 받는 것부터 차례로 시도한다
          (between_tools → disabled → 없음) 하고, 통한 값은 모델별로 기억한다. KSTOCK_LLM_THINKING=adaptive 면 끄지 않는다.
        - stop_reason 이 max_tokens(잘림) 면 4배(상한 MAX_TOKENS_CAP) 로 한 번 더 묻는다.
        - temperature 는 SDK 1.x 가 인자를 뺐고 Claude 5 계열은 기본값 외를 400 으로 거절한다 → 거부되면 뺀다.
        """
        client = self._client_or_raise()
        budget = max_tokens or self.max_tokens
        base: dict = {"model": self.model, "max_tokens": budget, "system": system,
                      "messages": [{"role": "user", "content": user}]}
        if self.temperature is not None and "temperature" not in self._dropped_kwargs:
            base["temperature"] = self.temperature
        msg = None
        last_err: Exception | None = None
        for mode in _thinking_modes(self.model):
            kw = dict(base)
            if mode is not None:
                kw["thinking"] = mode
            try:
                msg = _create_tolerant(client, kw, self._dropped_kwargs)
            except Exception as e:  # noqa: BLE001
                if _is_bad_request(e) and ("thinking" in str(e).lower() or "effort" in str(e).lower()):
                    last_err = e
                    continue                      # 이 모델은 이 값을 모른다 — 다음 값
                if _is_bad_request(e) and "temperature" in str(e).lower() and "temperature" in kw:
                    self._dropped_kwargs.add("temperature")
                    base.pop("temperature", None)
                    kw.pop("temperature")
                    msg = _create_tolerant(client, kw, self._dropped_kwargs)
                else:
                    raise
            _THINKING_MODE_OK[self.model or ""] = mode
            break
        if msg is None:
            raise RuntimeError(f"사고(thinking) 설정을 모델이 전부 거절했다: {last_err}")
        text = "".join(b.text for b in msg.content
                       if isinstance(getattr(b, "text", None), str) and getattr(b, "type", "text") not in ("thinking", "redacted_thinking"))
        stop = getattr(msg, "stop_reason", None)
        if stop == "max_tokens" and budget < self.MAX_TOKENS_CAP:
            # 잘렸다 — 예산을 늘려 한 번 더 (사고가 켜진 모델이면 사고 토큰이 먹은 것)
            return self._complete(system, user, max_tokens=min(budget * 4, self.MAX_TOKENS_CAP))
        if not text.strip():
            kinds = [getattr(b, "type", "?") for b in msg.content]
            raise RuntimeError(f"모델이 텍스트를 주지 않았다 (stop_reason={stop}, 블록={kinds or '없음'}, max_tokens={budget}) — "
                               "사고(thinking) 토큰이 예산을 다 썼을 수 있다. KSTOCK_LLM_MODEL 로 다른 모델을 고르거나 max_tokens 를 늘릴 것")
        return text

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
