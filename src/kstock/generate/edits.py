"""HTML 마인드맵에서 사용자가 고친 내용(편집 연산) 을 맵에 적용한다.

연산은 JSON 목록이다 — 로컬 편집 서버의 `/save` 와, file:// 로 연 HTML 이 내려받는 `*.edits.json` 이 같은 형식을 쓴다.

    {"map_id": "A:035420:2026-10-23", "base_hash": "…", "edits": [
      {"op": "set",    "id": "A1.1", "fields": {"label": "…", "driver": {…}, "as_of": "2026-10-23"}},
      {"op": "add",    "parent": "A5", "id": "new:1", "fields": {"label": "…", "value": "…", "node_type": "estimate"}},
      {"op": "delete", "id": "new:1"}]}

규칙
  · 먼저 **복사본**에 적용해 pydantic 검증과 맵 검증을 통과해야 원본에 반영한다. 맵 검증은 "편집 전보다 오류가 늘면" 거절 —
    초안에 원래 있던 오류(확률 미입력 등) 는 저장을 막지 않는다.
  · 값·드라이버·시나리오·규칙·이벤트가 바뀐 노드는 이전 상태를 history 에 남기고(출처 "사용자 편집 (HTML)"), `user` 태그를 얻고
    `draft`/`pending`/`warn` 태그를 잃는다 — 변경안(changes) 은 이 노드를 자동 적용하지 않는다.
  · 삭제는 가지 전체. 다른 노드의 links 에서 지운 id 를 걷어낸다. 루트는 지우지 못한다.
  · `add` 의 id 는 서버가 매긴다(`next_child_id`). 클라이언트의 임시 id(`new:n`) 는 뒤 연산에서 실제 id 로 바꿔 읽는다.
  · 적용 전 원본을 versions_dir 에 `<map>.v<N>.json` 으로 보존하고 meta.version 을 올린다. A8.2(업데이트 로그) 가 있으면 한 줄 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..engine.validate import validate
from ..models import HistoryPoint, MindMap, Node, Source

EDIT_SOURCE = "사용자 편집 (HTML)"
USER_TAG = "user"
DROP_TAGS = {"draft", "pending", "warn", "suggested"}   # suggested = 옵션으로 미리 채운 제안값 (설계 v4 §6) — 사람이 저장하면 user
CONTENT_KEYS = ("value", "driver", "scenario", "rule", "event")
ALLOWED_FIELDS = {"label", "question", "value", "value_type", "unit", "enum_options", "source", "as_of", "confidence", "status",
                  "next_check", "weight", "links", "tags", "formula", "node_type", *CONTENT_KEYS}


class EditError(ValueError):
    """적용할 수 없는 편집 — 메시지를 그대로 사용자에게 보여 준다."""


@dataclass
class EditResult:
    applied: list[str] = field(default_factory=list)      # "set A1.1", "add A5.5", "delete A5.5"
    warnings: list[str] = field(default_factory=list)
    new_errors: list[str] = field(default_factory=list)   # 적용하면 늘어나는 검증 오류 (미리보기용; 적용 시엔 비어 있다)
    id_map: dict[str, str] = field(default_factory=dict)  # 임시 id → 실제 id
    version: int | None = None


def edit_hash(m: MindMap) -> str:
    """노드 전체 + meta.version 의 해시 — 페이지를 연 뒤 맵이 바뀌었는지 확인 (변경안의 map_hash 보다 넓게, 라벨·질문도 본다)."""
    payload = {"v": m.meta.get("version", 1), "nodes": [m.nodes[k].model_dump(mode="json") for k in sorted(m.nodes)]}
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def next_child_id(m: MindMap, parent: str) -> str:
    """부모 아래 비어 있는 다음 번호. 루트(A0) 아래는 새 가지(A10…), `A0.*`(기본 정보 묶음) 아래는 보조 필드(A0.5…)."""
    root = m.root_id
    aux = parent.endswith(".*")
    if aux:
        parent = parent[:-2]
    if parent not in m.nodes:
        raise EditError(f"부모 {parent} 가 없다")
    kids = [c.id for c in m.children(parent)]
    if parent == root and not aux:
        branches = [c for c in kids if "." not in c]
        nums = [int(c[1:]) for c in branches if c[1:].isdigit()]
        return f"{root[0]}{(max(nums) if nums else 0) + 1}"
    nums = []
    for c in kids:
        if aux and "." not in c:
            continue
        tail = c.rsplit(".", 1)[-1]
        if tail.isdigit():
            nums.append(int(tail))
    return f"{parent}.{(max(nums) if nums else 0) + 1}"


def _errors(m: MindMap, today: date) -> set[tuple[str | None, str]]:
    return {(i.node_id, i.message) for i in validate(m, today=today) if i.level == "error"}


def _changed(old: dict[str, Any], new: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if old.get(k) != new.get(k)]


def _allows_none(ann: Any) -> bool:
    import types
    import typing

    origin = typing.get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        return any(a is type(None) for a in typing.get_args(ann))
    return ann is Any


def _clean_sub(cls: Any, data: Any) -> Any:
    """폼이 보낸 하위 모델 dict 에서, None 을 허용하지 않는 필드의 None 은 빼서 기본값이 적용되게 한다 (중첩 모델도)."""
    import types
    import typing

    from pydantic import BaseModel

    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for k, v in data.items():
        f = cls.model_fields.get(k)
        if f is None:
            out[k] = v
            continue
        ann = f.annotation
        if v is None:
            if _allows_none(ann):
                out[k] = None
            continue
        inner = ann
        if typing.get_origin(ann) in (typing.Union, types.UnionType):
            inner = next((a for a in typing.get_args(ann) if a is not type(None)), ann)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            v = _clean_sub(inner, v)
            if inner is Source and isinstance(v, dict) and not (v.get("name") or v.get("url")) and _allows_none(ann):
                v = None   # 빈 출처 칸은 출처 없음
            elif isinstance(v, dict) and _allows_none(ann) and not any(x not in (None, "", []) for x in v.values()):
                v = None   # 전부 빈 하위 칸(사건 뒤 outcome 등) 은 「없음」 — 빈 껍데기를 저장하지 않는다
        out[k] = v
    return out


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    from ..models import Driver, Event, Rule, Scenario

    sub_cls = {"driver": Driver, "scenario": Scenario, "rule": Rule, "event": Event}
    out = dict(fields)
    for k, cls in sub_cls.items():
        if isinstance(out.get(k), dict):
            out[k] = _clean_sub(cls, out[k])
    if isinstance(out.get("source"), dict) and not (out["source"].get("name") or out["source"].get("url")):
        out["source"] = None
    return out


def _apply_set(m: MindMap, nid: str, fields: dict[str, Any], today: date, res: EditResult) -> None:
    if nid not in m.nodes:
        raise EditError(f"{nid}: 노드가 없다")
    fields = dict(fields)
    accepted = fields.pop("_accepted", None) or []   # 편집 폼이 「채우기」 로 받아들인 제안값 (설계 v4 §6) — 노드에 저장하지 않고 history 출처로만
    bad = sorted(set(fields) - ALLOWED_FIELDS)
    if bad:
        raise EditError(f"{nid}: 고칠 수 없는 필드 {bad}")
    fields = _clean_fields(fields)
    old = m[nid]
    old_dump = old.model_dump(mode="json")
    data = dict(old_dump)
    data.update(fields)
    try:
        new = Node.model_validate(data)
    except ValidationError as e:
        raise EditError(f"{nid}: " + "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:3])) from e
    content = _changed(old_dump, new.model_dump(mode="json"), CONTENT_KEYS)
    accept_src = None
    if accepted and isinstance(accepted, list):
        from .suggest import record_acceptance

        accept_src = record_acceptance(m, nid, [a for a in accepted if isinstance(a, dict)], today=today)
    if content:
        prev = old.value if "value" in content else next((getattr(old, k).model_dump(mode="json") for k in content if getattr(old, k) is not None), None)
        # HistoryPoint.value 는 필수이고 맵 저장은 exclude_none 이라 None 을 넣으면 다시 읽지 못한다
        new.history = [*old.history, HistoryPoint(as_of=old.as_of or today, value="(비어 있음)" if prev is None else prev,
                                                  source=accept_src or Source(name=EDIT_SOURCE, retrieved_at=today))]
        if "as_of" not in fields:
            new.as_of = today
    new.tags = sorted((set(new.tags) | {USER_TAG}) - DROP_TAGS)
    m.nodes[nid] = new
    res.applied.append(f"set {nid}" + (f" ({', '.join(content)})" if content else "") + (" ← 제안 수락" if accept_src else ""))


def _apply_add(m: MindMap, parent: str, fields: dict[str, Any], today: date, res: EditResult, temp_id: str | None) -> str:
    nid = next_child_id(m, parent)
    real_parent = parent[:-2] if parent.endswith(".*") else parent
    bad = sorted(set(fields) - ALLOWED_FIELDS)
    if bad:
        raise EditError(f"새 노드: 고칠 수 없는 필드 {bad}")
    fields = {k: v for k, v in _clean_fields(fields).items() if v is not None or k in ("value", "source", "confidence", "weight")}
    data = {"id": nid, "map": m.kind.value, "parent": real_parent, "label": "", "node_type": "estimate", "value_type": "text", "as_of": today.isoformat()}
    data.update(fields)
    if not str(data.get("label") or "").strip():
        raise EditError("새 노드: 라벨이 비어 있다")
    try:
        node = Node.model_validate(data)
    except ValidationError as e:
        raise EditError("새 노드: " + "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:3])) from e
    node.tags = sorted((set(node.tags) | {USER_TAG}) - DROP_TAGS)
    if node.source is None:
        node.source = Source(name=EDIT_SOURCE, retrieved_at=today)
    m.add(node)
    if temp_id:
        res.id_map[temp_id] = nid
    res.applied.append(f"add {nid} ← {real_parent}")
    return nid


def _apply_delete(m: MindMap, nid: str, res: EditResult) -> None:
    if nid not in m.nodes:
        raise EditError(f"{nid}: 노드가 없다")
    if nid == m.root_id:
        raise EditError("루트는 지울 수 없다")
    gone = [nid, *(d.id for d in m.descendants(nid))]
    for g in gone:
        del m.nodes[g]
    for n in m.nodes.values():
        if any(g in n.links for g in gone):
            n.links = [x for x in n.links if x not in gone]
            res.warnings.append(f"{n.id} 의 연결에서 지운 노드를 걷어냈다")
    res.applied.append(f"delete {nid}" + (f" (+{len(gone) - 1})" if len(gone) > 1 else ""))


def _resolve(nid: str | None, res: EditResult) -> str | None:
    return res.id_map.get(nid, nid) if nid else nid


def apply_edit_ops(m: MindMap, edits: list[dict[str, Any]], *, today: date) -> EditResult:
    """연산을 순서대로 적용 (검증·버전 없음 — 복사본 위에서 쓰거나 apply_edits 가 부른다)."""
    res = EditResult()
    for i, e in enumerate(edits):
        op = e.get("op")
        fields = dict(e.get("fields") or {})
        if op == "set":
            nid = _resolve(e.get("id"), res)
            if not nid:
                raise EditError(f"연산 {i + 1}: id 가 없다")
            _apply_set(m, nid, fields, today, res)
        elif op == "add":
            parent = _resolve(e.get("parent"), res)
            if not parent:
                raise EditError(f"연산 {i + 1}: parent 가 없다")
            _apply_add(m, parent, fields, today, res, e.get("id"))
        elif op == "delete":
            nid = _resolve(e.get("id"), res)
            if not nid:
                raise EditError(f"연산 {i + 1}: id 가 없다")
            _apply_delete(m, nid, res)
        else:
            raise EditError(f"연산 {i + 1}: 모르는 op {op!r}")
    return res


def preview_edits(m: MindMap, edits: list[dict[str, Any]], *, today: date) -> tuple[MindMap, EditResult]:
    """복사본에 적용해 (새 맵, 결과) 를 돌려준다. 원본은 그대로. new_errors 에 편집으로 늘어난 검증 오류."""
    before = _errors(m, today)
    trial = m.model_copy(deep=True)
    res = apply_edit_ops(trial, edits, today=today)
    res.warnings += finalize_after_edit(trial, today=today)   # 미리보기도 계산 노드·민감도를 다시 계산해 보여 준다
    res.new_errors = [f"{nid or '-'}: {msg}" for nid, msg in sorted(_errors(trial, today) - before, key=str)]
    return trial, res


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def apply_edits(m: MindMap, payload: dict[str, Any], *, today: date, versions_dir: Path | None = None,
                check_hash: bool = True) -> EditResult:
    """payload = {"map_id", "base_hash", "edits"}. 복사본 검증 → 버전 보존 → 원본 적용. 실패는 EditError."""
    edits = payload.get("edits") or []
    if not isinstance(edits, list) or not edits:
        raise EditError("편집 내용이 없다")
    if payload.get("map_id") and payload["map_id"] != m.map_id:
        raise EditError(f"다른 맵의 편집이다 ({payload['map_id']} ≠ {m.map_id})")
    if check_hash and payload.get("base_hash") and payload["base_hash"] != edit_hash(m):
        raise EditError("맵이 편집을 시작했을 때와 다르다 — 이미 반영했거나 그 사이 다른 수정이 있었다. 페이지라면 새로고침, 편집 파일이면 내용을 확인한 뒤 --force")
    _, trial_res = preview_edits(m, edits, today=today)
    if trial_res.new_errors:
        raise EditError("저장하면 검증 오류가 늘어 되돌렸다: " + "; ".join(trial_res.new_errors[:3]))
    ver = int(m.meta.get("version", 1))
    if versions_dir is not None:
        versions_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(versions_dir / f"{m.map_id.replace(':', '_')}.v{ver}.json", json.dumps(m.model_dump(mode="json"), ensure_ascii=False, indent=2))
    res = apply_edit_ops(m, edits, today=today)
    res.version = ver + 1
    m.meta["version"] = ver + 1
    m.meta.setdefault("edit_log", []).append({"date": today.isoformat(), "version": ver + 1, "source": "html", "ops": res.applied[:50]})
    if "A8.2" in m.nodes:
        log = m["A8.2"].value if isinstance(m["A8.2"].value, list) else []
        log.append({"date": today.isoformat(), "text": f"HTML 편집 {len(res.applied)}건: " + "; ".join(res.applied)[:300], "version": ver + 1})
        m["A8.2"].value = log
        m["A8.2"].as_of = today
    m.touch(today)
    return res


# ---------------------------------------------------------------------------- 편집 폼 스키마 (HTML 이 필드를 그리는 데 쓴다)
_PATTERN_OPTIONS = {"^(fact|price|weight|time|event|evidence)$": ["fact", "price", "weight", "time", "event", "evidence"],
                    "^(armed|fired|disabled)$": ["armed", "fired", "disabled"],
                    "^(computed|user|broker)$": ["computed", "user", "broker"],
                    "^(consensus|my_estimate|normalized|user)$": ["consensus", "my_estimate", "normalized", "user"],
                    "^(quant|qualitative)$": ["quant", "qualitative"], "^(cause|result)$": ["cause", "result"],
                    # 설계 v7
                    "^(required|core|secondary)$": ["required", "core", "secondary"], "^(shift|review|warn|kill)$": ["shift", "review", "warn", "kill"],
                    "^(bear|bull)$": ["bear", "bull"], "^(abs|pct|cum|state)$": ["abs", "pct", "cum", "state"],
                    "^(none|qoq|yoy|vs_consensus)$": ["none", "qoq", "yoy", "vs_consensus"], "^(confirmed|reported|forecast)$": ["confirmed", "reported", "forecast"],
                    "^(broker|consensus|interpolated|user)$": ["broker", "consensus", "interpolated", "user"]}
_SCHEMA_SKIP = {"sensitivity", "model", "light", "light_reason", "trend"}   # 코드가 계산하는 필드 — 폼에서 고치지 않는다
_KO = {"current": "현재", "consensus": "컨센서스", "my_estimate": "내 추정", "value": "값", "as_of": "기준일", "source": "출처", "name": "이름",
       "metric": "변수 id", "period": "기간", "kind": "종류 (quant·qualitative)", "role": "역할 (cause·result)", "proxies": "대체 관측 지표",
       "invalidation_condition": "무효화 조건", "invalidation_rule": "Kill 규칙 노드", "sensitivity_inputs": "민감도 입력 (segment_revenue 억원 · pass_through · tax_rate · shares · per_target · fcf …)",
       "url": "URL", "retrieved_at": "확인일", "rationale": "근거", "check_indicator": "확인 지표", "next_check": "다음 확인일", "at_entry": "매수 시점 값",
       "direction": "방향", "higher_is_better": "높을수록 좋음", "assumptions": "가정", "probability": "확률 (0~1)", "target_price": "목표가",
       "horizon_months": "기간 (개월)", "valuation_method": "밸류 방식", "trigger_type": "트리거 종류", "condition": "조건식", "action": "행동",
       "driver_assumptions": "드라이버 가정 묶음 (JSON 목록: node_id · variable · delta_pct)", "eps_basis": "EPS 기준 (consensus·my_estimate·normalized·user)",
       "eps_year": "기준 연도", "per": "적용 PER", "conditions": "성립 조건", "indicators": "확인 지표", "falsifier": "버리는 신호",
       "price_source": "목표가 출처 (computed·user·broker)", "node_id": "노드", "driver_id": "드라이버 id", "variable": "변수", "delta_pct": "변화 %",
       "size": "크기", "escalate_to": "격상 등급", "status": "상태", "fired_at": "발동일", "note": "메모", "date": "날짜", "fact": "사실",
       "affects": "영향 노드", "priced_in": "선반영", "materiality": "중요도",
       # 2026-10-30 A6 보강 — 이벤트를 일정표가 아니라 판단 항목으로
       "certainty": "일정 확실성 (공시확정 · 공시상 예정 · 과거 주기 추정 · 회사 목표 · 리포트 전망 · 통상 일정 추정)",
       "precheck": "사전 확인 지표 (쉼표로 여러 개)", "bull": "상방 증거 — 논제가 강해지는 조건", "bear": "하방 증거 — 논제가 약해지는 조건",
       "rule": "판단 규칙 — 결과에 따라 어느 노드를 어떻게 고치나", "outcome": "사건 뒤", "actual": "실제 결과",
       "event_id": "이벤트 ID (영속 · 비우면 코드가 정한다)", "evidence": "관련 전망·근거 (증권사 문장 — 판정 조건이 아니다)", "project": "사업 묶음 (같은 사업의 마일스톤 — 드라이버 id)", "program": "사업명 (X-energy · NuScale · 미국 AP1000)", "stage": "계약 진행 단계 (협의 중 · 조건 합의 · 체결 대기 · 체결)", "delivery_period": "납기·운전 시점 (사건 시점이 아님)", "measured_from": "측정 기준 거래일", "reaction_d1": "D+1 수익률 (0.03 = +3%)", "reaction_d5": "D+5 수익률", "vs_market_d5": "D+5 시장 평균 대비", "verdict": "판정 (강화·약화·중립)",
       # 설계 v7
       "thesis_role": "논제 역할 (required 필수 · core 핵심 수익 · secondary 보조)", "thesis_role_source": "역할 출처 (user·proposed)",
       "observations": "관측값 목록 (JSON: metric_id · period · value_type · basis · value · as_of · compare_at · source · verification)",
       "required": "필요 경로 (JSON: ruler · points[at·value·source·band])", "band": "허용 띠 (± 자의 단위)", "bound_bear": "Bear 경계", "bound_bull": "Bull 경계",
       "bounds_source": "경계 출처 (user·proposed)", "grade": "규칙 등급 (shift 확률 이동 · review 재검토 · warn 경고 · kill)", "threshold": "경계값", "side": "경계 쪽 (bear·bull)",
       "persistence": "지속성 (연속 n회)", "proposal": "제안 (JSON: kind · scenario_from · scenario_to · pct_points)"}


def _field_schema(name: str, ann: Any, meta: list[Any], prefix: str) -> list[dict[str, Any]]:
    import enum
    import types
    import typing

    from pydantic import BaseModel

    key = f"{prefix}{name}"
    label = " · ".join(_KO.get(p, p) for p in (*prefix.rstrip(".").split("."), name) if p)
    adv = (".source." in key or key.endswith((".as_of", ".rationale", ".retrieved_at")) or key.startswith("source.")
           or key in ("metric", "period", "role", "invalidation_rule", "at_entry", "direction", "higher_is_better", "sensitivity_inputs",
                      "eps_year", "indicators", "falsifier", "horizon_months"))
    origin = typing.get_origin(ann)
    args = [a for a in typing.get_args(ann) if a is not type(None)] if origin in (typing.Union, types.UnionType) else []
    if args:
        ann = args[0]
        origin = typing.get_origin(ann)
    for mt in meta:
        pat = getattr(mt, "pattern", None)
        if pat in _PATTERN_OPTIONS:
            return [{"key": key, "label": label, "type": "select", "options": _PATTERN_OPTIONS[pat], "adv": adv}]
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        return [{"key": key, "label": label, "type": "select", "options": [e.value for e in ann], "adv": adv}]
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        out: list[dict[str, Any]] = []
        for k, f in ann.model_fields.items():
            out += _field_schema(k, f.annotation, list(f.metadata), f"{key}.")
        return out
    if ann is bool:
        return [{"key": key, "label": label, "type": "bool", "adv": adv}]
    if ann in (int, float):
        return [{"key": key, "label": label, "type": "number", "adv": adv}]
    if ann is date:
        return [{"key": key, "label": label, "type": "date", "adv": adv}]
    if origin is list:
        inner = [a for a in typing.get_args(ann) if a is not type(None)]
        if inner and isinstance(inner[0], type) and issubclass(inner[0], BaseModel):
            return [{"key": key, "label": label, "type": "json", "adv": adv}]   # 구조 목록 (드라이버 가정 묶음) 은 JSON 으로
        return [{"key": key, "label": label, "type": "list", "adv": adv}]
    if ann is str:
        return [{"key": key, "label": label, "type": "text", "adv": adv}]
    return [{"key": key, "label": label, "type": "json", "adv": adv}]


def edit_schemas() -> dict[str, Any]:
    """HTML 편집 폼이 쓰는 스키마: 하위 모델별 평탄화 필드(`current.value` 꼴) 와 노드 열거형."""
    from ..models import Confidence, Driver, Event, NodeStatus, NodeType, Rule, Scenario, ValueType

    subs: dict[str, list[dict[str, Any]]] = {}
    for kind, cls in (("driver", Driver), ("scenario", Scenario), ("rule", Rule), ("event", Event)):
        rows: list[dict[str, Any]] = []
        for k, f in cls.model_fields.items():
            if k in _SCHEMA_SKIP:
                continue
            rows += _field_schema(k, f.annotation, list(f.metadata), "")
        if kind == "event":
            # 이벤트의 기간·방향은 고급 칸이 아니다 (드라이버의 period 와 이름만 같다). 사건 뒤 칸은 접어 둔다
            for r in rows:
                if r["key"] in ("period", "direction"):
                    r["adv"] = False
                if r["key"].startswith("outcome.") or r["key"] == "event_id":
                    r["adv"] = True
        subs[kind] = rows
    return {"subs": subs, "enums": {"status": [e.value for e in NodeStatus], "confidence": [e.value for e in Confidence],
                                    "node_type": [e.value for e in NodeType], "value_type": [e.value for e in ValueType]},
            "sub_labels": {"driver": "드라이버", "scenario": "시나리오", "rule": "규칙", "event": "이벤트"}}


def _refresh_outcomes(m: MindMap, today: date) -> list[str]:
    """리서치 DB 가 닿으면 지난 촉매의 수익률을 채운다. 못 닿으면 조용히 비운다 — 편집 저장을 막을 일이 아니다."""
    try:
        from ..collect.adapters.research_db import ResearchDb
        from ..symbols import research_db_path
        from .draft import fill_event_outcomes

        rdb = research_db_path()
        if rdb is None or not rdb.exists() or "A6.1" not in m.nodes:
            return []
        return fill_event_outcomes(m, ResearchDb(rdb), today=today)
    except Exception:  # noqa: BLE001
        return []


def finalize_after_edit(m: MindMap, *, today: date) -> list[str]:
    """편집 뒤 계산 노드(ER·손익비·상태등) 를 다시 계산한다. 계산이 안 되는 상태(값 미입력) 는 경고로만."""
    from ..engine import evaluate_holding, evaluate_new_map
    from ..models import HoldingMap, NewMap

    notes: list[str] = []
    try:
        if isinstance(m, NewMap):
            res = evaluate_new_map(m, today=today, enforce_gate=True)
            notes += [f"계산: {x}" for x in getattr(res, "notes", [])]
            from ..engine.attractiveness import compute_valuation_all
            from ..engine.sensitivity import compute_all

            compute_all(m, today=today)   # 드라이버·민감도 입력이 바뀌었을 수 있다 (설계 v5 R3)
            notes += [f"밸류: {x}" for x in compute_valuation_all(m, today=today) if "⚠" in x or "미계산" in x]   # 설계 v6: 가정 묶음·평가 기준이 바뀌었을 수 있다
            from ..engine.decision import compute_v7_all

            notes += [f"v7: {x}" for x in compute_v7_all(m, today=today) if "이벤트" in x or "신호등" in x]   # 설계 v7: 관측값·띠·역할·규칙이 바뀌었을 수 있다
            res2 = evaluate_new_map(m, today=today, enforce_gate=True)   # 시나리오 가격이 모델로 바뀌었으면 ER·손익비 다시
            notes += [f"계산: {x}" for x in getattr(res2, "notes", []) if x not in notes]
            notes += [f"촉매 결과: {x}" for x in _refresh_outcomes(m, today)]   # 2026-10-30: 지난 촉매의 D+1/D+5 (시세가 있을 때만)
        elif isinstance(m, HoldingMap):
            evaluate_holding(m, today=today)
    except Exception as e:  # noqa: BLE001 — 편집 저장을 막지 않는다
        notes.append(f"재계산 실패 (저장은 됨): {e}")
    return notes


def load_edit_file(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "edits" not in data:
        raise EditError(f"{path}: 편집 파일 형식이 아니다 (map_id · base_hash · edits)")
    return data


__all__ = ["EditError", "EditResult", "edit_hash", "next_child_id", "apply_edit_ops", "preview_edits", "apply_edits", "load_edit_file",
           "EDIT_SOURCE", "USER_TAG", "edit_schemas", "finalize_after_edit"]
