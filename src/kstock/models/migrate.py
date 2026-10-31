"""맵 스키마 마이그레이션 — 2.0 (2026-10-28): A 맵의 「밸류에이션 & 시나리오」(옛 A3) 를 두 가지로 나눴다.

  옛 A3 밸류에이션 & 시나리오          →  A3 밸류에이션 (재무 전망 · 평가 기준 · 평가 모델 · 모델 신뢰도 · 밸류 근거)
                                       +  A4 시나리오 (한눈에 · Bear · Base · Bull · 기대수익률 · 손익비 · 확률가중 매력도)
  옛 A4 리스크 → A5, A5 촉매 → A6, A6 실행 계획 → A7, A7 업데이트 → A8, A8 출처 → A9.

노드 id 가 곧 참조(링크 · formula · meta 의 키) 라서 바꾸면 전부 같이 옮겨야 한다. `load_map` 이 schema_version < 2.0 인 맵을 읽을 때
`migrate_map_data` 를 한 번 돌리고 2.0 으로 표시한다 — 파일은 저장할 때 바뀐다 (`mindmap migrate` 가 한꺼번에 저장·렌더).
B·C 맵은 id 가 B·C 라 그대로지만 `links` 에 적힌 A id 는 같이 옮긴다.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "2.0"

# 옛 id (또는 그 접두) → 새 id. 긴 접두가 먼저 맞는다 ("A3.10" 은 "A3.1" 보다 먼저). 'A3' 묶음 자체와 A0·A1·A2 는 그대로.
ID_MAP_2_0: dict[str, str] = {
    "A3.7": "A3.1",     # 재무 전망 (결과 지표)
    "A3.8": "A3.2",     # 평가 기준
    "A3.9": "A3.3",     # 평가 모델
    "A3.11": "A3.4",    # 모델 신뢰도 · 갱신 이력
    "A3.6": "A3.5",     # 밸류 근거
    "A3.0": "A4.1",     # 시나리오 한눈에
    "A3.1": "A4.2",     # Bear
    "A3.2": "A4.3",     # Base
    "A3.3": "A4.4",     # Bull
    "A3.4": "A4.5",     # 기대수익률
    "A3.5": "A4.6",     # 손익비
    "A3.10": "A4.7",    # 확률가중 투자 매력도
    "A4": "A5",         # 리스크 & 무효화 조건
    "A5": "A6",         # 촉매
    "A6": "A7",         # 실행 계획
    "A7": "A8",         # 업데이트
    "A8": "A9",         # 출처
}
_TOKEN = re.compile(r"(?<![A-Za-z0-9_])A[0-9]+(?:\.[0-9]+)*(?![A-Za-z0-9_])")


def remap_id(node_id: str, id_map: dict[str, str] | None = None) -> str:
    """'A3.7.2' → 'A3.1.2', 'A4.4.1' → 'A5.4.1', 'A3' → 'A3'. 가장 긴 구간 접두를 찾아 바꾼다."""
    id_map = id_map or ID_MAP_2_0
    segs = node_id.split(".")
    for k in range(len(segs), 0, -1):
        pre = ".".join(segs[:k])
        if pre in id_map:
            rest = segs[k:]
            return id_map[pre] + ("." + ".".join(rest) if rest else "")
    return node_id


def remap_text(text: str, id_map: dict[str, str] | None = None) -> str:
    """문장 안의 A id 토큰을 전부 옮긴다 (formula · 질문 · 로그 문장). 종목코드 'A005930' 같은 것은 첫 구간이 표에 없어 그대로."""
    return _TOKEN.sub(lambda m: remap_id(m.group(0), id_map), text)


def _remap_value(v: Any, id_map: dict[str, str]) -> Any:
    """meta 처럼 자유로운 dict 의 키·값 가운데 A id 모양인 것을 옮긴다 (문장 속 토큰도)."""
    if isinstance(v, dict):
        return {(remap_id(k, id_map) if isinstance(k, str) and _TOKEN.fullmatch(k) else k): _remap_value(x, id_map) for k, x in v.items()}
    if isinstance(v, list):
        return [_remap_value(x, id_map) for x in v]
    if isinstance(v, str):
        return remap_id(v, id_map) if _TOKEN.fullmatch(v) else remap_text(v, id_map)
    return v


_A3_OLD_LABEL = "밸류에이션 & 시나리오"


def migrate_map_data(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """JSON dict 를 2.0 으로. 돌려주는 것은 (새 dict, 바뀌었나). 이미 2.0 이면 그대로."""
    ver = str(data.get("schema_version") or "1.0")
    if _version_tuple(ver) >= _version_tuple(SCHEMA_VERSION):
        return data, False
    id_map = ID_MAP_2_0
    kind = str(data.get("kind") or "").upper()
    out = dict(data)
    nodes = data.get("nodes") or {}
    new_nodes: dict[str, Any] = {}
    if kind == "NEW":
        for nid, n in nodes.items():
            n2 = dict(n)
            new_id = remap_id(nid, id_map)
            n2["id"] = remap_id(str(n.get("id") or nid), id_map)
            if n2.get("parent"):
                # 부모는 새 id 에서 다시 뺀다 — 옛 A3.1(Bear) 은 A4.2 가 되어 부모가 A3 에서 A4 로 바뀐다. 묶음(A1~A9) 의 부모 A0 은 그대로
                n2["parent"] = new_id.rsplit(".", 1)[0] if "." in new_id else remap_id(str(n2["parent"]), id_map)
            if n2.get("links"):
                n2["links"] = [remap_id(str(x), id_map) for x in n2["links"]]
            if isinstance(n2.get("formula"), str):
                n2["formula"] = remap_text(n2["formula"], id_map)
            if isinstance(n2.get("question"), str):
                n2["question"] = remap_text(n2["question"], id_map)
            if isinstance(n2.get("value"), (str, list, dict)):
                n2["value"] = _remap_value(n2["value"], id_map)
            if new_id == "A3" and n2.get("label") == _A3_OLD_LABEL:
                n2["label"] = "밸류에이션"
                n2["question"] = "값이 얼마인가 — 결과 지표 전망 · 평가 기준(사람) · 평가 모델(코드) · 모델 신뢰도 · 밸류 근거"
            new_nodes[new_id] = n2
        # 옛 A3 의 시나리오 자식들이 옮겨 간 새 묶음 A4 「시나리오」 — 자식이 하나라도 있으면 묶음 노드를 만든다
        if any(k.startswith("A4.") for k in new_nodes) and "A4" not in new_nodes:
            new_nodes["A4"] = {"id": "A4", "map": "NEW", "parent": "A0", "label": "시나리오", "node_type": "question", "value_type": "text",
                               "question": "확률·목표가로 숫자화된 세 갈래 — 드라이버 가정 묶음이 통합 모델을 거쳐 목표가가 된다 (가정·확률은 사람)",
                               "status": "unverified"}
        # 부모가 A0 인 묶음 순서는 id 정렬이 정한다 — nodes dict 순서는 보기 좋게 id 순
        out["nodes"] = {k: new_nodes[k] for k in sorted(new_nodes, key=_sort_key)}
        if isinstance(data.get("meta"), dict):
            out["meta"] = _remap_value(data["meta"], id_map)
    else:
        for nid, n in nodes.items():
            n2 = dict(n)
            if n2.get("links"):
                n2["links"] = [remap_id(str(x), id_map) if str(x).startswith("A") else x for x in n2["links"]]
            new_nodes[nid] = n2
        out["nodes"] = new_nodes
    out["schema_version"] = SCHEMA_VERSION
    return out, True


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (1, 0)


def _sort_key(node_id: str) -> tuple:
    out: list[Any] = []
    for part in node_id.split("."):
        if part.isdigit():
            out.append((0, int(part)))
        elif len(part) >= 2 and part[1:].isdigit():
            out.append((0, int(part[1:])))
        else:
            out.append((1, part))
    return tuple(out)


__all__ = ["SCHEMA_VERSION", "ID_MAP_2_0", "remap_id", "remap_text", "migrate_map_data"]
